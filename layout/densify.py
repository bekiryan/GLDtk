"""Densify — fill the large empty regions of a verified layout with extra platforms.

LLMs emit about ten small platforms even for a 120×30 level, which reads as a
few specks in a void.  ``densify`` runs after a layout has verified
(``pipeline/level.py``) and adds Platform nodes (ids ``fill_N``), in three
phases, in this priority:

1. **ground** (horizontal levels only): segments 3–8 tiles wide on the bottom
   row under long empty stretches (at least ``GROUND_MIN_RUN`` columns),
   separated by 2–3 tile pits; skipped in sections whose theme has no ground
   (sky, clouds, space, ...);
2. **ledges**: an intermediate ledge for every golden-path hop that climbs or
   drops at least ``LEDGE_MIN_ROWS`` rows or crosses ``LEDGE_MIN_COLS``
   columns, reachable from the hop's source and reaching its target;
3. **fill**: platforms 3–8 tiles wide in the emptiest cells — the cell with
   the largest Chebyshev distance (capped at ``EMPTINESS_CAP``) to any tile
   first, seeded-random among ties — among the cells within a plain jump of
   a node the player can reach (cells within *comfortable* reach of one —
   the Game Profile's ``design`` limits, v3 Part 20 — first), while a cell
   at least ``MIN_EMPTINESS`` tiles from every tile exists, up to about one platform per
   ``TILES_PER_PLATFORM`` empty tiles in all (at most ``MAX_ADDED``).

Every addition must:

* lie on empty cells inside one section band (it joins that section);
* keep ``SAME_ROW_GAP`` empty columns to other tiles on its row (spans never
  merge, so the grid's existing moves keep their spans);
* not conflict with any node (overlap, or less than the player's height of
  headroom above the lower of two nodes, ``SpecLayout.conflict``);
* avoid every entity cell and the **protected cells**: the cells the player's
  hitbox sweeps along one collision-free trajectory per edge of a spanning
  tree of the grid's reachable spans (first the spans reachable without
  passing an Exit, then the rest), so every span reachable before stays
  reachable by the same move — in particular no tile lies on a golden-path arc —
  plus the arcs of the golden route (the cheapest path by effort,
  ``layout.route.cheapest_path``), so the short hops the comfort pass made stay
  the route;
* be reachable from a node the player can reach and reach one back, trying
  the nodes within comfortable reach first (``move_between``: the oracle's
  collision-aware reach with the profile's abilities and the section's gravity).  The trajectories of those two moves
  join the protected cells.

The additions are not trusted blindly: the layout is re-rasterised, the
entities re-placed and the grid re-verified.  If it fails, the longest prefix
of the additions (in priority order) that still verifies is kept (binary
search), down to none — densify never fails a level.  Fill nodes get no spec
edges; they appear in ``spec.nodes`` through ``SpecLayout.added_nodes`` like
repair's stepping stones.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from game.profile import GameProfile
from spec.level_spec import LevelSpec
from symbolic.extractor import move_between
from symbolic.physics import (
    flat_jump_reach,
    gravity_for,
    max_jump_height,
    trajectory_candidates,
    trajectory_hits,
)

from .ir import IREntity, IRLevel, TileValue
from .repair import _swept_cells
from .route import move_measure
from .spec_entities import place_spec_entities
from .spec_layout import Rect, SectionBand, SpecLayout
from .sugiyama import NodeLayout
from .verify import GridReport, gravity_field_of, verify_grid

FILL_PREFIX = "fill_"
MIN_WIDTH, MAX_WIDTH = 3, 8  # tiles
import contextvars as _cv

# Design memory (v4 Part 27): multiplier on the platform budget, set per request.
DENSITY_SCALE: "_cv.ContextVar[float]" = _cv.ContextVar("densify_scale", default=1.0)
TILES_PER_PLATFORM = 50  # target: about one platform per this many empty tiles
MIN_EMPTINESS = 4  # a fill platform's centre is at least this far (Chebyshev) from any tile
EMPTINESS_CAP = 10
SAME_ROW_GAP = 2  # empty columns kept to other tiles on the same row
GROUND_MIN_RUN = 8  # empty bottom-row columns needed for ground
GROUND_CLEAR_ROWS = 4  # rows above the ground that must be empty
LEDGE_MIN_ROWS = 5
LEDGE_MIN_COLS = 8
MAX_ADDED = 100  # verification cost grows with the square of the platform count
TOP_MARGIN = 1  # fill platforms keep one row more than the player's height below the top
MAX_FILL_TRIES = 1500  # candidate centres tried by the fill phase
MAX_SOURCES = 8  # reachable nodes tried per reach check (nearest first)
NO_GROUND_THEMES = ("sky", "cloud", "space", "air", "heaven", "star", "void", "float")

Cell = Tuple[int, int]  # (col, row)


@dataclass
class DensifyResult:
    added: List[str] = field(default_factory=list)  # fill node ids kept
    dropped: List[str] = field(default_factory=list)  # removed to keep the level verified
    report: Optional[GridReport] = None  # verification of the final layout
    notes: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Protected cells
# ─────────────────────────────────────────────────────────────────────────────


def _edge_cells(layout: SpecLayout, profile: GameProfile, edge) -> Set[Cell]:
    """Cells swept by the first collision-free trajectory of a grid edge."""
    meta = edge.metadata
    if "launch" not in meta or "land" not in meta:
        return set()  # a walk: covered by the headroom rule
    p1, p2 = meta["launch"], meta["land"]
    scale = float(meta.get("gravity_scale", 1.0))
    return _move_cells(layout, profile, meta.get("ability"), p1, p2, scale)


def _move_cells(layout: SpecLayout, profile: GameProfile, ability, p1, p2, scale: float) -> Set[Cell]:
    grid = layout.ir.grid
    ts = float(layout.tile_size)
    hitbox = (profile.player.width_px, profile.player.height_px)
    field = gravity_field_of(layout, profile)  # gravity changes at section borders
    for samples in trajectory_candidates(grid, p1, p2, profile, scale, ability, gravity_field=field):
        if not trajectory_hits(grid, samples, p2, ts, hitbox):
            return _swept_cells(layout, samples, hitbox)
    return set()


def protected_cells(layout: SpecLayout, report: GridReport, profile: GameProfile) -> Set[Cell]:
    """Cells to keep empty so every reachable span stays reachable (see module doc).

    One trajectory per edge of a BFS tree over ``report.graph`` from the start
    span: first without expanding Exit spans (keys stay reachable before the
    Exit), then from everything reached so far.
    """
    graph = report.graph
    if graph is None or report.start_span is None:
        return set()
    g = graph.nx_graph
    cells: Set[Cell] = set()
    seen = {report.start_span}
    frontier = [report.start_span]
    for expand_exits in (False, True):
        if expand_exits:
            frontier = sorted(seen)
        while frontier:
            nxt: List[str] = []
            for cur in frontier:
                if cur in report.exit_spans and not expand_exits:
                    continue
                for succ in sorted(g.successors(cur)):
                    if succ in seen:
                        continue
                    seen.add(succ)
                    nxt.append(succ)
                    cells |= _edge_cells(layout, profile, graph.get_edge(cur, succ))
            frontier = nxt
    return cells


def route_cells(layout: SpecLayout, report: GridReport, profile: GameProfile) -> Set[Cell]:
    """Cells swept along the golden route (cheapest Start → Exit path by effort)."""
    from .route import cheapest_path

    graph = report.graph
    if graph is None or report.start_span is None or not report.exit_spans:
        return set()
    path = cheapest_path(graph, report.start_span, sorted(report.exit_spans), profile)
    cells: Set[Cell] = set()
    for u, v in zip(path or [], (path or [])[1:]):
        cells |= _edge_cells(layout, profile, graph.get_edge(u, v))
    return cells


def _entity_cells(layout: SpecLayout) -> Set[Cell]:
    """Every entity cell (the Start's whole hitbox), so re-placing finds the same cells."""
    cells: Set[Cell] = set()
    for ent in layout.ir.entities:
        height = layout.player_tiles[1] if ent.entity_type == "Start" else 1
        for k in range(height):
            cells.add((ent.col, ent.row - k))
    return cells


# ─────────────────────────────────────────────────────────────────────────────
# Emptiness map
# ─────────────────────────────────────────────────────────────────────────────


def _emptiness(grid: List[List[int]]) -> List[List[int]]:
    """Chebyshev distance of every cell to the nearest non-empty cell, capped."""
    height, width = len(grid), len(grid[0])
    dist = [[EMPTINESS_CAP] * width for _ in range(height)]
    for r in range(height):
        for c in range(width):
            if grid[r][c] != TileValue.EMPTY:
                _stamp(dist, r, c, c)
    return dist


def _stamp(dist: List[List[int]], row: int, c0: int, c1: int) -> None:
    """Lower the distances around the tiles ``(row, c0..c1)``."""
    height, width = len(dist), len(dist[0])
    cap = EMPTINESS_CAP
    for r in range(max(0, row - cap), min(height, row + cap + 1)):
        dr = abs(r - row)
        line = dist[r]
        for c in range(max(0, c0 - cap), min(width, c1 + cap + 1)):
            dc = c0 - c if c < c0 else (c - c1 if c > c1 else 0)
            d = dr if dr > dc else dc
            if d < line[c]:
                line[c] = d


# ─────────────────────────────────────────────────────────────────────────────
# Densifier
# ─────────────────────────────────────────────────────────────────────────────


class _Densifier:
    def __init__(
        self, layout: SpecLayout, report: GridReport, profile: GameProfile, seed: int,
        bounds: Optional[Tuple[int, int, int, int]] = None,
        reachable: Optional[Set[str]] = None,
        extra_protected: Optional[Set[Cell]] = None,
    ) -> None:
        """*bounds* ``(col0, row0, col1, row1)`` (inclusive) confines every addition to
        a rectangle (edits); *reachable* overrides ``report.reachable_nodes``."""
        self.layout = layout
        self.profile = profile
        self.rng = random.Random(seed)
        self.grid = layout.ir.grid
        self.field = gravity_field_of(layout, profile)
        self.bounds = bounds
        self.protected = (protected_cells(layout, report, profile)
                          | route_cells(layout, report, profile) | set(extra_protected or ()))
        self.avoid = _entity_cells(layout)
        self.reachable: List[str] = sorted(
            n for n in (report.reachable_nodes if reachable is None else reachable)
            if layout.standable(n)
        )
        self.added: List[str] = []
        self.dist = _emptiness(self.grid)
        if bounds is None:
            empty = sum(row.count(TileValue.EMPTY) for row in self.grid)
            standable = sum(1 for n in layout.node_layouts if layout.standable(n))
        else:
            empty = sum(1 for r in range(bounds[1], bounds[3] + 1) for c in range(bounds[0], bounds[2] + 1)
                        if self.grid[r][c] == TileValue.EMPTY)
            standable = sum(1 for n in layout.node_layouts if layout.standable(n)
                            and self._in(layout.rect(n)[1], layout.rect(n)[0]))
        self.budget = min(MAX_ADDED, max(0, round(empty / TILES_PER_PLATFORM * DENSITY_SCALE.get()) - standable))
        # Cells within a jump of a node the player can reach (fill candidates).
        g = gravity_for(profile, 1.0)
        ts = float(layout.tile_size)
        self.rise = max(1, int(max_jump_height(g, profile.physics.jump_v) / ts))
        self.reach = max(1, int(flat_jump_reach(g, profile.physics.jump_v, profile.physics.run_speed) / ts))
        self.near = [[False] * layout.width for _ in range(layout.height)]
        # Cells within comfortable reach (design limits) of a reachable node: tried first.
        d = profile.design
        self.comfy = [[False] * layout.width for _ in range(layout.height)]
        self.comfy_up, self.comfy_down = d.comfort_rise_tiles, d.comfort_drop_tiles
        self.comfy_hop = d.comfort_hop_tiles
        for nid in self.reachable:
            self._mark_near(layout.rect(nid))
        n = 1
        while f"{FILL_PREFIX}{n}" in layout.node_layouts:
            n += 1
        self.next_id = n

    def _in(self, col: int, row: int) -> bool:
        b = self.bounds
        return b is None or (b[0] <= col <= b[2] and b[1] <= row <= b[3])

    def _mark_near(self, rect: Rect) -> None:
        row, c0, c1 = rect
        lay = self.layout
        for r in range(max(0, row - self.rise), min(lay.height, row + self.rise + 1)):
            line = self.near[r]
            for c in range(max(0, c0 - self.reach), min(lay.width, c1 + self.reach + 1)):
                line[c] = True
        # A platform centred here is comfortable when its edge is within the hop
        # (a fill platform is at least MIN_WIDTH wide, so half of it lies toward the source).
        hop = self.comfy_hop + 1 + MIN_WIDTH // 2
        for r in range(max(0, row - self.comfy_up), min(lay.height, row + self.comfy_down + 1)):
            line = self.comfy[r]
            for c in range(max(0, c0 - hop), min(lay.width, c1 + hop + 1)):
                line[c] = True

    # ── checks ──────────────────────────────────────────────────────────

    def _fits(self, rect: Rect) -> Optional[str]:
        """The section id the platform *rect* would join, or None if it does not fit."""
        lay = self.layout
        row, c0, c1 = rect
        if c0 < 0 or c1 >= lay.width or not lay.player_tiles[1] <= row <= lay.height - 1:
            return None
        if not (self._in(c0, row) and self._in(c1, row)):
            return None
        band = lay.section_at(c0, row)
        if not (band.contains(c0, row) and band.contains(c1, row)):
            return None
        if band.is_terrain:  # carved terrain is never filled (v3 Part 21)
            return None
        line = self.grid[row]
        for c in range(max(0, c0 - SAME_ROW_GAP), min(lay.width, c1 + SAME_ROW_GAP + 1)):
            if line[c] != TileValue.EMPTY:
                return None
        for c in range(c0, c1 + 1):
            if (c, row) in self.protected or (c, row) in self.avoid:
                return None
        if any(lay.conflict("", other, rect) for other in lay.node_layouts):
            return None
        return band.id

    def _connect(self, rect: Rect, sources: Optional[List[str]] = None,
                 targets: Optional[List[str]] = None) -> Optional[Set[Cell]]:
        """Swept cells of a move into *rect* and one out of it, or None.

        The platform's tiles must already be on the grid.
        """
        lay = self.layout
        centre = ((rect[1] + rect[2]) / 2.0, rect[0])

        def nearest(ids: List[str]) -> List[str]:
            def d(n: str) -> float:
                r = lay.rect(n)
                return math.hypot((r[1] + r[2]) / 2.0 - centre[0], r[0] - centre[1])
            return sorted(ids, key=lambda n: (d(n), n))[:MAX_SOURCES]

        pool = self.reachable + self.added
        cells: Set[Cell] = set()
        design = self.profile.design

        def comfortable_first(ids: List[str], into: bool) -> List[str]:
            def ok(n: str) -> bool:
                a, b = (lay.rect(n), rect) if into else (rect, lay.rect(n))
                return design.is_comfortable(*move_measure(a, b))
            return sorted(nearest(ids), key=lambda n: not ok(n))  # stable: nearest within each

        for src in comfortable_first(sources if sources is not None else pool, True):
            s = lay.rect(src)
            move = move_between(self.grid, s, rect, self.profile, lay.gravity_scale_of(s),
                                gravity_field=self.field)
            if move is not None:
                cells |= _move_cells(lay, self.profile, move[0], move[1], move[2],
                                     lay.gravity_scale_of(s))
                break
        else:
            return None
        scale = lay.gravity_scale_of(rect)
        for dst in comfortable_first(targets if targets is not None else pool, False):
            move = move_between(self.grid, rect, lay.rect(dst), self.profile, scale,
                                gravity_field=self.field)
            if move is not None:
                cells |= _move_cells(lay, self.profile, move[0], move[1], move[2], scale)
                return cells
        return None

    def _try(self, rect: Rect, sources: Optional[List[str]] = None,
             targets: Optional[List[str]] = None) -> bool:
        """Add a platform at *rect* if it fits and connects."""
        section = self._fits(rect)
        if section is None:
            return False
        row, c0, c1 = rect
        line = self.grid[row]
        line[c0 : c1 + 1] = [TileValue.SOLID] * (c1 - c0 + 1)
        cells = self._connect(rect, sources, targets)
        if cells is None or any(c0 <= c <= c1 and r == row for c, r in cells):
            line[c0 : c1 + 1] = [TileValue.EMPTY] * (c1 - c0 + 1)
            return False
        nid = f"{FILL_PREFIX}{self.next_id}"
        self.next_id += 1
        lay = self.layout
        lay.node_layouts[nid] = NodeLayout(node_id=nid, col=c0, row=row, width=c1 - c0 + 1, height=1)
        lay.node_kinds[nid] = "Platform"
        lay.node_sections[nid] = section
        self.added.append(nid)
        self.protected |= cells
        _stamp(self.dist, row, c0, c1)
        self._mark_near(rect)
        return True

    def _width(self) -> int:
        return self.rng.randint(MIN_WIDTH, MAX_WIDTH)

    # ── phases ──────────────────────────────────────────────────────────

    def ground(self) -> None:
        lay = self.layout
        if lay.orientation != "horizontal":
            return
        row = lay.height - 1
        if row - GROUND_CLEAR_ROWS < 0:
            return

        def open_col(c: int) -> bool:
            if not self._in(c, row):
                return False
            band = lay.section_at(c, row)
            if any(word in band.theme.lower() for word in NO_GROUND_THEMES):
                return False
            return all(
                self.grid[r][c] == TileValue.EMPTY
                for r in range(row - GROUND_CLEAR_ROWS, row + 1)
            )

        c = 0
        while c < lay.width:
            if not open_col(c):
                c += 1
                continue
            start = c
            while c < lay.width and open_col(c):
                c += 1
            end = c - 1
            if end - start + 1 < GROUND_MIN_RUN:
                continue
            x = start + (SAME_ROW_GAP if start > 0 else 0)
            while x <= end and len(self.added) < self.budget:
                w = min(self._width(), end - x + 1)
                if w >= MIN_WIDTH:
                    while w >= MIN_WIDTH and not self._try((row, x, x + w - 1)):
                        w -= 1
                x += max(w, 1) + self.rng.randint(2, 3)

    def ledges(self) -> None:
        lay = self.layout
        path = [n for n in lay.golden_path if n in lay.node_layouts and lay.standable(n)]
        for a, b in zip(path, path[1:]):
            if len(self.added) >= self.budget:
                return
            ra, rb = lay.rect(a), lay.rect(b)
            ca, cb = (ra[1] + ra[2]) / 2.0, (rb[1] + rb[2]) / 2.0
            gap_cols = max(rb[1] - ra[2] - 1, ra[1] - rb[2] - 1)
            if abs(ra[0] - rb[0]) < LEDGE_MIN_ROWS and gap_cols < LEDGE_MIN_COLS:
                continue
            mid = ((ca + cb) / 2.0, (ra[0] + rb[0]) / 2.0)
            w = self._width()
            cands = [
                (r, c)
                for r in range(min(ra[0], rb[0]) - 2, max(ra[0], rb[0]) + 3)
                for c in range(int(min(ca, cb)) - lay.width // 4, int(max(ca, cb)) + lay.width // 4)
            ]
            cands.sort(key=lambda rc: (math.hypot(rc[1] + w / 2.0 - mid[0], rc[0] - mid[1]), rc))
            for r, c in cands[:300]:
                if self._try((r, c, c + w - 1), sources=[a], targets=[b]):
                    break

    def fill(self) -> None:
        lay = self.layout
        min_row = lay.player_tiles[1] + TOP_MARGIN
        tried: Set[Tuple[int, int]] = set()  # (row, col) centres already tried
        tries = 0
        while len(self.added) < self.budget and tries < MAX_FILL_TRIES:
            # One epoch: the candidate centres as of now, emptiest first (seeded
            # random among equals).  Distances only shrink, so each is re-checked.
            cells = [
                (self.dist[r][c], r, c)
                for r in range(min_row, lay.height)
                for c in range(lay.width)
                if self.near[r][c] and self.dist[r][c] >= MIN_EMPTINESS and (r, c) not in tried
                and self._in(c, r)
            ]
            if not cells:
                return
            # comfortable positions first, then the emptiest (seeded random among equals)
            cells.sort(key=lambda t: (not self.comfy[t[1]][t[2]], -t[0], self.rng.random()))
            placed = False
            for _d, r, c in cells:
                if len(self.added) >= self.budget or tries >= MAX_FILL_TRIES:
                    return
                if self.dist[r][c] < MIN_EMPTINESS:
                    continue
                tried.add((r, c))
                tries += 1
                w = self._width()
                for width in range(w, MIN_WIDTH - 1, -1):
                    c0 = c - width // 2
                    if self._try((r, c0, c0 + width - 1)):
                        placed = True
                        break
            if not placed:
                return


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def longest_valid_prefix(n: int, ok: Callable[[int], bool]) -> int:
    """Largest k < n such that the first k additions verify (binary search; k = 0 is
    the input, which verifies).  Called once the full set of *n* has failed."""
    lo, hi = 0, n  # prefix lo verifies, hi does not
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


def _apply(
    layout: SpecLayout, spec: LevelSpec, profile: GameProfile, base: List[str], fills: List[str],
    keep: int, saved: Dict[str, Tuple[NodeLayout, str, str]],
) -> GridReport:
    """Keep the first *keep* fills, rebuild the grid and entities, verify."""
    for nid in fills:
        layout.node_layouts.pop(nid, None)
        layout.node_kinds.pop(nid, None)
        layout.node_sections.pop(nid, None)
    for nid in fills[:keep]:
        nl, kind, section = saved[nid]
        layout.node_layouts[nid] = nl
        layout.node_kinds[nid] = kind
        layout.node_sections[nid] = section
    layout.added_nodes = base + fills[:keep]
    layout.rasterise()
    place_spec_entities(spec, layout)
    return verify_grid(layout, profile, spec)


def densify(
    layout: SpecLayout,
    report: GridReport,
    profile: Optional[GameProfile],
    spec: LevelSpec,
    seed: int = 0,
) -> DensifyResult:
    """Add fill platforms to a *verified* layout (mutated in place; see the module doc).

    Returns the kept and dropped node ids and the final report, which is
    always ``ok`` when *report* was.
    """
    profile = profile or GameProfile()
    result = DensifyResult(report=report)
    if not report.ok:
        return result
    base = list(layout.added_nodes)
    d = _Densifier(layout, report, profile, seed)
    if d.budget > 0:
        d.ground()
        d.ledges()
        d.fill()
    fills = list(d.added)
    if not fills:
        return result
    saved = {n: (layout.node_layouts[n], layout.node_kinds[n], layout.node_sections[n]) for n in fills}

    final = _apply(layout, spec, profile, base, fills, len(fills), saved)
    keep = len(fills)
    if not final.ok:
        keep = longest_valid_prefix(
            len(fills), lambda k: _apply(layout, spec, profile, base, fills, k, saved).ok)
        final = _apply(layout, spec, profile, base, fills, keep, saved)
    result.added = fills[:keep]
    result.dropped = fills[keep:]
    result.report = final
    result.notes.append(
        f"densify: added {keep} fill platform(s)"
        + (f", dropped {len(result.dropped)} that broke verification" if result.dropped else "")
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Grid-level entry point (region and section edits)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class GridDensifyResult:
    grid: List[List[int]]  # the input grid plus the kept platforms
    added: List[Rect] = field(default_factory=list)  # kept platforms (row, col0, col1), in order
    dropped: List[Rect] = field(default_factory=list)  # removed to keep the level verified


@dataclass
class _GridReach:
    """What ``_Densifier`` reads from a ``GridReport`` (here from an edit's Spans)."""

    graph: object
    start_span: Optional[str]
    exit_spans: Set[str]
    reachable_nodes: Set[str] = field(default_factory=set)


def _grid_layout(grid: List[List[int]], entities: Sequence[IREntity], tile_size: int,
                 sections: List[SectionBand], player_tiles: Tuple[int, int]) -> SpecLayout:
    """A SpecLayout over a raw grid: every horizontal run of tiles is a node."""
    height, width = len(grid), len(grid[0])
    nodes: Dict[str, NodeLayout] = {}
    kinds: Dict[str, str] = {}
    for r, line in enumerate(grid):
        c = 0
        while c < width:
            v = int(line[c])
            if v == TileValue.EMPTY:
                c += 1
                continue
            c0 = c
            while c < width and int(line[c]) == v:
                c += 1
            nid = f"g{r}_{c0}"
            nodes[nid] = NodeLayout(node_id=nid, col=c0, row=r, width=c - c0, height=1)
            kinds[nid] = "Hazard" if v == TileValue.HAZARD else "Platform"
    ir = IRLevel(identifier="grid", width_tiles=width, height_tiles=height, tile_size=tile_size,
                 grid=[[int(v) for v in line] for line in grid], entities=list(entities))
    return SpecLayout(node_layouts=nodes, ir=ir, sections=sections, node_kinds=kinds,
                      player_tiles=player_tiles)


def densify_grid(
    grid: List[List[int]],
    entities: Sequence[IREntity],
    bounds: Tuple[int, int, int, int],
    profile: GameProfile,
    *,
    sections: List[SectionBand],
    graph,
    start_span: Optional[str],
    exit_spans: Set[str],
    reach_rects: Sequence[Rect],
    protected: Set[Cell],
    verify: Callable[[List[List[int]]], bool],
    seed: int = 0,
    tile_size: int = 16,
) -> GridDensifyResult:
    """Add fill platforms inside *bounds* ``(col0, row0, col1, row1)`` of a raw grid.

    The same ground / ledge / fill algorithm as :func:`densify` (one ``_Densifier``),
    run on a ``SpecLayout`` built from the grid, with additions confined to *bounds*.

    *graph*, *start_span*, *exit_spans* describe the collision-aware span graph of *grid*
    (its arcs are protected); *reach_rects* are the spans the player can reach (nodes
    standing on them are fill sources); *protected* cells stay empty on top of the arcs.
    *verify(grid)* says whether a candidate grid still passes the caller's checks;
    additions that break it are dropped (longest valid prefix), so this never fails.
    """
    layout = _grid_layout(grid, entities, tile_size, sections, profile.player_tiles())
    reach_by_row: Dict[int, List[Tuple[int, int]]] = {}
    for row, c0, c1 in reach_rects:
        reach_by_row.setdefault(row, []).append((c0, c1))
    reachable = {
        nid for nid, nl in layout.node_layouts.items()
        if layout.standable(nid)
        and any(a <= nl.col + nl.width - 1 and nl.col <= b for a, b in reach_by_row.get(nl.row, ()))
    }
    reach = _GridReach(graph, start_span, exit_spans, reachable)
    d = _Densifier(layout, reach, profile, seed, bounds=bounds, reachable=reachable,
                   extra_protected=protected)  # type: ignore[arg-type]
    if d.budget > 0:
        d.ground()
        d.ledges()
        d.fill()
    rects = [layout.rect(n) for n in d.added]

    def stamped(k: int) -> List[List[int]]:
        out = [list(line) for line in grid]
        for row, c0, c1 in rects[:k]:
            out[row][c0 : c1 + 1] = [TileValue.SOLID] * (c1 - c0 + 1)
        return out

    keep = len(rects)
    if rects and not verify(stamped(keep)):
        keep = longest_valid_prefix(len(rects), lambda k: verify(stamped(k)))
    return GridDensifyResult(stamped(keep), rects[:keep], rects[keep:])
