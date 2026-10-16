"""Comfortable golden path — short hops from one platform to the next (v3 Part 20).

Physics says what the player *can* do; the Game Profile's ``design`` block says
what a normal move *should* be (``comfort_hop_tiles`` across, ``comfort_rise_tiles``
up, ``comfort_drop_tiles`` down).  LLM layouts keep the LLM's coordinates, so a
verified level may cross the whole jump reach in one jump (about 7–8 tiles with
the default profile since v4 Part 28; 10–15 before), over other platforms.  This
pass runs after a layout has verified and before densify:

1. **Check.**  The golden route is the cheapest Start → Exit span path by effort
   (``layout.route.cheapest_path`` / ``move_cost``, the route the overlay shows).
   Each of its moves (walks excepted) is measured as ``(gap, rise, drop)`` between
   its spans (``layout.route.move_measure``) and judged by the style of the
   section its source span lies in (``Section.style``, null = ``mixed``):

   * ``walk``    — every move must be comfortable;
   * ``mixed``   — every move must be comfortable, except one *long* jump in a
     row: a move within ``LONG_FACTOR`` × the comfort limits whose previous move
     on the route was not long too (a longer one is always fixed);
   * ``parkour`` — moves are kept up to the physics limit (they are verified).

2. **Fix.**  For each move to fix, insert a chain of intermediate platforms
   (``WIDTHS``: 5, 4 or 3 tiles wide; on the bottom row they are ground
   segments) with repair's stone placement (``layout.repair.find_stone``)
   bounded so each stone is within comfortable reach of the previous one, and
   the last one within comfortable reach of the target.  Stone placement keeps
   every walk's headroom and conflicts with no node; every hop is checked by the
   collision-aware oracle (``move_between``).  At most ``MAX_CHAIN`` stones per
   move; a chain that does not link is removed.

3. **Verify.**  After each chain the level is re-verified; a chain that breaks
   verification is removed.  The route is recomputed and the check repeats, at
   most ``MAX_ROUNDS`` times.

A move that cannot be made comfortable stays and gets a warning; the pass never
fails a level.  The added nodes (ids ``hop_N``) appear in ``spec.nodes`` through
``SpecLayout.added_nodes`` like repair's stones and densify's fills.

Region and section edits run the same pass on the raw grid (:func:`comfort_grid`),
with stones confined to the edit's rectangle and only for moves that start or
end in it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Set, Tuple

from game.profile import GameProfile
from spec.level_spec import LevelSpec
from symbolic.schema import AbstractLevelGraph

from .densify import _edge_cells, _grid_layout, protected_cells, route_cells
from .ir import IREntity, TileValue
from .repair import find_stone
from .route import _edge_kind, cheapest_path, move_measure, span_rect
from .spec_entities import place_spec_entities
from .spec_layout import Rect, SectionBand, SpecLayout
from .sugiyama import NodeLayout
from .verify import GridReport, verify_grid

HOP_PREFIX = "hop_"
WIDTHS = (5, 4, 3)  # intermediate platform widths, widest first
MAX_CHAIN = 4  # stones per long move
MAX_ROUNDS = 3  # route → fix → re-verify rounds
LONG_FACTOR = 1.5  # a "long" jump a mixed section may keep (one in a row)
STYLES = ("walk", "parkour", "mixed")

Cell = Tuple[int, int]  # (col, row)
State = Tuple[AbstractLevelGraph, Optional[str], Set[str]]  # graph, start span, exit spans


@dataclass
class Move:
    """One move of the golden route (walks excluded)."""

    src: Rect
    dst: Rect
    kind: str  # jump | fall | double_jump | dash | wall_jump
    style: str  # walk | parkour | mixed (of the source span's section)
    u: str = ""  # span ids in the graph
    v: str = ""

    @property
    def measure(self) -> Tuple[int, int, int]:
        return move_measure(self.src, self.dst)

    def describe(self) -> str:
        gap, rise, drop = self.measure
        vertical = f"{rise} up" if rise else f"{drop} down"
        return (f"{self.kind} from column {self.src[1]}–{self.src[2]}, row {self.src[0]} to "
                f"column {self.dst[1]}–{self.dst[2]}, row {self.dst[0]} ({gap} tiles across, "
                f"{vertical})")


@dataclass
class ComfortResult:
    added: List[str] = field(default_factory=list)  # hop node ids kept
    warnings: List[str] = field(default_factory=list)  # moves left uncomfortable
    notes: List[str] = field(default_factory=list)  # summary + warnings, for the build warnings
    report: Optional[GridReport] = None  # verification of the final layout (SpecLayout pass)


def normal_style(style: Optional[str]) -> str:
    return style if style in STYLES else "mixed"


# ─────────────────────────────────────────────────────────────────────────────
# Check
# ─────────────────────────────────────────────────────────────────────────────


def golden_moves(
    graph: AbstractLevelGraph,
    start_span: Optional[str],
    exit_spans: Set[str],
    profile: GameProfile,
    style_at: Callable[[int, int], Optional[str]],
) -> List[Move]:
    """The non-walk moves of the cheapest Start → Exit route ([] when there is none)."""
    if start_span is None or not exit_spans:
        return []
    path = cheapest_path(graph, start_span, sorted(exit_spans), profile)
    if not path:
        return []
    moves: List[Move] = []
    for u, v in zip(path, path[1:]):
        kind, _ability = _edge_kind(graph.get_edge(u, v))
        if kind == "walk":
            continue
        src, dst = span_rect(graph, u), span_rect(graph, v)
        style = normal_style(style_at((src[1] + src[2]) // 2, src[0]))
        moves.append(Move(src, dst, kind, style, u, v))
    return moves


def is_long(move: Move, profile: GameProfile) -> bool:
    """Within ``LONG_FACTOR`` × the comfort limits (what a mixed section may keep)."""
    d = profile.design
    gap, rise, drop = move.measure
    return (gap <= LONG_FACTOR * d.comfort_hop_tiles and rise <= LONG_FACTOR * d.comfort_rise_tiles
            and drop <= LONG_FACTOR * d.comfort_drop_tiles)


def moves_to_fix(moves: Sequence[Move], profile: GameProfile) -> List[Move]:
    """The moves the section styles do not allow (see the module doc)."""
    out: List[Move] = []
    prev_long = False
    for m in moves:
        if profile.design.is_comfortable(*m.measure) or m.style == "parkour":
            prev_long = False
            continue
        if m.style == "mixed" and not prev_long and is_long(m, profile):
            prev_long = True
            continue
        out.append(m)
        prev_long = False  # once fixed, the move is a comfortable chain
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Fix
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _Reach:
    """What ``densify.protected_cells`` / ``route_cells`` read from a GridReport."""

    graph: AbstractLevelGraph
    start_span: Optional[str]
    exit_spans: Set[str]


class _Pass:
    """The check → fix → verify loop over a SpecLayout (whole level or an edit's grid)."""

    def __init__(
        self,
        layout: SpecLayout,
        profile: GameProfile,
        state: State,
        commit: Callable[[], Optional[State]],
        restore: Callable[[], None],
        style_at: Callable[[int, int], Optional[str]],
        in_scope: Callable[[Move], bool] = lambda m: True,
        allowed: Optional[Callable[[Rect], bool]] = None,
    ) -> None:
        self.layout = layout
        self.profile = profile
        self.state = state
        self.commit = commit
        self.restore = restore
        self.style_at = style_at
        self.in_scope = in_scope
        self.allowed = allowed
        self.added: List[str] = []
        self._protected: Optional[Set[Cell]] = None
        n = 1
        while f"{HOP_PREFIX}{n}" in layout.node_layouts:
            n += 1
        self.next_id = n

    def _moves(self) -> List[Move]:
        graph, start, exits = self.state
        moves = golden_moves(graph, start, exits, self.profile, self.style_at)
        return [m for m in moves_to_fix(moves, self.profile) if self.in_scope(m)]

    def _add(self, stone: Rect) -> str:
        lay = self.layout
        row, c0, c1 = stone
        nid = f"{HOP_PREFIX}{self.next_id}"
        self.next_id += 1
        lay.node_layouts[nid] = NodeLayout(node_id=nid, col=c0, row=row, width=c1 - c0 + 1, height=1)
        lay.node_kinds[nid] = "Platform"
        lay.node_sections[nid] = lay.section_at((c0 + c1) // 2, row).id
        lay.added_nodes.append(nid)
        lay.ir.grid[row][c0 : c1 + 1] = [TileValue.SOLID] * (c1 - c0 + 1)
        return nid

    def _remove(self, ids: Sequence[str]) -> None:
        lay = self.layout
        for nid in ids:
            row, c0, c1 = lay.rect(nid)
            lay.ir.grid[row][c0 : c1 + 1] = [TileValue.EMPTY] * (c1 - c0 + 1)
            del lay.node_layouts[nid]
            lay.node_kinds.pop(nid, None)
            lay.node_sections.pop(nid, None)
            lay.added_nodes.remove(nid)
        self.restore()

    def protected(self) -> Set[Cell]:
        """Cells the arcs of the level's moves sweep (densify's protected cells plus the
        golden route): a stone there would cut a move the level relies on."""
        if self._protected is None:
            graph, start, exits = self.state
            reach = _Reach(graph, start, set(exits))
            self._protected = (protected_cells(self.layout, reach, self.profile)  # type: ignore[arg-type]
                               | route_cells(self.layout, reach, self.profile))  # type: ignore[arg-type]
        return self._protected

    def _chain(self, move: Move) -> Optional[List[str]]:
        """Stones from *move*'s source to its target, each hop comfortable (or None)."""
        design = self.profile.design
        graph = self.state[0]
        own: Set[Cell] = set()
        if move.u and move.v and graph.nx_graph.has_edge(move.u, move.v):
            own = _edge_cells(self.layout, self.profile, graph.get_edge(move.u, move.v))
        keep_clear = self.protected() - own  # the long move itself may be cut: the chain replaces it

        def allowed(rect: Rect) -> bool:
            row, a, b = rect
            if any((c, row) in keep_clear for c in range(a, b + 1)):
                return False
            return self.allowed is None or self.allowed(rect)

        cur = move.src
        chain: List[str] = []
        for _ in range(MAX_CHAIN):
            here = cur
            found = find_stone(
                self.layout, self.profile, here, move.dst, widths=WIDTHS,
                accept=lambda s: design.is_comfortable(*move_measure(here, s)),
                links=lambda s: design.is_comfortable(*move_measure(s, move.dst)),
                allowed=allowed,
            )
            if found is None:
                break
            stone, linked = found
            chain.append(self._add(stone))
            if linked:
                return chain
            cur = stone
        if chain:
            self._remove(chain)
        return None

    def _mark_golden(self, move: Move, chain: List[str]) -> None:
        path = self.layout.golden_path
        row, c0, c1 = move.dst
        for i, nid in enumerate(path):
            nl = self.layout.node_layouts.get(nid)
            if nl is not None and nl.row == row and nl.col <= c1 and c0 <= nl.col + nl.width - 1:
                path[i:i] = chain
                return

    def run(self) -> List[str]:
        """Fix what can be fixed; returns the warnings for the moves left as they were."""
        failed: Set[Tuple[Rect, Rect]] = set()
        for _ in range(MAX_ROUNDS):
            todo = [m for m in self._moves() if (m.src, m.dst) not in failed]
            if not todo:
                break
            progress = False
            for move in todo:
                chain = self._chain(move)
                if chain is None:
                    failed.add((move.src, move.dst))
                    continue
                new = self.commit()
                if new is None:  # the stones broke verification
                    self._remove(chain)
                    failed.add((move.src, move.dst))
                    continue
                self.state = new
                self._protected = None
                self.added += chain
                self._mark_golden(move, chain)
                progress = True
            if not progress:
                break
        return [f"comfort: could not shorten the {m.describe()}" for m in self._moves()]


def style_of(band: SectionBand) -> Optional[str]:
    """A section's comfort style; a ``parkour`` structure plays as parkour (v3 Part 21)."""
    return "parkour" if band.structure == "parkour" else band.style


COMFORT_STRUCTURES = (None, "open", "parkour")
"""Structures the comfort pass works in (v3 Part 21): carved terrain has its own rhythm."""


def terrain_scope(layout: SpecLayout) -> Tuple[Callable[[Move], bool], Optional[Callable[[Rect], bool]]]:
    """Moves to fix and stone rectangles allowed: only in open-air (and parkour) sections."""
    if not layout.has_terrain:
        return (lambda m: True), None

    def ok_cell(col: int, row: int) -> bool:
        return layout.section_at(col, row).structure in COMFORT_STRUCTURES

    def in_scope(m: Move) -> bool:
        return ok_cell((m.src[1] + m.src[2]) // 2, m.src[0])

    def allowed(rect: Rect) -> bool:
        return ok_cell(rect[1], rect[0]) and ok_cell(rect[2], rect[0])

    return in_scope, allowed


def _summary(added: int, warnings: List[str]) -> List[str]:
    notes = [f"comfort: added {added} platform(s) for short hops"] if added else []
    return notes + warnings


# ─────────────────────────────────────────────────────────────────────────────
# Entry points
# ─────────────────────────────────────────────────────────────────────────────


def make_comfortable(
    layout: SpecLayout, report: GridReport, profile: Optional[GameProfile], spec: LevelSpec
) -> ComfortResult:
    """Make the golden path of a *verified* layout comfortable (mutated in place).

    Returns the hop node ids kept, the warnings and the final report, which is
    always ``ok`` when *report* was.
    """
    profile = profile or GameProfile()
    result = ComfortResult(report=report)
    if not report.ok or report.graph is None:
        return result
    holder = [report]

    def commit() -> Optional[State]:
        layout.rasterise()
        place_spec_entities(spec, layout)
        rep = verify_grid(layout, profile, spec)
        if not rep.ok or rep.graph is None:
            return None
        holder[0] = rep
        return rep.graph, rep.start_span, set(rep.exit_spans)

    def restore() -> None:
        layout.rasterise()
        place_spec_entities(spec, layout)

    in_scope, allowed = terrain_scope(layout)
    p = _Pass(layout, profile, (report.graph, report.start_span, set(report.exit_spans)),
              commit, restore, lambda c, r: style_of(layout.section_at(c, r)),
              in_scope=in_scope, allowed=allowed)
    result.warnings = p.run()
    result.added = list(p.added)
    result.report = holder[0]
    result.notes = _summary(len(result.added), result.warnings)
    return result


@dataclass
class GridComfortResult:
    grid: List[List[int]]  # the input grid plus the kept platforms
    added: List[Rect] = field(default_factory=list)  # kept platforms (row, col0, col1)
    warnings: List[str] = field(default_factory=list)

    @property
    def notes(self) -> List[str]:
        return _summary(len(self.added), self.warnings)


def comfort_grid(
    grid: List[List[int]],
    entities: Sequence[IREntity],
    bounds: Tuple[int, int, int, int],
    profile: GameProfile,
    *,
    sections: List[SectionBand],
    graph: AbstractLevelGraph,
    start_span: Optional[str],
    exit_spans: Set[str],
    forbidden: Set[Cell],
    check: Callable[[List[List[int]]], Optional[State]],
    tile_size: int = 16,
) -> GridComfortResult:
    """The comfort pass on a raw grid, confined to *bounds* ``(col0, row0, col1, row1)``.

    Only moves whose source or target span overlaps *bounds* are fixed, and
    stones lie inside *bounds*, off the *forbidden* cells (seams, entities).
    *graph*, *start_span*, *exit_spans* describe the verified grid;
    ``check(grid)`` re-verifies a candidate grid and returns its new
    ``(graph, start_span, exit_spans)``, or None when it fails (the stones are
    then removed).  Never fails.
    """
    layout = _grid_layout(grid, entities, tile_size, sections, profile.player_tiles())
    c0, r0, c1, r1 = bounds

    def overlaps(rect: Rect) -> bool:
        return r0 <= rect[0] <= r1 and rect[1] <= c1 and c0 <= rect[2]

    def allowed(rect: Rect) -> bool:
        row, a, b = rect
        if not (r0 <= row <= r1 and c0 <= a and b <= c1):
            return False
        return not any((c, row) in forbidden for c in range(a, b + 1))

    def commit() -> Optional[State]:
        return check([list(line) for line in layout.ir.grid])

    p = _Pass(layout, profile, (graph, start_span, set(exit_spans)), commit, lambda: None,
              lambda c, r: layout.section_at(c, r).style,
              in_scope=lambda m: overlaps(m.src) or overlaps(m.dst), allowed=allowed)
    warnings = p.run()
    added = [layout.rect(n) for n in p.added]
    return GridComfortResult([list(line) for line in layout.ir.grid], added, warnings)


__all__ = [
    "ComfortResult",
    "GridComfortResult",
    "LONG_FACTOR",
    "Move",
    "comfort_grid",
    "golden_moves",
    "make_comfortable",
    "moves_to_fix",
]
