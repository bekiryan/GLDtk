"""Layout v2 — lay a LevelSpec out on a grid of ``spec.size``.

Unlike the legacy Sugiyama layout (``layout/sugiyama.py``), this keeps the
LLM's positional intent: every node lands where its coordinates say, sections
keep their left-to-right order, and only real conflicts are nudged.

Coordinate convention
---------------------
Spec: pixels, Y-up; a node's ``x`` is its left edge, ``y`` its top-surface
height.  IR: ``grid[row][col]``, row 0 is the visual top, ``H`` rows.

    col   = round(x / ts)
    row   = H − 1 − round(y / ts)              (y = 0 → bottom row)
    width = max(1, round(w / ts)),  height = 1 tile

A node is one tile row and that row *is* its standing surface: a platform on
row r has surface y = (H − 1 − r)·ts.  This is the convention of
``symbolic.extractor`` (a span's ``position.y``), so spec heights and grid
heights agree.  Inverse: ``x = col·ts``, ``y = (H − 1 − row)·ts``
(:func:`spec_to_grid` / :func:`grid_to_spec`).  Rounding is half-up.

Standable nodes are clamped to rows ``[player_h, H − 1]`` so the player (its
hitbox ``player_h`` tiles tall) fits above them; hazards may use any row.

Sections
--------
Each section gets a contiguous column band, ordered by ``Section.order``; the
bands cover the full width.  If the nodes' x-extents already respect the order
the band boundaries sit midway between neighbouring sections' extents.
Otherwise every section's nodes are remapped affinely into bands sized in
proportion to the section's node extent (minimum 8 columns): translated when
the band is wide enough, scaled down when it is not.

Conflicts
---------
Two nodes on the same row whose columns overlap collide; a standable node with
less than ``player_h`` empty rows between it and a node above that overlaps it
horizontally has no clearance.  Either is resolved by nudging one node to the
nearest free row — off-golden-path nodes first, then non-terminal ones.  Every
adjustment is recorded in ``SpecLayout.adjustments``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

import networkx as nx

from game.profile import GameProfile
from spec.level_spec import LevelSpec

from .ir import IREntity, IRLevel, TileValue
from .sugiyama import NodeLayout

MIN_SECTION_COLS = 8

Rect = Tuple[int, int, int]  # (row, col_start, col_end) — col_end inclusive


# ─────────────────────────────────────────────────────────────────────────────
# Coordinate conversion
# ─────────────────────────────────────────────────────────────────────────────


def _round_half_up(v: float) -> int:
    return int(math.floor(v + 0.5))


def spec_to_grid(
    x: float, y: float, w: float, height_tiles: int, tile_size: int = 16
) -> Tuple[int, int, int]:
    """Spec ``(x, y, w)`` px (Y-up, y = surface) → ``(col, row, width_tiles)``."""
    col = _round_half_up(x / tile_size)
    row = height_tiles - 1 - _round_half_up(y / tile_size)
    width = max(1, _round_half_up(w / tile_size))
    return col, row, width


def grid_to_spec(
    col: int, row: int, width: int, height_tiles: int, tile_size: int = 16
) -> Tuple[float, float, float]:
    """Inverse of :func:`spec_to_grid`: ``(col, row, width)`` → ``(x, y, w)`` px."""
    return (
        float(col * tile_size),
        float((height_tiles - 1 - row) * tile_size),
        float(width * tile_size),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SectionBand:
    id: str
    name: str
    col_start: int
    col_end: int  # inclusive
    gravity_scale: float
    theme: str
    difficulty: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "col_start": self.col_start,
            "col_end": self.col_end,
            "gravity_scale": self.gravity_scale,
            "theme": self.theme,
            "difficulty": self.difficulty,
        }


@dataclass
class SpecLayout:
    """A spec laid out on its tile grid (plus everything repair needs)."""

    node_layouts: Dict[str, NodeLayout]
    ir: IRLevel
    sections: List[SectionBand]
    adjustments: List[str] = field(default_factory=list)
    node_kinds: Dict[str, str] = field(default_factory=dict)  # spec node type
    node_sections: Dict[str, str] = field(default_factory=dict)
    golden_path: List[str] = field(default_factory=list)  # spec Start → Exit
    player_tiles: Tuple[int, int] = (1, 2)  # hitbox (width, height) in tiles
    entity_anchors: Dict[str, str] = field(default_factory=dict)  # repair overrides
    entity_warnings: List[str] = field(default_factory=list)  # last placement
    added_nodes: List[str] = field(default_factory=list)  # stepping stones

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    @property
    def width(self) -> int:
        return self.ir.width_tiles

    @property
    def height(self) -> int:
        return self.ir.height_tiles

    @property
    def tile_size(self) -> int:
        return self.ir.tile_size

    def rect(self, node_id: str) -> Rect:
        nl = self.node_layouts[node_id]
        return (nl.row, nl.col, nl.col + nl.width - 1)

    def standable(self, node_id: str) -> bool:
        return self.node_kinds.get(node_id) != "Hazard"

    def section_at(self, col: int) -> SectionBand:
        for band in self.sections:
            if band.col_start <= col <= band.col_end:
                return band
        return self.sections[0] if col < 0 else self.sections[-1]

    def gravity_scale_at(self, col: int) -> float:
        return self.section_at(col).gravity_scale

    def node_at(self, col: int, row: int) -> Optional[str]:
        for nid, nl in self.node_layouts.items():
            if nl.row == row and nl.col <= col < nl.col + nl.width:
                return nid
        return None

    def surface_y(self, row: int) -> float:
        return float((self.height - 1 - row) * self.tile_size)

    # ------------------------------------------------------------------
    # Rasterisation
    # ------------------------------------------------------------------

    def rasterise(self) -> IRLevel:
        """Rebuild ``ir.grid`` and the structural Start/Exit markers from the layouts.

        Spec entities are dropped; call ``place_spec_entities`` afterwards.
        """
        ir = self.ir
        ir.grid = [[TileValue.EMPTY] * ir.width_tiles for _ in range(ir.height_tiles)]
        ir.entities = []
        for nid, nl in self.node_layouts.items():
            kind = self.node_kinds.get(nid, "Platform")
            value = TileValue.HAZARD if kind == "Hazard" else TileValue.SOLID
            ir.fill_rect(nl.col, nl.row, nl.width, nl.height, value)
            if kind in ("Start", "Exit"):
                ir.entities.append(
                    IREntity(
                        entity_type=kind,
                        col=nl.col + nl.width // 2,
                        row=nl.row - 1,
                        metadata={"node_id": nid, "structural": True},
                    )
                )
        ir.metadata["sections"] = [b.as_dict() for b in self.sections]
        return ir

    # ------------------------------------------------------------------
    # Conflicts
    # ------------------------------------------------------------------

    def conflict(self, a: str, b: str, rect_a: Optional[Rect] = None) -> Optional[str]:
        """Why nodes *a* and *b* conflict (``"overlap"`` / ``"clearance"``), or None."""
        ra = rect_a or self.rect(a)
        rb = self.rect(b)
        if ra[1] > rb[2] or rb[1] > ra[2]:
            return None  # no horizontal overlap
        if ra[0] == rb[0]:
            return "overlap"
        lower = a if ra[0] > rb[0] else b
        lower_row = max(ra[0], rb[0])
        upper_row = min(ra[0], rb[0])
        if not self.standable(lower):
            return None
        if lower_row - upper_row - 1 < self.player_tiles[1]:
            return "clearance"
        return None

    def row_is_free(
        self,
        node_id: str,
        row: int,
        avoid: Iterable[Tuple[int, int]] = (),
        ignore: Iterable[str] = (),
    ) -> bool:
        """Can *node_id* sit on *row* (same columns) without any conflict?"""
        nl = self.node_layouts[node_id]
        min_row = self.player_tiles[1] if self.standable(node_id) else 0
        if not (min_row <= row <= self.height - 1):
            return False
        rect = (row, nl.col, nl.col + nl.width - 1)
        for c, r in avoid:
            if r == row and rect[1] <= c <= rect[2]:
                return False
        skip = set(ignore) | {node_id}
        return all(
            self.conflict(node_id, other, rect) is None
            for other in self.node_layouts
            if other not in skip
        )

    def nearest_free_row(
        self,
        node_id: str,
        prefer_down: bool = False,
        avoid: Iterable[Tuple[int, int]] = (),
        exclude_current: bool = False,
    ) -> Optional[int]:
        """Closest row (by |Δ|) where *node_id* has no conflicts, or None."""
        avoid = list(avoid)
        row = self.node_layouts[node_id].row
        if not exclude_current and self.row_is_free(node_id, row, avoid):
            return row
        for d in range(1, self.height):
            for cand in ((row + d, row - d) if prefer_down else (row - d, row + d)):
                if self.row_is_free(node_id, cand, avoid):
                    return cand
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Sections
# ─────────────────────────────────────────────────────────────────────────────


def _proportional_widths(weights: List[float], total: int, minimum: int) -> List[int]:
    """Integer widths summing to *total*, ∝ *weights*, each ≥ *minimum* when possible."""
    n = len(weights)
    if n * minimum >= total:
        base = total // n
        widths = [base] * n
        for i in range(total - base * n):
            widths[i] += 1
        return widths
    fixed: Set[int] = set()
    real = [0.0] * n
    while True:
        free = total - minimum * len(fixed)
        rest = [i for i in range(n) if i not in fixed]
        wsum = sum(weights[i] for i in rest)
        for i in rest:
            real[i] = free * (weights[i] / wsum if wsum > 0 else 1.0 / len(rest))
        newly = [i for i in rest if real[i] < minimum]
        if not newly:
            break
        for i in newly:
            fixed.add(i)
            real[i] = float(minimum)
    widths = [int(math.floor(r)) for r in real]
    remainders = sorted(range(n), key=lambda i: (-(real[i] - widths[i]), i))
    for i in remainders[: total - sum(widths)]:
        widths[i] += 1
    return widths


def _assign_sections(
    spec: LevelSpec,
    cols: Dict[str, List[int]],  # node id → [col, width]; mutated on remap
    width: int,
    adjustments: List[str],
) -> List[SectionBand]:
    ordered = sorted(spec.sections, key=lambda s: (s.order, s.id))
    members: Dict[str, List[str]] = {s.id: [] for s in ordered}
    for n in spec.nodes:
        members.setdefault(n.section, []).append(n.id)

    def extent(sid: str) -> Optional[Tuple[int, int]]:
        ids = members.get(sid) or []
        if not ids:
            return None
        return (
            min(cols[i][0] for i in ids),
            max(cols[i][0] + cols[i][1] - 1 for i in ids),
        )

    extents = [extent(s.id) for s in ordered]
    respected = all(e is not None for e in extents) and all(
        extents[i][1] < extents[i + 1][0] for i in range(len(extents) - 1)  # type: ignore[index]
    )

    bounds: List[Tuple[int, int]] = []
    if respected:
        start = 0
        for i in range(len(ordered)):
            if i == len(ordered) - 1:
                end = width - 1
            else:
                end = (extents[i][1] + extents[i + 1][0]) // 2  # type: ignore[index]
            bounds.append((start, end))
            start = end + 1
    else:
        weights = [float(e[1] - e[0] + 1) if e else 0.0 for e in extents]
        widths = _proportional_widths(weights, width, MIN_SECTION_COLS)
        start = 0
        for sec, ext, bw in zip(ordered, extents, widths):
            band = (start, start + bw - 1)
            bounds.append(band)
            start += bw
            if ext is None:
                continue
            ext_w = ext[1] - ext[0] + 1
            ids = members[sec.id]
            if ext_w <= bw:
                shift = band[0] + (bw - ext_w) // 2 - ext[0]
                for nid in ids:
                    cols[nid][0] += shift
            else:
                scale = bw / ext_w
                for nid in ids:
                    c, w = cols[nid]
                    nw = max(1, _round_half_up(w * scale))
                    nc = band[0] + int(math.floor((c - ext[0]) * scale))
                    cols[nid] = [min(nc, band[1] - nw + 1), nw]
            new_ext = extent(sec.id)
            adjustments.append(
                f"remapped section '{sec.id}' from columns {ext[0]}..{ext[1]} to "
                f"{new_ext[0]}..{new_ext[1]} (band {band[0]}..{band[1]}) to keep the "
                "section order"
            )

    return [
        SectionBand(
            id=s.id,
            name=s.name,
            col_start=b[0],
            col_end=b[1],
            gravity_scale=s.gravity_scale,
            theme=s.theme,
            difficulty=s.difficulty,
        )
        for s, b in zip(ordered, bounds)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Golden path + conflict resolution
# ─────────────────────────────────────────────────────────────────────────────


def spec_golden_path(spec: LevelSpec) -> List[str]:
    """Fewest-hop Start → Exit path through the spec's edges ([] if none)."""
    g = nx.DiGraph()
    g.add_nodes_from(n.id for n in spec.nodes)
    g.add_edges_from((e.from_, e.to) for e in spec.edges if e.from_ in g and e.to in g)
    starts = [n.id for n in spec.nodes if n.type == "Start"]
    exits = [n.id for n in spec.nodes if n.type == "Exit"]
    best: List[str] = []
    for s in starts:
        for e in exits:
            if nx.has_path(g, s, e):
                path = nx.shortest_path(g, s, e)
                if not best or len(path) < len(best):
                    best = path
    return best


def resolve_conflicts(layout: SpecLayout, protected: Iterable[str] = ()) -> None:
    """Nudge nodes vertically until no two conflict (records every adjustment).

    The mover is chosen off-golden-path first, then non-terminal, then the
    later id; nodes in *protected* move only as a last resort.
    """
    golden = set(layout.golden_path) | set(protected)
    order = list(layout.node_layouts)
    unresolved: Set[Tuple[str, str]] = set()

    def rank(nid: str) -> Tuple[int, int]:
        return (
            1 if nid in golden else 0,
            1 if layout.node_kinds.get(nid) in ("Start", "Exit") else 0,
        )

    for _ in range(4 * len(order) + 4):
        found = None
        for i, a in enumerate(order):
            for b in order[i + 1 :]:
                if (a, b) in unresolved:
                    continue
                why = layout.conflict(a, b)
                if why:
                    found = (a, b, why)
                    break
            if found:
                break
        if not found:
            return
        a, b, why = found
        movers = sorted((b, a), key=rank)
        moved = False
        for mover in movers:
            other = a if mover == b else b
            prefer_down = layout.node_layouts[mover].row > layout.node_layouts[other].row
            row = layout.nearest_free_row(mover, prefer_down=prefer_down, exclude_current=True)
            if row is None:
                continue
            old = layout.node_layouts[mover].row
            layout.node_layouts[mover].row = row
            layout.adjustments.append(
                f"moved '{mover}' {'up' if row < old else 'down'} {abs(row - old)} "
                f"tile(s) (row {old} → {row}) to fix {why} with '{other}'"
            )
            moved = True
            break
        if not moved:
            unresolved.add((a, b))
            layout.adjustments.append(
                f"could not resolve {why} between '{a}' and '{b}'"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────


def layout_spec(spec: LevelSpec, profile: Optional[GameProfile] = None, seed: int = 0) -> SpecLayout:
    """Lay *spec* out on a ``spec.size`` grid (see the module docstring).

    *seed* is accepted for API symmetry; the layout is fully deterministic.
    """
    profile = profile or GameProfile()
    ts = profile.tile_size
    width, height = spec.size.width_tiles, spec.size.height_tiles
    player_tiles = profile.player_tiles()
    adjustments: List[str] = []

    raw: Dict[str, Tuple[int, int, int]] = {
        n.id: spec_to_grid(n.x, n.y, n.w, height, ts) for n in spec.nodes
    }
    cols: Dict[str, List[int]] = {}
    for nid, (col, _row, w) in raw.items():
        w = min(w, width)
        c = min(max(col, 0), width - w)
        if c != col:
            adjustments.append(f"clamped '{nid}' to column {c} (was {col}) to fit the level width")
        cols[nid] = [c, w]

    sections = _assign_sections(spec, cols, width, adjustments)

    kinds = {n.id: n.type for n in spec.nodes}
    node_layouts: Dict[str, NodeLayout] = {}
    for n in spec.nodes:
        row = raw[n.id][1]
        min_row = player_tiles[1] if n.type != "Hazard" else 0
        clamped = min(max(row, min_row), height - 1)
        if clamped != row:
            adjustments.append(
                f"clamped '{n.id}' to row {clamped} (was {row}) to keep it inside the "
                "level with headroom for the player"
            )
        c, w = cols[n.id]
        node_layouts[n.id] = NodeLayout(node_id=n.id, col=c, row=clamped, width=w, height=1)

    ir = IRLevel.empty("Level_0", width, height, ts, title=spec.title)
    layout = SpecLayout(
        node_layouts=node_layouts,
        ir=ir,
        sections=sections,
        adjustments=adjustments,
        node_kinds=kinds,
        node_sections={n.id: n.section for n in spec.nodes},
        golden_path=spec_golden_path(spec),
        player_tiles=player_tiles,
    )
    resolve_conflicts(layout)
    layout.rasterise()
    return layout
