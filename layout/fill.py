"""Noise terrain fill — natural rock in the large empty areas of a verified level (v4 Part 25a).

``noise_fill`` turns EMPTY cells far from the playable space into rock
(``TileValue.SOLID``):

1. **candidates**: empty cells inside the region whose Chebyshev distance to
   every protected cell is more than ``min_distance_from_path`` tiles;
2. **noise**: fBm (``layout/noise.py``) sampled at *world* coordinates
   (``params.origin`` + cell), plus a per-style bias by depth: ``cave`` gets
   strata near the bottom, a ceiling and stalactites near the top and hanging
   rock in between; ``ruins`` samples the noise on 4×3 blocks (masonry) with a
   floor; ``sky`` is very sparse floating islands.  A cell becomes rock when
   noise + bias passes a threshold that falls as ``density`` rises; more
   distance from the path helps a little, so the fill thickens away from it;
3. **smoothing**: cellular automata, 4-5 rule (fewer rounds as ``roughness``
   rises; none for ruins); protected and non-candidate cells are pinned;
4. **blobs**: the 4-connected components of the new rock; specks smaller than
   ``min_blob`` cells are removed;
5. **verification** (*verify*, optional): if the grid with every blob fails,
   blobs are dropped one at a time (each first failing blob, found by binary
   search over the ordered blobs) until the rest verifies — the fill never
   makes a level fail.

**Protected cells** (never filled, then widened by the margin): built by
:func:`level_protected` for a whole level — the arcs of a spanning tree of
the reachable spans and of the golden route (``layout/densify.py``), the
headroom of every span, every entity cell and its headroom, plus the caller's
extra cells (seams).  The comfort and densify platforms are tiles already;
their arcs and headroom are among the above.

Entry points: :func:`fill_layout` (``pipeline/level.py``, after densify) and
:func:`fill_grid` (region and section edits, inside the rect only).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from game.profile import GameProfile
from spec.level_spec import LevelSpec

from .ir import IREntity, TileValue
from .noise import fbm, hash01, value_noise
from .spec_layout import Rect, SectionBand, SpecLayout
from .sugiyama import NodeLayout
from .verify import GridReport, verify_grid

Cell = Tuple[int, int]  # (col, row)
Grid = List[List[int]]
STYLES = ("cave", "ruins", "sky")
DIST_CAP = 32


@dataclass
class FillParams:
    density: float = 0.5  # 0..1
    roughness: float = 0.5  # 0 = smooth masses, 1 = ragged
    min_distance_from_path: int = 2  # tiles kept empty around every protected cell
    style: str = "cave"  # cave | ruins | sky
    origin: Tuple[int, int] = (0, 0)  # world offset of the grid's (0, 0) cell (chunks)
    warp: float = 0.0  # domain-warp strength in tiles (0 = off)
    min_blob: int = 6  # smaller rock specks are removed
    keep_clear: Tuple[Cell, ...] = ()  # extra protected cells (chunk seam openings)

    @classmethod
    def of(cls, value: Union["FillParams", float, None]) -> "FillParams":
        if isinstance(value, FillParams):
            return value
        return cls(density=float(value if value is not None else 0.5))


@dataclass
class FillResult:
    grid: Grid  # the input grid plus the kept rock
    added: List[Cell] = field(default_factory=list)  # rock cells kept
    blobs: List[List[Cell]] = field(default_factory=list)  # kept blobs
    dropped: int = 0  # blobs removed to keep the level verified
    candidates: int = 0  # cells the fill was allowed to use


# ─────────────────────────────────────────────────────────────────────────────
# Core
# ─────────────────────────────────────────────────────────────────────────────


def _distance(height: int, width: int, sources: Iterable[Cell]) -> List[List[int]]:
    """Chebyshev distance of every cell to the nearest source (BFS), capped."""
    dist = [[DIST_CAP] * width for _ in range(height)]
    q: deque = deque()
    for c, r in sources:
        if 0 <= r < height and 0 <= c < width and dist[r][c]:
            dist[r][c] = 0
            q.append((c, r))
    while q:
        c, r = q.popleft()
        d = dist[r][c] + 1
        if d >= DIST_CAP:
            continue
        for dr in (-1, 0, 1):
            rr = r + dr
            if not 0 <= rr < height:
                continue
            line = dist[rr]
            for dc in (-1, 0, 1):
                cc = c + dc
                if 0 <= cc < width and line[cc] > d:
                    line[cc] = d
                    q.append((cc, rr))
    return dist


def _score(seed: int, p: FillParams, wx: int, wy: int, depth: float, height: int) -> float:
    """Noise + style bias of world cell ``(wx, wy)`` (``depth`` 0 = top row, 1 = bottom)."""
    octaves = 2 + round(3 * p.roughness)
    gain = 0.35 + 0.3 * p.roughness
    x, y = float(wx), float(wy)
    if p.warp > 0:
        from .noise import warp
        x, y = warp(seed, x, y, p.warp)
    if p.style == "ruins":
        bx, by = math.floor(x / 4), math.floor(y / 3)
        n = 0.65 * value_noise(seed, bx * 0.5, by * 0.5) + 0.35 * hash01(seed, bx, by)
        return n + 0.35 * max(0.0, (depth - 0.75) / 0.25)
    if p.style == "sky":
        # wide, flat islands: squash the noise vertically
        return fbm(seed, x, y * 2.2, octaves=octaves, frequency=1 / 10, gain=gain) - 0.12
    n = fbm(seed, x, y, octaves=octaves, frequency=1 / 12, gain=gain)
    bias = 0.35 * max(0.0, (depth - 0.7) / 0.3)  # strata near the bottom
    bias += 0.04 * math.sin(y * 1.7 + 3 * value_noise(seed + 5, x / 9, 0.0))  # layering
    bias += 0.3 * max(0.0, (0.18 - depth) / 0.18)  # the ceiling
    stalactite = hash01(seed, wx, 9973)  # per column
    if stalactite > 0.55:
        length = (stalactite - 0.55) / 0.45 * 0.3  # up to 30% of the height
        if depth < length:
            bias += 0.25 * (1 - depth / max(length, 1e-6))
    return n + bias


def _threshold(p: FillParams) -> float:
    d = min(1.0, max(0.0, p.density))
    if p.style == "sky":
        return 0.78 - 0.18 * d
    return 0.8 - 0.3 * d


def _components(cells: Set[Cell]) -> List[List[Cell]]:
    out: List[List[Cell]] = []
    left = set(cells)
    for start in sorted(cells, key=lambda c: (c[1], c[0])):
        if start not in left:
            continue
        left.discard(start)
        comp = [start]
        q = deque([start])
        while q:
            c, r = q.popleft()
            for nb in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
                if nb in left:
                    left.discard(nb)
                    comp.append(nb)
                    q.append(nb)
        out.append(sorted(comp, key=lambda c: (c[1], c[0])))
    return out


def _stamp(grid: Grid, blobs: Sequence[Sequence[Cell]]) -> Grid:
    out = [list(line) for line in grid]
    for blob in blobs:
        for c, r in blob:
            out[r][c] = TileValue.SOLID
    return out


def noise_fill(
    grid: Grid,
    protected: Iterable[Cell],
    rng_seed: int,
    params: Union[FillParams, float, None] = None,
    region: Optional[Tuple[int, int, int, int]] = None,
    verify: Optional[Callable[[Grid], bool]] = None,
) -> FillResult:
    """Fill the large empty areas of *grid* with rock (see the module doc).

    *region* ``(col0, row0, col1, row1)`` (inclusive) confines the fill; *verify(grid)*
    says whether a candidate grid still passes the caller's checks (blobs that break it
    are dropped).  The input grid is not modified.
    """
    p = FillParams.of(params)
    height = len(grid)
    width = len(grid[0]) if height else 0
    if not height or p.density <= 0:
        return FillResult([list(line) for line in grid])
    c0, r0, c1, r1 = region or (0, 0, width - 1, height - 1)
    c0, r0, c1, r1 = max(0, c0), max(0, r0), min(width - 1, c1), min(height - 1, r1)
    dist = _distance(height, width, list(protected) + list(p.keep_clear))
    margin = max(0, int(p.min_distance_from_path))
    ox, oy = p.origin
    seed = int(rng_seed) & 0x7FFFFFFF
    thr = _threshold(p)

    cand: Set[Cell] = set()
    state = [[grid[r][c] != TileValue.EMPTY for c in range(width)] for r in range(height)]
    for r in range(r0, r1 + 1):
        depth = r / max(1, height - 1)
        for c in range(c0, c1 + 1):
            if grid[r][c] != TileValue.EMPTY or dist[r][c] <= margin:
                continue
            cand.add((c, r))
            far = min(1.0, (dist[r][c] - margin) / 6.0)
            state[r][c] = _score(seed, p, ox + c, oy + r, depth, height) + 0.08 * far > thr

    if cand and p.style != "ruins":
        from .noise import smooth_ca
        rounds = max(1, round(3 - 2 * p.roughness))
        state = smooth_ca(state, rounds, mutable=lambda c, r: (c, r) in cand,
                          outside=p.style == "cave")

    rock = {(c, r) for c, r in cand if state[r][c]}
    blobs = [b for b in _components(rock) if len(b) >= max(1, p.min_blob)]
    # far blobs first: they are the least likely to break anything
    blobs.sort(key=lambda b: (-max(dist[r][c] for c, r in b), b[0][1], b[0][0]))

    dropped = 0
    if verify is not None and blobs and not verify(_stamp(grid, blobs)):
        kept: List[List[Cell]] = []
        rest = list(blobs)
        while rest:
            if verify(_stamp(grid, kept + rest)):
                kept += rest
                break
            # first failing blob: the longest prefix of *rest* that verifies, plus one
            lo, hi = 0, len(rest)  # kept + rest[:lo] verifies, kept + rest[:hi] does not
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if verify(_stamp(grid, kept + rest[:mid])):
                    lo = mid
                else:
                    hi = mid
            kept += rest[:lo]
            dropped += 1
            rest = rest[hi:]
        blobs = kept
    out = _stamp(grid, blobs)
    return FillResult(out, [cell for b in blobs for cell in b], blobs, dropped, len(cand))


# ─────────────────────────────────────────────────────────────────────────────
# Protected cells
# ─────────────────────────────────────────────────────────────────────────────


def span_headroom(graph, player_h: int) -> Set[Cell]:
    """The ``player_h + 1`` rows above every span of an extracted graph, one column wider
    on each side."""
    from .route import span_rect

    cells: Set[Cell] = set()
    if graph is None:
        return cells
    for sid in graph.nx_graph.nodes:
        try:
            row, a, b = span_rect(graph, sid)
        except (KeyError, TypeError):
            continue
        for r in range(row - player_h - 1, row):
            for c in range(a - 1, b + 2):
                cells.add((c, r))
    return cells


def entity_headroom(entities: Iterable[IREntity], player_h: int) -> Set[Cell]:
    return {(e.col, e.row - d) for e in entities for d in range(0, player_h + 1)}


def level_protected(layout: SpecLayout, report: Union[GridReport, object], profile: GameProfile
                    ) -> Set[Cell]:
    """The protected cells of a verified level (see the module doc)."""
    from .densify import protected_cells, route_cells

    ph = layout.player_tiles[1]
    cells = protected_cells(layout, report, profile)  # type: ignore[arg-type]
    cells |= route_cells(layout, report, profile)  # type: ignore[arg-type]
    cells |= span_headroom(getattr(report, "graph", None), ph)
    cells |= entity_headroom(layout.ir.entities, ph)
    return cells


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline and edit entry points
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class LayoutFillResult:
    added: int = 0  # rock cells kept
    blobs: int = 0
    dropped: int = 0
    report: Optional[GridReport] = None
    notes: List[str] = field(default_factory=list)


def fill_layout(
    layout: SpecLayout,
    report: GridReport,
    profile: Optional[GameProfile],
    spec: LevelSpec,
    params: Union[FillParams, float, None],
    seed: int = 0,
    extra_protected: Iterable[Cell] = (),
) -> LayoutFillResult:
    """Noise-fill a *verified* layout's grid in place (``layout.ir.grid``); the result's
    report is always ``ok`` when *report* was.  Rock is grid-only (no spec nodes)."""
    profile = profile or GameProfile()
    result = LayoutFillResult(report=report)
    if not report.ok:
        return result
    protected = level_protected(layout, report, profile) | set(extra_protected)
    original = layout.ir.grid
    last: Dict[str, GridReport] = {}

    def verify(g: Grid) -> bool:
        layout.ir.grid = g
        rep = verify_grid(layout, profile, spec)
        if rep.ok:
            last["ok"] = rep
        return rep.ok

    res = noise_fill(original, protected, seed, params, verify=verify)
    layout.ir.grid = res.grid
    if res.blobs:
        result.report = verify_grid(layout, profile, spec)
        if not result.report.ok:  # cannot happen (verify passed on this grid); be safe
            layout.ir.grid = original
            result.report = report
            return result
    result.added, result.blobs, result.dropped = len(res.added), len(res.blobs), res.dropped
    register_rock(layout, res.added)
    result.notes.append(_fill_note(layout, res))
    return result


def _fill_note(layout: SpecLayout, res: FillResult) -> str:
    """The pipeline note for a fill; says why when nothing was added."""
    if res.added or res.dropped:
        return (f"fill: added {len(res.added)} rock tile(s) in {len(res.blobs)} mass(es)"
                + (f", dropped {res.dropped} that broke verification" if res.dropped else ""))
    if getattr(layout, "terrain", None) is not None:
        return "fill: nothing to fill — the level is already carved from rock"
    if res.candidates == 0:
        return "fill: nothing to fill — no empty area far enough from the path"
    return "fill: nothing to fill — no rock mass at this density"


ROCK_PREFIX = "rock_"


def register_rock(layout: SpecLayout, cells: Iterable[Cell]) -> List[str]:
    """Record rock cells as layout nodes (``rock_N``, one per horizontal run, kind
    ``Rock``) so a later ``rasterise`` keeps them.  They are not spec nodes
    (``added_nodes`` is untouched), and the grid is already stamped."""
    rows: Dict[int, List[int]] = {}
    for c, r in cells:
        rows.setdefault(r, []).append(c)
    n = sum(1 for nid in layout.node_layouts if nid.startswith(ROCK_PREFIX))
    ids: List[str] = []
    for r in sorted(rows):
        cols = sorted(rows[r])
        start = prev = cols[0]
        for c in cols[1:] + [None]:  # type: ignore[list-item]
            if c is not None and c == prev + 1:
                prev = c
                continue
            n += 1
            nid = f"{ROCK_PREFIX}{n}"
            layout.node_layouts[nid] = NodeLayout(node_id=nid, col=start, row=r,
                                                  width=prev - start + 1, height=1)
            layout.node_kinds[nid] = "Rock"
            layout.node_sections[nid] = layout.section_at(start, r).id
            ids.append(nid)
            if c is not None:
                start = prev = c
    return ids


def fill_grid(
    grid: Grid,
    entities: Sequence[IREntity],
    bounds: Tuple[int, int, int, int],
    profile: GameProfile,
    *,
    sections: List[SectionBand],
    graph,
    start_span: Optional[str],
    exit_spans: Set[str],
    protected: Set[Cell],
    verify: Callable[[Grid], bool],
    params: Union[FillParams, float, None],
    seed: int = 0,
    tile_size: int = 16,
) -> FillResult:
    """Noise-fill *bounds* ``(col0, row0, col1, row1)`` of a raw grid (region edits).

    Protected on top of *protected*: the arcs of a spanning tree of *graph* from
    *start_span* and of its golden route, the headroom of every span and of every entity.
    """
    from .densify import _grid_layout, _GridReach

    layout = _grid_layout(grid, entities, tile_size, sections, profile.player_tiles())
    reach = _GridReach(graph, start_span, set(exit_spans))
    cells = level_protected(layout, reach, profile) | set(protected)
    return noise_fill(grid, cells, seed, params, region=bounds, verify=verify)


__all__ = [
    "FillParams",
    "FillResult",
    "LayoutFillResult",
    "STYLES",
    "fill_grid",
    "fill_layout",
    "level_protected",
    "noise_fill",
    "register_rock",
]
