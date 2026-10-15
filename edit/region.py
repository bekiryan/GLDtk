"""Region and section edits: regenerate the content of a rectangle with locked seams.

    1. read the file (``context.load_context``), extract the platform graph,
       record the baseline (``check.make_baseline``) and find the seams
    2. the LLM returns a :class:`RegionSpec` (nodes, edges, entities and new
       requirements in region-local tiles); ``_parser`` validates and patches it
    3. stamp it into the grid (``stitch.apply_region``; the seams and the
       footing of kept entities win), place its entities
    4. verify the whole level (``check.verify_level``); on failure repair
       deterministically inside the region (stepping stones, moving region
       entities), then at most one LLM re-prompt with the errors; once it
       verifies, the comfort pass (``layout.comfort.comfort_grid``) shortens
       long golden-path moves that start or end in the region with platforms
       inside it, then densify fills the region
    5. still failing → an error, nothing written; otherwise ``outcome.finish``
       snapshots and writes the file

A section edit is a region edit whose rect is the section's band.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from pydantic import Field

from api import jobs
from layout.comfort import comfort_grid
from layout.densify import densify_grid
from layout.ir import IREntity, TileValue
from layout.spec_entities import FLYER_HEIGHTS, entity_metadata, spread_cols
from layout.spec_layout import SectionBand, grid_to_spec
from llm.providers import LLMProvider
from spec.level_spec import (
    AbilityName,
    EdgeKind,
    Requirement,
    SpecEdge,
    SpecEntity,
    SpecNode,
    _SpecModel,
)
from symbolic.extractor import move_between

from .check import Baseline, LevelCheck, make_baseline, preexisting_blocker, verify_level
from .context import (
    FIXED_KINDS,
    Cell,
    EditContext,
    EditError,
    Locks,
    Rect,
    Seam,
    SpanRect,
    find_seams,
    load_context,
    locked_cells,
    region_view,
)
from .llm import EditLLMFailed, run_structured
from .outcome import EditOutcome, add_requirements, base_spec, failure, finish, spec_entity
from .prompts import REGION_SYSTEM_PROMPT, with_preferences
from .stitch import RegionNode, apply_region

MAX_REPAIR_ROUNDS = 4
STONE_WIDTH = 3
_MAX_STONE_CANDIDATES = 400


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────


class RegionNodeSpec(_SpecModel):
    id: str = Field(..., description="Unique node id, no spaces.")
    type: Literal["Platform", "Hazard"]
    col: int = Field(..., description="Region-local column of the left end (0 = region left).")
    row: int = Field(..., description="Region-local row (0 = region top, rows grow downward).")
    w: int = Field(3, description="Width in tiles.")


class RegionEdge(_SpecModel):
    from_: str = Field(..., alias="from", description="Node or seam id.")
    to: str = Field(..., description="Node or seam id.")
    type: EdgeKind
    ability: Optional[AbilityName] = None


class RegionEntity(SpecEntity):
    anchor: str = Field(..., description="Id of the Platform node or seam the entity stands on.")
    section: Optional[str] = Field(None, description="Section id; null = the anchor's section.")


class RegionSpec(_SpecModel):
    nodes: List[RegionNodeSpec]
    edges: List[RegionEdge]
    entities: List[RegionEntity]
    requirements: List[Requirement]


# ─────────────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────────────


def _local(rect: Rect, col: int, row: int) -> Tuple[int, int]:
    return col - rect.col, row - rect.row


def _entity_brief(ent: IREntity, rect: Rect) -> Dict[str, Any]:
    c, r = _local(rect, ent.col, ent.row)
    skip = {"iid", "anchor", "original_anchor", "blocked", "node_id", "structural"}
    out: Dict[str, Any] = {"id": ent.metadata.get("spec_id") or ent.metadata.get("iid"),
                           "kind": ent.entity_type, "col": c, "row": r}
    out.update({k: v for k, v in ent.metadata.items() if k not in skip and k != "spec_id"
                and v not in (None, "", [])})
    return out


def build_region_messages(
    ctx: EditContext, rect: Rect, seams: List[Seam], prompt: str, region_entities: List[IREntity]
) -> List[Dict[str, Any]]:
    lines = [f"REQUEST: {prompt.strip()}", ""]
    if ctx.level.prompt:
        lines += [f"The whole level was generated from: \"{ctx.level.prompt.strip()}\"", ""]
    lines.append(
        f"REGION: {rect.w} × {rect.h} tiles (level columns {rect.col}..{rect.right}, rows "
        f"{rect.row}..{rect.bottom}) of a {ctx.width} × {ctx.height} level."
    )
    lines.append("SECTIONS in the region:")
    for b in ctx.bands_in(rect):
        lines.append(
            f"  - {b.id} \"{b.name}\": theme {b.theme or 'default'}, difficulty {b.difficulty}, "
            f"gravity_scale {b.gravity_scale:g}; region columns "
            f"{max(b.col_start, rect.col) - rect.col}..{min(b.col_end, rect.right) - rect.col}, rows "
            f"{max(b.row_start, rect.row) - rect.row}..{min(b.row_end, rect.bottom) - rect.row}"
            + (f"; built as {b.structure}, carved from rock: the rock and the carved space "
               "stay, your nodes are added inside the carved space" if b.is_terrain else "")
        )
    lines.append("SEAMS (locked; connect your content to them):")
    if not seams:
        lines.append("  none — no path crosses the region boundary.")
    for i, s in enumerate(seams, 1):
        c0, r = _local(rect, s.col_start, s.row)
        c1 = s.col_end - rect.col
        what = {"in": "the player arrives here from outside",
                "out": "the player leaves the region from here",
                "both": "the player arrives and leaves here"}[s.direction]
        lines.append(f"  - {s.id} (digit {i % 10} on the map): row {r}, columns {c0}..{c1}, "
                     f"{s.kind} seam on the {s.side} side; {what}.")
    lines.append("CURRENT ENTITIES in the region (Start / Exit are fixed and not listed):")
    if not region_entities:
        lines.append("  none")
    for e in region_entities:
        lines.append("  " + json.dumps(_entity_brief(e, rect), ensure_ascii=False))
    lines += ["", "MAP (region and surroundings):", region_view(ctx, rect, seams), ""]
    lines += ["GAME PROFILE (fixed for this game):", ctx.profile.summary_for_prompt()]
    return [{"role": "user", "content": with_preferences("\n".join(lines))}]


# ─────────────────────────────────────────────────────────────────────────────
# Validate + patch
# ─────────────────────────────────────────────────────────────────────────────


def _unique(base: str, taken: Set[str]) -> str:
    base = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in (base or "e")) or "e"
    name, k = base, 1
    while name in taken:
        k += 1
        name = f"{base}_{k}"
    taken.add(name)
    return name


def _parser(ctx: EditContext, rect: Rect, seams: List[Seam], outside_ids: Set[str]):
    seam_ids = {s.id for s in seams}
    band_ids = {b.id for b in ctx.bands}
    def parse(spec: RegionSpec):
        notes: List[str] = []
        errors: List[str] = []
        taken: Set[str] = set(seam_ids)
        nodes: List[RegionNodeSpec] = []
        renamed: Dict[str, str] = {}
        for n in spec.nodes:
            nid = _unique(n.id, taken)
            if nid != n.id:
                renamed.setdefault(n.id, nid)
            w = max(1, min(n.w, rect.w))
            col = min(max(n.col, 0), rect.w - w)
            min_row = 1 if n.type == "Platform" and rect.h > 1 else 0
            row = min(max(n.row, min_row), rect.h - 1)
            if (col, row, w) != (n.col, n.row, n.w):
                notes.append(f"clamped node '{nid}' into the region")
            nodes.append(n.model_copy(update={"id": nid, "col": col, "row": row, "w": w}))
        platforms = [n.id for n in nodes if n.type == "Platform"] + [s.id for s in seams]
        valid = set(platforms) | {n.id for n in nodes}

        edges = []
        for e in spec.edges:
            a, b = renamed.get(e.from_, e.from_), renamed.get(e.to, e.to)
            if a in valid and b in valid and a != b:
                edges.append(e.model_copy(update={"from_": a, "to": b}))
            else:
                notes.append(f"dropped edge {e.from_}→{e.to} (unknown node)")

        entities: List[RegionEntity] = []
        ent_taken = set(outside_ids)
        for e in spec.entities:
            if e.kind in FIXED_KINDS:
                notes.append(f"ignored {e.kind} entity '{e.id}' (Start and Exit are fixed)")
                continue
            anchor = renamed.get(e.anchor, e.anchor)
            if anchor not in platforms:
                if not platforms:
                    errors.append(f"Entity '{e.id}' needs a Platform node to stand on, and the "
                                  "region has none: add Platform nodes.")
                    continue
                notes.append(f"re-anchored '{e.id}' from unknown '{e.anchor}' to '{platforms[0]}'")
                anchor = platforms[0]
            eid = _unique(e.id, ent_taken)
            section = e.section if e.section in band_ids else None
            entities.append(e.model_copy(update={"id": eid, "anchor": anchor, "section": section}))
        local_ids = {e.id for e in entities}
        for i, e in enumerate(entities):
            upd = {}
            for ref in ("linked_to", "opens"):
                v = getattr(e, ref)
                if v and v not in local_ids and v not in outside_ids:
                    notes.append(f"cleared {ref} of '{e.id}' (unknown entity '{v}')")
                    upd[ref] = None
            if upd:
                entities[i] = e.model_copy(update=upd)
        if errors:
            return None, errors, notes
        return RegionSpec(nodes=nodes, edges=edges, entities=entities,
                          requirements=list(spec.requirements)), [], notes

    return parse


# ─────────────────────────────────────────────────────────────────────────────
# Entity placement
# ─────────────────────────────────────────────────────────────────────────────


def _free(grid, rect: Rect, col: int, row: int, taken: Set[Cell]) -> bool:
    return rect.contains(col, row) and (col, row) not in taken and int(grid[row][col]) == TileValue.EMPTY


def _standing_cells(grid, rect: Rect, row: int, c0: int, c1: int, taken: Set[Cell]) -> List[int]:
    return [c for c in range(c0, c1 + 1)
            if rect.contains(c, row) and int(grid[row][c]) == TileValue.SOLID
            and _free(grid, rect, c, row - 1, taken)]


def place_region_entities(
    ctx: EditContext, rect: Rect, grid, spec: RegionSpec, nodes: Dict[str, RegionNode],
    seams: List[Seam], taken: Set[Cell],
) -> Tuple[List[IREntity], List[str]]:
    """Place the RegionSpec's entities on their anchors (spread, never two per cell)."""
    warnings: List[str] = []
    anchors: Dict[str, SpanRect] = {nid: (n.row, n.col, n.col + n.w - 1)
                                    for nid, n in nodes.items() if n.type == "Platform"}
    anchors.update({s.id: (s.row, s.col_start, s.col_end) for s in seams})
    groups: Dict[str, List[RegionEntity]] = {}
    for e in spec.entities:
        groups.setdefault(e.anchor, []).append(e)
    placed: List[IREntity] = []
    for anchor, ents in groups.items():
        row, c0, c1 = anchors[anchor]
        for ent, want in zip(ents, spread_cols(c0, c1 - c0 + 1, len(ents))):
            spot = _pick_cell(grid, rect, ent, row, c0, c1, want, taken)
            where = anchor
            if spot is None:  # the anchor is full: the nearest other platform with room
                others = sorted((a for a in anchors if a != anchor),
                                key=lambda a: abs(anchors[a][0] - row) + abs(anchors[a][1] - c0))
                for other in others:
                    r2, a0, a1 = anchors[other]
                    spot = _pick_cell(grid, rect, ent, r2, a0, a1, (a0 + a1) // 2, taken)
                    if spot is not None:
                        warnings.append(f"moved {ent.id} from {anchor} to {other} (no free cell)")
                        where = other
                        break
            if spot is None:
                warnings.append(f"dropped {ent.id}: no free cell with headroom in the region")
                continue
            taken.add(spot)
            section = ent.section or ctx.band_at(*spot).id
            meta = entity_metadata(SpecEntity.model_validate({**ent.model_dump(by_alias=True),
                                                              "section": section}), where)
            meta["iid"] = str(uuid.uuid4())
            placed.append(IREntity(ent.kind, spot[0], spot[1], meta))
    return placed, warnings


def _pick_cell(grid, rect, ent: RegionEntity, row: int, c0: int, c1: int, want: int,
               taken: Set[Cell]) -> Optional[Cell]:
    cols = sorted(_standing_cells(grid, rect, row, c0, c1, taken), key=lambda c: (abs(c - want), c))
    if not cols:
        return None
    col = cols[0]
    if ent.kind == "Enemy" and ent.archetype == "flyer":
        for h in FLYER_HEIGHTS:
            if all(_free(grid, rect, col, row - d, taken) for d in range(1, h + 1)):
                return (col, row - h)
    return (col, row - 1)


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic repair (inside the region only)
# ─────────────────────────────────────────────────────────────────────────────


def _centre(span: SpanRect) -> Tuple[float, float]:
    return ((span[1] + span[2] + 1) / 2.0, float(span[0]))


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _insert_stone(ctx: EditContext, rect: Rect, grid, locks: Locks, blocked: Set[Cell],
                  sources: List[SpanRect], targets: List[SpanRect]) -> Optional[SpanRect]:
    """Add a 3-tile platform in *rect* that links a source span to a target span.

    Falls back to a stone reachable from a source that is closer to the target
    (the next round continues from it).  Returns the stone or None.
    """
    if not sources or not targets:
        return None
    ph = ctx.player_h
    pairs = sorted(((s, t) for s in sources for t in targets),
                   key=lambda st: _dist(_centre(st[0]), _centre(st[1])))[:4]
    width = min(STONE_WIDTH, rect.w)
    for src, tgt in pairs:
        mid = ((_centre(src)[0] + _centre(tgt)[0]) / 2.0, (src[0] + tgt[0]) / 2.0)
        cands = []
        for row in range(max(rect.row, ph), rect.bottom + 1):
            for col in range(rect.col, rect.right - width + 2):
                cells = [(c, row) for c in range(col, col + width)]
                head = [(c, row - d) for c in range(col, col + width) for d in range(1, ph + 1)]
                if any(int(grid[r][c]) != TileValue.EMPTY or (c, r) in locks.clear for c, r in cells):
                    continue
                if any(r < 0 or int(grid[r][c]) != TileValue.EMPTY or (c, r) in blocked for c, r in head):
                    continue
                cands.append((row, col))
        cands.sort(key=lambda rc: (_dist((rc[1] + width / 2.0, rc[0]), mid), rc))
        base = _dist(_centre(src), _centre(tgt))
        partial: Optional[Tuple[float, int, int]] = None
        for row, col in cands[:_MAX_STONE_CANDIDATES]:
            stone = (row, col, col + width - 1)
            saved = grid[row][col:col + width]
            grid[row][col:col + width] = [TileValue.SOLID] * width
            s_scale = ctx.gravity_scale_at((src[1] + src[2]) // 2, src[0])
            reached = move_between(grid, src, stone, ctx.profile, s_scale) is not None
            if reached and move_between(grid, stone, tgt, ctx.profile, ctx.gravity_scale_at(col, row)):
                return stone  # kept in the grid
            grid[row][col:col + width] = saved
            if reached:
                d = _dist(_centre(stone), _centre(tgt))
                if d < base - 2 and (partial is None or d < partial[0]):
                    partial = (d, row, col)
        if partial is not None:
            _, row, col = partial
            grid[row][col:col + width] = [TileValue.SOLID] * width
            return (row, col, col + width - 1)
    return None


def _move_entity(ctx: EditContext, rect: Rect, grid, ent: IREntity, reach_spans: List[SpanRect],
                 taken: Set[Cell]) -> bool:
    here = (float(ent.col), float(ent.row + 1))
    for span in sorted(reach_spans, key=lambda s: _dist(_centre(s), here)):
        row, c0, c1 = span
        cols = sorted(_standing_cells(grid, rect, row, c0, c1, taken), key=lambda c: abs(c - ent.col))
        if cols:
            taken.discard((ent.col, ent.row))
            ent.col, ent.row = cols[0], row - 1
            taken.add((ent.col, ent.row))
            return True
    return False


def repair_region(
    ctx: EditContext, rect: Rect, grid, entities: List[IREntity], region_iids: Set[str],
    locks: Locks, baseline: Baseline, check: LevelCheck, name,
) -> Tuple[LevelCheck, List[str]]:
    """Bounded deterministic repair; mutates *grid* and the region entities."""
    applied: List[str] = []
    taken = {(e.col, e.row) for e in entities}
    for _ in range(MAX_REPAIR_ROUNDS):
        if check.ok:
            break
        spans = check.spans
        rects = spans.rects
        path = [e for e in check.errors if e.code in ("NO_PATH", "SEAM_BROKEN", "SEAM_DEAD_END")]
        done = False
        if path:
            err = path[0]
            if err.code == "SEAM_DEAD_END" and err.span in rects:
                sources = [rects[err.span]]
                targets = [rects[s] for s in check.coreach if s in rects]
                targets = sorted(targets, key=lambda t: _dist(_centre(t), _centre(sources[0])))[:6]
            elif err.span in rects:
                targets = [rects[err.span]]
                sources = sorted((rects[s] for s in check.reach if s in rects),
                                 key=lambda s: _dist(_centre(s), _centre(targets[0])))[:6]
            else:
                sources, targets = [], []
            stone = _insert_stone(ctx, rect, grid, locks, taken, sources, targets)
            if stone:
                r, c0, c1 = stone
                applied.append(f"inserted a stepping stone at {name(c0, r)} (3 tiles) to fix "
                               f"{err.code}")
                done = True
        else:
            reach_spans = [rects[s] for s in check.reach if s in rects
                           and any(rect.contains(c, rects[s][0]) for c in range(rects[s][1], rects[s][2] + 1))]
            for err in check.errors:
                ent = next((e for e in entities if e.metadata.get("iid") == err.entity_iid), None)
                if ent is None or err.entity_iid not in region_iids:
                    continue
                if err.code == "ENTITY_UNREACHABLE" and err.span in rects:
                    # Keep the ledge the prompt asked for: link it to the reachable part first.
                    target = rects[err.span]
                    sources = sorted((rects[s] for s in check.reach if s in rects),
                                     key=lambda s: _dist(_centre(s), _centre(target)))[:6]
                    stone = _insert_stone(ctx, rect, grid, locks, taken, sources, [target])
                    if stone:
                        r, c0, _c1 = stone
                        applied.append(f"inserted a stepping stone at {name(c0, r)} (3 tiles) so "
                                       f"{ent.metadata.get('spec_id') or ent.entity_type} can be reached")
                        done = True
                        break  # re-check: the stone may reach other entities too
                old = (ent.col, ent.row)
                if _move_entity(ctx, rect, grid, ent, reach_spans, taken):
                    applied.append(f"moved {ent.metadata.get('spec_id') or ent.entity_type} from "
                                   f"{name(*old)} to {name(ent.col, ent.row)} (it was unreachable)")
                    done = True
        if not done:
            break
        check = verify_level(ctx, grid, entities, baseline, name)
    return check, applied


# ─────────────────────────────────────────────────────────────────────────────
# Spec bookkeeping
# ─────────────────────────────────────────────────────────────────────────────


def _updated_spec(ctx: EditContext, rect: Rect, region: RegionSpec, nodes: Dict[str, RegionNode],
                  removed_ids: Set[str], placed: List[IREntity]):
    from layout.spec_layout import spec_to_grid

    spec = base_spec(ctx)
    ts, H = ctx.tile_size, ctx.height
    keep_nodes, dropped = [], set()
    for n in spec.nodes:
        col, row, w = spec_to_grid(n.x, n.y, n.w, H, ts)
        inside = any(rect.contains(c, row) for c in range(col, col + w))
        if inside and n.type not in ("Start", "Exit"):
            dropped.add(n.id)
        else:
            keep_nodes.append(n)
    taken = {n.id for n in keep_nodes}
    ids: Dict[str, str] = {}
    for nid, rn in nodes.items():
        sid = _unique(nid, taken)
        ids[nid] = sid
        x, y, w = grid_to_spec(rn.col, rn.row, rn.w, H, ts)
        keep_nodes.append(SpecNode(id=sid, type=rn.type, x=x, y=y, w=w, h=float(ts),
                                   section=ctx.band_at(rn.col + rn.w // 2, rn.row).id))
    edges = [e for e in spec.edges if e.from_ not in dropped and e.to not in dropped]
    for e in region.edges:
        if e.from_ in ids and e.to in ids:
            edges.append(SpecEdge.model_validate({"from": ids[e.from_], "to": ids[e.to],
                                                  "type": e.type, "ability": e.ability}))
    ents = [e for e in spec.entities if e.id not in removed_ids]
    for ent in placed:
        anchor = str(ent.metadata.get("anchor"))
        se = spec_entity(ent, ids.get(anchor, anchor), str(ent.metadata.get("section")))
        if se is not None:
            ents.append(se)
    spec = spec.model_copy(update={"nodes": keep_nodes, "edges": edges, "entities": ents})
    return add_requirements(spec, region.requirements)


# ─────────────────────────────────────────────────────────────────────────────
# Entry points
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _Attempt:
    grid: List[List[Any]]
    entities: List[IREntity]
    placed: List[IREntity]
    nodes: Dict[str, RegionNode]
    check: LevelCheck
    warnings: List[str] = field(default_factory=list)
    repairs: List[str] = field(default_factory=list)
    fills: List[RegionNode] = field(default_factory=list)  # densify's fill platforms
    dropped_fills: int = 0
    hops: List[RegionNode] = field(default_factory=list)  # the comfort pass's platforms
    comfort_notes: List[str] = field(default_factory=list)


def _section_bands(ctx: EditContext) -> List[SectionBand]:
    return [SectionBand(id=b.id, name=b.name, col_start=b.col_start, col_end=b.col_end,
                        gravity_scale=b.gravity_scale, theme=b.theme or "",
                        difficulty=b.difficulty, row_start=b.row_start, row_end=b.row_end,
                        orientation=b.orientation, style=b.raw.get("style"))
            for b in ctx.bands]


def _new_node_ids(ctx: EditContext, rects, prefix: str, taken: Set[str]) -> List[RegionNode]:
    out: List[RegionNode] = []
    for row, c0, c1 in rects:
        n = 1
        while f"{prefix}{n}" in taken:
            n += 1
        taken.add(f"{prefix}{n}")
        out.append(RegionNode(f"{prefix}{n}", c0, row, c1 - c0 + 1, "Platform"))
    return out


def _comfort_region(
    ctx: EditContext, rect: Rect, grid, entities: List[IREntity], locks: Locks,
    baseline: Baseline, check: LevelCheck, name,
) -> Tuple[List[List[Any]], LevelCheck, List[RegionNode], List[str]]:
    """Shorten long golden-path moves touching *rect* with platforms inside it (v3 Part 20).

    Kept clear: the locked seams and their headroom and every entity's cell and
    headroom.  Platforms that break verification are removed, so this never fails
    an edit.
    """
    ph = ctx.player_h
    forbidden: Set[Cell] = set(locks.solid) | set(locks.clear)
    for e in entities:
        for d in range(0, ph + 1):
            forbidden.add((e.col, e.row - d))
    latest = [check]

    def recheck(g):
        c = verify_level(ctx, g, entities, baseline, name)
        if not c.ok:
            return None
        latest[0] = c
        return c.spans.graph, c.start_span, set(c.exit_spans)

    res = comfort_grid(
        grid, entities, (rect.col, rect.row, rect.right, rect.bottom), ctx.profile,
        sections=_section_bands(ctx), graph=check.spans.graph, start_span=check.start_span,
        exit_spans=set(check.exit_spans), forbidden=forbidden, check=recheck,
        tile_size=ctx.tile_size,
    )
    if not res.added:
        return grid, check, [], res.warnings
    taken = {n.id for n in ctx.spec.nodes} if ctx.spec else set()
    return res.grid, latest[0], _new_node_ids(ctx, res.added, "hop_", taken), res.notes


def _densify_region(
    ctx: EditContext, rect: Rect, grid, entities: List[IREntity], locks: Locks,
    baseline: Baseline, check: LevelCheck, name, seed: int,
) -> Tuple[List[List[Any]], LevelCheck, List[RegionNode], int]:
    """Add fill platforms inside *rect* (``layout.densify.densify_grid``), then re-verify.

    Protected, left empty: the locked seams and their headroom, every move arc of the
    current level (including arcs that cross the rect border) and the headroom of every
    entity.  Additions that break verification are dropped, so this never fails an edit.
    """
    ph = ctx.player_h
    protected: Set[Cell] = set(locks.solid) | set(locks.clear)
    for e in entities:
        for d in range(0, ph + 1):
            protected.add((e.col, e.row - d))
    sections = _section_bands(ctx)
    spans = check.spans
    res = densify_grid(
        grid, entities, (rect.col, rect.row, rect.right, rect.bottom), ctx.profile,
        sections=sections, graph=spans.graph, start_span=check.start_span,
        exit_spans=check.exit_spans,
        reach_rects=[spans.rects[s] for s in check.reach if s in spans.rects],
        protected=protected,
        verify=lambda g: verify_level(ctx, g, entities, baseline, name).ok,
        seed=seed, tile_size=ctx.tile_size,
    )
    if not res.added:
        return grid, check, [], len(res.dropped)
    taken = {n.id for n in ctx.spec.nodes} if ctx.spec else set()
    fills: List[RegionNode] = []
    for row, c0, c1 in res.added:
        n = 1
        while f"fill_{n}" in taken:
            n += 1
        taken.add(f"fill_{n}")
        fills.append(RegionNode(f"fill_{n}", c0, row, c1 - c0 + 1, "Platform"))
    return res.grid, verify_level(ctx, res.grid, entities, baseline, name), fills, len(res.dropped)


def edit_region(
    path: str,
    rect: Rect,
    prompt: str,
    provider: LLMProvider,
    *,
    mode: str = "region",
    max_retries: int = 3,
    ctx: Optional[EditContext] = None,
    densify: bool = True,
    seed: int = 0,
    level: Optional[str] = None,
) -> EditOutcome:
    """Regenerate *rect* of the level at *path* from *prompt* (see the module docstring).

    *level* (an iid or identifier; None = the first level) picks the level of a world.

    With *densify*, a verified result is filled with extra platforms inside the rect
    (as generate does for a whole level), seeded by *seed*.
    """
    try:
        ctx = ctx or load_context(path, level)
    except EditError as exc:
        return failure(mode, str(exc), rect)
    clipped = rect.clipped(ctx.width, ctx.height)
    if clipped is None:
        return failure(mode, f"The rectangle {rect.as_dict()} lies outside the "
                             f"{ctx.width} × {ctx.height} level.", rect)
    rect = clipped

    spans0 = ctx.extract(ctx.grid)
    baseline, before = make_baseline(ctx, rect, spans0)
    blocker = preexisting_blocker(before, ctx, rect)
    if blocker:
        return failure(mode, blocker, rect)
    seams = find_seams(ctx, rect, spans0)
    jobs.report("seams", f"Found {len(seams)} seam(s) on the region border")
    locks = locked_cells(ctx, rect, seams)
    region_old = [e for e in ctx.entities
                  if rect.contains(e.col, e.row) and e.entity_type not in FIXED_KINDS]
    removed = {str(e.metadata["iid"]) for e in region_old if e.metadata.get("iid")}
    removed_ids = {str(e.metadata.get("spec_id")) for e in region_old if e.metadata.get("spec_id")}
    kept = [e for e in ctx.entities if not any(e is r for r in region_old)]
    outside_ids = {str(e.metadata.get("spec_id")) for e in kept if e.metadata.get("spec_id")}

    def name(col: int, row: int) -> str:
        if rect.contains(col, row):
            return f"region column {col - rect.col}, row {row - rect.row}"
        return f"level column {col}, row {row} (outside the region)"

    def attempt(region: RegionSpec) -> _Attempt:
        nodes = {n.id: RegionNode(n.id, n.col + rect.col, n.row + rect.row, n.w, n.type)
                 for n in region.nodes}
        grid = apply_region(ctx.grid, rect, nodes.values(), locks,
                            keep=(lambda c, r: ctx.band_at(c, r).is_terrain) if ctx.has_terrain
                            else None)
        taken = {(e.col, e.row) for e in kept}
        placed, warns = place_region_entities(ctx, rect, grid, region, nodes, seams, taken)
        entities = [IREntity(e.entity_type, e.col, e.row, dict(e.metadata)) for e in kept] + placed
        jobs.report("region", "Stamping the region and verifying the level")
        check = verify_level(ctx, grid, entities, baseline, name)
        region_iids = {str(e.metadata["iid"]) for e in placed}
        repairs: List[str] = []
        if not check.ok:
            jobs.report("repair", f"Repairing the region ({len(check.messages())} problem(s))")
            check, repairs = repair_region(ctx, rect, grid, entities, region_iids, locks,
                                           baseline, check, name)
        hops: List[RegionNode] = []
        comfort_notes: List[str] = []
        if check.ok:
            jobs.report("comfort", "Shortening long jumps")
            grid, check, hops, comfort_notes = _comfort_region(
                ctx, rect, grid, entities, locks, baseline, check, name)
        fills: List[RegionNode] = []
        dropped = 0
        if densify and check.ok:
            jobs.report("densify", "Adding fill platforms")
            grid, check, fills, dropped = _densify_region(
                ctx, rect, grid, entities, locks, baseline, check, name, seed)
        return _Attempt(grid, entities, placed, nodes, check, warns, repairs, fills, dropped,
                        hops, comfort_notes)

    stats: Dict[str, Any] = {"llm_attempts": 0, "grid_repairs": 0, "layout_retries": 0,
                             "seams": len(seams)}
    parse = _parser(ctx, rect, seams, outside_ids)
    messages = build_region_messages(ctx, rect, seams, prompt, region_old)
    try:
        run = run_structured(provider, REGION_SYSTEM_PROMPT, messages, RegionSpec, parse, max_retries,
                             what="the region")
        stats["llm_attempts"] += run.attempts
        result = attempt(run.value)
        if not result.check.ok:  # at most one LLM re-prompt with the grid errors
            stats["layout_retries"] = 1
            jobs.report("repair", "The level still fails verification, asking the LLM to fix "
                                  "the region")
            numbered = "\n".join(f"  {i + 1}. {m}" for i, m in enumerate(result.check.messages()))
            retry = run.messages + [
                {"role": "assistant", "content": run.raw},
                {"role": "user", "content": (
                    "LEVEL VERIFICATION FAILED. Your region was stamped into the level and the "
                    "whole level was re-checked with the player's hitbox and tile collisions. "
                    f"It failed:\n{numbered}\n\nOutput a corrected RegionSpec that fixes every "
                    "listed problem (connect every seam, add Platform nodes, keep jump arcs and "
                    "headroom clear). Output ONLY the JSON object.")},
            ]
            run2 = run_structured(provider, REGION_SYSTEM_PROMPT, retry, RegionSpec, parse, max_retries,
                                  what="a corrected region")
            stats["llm_attempts"] += run2.attempts
            run, result = run2, attempt(run2.value)
    except EditLLMFailed as exc:
        return failure(mode, str(exc), rect, stats=stats)
    except Exception as exc:  # provider errors (HTTP, quota, …)
        return failure(mode, f"LLM error: {exc}", rect, stats=stats)

    stats["grid_repairs"] = len(result.repairs)
    stats["densify_added"] = len(result.fills)
    stats["comfort_added"] = len(result.hops)
    densify_notes: List[str] = []
    if result.fills:
        densify_notes.append(f"densify: added {len(result.fills)} fill platform(s)"
                             + (f", dropped {result.dropped_fills} that broke verification"
                                if result.dropped_fills else ""))
    if not result.check.ok:
        return failure(
            mode,
            "The edited level failed verification, so the file was not changed: "
            + " ".join(result.check.messages()),
            rect, warnings=result.warnings + result.repairs, stats=stats,
        )

    all_nodes = dict(result.nodes)
    all_nodes.update({n.id: n for n in result.hops})
    all_nodes.update({n.id: n for n in result.fills})
    spec = _updated_spec(ctx, rect, run.value, all_nodes, removed_ids, result.placed)
    return finish(
        ctx, mode=mode, label_prompt=prompt, grid=result.grid, entities=result.entities,
        spec=spec, check=result.check, rect=rect, removed=removed,
        added={str(e.metadata["iid"]) for e in result.placed},
        warnings=run.notes + result.warnings + result.repairs + result.comfort_notes
        + densify_notes, stats=stats,
    )


def section_rect(ctx: EditContext, section_id: str) -> Rect:
    for b in ctx.bands:
        if b.id == section_id:
            return b.rect
    raise EditError(f"No section '{section_id}' in this level (sections: "
                    f"{', '.join(b.id for b in ctx.bands)}).")


def edit_section(path: str, section_id: str, prompt: str, provider: LLMProvider,
                 *, max_retries: int = 3, densify: bool = True, seed: int = 0,
                 level: Optional[str] = None, structure: Optional[str] = None,
                 complexity: Optional[float] = None) -> EditOutcome:
    """A region edit over the section's band (column or row band) of *level*.

    With *structure* (or a prompt like "make this a labyrinth", v3 Part 21) the
    section is carved again by that structure's generator instead, between the
    seams it shares with its neighbours (no LLM call).  *complexity* (0..1, v4 Part 24)
    sets the section's target: a carved section is carved again with the terrain params it
    implies (no LLM call); an open one gets the targets as a line of the prompt.
    """
    try:
        ctx = load_context(path, level)
        rect = section_rect(ctx, section_id)
    except EditError as exc:
        return failure("section", str(exc))
    from .terrain import restructure_section, structure_from_prompt

    structure = structure or structure_from_prompt(prompt)
    if complexity is not None and not structure:
        band = next((b for b in ctx.bands if b.id == section_id), None)
        if band is not None and band.is_terrain:
            structure = band.structure
    if structure:
        return restructure_section(ctx, section_id, structure, prompt, seed=seed,
                                   complexity=complexity)
    if complexity is not None:
        from pipeline.complexity import targets

        prompt = f"{prompt}\n({targets(complexity).for_prompt()})"
    return edit_region(path, rect, prompt, provider, mode="section", max_retries=max_retries,
                       ctx=ctx, densify=densify, seed=seed)


__all__ = [
    "RegionEdge",
    "RegionEntity",
    "RegionNodeSpec",
    "RegionSpec",
    "build_region_messages",
    "edit_region",
    "edit_section",
    "place_region_entities",
    "repair_region",
    "section_rect",
]
