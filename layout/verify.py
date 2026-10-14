"""Grid verification — prove the *final* tile grid is beatable.

``verify_grid`` re-extracts the platform graph from ``spec_layout.ir.grid``
with collision-aware arcs (``extract_graph(..., collision=True)``), maps the
grid spans back to spec nodes by overlap (the grid may split or merge
platforms), and checks:

  NO_GRID_PATH        no Start → Exit path; names the last reachable spec node
                      and the failing next hop on the spec's golden path
  WALL_MISSING        that failing hop needs a wall jump and there is no solid
                      column (≥ 3 tiles) next to the arc
  ENTITY_UNREACHABLE  a gameplay entity's anchor is unreachable from Start
                      (Pickup, Key, Weapon, Checkpoint, Interactive other than
                      signs; Enemies, NPCs and signs only warn)
  ENTITY_BLOCKED      an entity found no free cell with headroom on its anchor
                      (same severity rules as ENTITY_UNREACHABLE)
  KEY_LOCK_ORDER      an Exit has ``requires_key`` but no Key is reachable
                      without passing through the Exit
  START_BLOCKED       the player has no headroom at the Start
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import networkx as nx

from game.profile import GameProfile
from spec.level_spec import LevelSpec
from symbolic.extractor import extract_graph
from symbolic.physics import best_ability, find_walls
from symbolic.schema import AbstractLevelGraph, NodeType

from .ir import IREntity, TileValue
from .spec_layout import Rect, SpecLayout

REQUIRED_KINDS = {"Pickup", "Key", "Weapon", "Checkpoint", "Interactive", "Exit"}
"""Entity kinds whose reachability is an error (signs are exempt)."""


@dataclass
class GridError:
    code: str
    message: str
    node_ids: List[str] = field(default_factory=list)
    entity_ids: List[str] = field(default_factory=list)  # spec entity ids involved


@dataclass
class GridReport:
    ok: bool
    errors: List[GridError]
    golden_path: List[str]  # spec node ids along the grid's Start → Exit path
    stats: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)
    reachable_nodes: Set[str] = field(default_factory=set)  # spec ids reachable from Start
    pre_exit_nodes: Set[str] = field(default_factory=set)  # … without passing an Exit
    graph: Optional[AbstractLevelGraph] = field(default=None, repr=False)
    start_span: Optional[str] = None  # span id (in ``graph``) the player starts on
    exit_spans: Set[str] = field(default_factory=set)  # span ids of the Exit nodes

    def error_messages(self) -> List[str]:
        return [e.message for e in self.errors]


# ─────────────────────────────────────────────────────────────────────────────
# Span ↔ spec node mapping
# ─────────────────────────────────────────────────────────────────────────────


class SpanIndex:
    """Grid spans of an extracted graph, indexed by row, mapped to spec nodes."""

    def __init__(self, graph: AbstractLevelGraph, layout: SpecLayout) -> None:
        self.rects: Dict[str, Rect] = {}
        self.by_row: Dict[int, List[Tuple[int, int, str]]] = {}
        for n in graph.nodes():
            if n.node_type == NodeType.HAZARD:
                continue
            rect = (int(n.metadata["row"]), int(n.metadata["col_start"]), int(n.metadata["col_end"]))
            self.rects[n.id] = rect
            self.by_row.setdefault(rect[0], []).append((rect[1], rect[2], n.id))
        self.node_spans: Dict[str, List[str]] = {}
        self.span_nodes: Dict[str, List[str]] = {}
        for nid, nl in layout.node_layouts.items():
            if not layout.standable(nid):
                continue
            spans = [
                sid
                for c0, c1, sid in self.by_row.get(nl.row, [])
                if c0 <= nl.col + nl.width - 1 and nl.col <= c1
            ]
            self.node_spans[nid] = spans
            for sid in spans:
                self.span_nodes.setdefault(sid, []).append(nid)
        for sid, nids in self.span_nodes.items():
            nids.sort(key=lambda i: layout.node_layouts[i].col)

    def span_at(self, col: int, row: int) -> Optional[str]:
        for c0, c1, sid in self.by_row.get(row, []):
            if c0 <= col <= c1:
                return sid
        return None


def _reach(graph: AbstractLevelGraph, source: Optional[str], stop: Iterable[str] = ()) -> Set[str]:
    """Spans reachable from *source*; spans in *stop* are reached but not expanded."""
    if source is None:
        return set()
    stop = set(stop)
    g = graph.nx_graph
    seen = {source}
    todo = [source]
    while todo:
        cur = todo.pop()
        if cur in stop and cur != source:
            continue
        for nxt in g.successors(cur):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def _anchor_span(ent: IREntity, layout: SpecLayout, index: SpanIndex) -> Optional[str]:
    anchor = ent.metadata.get("anchor") or ent.metadata.get("node_id")
    nl = layout.node_layouts.get(str(anchor)) if anchor else None
    if nl is None:
        return index.span_at(ent.col, ent.row + 1)
    col = min(max(ent.col, nl.col), nl.col + nl.width - 1)
    return index.span_at(col, nl.row)


def hop_points(layout: SpecLayout, a: str, b: str) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Surface-centre feet points (Y-up px) of two laid-out nodes."""
    ts = layout.tile_size

    def centre(nid: str) -> Tuple[float, float]:
        nl = layout.node_layouts[nid]
        return ((nl.col + nl.width / 2.0) * ts, layout.surface_y(nl.row))

    return centre(a), centre(b)


def _is_required(ent: IREntity) -> bool:
    if ent.entity_type == "Interactive" and ent.metadata.get("interactive_kind") == "sign":
        return False
    return ent.entity_type in REQUIRED_KINDS


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def verify_grid(
    spec_layout: SpecLayout, profile: Optional[GameProfile], spec: LevelSpec
) -> GridReport:
    """Check that the final grid is beatable and every entity is usable."""
    profile = profile or GameProfile()
    t0 = time.perf_counter()
    layout = spec_layout
    ir = layout.ir
    graph = extract_graph(
        ir.grid, profile=profile, gravity_scale_at_cell=layout.gravity_scale_at, collision=True
    )
    index = SpanIndex(graph, layout)
    errors: List[GridError] = []
    warnings: List[str] = []

    spec_ents = [e for e in ir.entities if "spec_id" in e.metadata]
    start_ent = next((e for e in ir.entities if e.entity_type == "Start"), None)
    start_nodes = [n for n, k in layout.node_kinds.items() if k == "Start"]
    exit_nodes = [n for n, k in layout.node_kinds.items() if k == "Exit"]

    start_span = _anchor_span(start_ent, layout, index) if start_ent else None
    if start_span is None and start_nodes:
        spans = index.node_spans.get(start_nodes[0]) or []
        start_span = spans[0] if spans else None
    exit_spans = {s for n in exit_nodes for s in index.node_spans.get(n, [])}

    reach = _reach(graph, start_span)
    pre_exit = _reach(graph, start_span, stop=exit_spans)
    reachable_nodes = {n for n, ss in index.node_spans.items() if any(s in reach for s in ss)}
    pre_exit_nodes = {n for n, ss in index.node_spans.items() if any(s in pre_exit for s in ss)}

    # ── START_BLOCKED ──
    if start_ent is not None:
        col, row = start_ent.col, start_ent.row
        rows = range(row - layout.player_tiles[1] + 1, row + 1)
        if any(r < 0 or ir.get_tile(col, r) != TileValue.EMPTY for r in rows):
            anchor = str(start_ent.metadata.get("anchor") or start_ent.metadata.get("node_id"))
            errors.append(
                GridError(
                    "START_BLOCKED",
                    f"The player has no headroom at the Start on '{anchor}' (column {col}): "
                    f"{layout.player_tiles[1]} empty tiles are needed above the surface.",
                    [anchor],
                    [str(start_ent.metadata.get("spec_id", "Start"))],
                )
            )

    # ── NO_GRID_PATH / WALL_MISSING ──
    reached_exit = [s for s in exit_spans if s in reach]
    golden_path: List[str] = []
    if reached_exit and start_span is not None:
        best: List[str] = []
        for s in reached_exit:
            p = nx.shortest_path(graph.nx_graph, start_span, s)
            if not best or len(p) < len(best):
                best = p
        for sid in best:
            for nid in index.span_nodes.get(sid, []):
                if not golden_path or golden_path[-1] != nid:
                    golden_path.append(nid)
    else:
        errors.append(_no_path_error(layout, profile, spec, reachable_nodes, ir.grid))

    # ── entities ──
    for ent in spec_ents:
        if ent.entity_type == "Start":
            continue
        sid = ent.metadata["spec_id"]
        anchor = str(ent.metadata.get("anchor"))
        required = _is_required(ent)
        if ent.metadata.get("blocked"):
            msg = (
                f"Entity '{sid}' ({ent.entity_type}) has no free cell with headroom on "
                f"'{anchor}'."
            )
            if required:
                errors.append(GridError("ENTITY_BLOCKED", msg, [anchor], [sid]))
            else:
                warnings.append(msg)
        if ent.entity_type == "Exit":
            continue  # covered by NO_GRID_PATH
        if _anchor_span(ent, layout, index) not in reach:
            msg = (
                f"Entity '{sid}' ({ent.entity_type}) stands on '{anchor}', which the player "
                "cannot reach from the Start."
            )
            if required:
                errors.append(GridError("ENTITY_UNREACHABLE", msg, [anchor], [sid]))
            else:
                warnings.append(msg)

    # ── KEY_LOCK_ORDER ──
    locked = [e for e in spec_ents if e.entity_type == "Exit" and e.metadata.get("requires_key")]
    if locked:
        keys = [e for e in spec_ents if e.entity_type == "Key"]
        if not any(_anchor_span(k, layout, index) in pre_exit for k in keys):
            exit_anchor = str(locked[0].metadata.get("anchor"))
            where = (
                f"Key(s) {[k.metadata['spec_id'] for k in keys]} are not reachable before it"
                if keys
                else "there is no Key entity"
            )
            errors.append(
                GridError(
                    "KEY_LOCK_ORDER",
                    f"Exit '{locked[0].metadata['spec_id']}' on '{exit_anchor}' requires a key, "
                    f"but {where}. Place a Key on a platform reachable before the Exit.",
                    [exit_anchor] + [str(k.metadata.get("anchor")) for k in keys],
                    [str(k.metadata["spec_id"]) for k in keys],
                )
            )

    stats = {
        "spans": len(index.rects),
        "edges": len(graph.edges()),
        "reachable_spans": len(reach),
        "verify_seconds": round(time.perf_counter() - t0, 4),
    }
    return GridReport(
        ok=not errors,
        errors=errors,
        golden_path=golden_path,
        stats=stats,
        warnings=warnings,
        reachable_nodes=reachable_nodes,
        pre_exit_nodes=pre_exit_nodes,
        graph=graph,
        start_span=start_span,
        exit_spans=exit_spans,
    )


def failing_hop(layout: SpecLayout, reachable_nodes: Set[str]) -> Optional[Tuple[str, str]]:
    """First hop of the spec golden path whose target is unreachable on the grid."""
    path = layout.golden_path
    for i, nid in enumerate(path):
        if nid not in reachable_nodes:
            return (path[i - 1], nid) if i > 0 else (nid, nid)
    return None


def _hop_needs_wall_jump(
    layout: SpecLayout, profile: GameProfile, spec: LevelSpec, a: str, b: str
) -> bool:
    if not profile.abilities.wall_jump.enabled:
        return False
    for e in spec.edges:
        if e.from_ == a and e.to == b and e.ability == "wall_jump":
            return True
    p1, p2 = hop_points(layout, a, b)
    scale = layout.gravity_scale_of(layout.rect(a))
    return best_ability(p1, p2, profile, scale) == "wall_jump"


def _no_path_error(
    layout: SpecLayout,
    profile: GameProfile,
    spec: LevelSpec,
    reachable_nodes: Set[str],
    grid: Any,
) -> GridError:
    hop = failing_hop(layout, reachable_nodes)
    if hop is None:
        return GridError(
            "NO_GRID_PATH",
            "No Start→Exit path exists on the final tile grid.",
            list(layout.golden_path[-1:]),
        )
    a, b = hop
    p1, p2 = hop_points(layout, a, b)
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    scale = layout.gravity_scale_of(layout.rect(a))
    if _hop_needs_wall_jump(layout, profile, spec, a, b) and not find_walls(
        grid, p1, p2, profile, float(layout.tile_size)
    ):
        return GridError(
            "WALL_MISSING",
            f"The hop '{a}'→'{b}' (Δx={dx:.0f} px, Δy={dy:.0f} px) needs a wall jump, but "
            "there is no solid wall (a column of at least 3 tiles) next to the arc. "
            f"'{a}' is the last node the player can reach. Lower '{b}' or add a Platform "
            "node between them.",
            [a, b],
        )
    reason = (
        "it is out of the player's reach"
        if best_ability(p1, p2, profile, scale) is False
        else "the jump arc is blocked by tiles (a ceiling or an obstacle in the way)"
    )
    return GridError(
        "NO_GRID_PATH",
        f"No Start→Exit path on the final tile grid: '{a}' is the last reachable node on "
        f"the golden path and the hop '{a}'→'{b}' fails (Δx={dx:.0f} px, Δy={dy:.0f} px "
        f"between surface centres): {reason}. Move '{b}' closer to '{a}', or add a "
        "Platform node between them, and keep the space above the jump clear.",
        [a, b],
    )
