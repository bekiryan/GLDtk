"""Terrain from section structure types (v3 Part 21).

The LLM picks each section's ``structure`` (``spec.level_spec.StructureType``);
a deterministic, seeded generator per type carves the section's band out of
solid rock.  One module per type: :mod:`.corridor`, :mod:`.parkour`,
:mod:`.labyrinth`, :mod:`.arena`, :mod:`.shaft`, :mod:`.hub`, :mod:`.open`;
shared primitives in :mod:`.base`, vertical travel in :mod:`.vertical`,
node / entity placement in :mod:`.place`.

``build_terrain`` runs at the end of ``layout_spec`` when the spec has a
terrain section (specs without one, or with only ``open`` sections, are laid
out exactly as before):

1. **Bands.**  Section bands are re-sized by structure (a labyrinth or a hub
   gets more room than a shaft; at least ``MIN_BAND`` tiles each) and the
   nodes of open sections are moved into their new band.
2. **Joins.**  The ports between consecutive sections are planned first, from
   the level seed only, so a section can be regenerated with another seed
   without moving its neighbours.  Horizontal levels: a door on the seam at a
   floor row (the next section enters at the row the previous one leaves).
   Vertical levels: a hole in the upper band's bottom row over a pedestal in the
   lower band (``base.link``).  Next to an open section the door is at the row
   of its nearest platform.
3. **Carving.**  Each terrain band is filled with rock and its generator
   carves it (``random.Random(f"{seed}:{section}:{attempt}")``).  The level
   border of a terrain band stays rock (a frame); open sections stay open air.
   A section with retrieved patterns (v4 Part 26b, :mod:`.stamp`) is stamped from
   them first; the generator runs when none fits or verification rejected them.
4. **Placement** (:mod:`.place`): nodes snap onto the carved floors, synthetic
   nodes cover the rest, entities with logical places get anchors.

The result is ``SpecLayout.terrain`` (the base grid ``rasterise`` starts from,
plus the per-section results); ``SpecLayout.fixed_nodes`` lists the snapped
and synthetic nodes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from spec.level_spec import TERRAIN_STRUCTURES, LevelSpec, Section

from ..ir import TileValue
from . import arena, corridor, hub, labyrinth, open as open_, parkour, shaft
from .base import HOP_AIR, JUMP_AIR, Canvas, Port, TerrainContext, TerrainResult
from .place import SectionPlacer
from .stamp import stamp_section

GENERATORS: Dict[str, Callable[[TerrainContext, Canvas], TerrainResult]] = {
    "corridor": corridor.generate,
    "parkour": parkour.generate,
    "labyrinth": labyrinth.generate,
    "arena": arena.generate,
    "shaft": shaft.generate,
    "hub": hub.generate,
    "open": open_.generate,
}
STRUCTURES = tuple(GENERATORS)
MIN_BAND = 16
MIN_BAND_V = 20
BAND_WEIGHT = {"corridor": 1.0, "parkour": 0.9, "labyrinth": 1.3, "arena": 0.9,
               "shaft": 0.7, "hub": 1.2, "open": 1.0}
MAX_ATTEMPTS = 3  # generator seeds tried per section before an LLM re-prompt


def is_terrain(structure: Optional[str]) -> bool:
    return structure in TERRAIN_STRUCTURES


@dataclass
class SectionTerrain:
    id: str
    structure: str
    rect: Tuple[int, int, int, int]
    entry: Port
    exit: Port
    attempt: int
    result: TerrainResult

    def as_dict(self) -> Dict[str, object]:
        return {"structure": self.structure, "attempt": self.attempt, **{
            k: v for k, v in self.result.stats.items() if isinstance(v, (int, float, str))}}


@dataclass
class TerrainLayout:
    base: List[List[TileValue]]
    sections: Dict[str, SectionTerrain] = field(default_factory=dict)

    def stats(self) -> Dict[str, Dict[str, object]]:
        return {sid: st.as_dict() for sid, st in self.sections.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Bands
# ─────────────────────────────────────────────────────────────────────────────


def _resize_bands(layout, spec: LevelSpec) -> None:  # type: ignore[no-untyped-def]
    from ..spec_layout import _proportional_widths, resolve_conflicts

    vertical = layout.orientation == "vertical"
    total = layout.height if vertical else layout.width
    secs = {s.id: s for s in spec.sections}
    bands = layout.sections  # in order: left→right / bottom→top
    weights = [BAND_WEIGHT.get(secs[b.id].structure or "open", 1.0) for b in bands]
    if vertical:  # climbing needs height: shafts and arenas get more of it
        weights = [w * (1.6 if secs[b.id].structure in ("shaft", "parkour", "arena") else 1.0)
                   for w, b in zip(weights, bands)]
    sizes = _proportional_widths(weights, total, MIN_BAND_V if vertical else MIN_BAND)
    start = 0
    for band, size in zip(bands, sizes):
        lo, hi = start, start + size - 1  # along the axis (rows counted from the bottom)
        start += size
        if vertical:
            old = (layout.height - 1 - band.row_end, layout.height - 1 - band.row_start)
            band.row_start, band.row_end = layout.height - 1 - hi, layout.height - 1 - lo
        else:
            old = (band.col_start, band.col_end)
            band.col_start, band.col_end = lo, hi
        if is_terrain(secs[band.id].structure):
            continue
        ow, nw = old[1] - old[0] + 1, hi - lo + 1
        scale = min(1.0, nw / ow)
        offset = (nw - ow * scale) / 2.0
        for nid, sid in layout.node_sections.items():
            if sid != band.id or nid not in layout.node_layouts:
                continue
            nl = layout.node_layouts[nid]
            if vertical:
                u = layout.height - 1 - nl.row
                nu = lo + int(round((u - old[0]) * scale + offset))
                nl.row = min(max(layout.height - 1 - min(max(nu, lo), hi), layout.player_tiles[1]),
                             layout.height - 1)
            else:
                nw_ = max(1, int(round(nl.width * scale)))
                nc = lo + int(round((nl.col - old[0]) * scale + offset))
                nl.width = min(nw_, nw)
                nl.col = min(max(nc, lo), hi - nl.width + 1)
    # open nodes may now collide: resolve among them only
    terrain_nodes = {nid: nl for nid, nl in layout.node_layouts.items()
                     if is_terrain(secs.get(layout.node_sections.get(nid, ""), spec.sections[0]).structure)}
    for nid in terrain_nodes:
        del layout.node_layouts[nid]
    resolve_conflicts(layout)
    layout.node_layouts.update(terrain_nodes)


# ─────────────────────────────────────────────────────────────────────────────
# Joins
# ─────────────────────────────────────────────────────────────────────────────


def _row_limits(height: int, jump_air: int = JUMP_AIR) -> Tuple[int, int]:
    hi = height - 3
    lo = min(1 + jump_air + 1, hi)
    return lo, hi


def _clamp(v: int, lo: int, hi: int) -> int:
    return min(max(v, lo), hi)


def _next_row(sec: Section, entry: int, rng: random.Random, height: int,
              jump_air: int = JUMP_AIR) -> int:
    lo, hi = _row_limits(height, jump_air)
    p = sec.params.model_dump() if sec.params else {}
    complexity = p.get("complexity") if p.get("complexity") is not None else 0.5
    kind = sec.structure
    if kind == "shaft" or (kind == "parkour" and p.get("vertical")):
        span = max(6, min(hi - lo, 15))
        up = p.get("vertical") != "down"
        if up and entry - span < lo and entry + span <= hi:
            up = False
        if not up and entry + span > hi and entry - span >= lo:
            up = True
        return _clamp(entry - span if up else entry + span, lo, hi)
    if kind == "arena":
        return entry
    if kind == "parkour":
        return _clamp(entry + rng.randint(-3, 3), lo, hi)
    spread = max(1, round(1 + 2 * complexity))
    return _clamp(entry + rng.randint(-spread, spread), lo, hi)


def _edge_node_row(layout, sid: str, side: str) -> Optional[Tuple[int, int]]:  # type: ignore[no-untyped-def]
    """(col, row) of the open section's standable node nearest its *side* seam."""
    best = None
    for nid, nl in layout.node_layouts.items():
        if layout.node_sections.get(nid) != sid or not layout.standable(nid):
            continue
        if layout.orientation == "vertical":
            key = nl.row if side == "bottom" else -nl.row
            key = -key
        else:
            key = nl.col if side == "left" else -(nl.col + nl.width - 1)
        if best is None or key < best[0]:
            best = (key, (nl.col + nl.width // 2, nl.row))
    return best[1] if best else None


def plan_ports(layout, spec: LevelSpec, seed: int) -> Dict[str, Tuple[Port, Port]]:  # type: ignore[no-untyped-def]
    rng = random.Random(f"{seed}:ports")
    secs = {s.id: s for s in spec.sections}
    bands = layout.sections
    vertical = layout.orientation == "vertical"
    w, h = layout.width, layout.height
    jump_air = max(JUMP_AIR, getattr(layout, "hop_air", HOP_AIR))
    lo_r, hi_r = _row_limits(h, jump_air)
    ports: Dict[str, List[Optional[Port]]] = {b.id: [None, None] for b in bands}
    if not vertical:
        first = secs[bands[0].id]
        row = _clamp(hi_r - rng.randint(0, 3), lo_r, hi_r)
        if not is_terrain(first.structure):
            got = _edge_node_row(layout, first.id, "right")
            row = _clamp(got[1], lo_r, hi_r) if got else row
        ports[bands[0].id][0] = Port("end", 1, row)
        for k, band in enumerate(bands):
            sec = secs[band.id]
            entry = ports[band.id][0]
            assert entry is not None
            if k == len(bands) - 1:
                nxt = _next_row(sec, entry.row, rng, h, jump_air) if is_terrain(sec.structure) else entry.row
                ports[band.id][1] = Port("end", w - 2, nxt)
                break
            nb = bands[k + 1]
            nsec = secs[nb.id]
            if not is_terrain(nsec.structure):
                got = _edge_node_row(layout, nb.id, "left")
                row = _clamp(got[1], lo_r, hi_r) if got else entry.row
                kind = "open"
            elif not is_terrain(sec.structure):
                got = _edge_node_row(layout, sec.id, "right")
                row = _clamp(got[1], lo_r, hi_r) if got else entry.row
                kind = "open"
            else:
                row = _next_row(sec, entry.row, rng, h, jump_air)
                kind = "door"
            port = Port(kind, nb.col_start, row)
            ports[band.id][1] = port
            ports[nb.id][0] = port
    else:
        c_lo, c_hi = 1, w - 2
        ports[bands[0].id][0] = Port("end", rng.randint(c_lo + 2, c_hi - 2), h - 2)
        for k, band in enumerate(bands):
            sec = secs[band.id]
            if k == len(bands) - 1:
                ports[band.id][1] = Port("end", rng.randint(c_lo + 2, c_hi - 2), band.row_start + 6)
                break
            nb = bands[k + 1]
            nsec = secs[nb.id]
            hx = rng.randint(c_lo + 3, c_hi - 4)
            if is_terrain(sec.structure) and is_terrain(nsec.structure):
                port = Port("hole", hx, nb.row_end)
                ports[band.id][1] = port
                ports[nb.id][0] = port
            elif is_terrain(sec.structure):  # open air above
                got = _edge_node_row(layout, nb.id, "bottom")
                col = _clamp(got[0], c_lo + 2, c_hi - 3) if got else hx
                ports[band.id][1] = Port("open", col, band.row_start + 4)
                ports[nb.id][0] = Port("open", col, nb.row_end)
            else:
                got = _edge_node_row(layout, sec.id, "top")
                col = _clamp(got[0], c_lo + 2, c_hi - 3) if got else hx
                ports[band.id][1] = Port("open", col, band.row_start)
                ports[nb.id][0] = Port("open", col, nb.row_end - 1)
    return {sid: (p[0], p[1]) for sid, p in ports.items()}  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def _rect(band) -> Tuple[int, int, int, int]:  # type: ignore[no-untyped-def]
    return band.col_start, band.row_start, band.col_end, band.row_end


def generate_section(
    layout, spec: LevelSpec, sec: Section, rect: Tuple[int, int, int, int],  # type: ignore[no-untyped-def]
    entry: Port, exit_: Port, seed: int, attempt: int, grid: List[List[TileValue]],
    stamps: Optional[List[Dict[str, Any]]] = None,
) -> TerrainResult:
    """Carve one section into *grid* (in place) and return its result.

    *stamps* (v4 Part 26b) are retrieved patterns for this section, best first: attempt
    ``k`` stamps the ``k``-th one that fits (``stats["pattern"]`` is its id); later
    attempts run the generator with seed attempt ``attempt - fits``."""
    params = sec.params.model_dump() if sec.params else {}
    gen_attempt = attempt
    hazards = sum(1 for n in spec.nodes if n.section == sec.id and n.type == "Hazard")
    ctx = TerrainContext(
        section_id=sec.id,
        structure=sec.structure or "open",
        rect=rect,
        level_size=(layout.width, layout.height),
        orientation=layout.orientation,
        entry=entry,
        exit=exit_,
        rng=random.Random(f"{seed}:{sec.id}:{gen_attempt}"),
        params=params,
        hazards_wanted=hazards,
        player_h=layout.player_tiles[1],
        hop_air=getattr(layout, "hop_air", HOP_AIR),
    )
    cv = Canvas(ctx)
    if stamps:
        result, fits = stamp_section(ctx, cv, stamps, attempt)
        if result is not None:
            cv.apply(grid)
            return result
        gen_attempt = attempt - fits
        ctx.rng = random.Random(f"{seed}:{sec.id}:{gen_attempt}")
    result = GENERATORS[ctx.structure](ctx, cv)
    cv.apply(grid)
    return result


def build_terrain(
    layout, spec: LevelSpec, seed: int = 0,  # type: ignore[no-untyped-def]
    attempts: Optional[Dict[str, int]] = None,
    stamps: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Optional[TerrainLayout]:
    """Carve every terrain section of *layout* (mutated in place; see the module doc).
    *stamps* maps a section id to its retrieved patterns (v4 Part 26b, ``generate_section``)."""
    if not spec.has_terrain:
        return None
    attempts = attempts or {}
    secs = {s.id: s for s in spec.sections}
    for band in layout.sections:
        band.structure = secs[band.id].structure
        band.params = secs[band.id].params.model_dump(exclude_none=True) if secs[band.id].params else None
    _resize_bands(layout, spec)
    ports = plan_ports(layout, spec, seed)
    base = [[TileValue.EMPTY] * layout.width for _ in range(layout.height)]
    terrain = TerrainLayout(base=base)
    order = [b.id for b in layout.sections]
    for band in layout.sections:
        sec = secs[band.id]
        if not is_terrain(sec.structure):
            continue
        entry, exit_ = ports[band.id]
        attempt = attempts.get(band.id, 0)
        result = generate_section(layout, spec, sec, _rect(band), entry, exit_, seed, attempt, base,
                                  (stamps or {}).get(band.id))
        band.pattern = result.stats.get("pattern")  # type: ignore[assignment]
        band.stacked = result.stats.get("stacked")  # type: ignore[assignment]
        terrain.sections[band.id] = SectionTerrain(band.id, sec.structure or "open", _rect(band),
                                                   entry, exit_, attempt, result)
    layout.terrain = terrain
    layout.fixed_nodes = set()
    for sid, st in terrain.sections.items():
        placer = SectionPlacer(layout, spec, secs[sid], st.result, st.rect, base)
        placer.snap_nodes(first=order[0] == sid, last=order[-1] == sid)
        placer.place_entities()
        layout.fixed_nodes |= placer.fixed
        layout.adjustments += placer.warnings
    layout.rasterise()
    return terrain


__all__ = [
    "GENERATORS", "STRUCTURES", "MAX_ATTEMPTS", "TerrainLayout", "SectionTerrain",
    "build_terrain", "generate_section", "plan_ports", "is_terrain",
]
