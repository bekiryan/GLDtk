"""Exact wall-jump check on the tile grid (GLDtk v3, Part 11).

A wall-jump move is accepted only if the grid holds a path made of real arcs:

1. **Reach a wall in the air.**  From the launch point the player jumps (or
   drops) along an ordinary arc (|vx| ≤ run_speed) and touches a *wall face*:
   the side of a contiguous column of solid tiles at least the player's height
   tall, with the whole hitbox beside the wall.
2. **Kick.**  At a contact the player kicks off: vertical speed
   ``kick_v = min(wall_jump.jump_v, √(2·g·max_climb_px))`` (so one kick gains at
   most ``max_climb_px``), then is pushed away from the wall at run_speed for
   ``push_px``, then steers freely with |vx| ≤ run_speed.
3. Each kick must lead to the **target** (landing on it) or to **another wall
   face** on the far side (a different wall: a single wall cannot be climbed by
   kicking off it and steering back).  This is a depth-bounded search over wall
   contacts (:data:`MAX_KICKS`), with a budget on the arcs it tries.

Every arc is integrated under the level's :class:`~symbolic.gravity.GravityField`
and swept with the player hitbox on the grid; an arc that touches a tile is
rejected.  The graph-level envelope (``check_wall_jump``, with the weakest
gravity) stays as a quick reject before the search.

Arrival times at a wall are chosen from a few useful candidates (as fast as
possible, at the apex, at the top or middle of the usable contact range);
the contact height that results is then computed exactly.  The search is
therefore sound (every accepted path is a real, collision-free trajectory) but
may miss exotic paths that need other arrival times.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from .gravity import INF, Flight, GravityField, solve_times
from .physics import (
    _CONTACT_EPS,
    _EPS,
    _MAX_SAMPLES,
    LAUNCH_FRACTIONS,
    SAMPLE_STEP_PX,
    WALL_TILE_EXCLUDED,
    check_wall_jump,
    flat_jump_reach,
    max_jump_height,
    trajectory_hits,
)

if TYPE_CHECKING:  # pragma: no cover
    from game.profile import GameProfile

Sample = Tuple[float, float]
Phases = List[Tuple[float, float]]

MAX_KICKS = 6
"""Most kicks in one wall-jump move."""
MAX_ARCS = 400
"""Budget of arcs swept on the grid per move."""
_LAND_SNAP_PX = 0.5


@dataclass(frozen=True)
class WallFace:
    """One side of a wall run, as a place the player can touch.

    ``x`` is the feet x of a player touching it; ``away`` is the kick direction
    (+1 right, −1 left); ``y_lo``..``y_hi`` is the feet-y range in which the
    whole hitbox is beside the wall.
    """

    col: int
    row_top: int
    row_bottom: int
    away: int
    x: float
    y_lo: float
    y_hi: float


def _column_runs(grid: Sequence[Sequence[int]], c: int, min_tiles: int) -> List[Tuple[int, int]]:
    """``(row_top, row_bottom)`` of every wall run in column c (≥ min_tiles tall)."""
    height = len(grid)
    runs: List[Tuple[int, int]] = []
    r = 0
    while r < height:
        if grid[r][c] in WALL_TILE_EXCLUDED:
            r += 1
            continue
        top = r
        while r + 1 < height and grid[r + 1][c] not in WALL_TILE_EXCLUDED:
            r += 1
        if r - top + 1 >= min_tiles:
            runs.append((top, r))
        r += 1
    return runs


class _RunIndex:
    """Wall runs per column of one grid, computed on first use."""

    def __init__(self, grid: Sequence[Sequence[int]]) -> None:
        self.grid = grid
        self.by_min: Dict[int, List[List[Tuple[int, int]]]] = {}

    def runs(self, c: int, min_tiles: int) -> List[Tuple[int, int]]:
        cols = self.by_min.get(min_tiles)
        if cols is None:
            width = len(self.grid[0]) if self.grid else 0
            cols = [_column_runs(self.grid, cc, min_tiles) for cc in range(width)]
            self.by_min[min_tiles] = cols
        return cols[c]


_RUN_INDEX: ContextVar[Optional[_RunIndex]] = ContextVar("gldtk_wall_runs", default=None)


@contextmanager
def cached_wall_runs(grid: Sequence[Sequence[int]]) -> Iterator[None]:
    """Reuse the wall runs of *grid* for every wall-jump check in the block.

    Only for blocks that do not change *grid* (e.g. one graph extraction).
    """
    token = _RUN_INDEX.set(_RunIndex(grid))
    try:
        yield
    finally:
        _RUN_INDEX.reset(token)


def wall_faces(
    grid: Sequence[Sequence[int]],
    profile: "GameProfile",
    tile_size: float,
    x_lo: float,
    x_hi: float,
    y_lo: float,
    y_hi: float,
) -> List[WallFace]:
    """Wall faces whose contact range meets the box ``x_lo..x_hi, y_lo..y_hi``.

    A wall is a vertical run of solid tiles (not hazards) at least the player's
    height in tiles tall.  A face is skipped when the column next to it is
    solid along the whole run (the inside of a thick wall).
    """
    height = len(grid)
    if height == 0:
        return []
    width = len(grid[0])
    ts = float(tile_size)
    min_tiles = profile.player_tiles()[1]
    half = profile.player.width_px / 2.0
    body = profile.player.height_px
    off = half + 2.0 * _CONTACT_EPS
    c0 = max(0, int(math.floor(x_lo / ts)) - 1)
    c1 = min(width - 1, int(math.floor(x_hi / ts)) + 1)
    index = _RUN_INDEX.get()
    if index is not None and index.grid is not grid:
        index = None
    faces: List[WallFace] = []
    for c in range(c0, c1 + 1):
        runs = index.runs(c, min_tiles) if index is not None else _column_runs(grid, c, min_tiles)
        for top, bottom in runs:
            lo = (height - 2 - bottom) * ts
            hi = (height - 1 - top) * ts - body
            if hi < lo or hi < y_lo or lo > y_hi:
                continue
            for side, nc, x in ((-1, c - 1, c * ts - off), (1, c + 1, (c + 1) * ts + off)):
                if not 0 <= nc < width:
                    continue
                if all(grid[rr][nc] != 0 for rr in range(top, bottom + 1)):
                    continue
                faces.append(WallFace(c, top, bottom, side, x, lo, hi))
    return faces


def _kick_speed(profile: "GameProfile", g: float) -> float:
    wj = profile.abilities.wall_jump
    if wj.max_climb_px <= 0.0:
        return 0.0
    return min(wj.jump_v, math.sqrt(2.0 * g * wj.max_climb_px))


def _kick_phases(away: int, push: float, vmax: float, u: float) -> Phases:
    """Pushed away at run_speed for push_px, then vx = u."""
    if push <= 0.0 or vmax <= 0.0:
        return [(INF, u)]
    return [(push / vmax, away * vmax), (INF, u)]


def _arrivals(
    d: float,
    push: float,
    vmax: float,
    vy: float,
    y0: float,
    g: float,
    face: WallFace,
) -> List[Tuple[float, Phases]]:
    """Candidate ``(arrival time, phases)`` that bring x exactly onto a face *d* px away.

    With ``push > 0`` (a kick) the first ``push`` px are the forced push.  The
    candidate times aim at: the earliest arrival, the apex, and the top or
    middle of the face's contact range (using *g* only to pick times — the
    resulting contact height is computed exactly by the caller).
    """
    if vmax <= 0.0 or d == 0.0:
        return []
    s = 1.0 if d > 0 else -1.0
    ad = abs(d)
    t_p = push / vmax if push > 0.0 else 0.0
    if push > 0.0 and ad <= push + 1e-9:
        return [(ad / vmax, [(INF, s * vmax)])]
    t_fast = t_p + (ad - push) / vmax if push > 0.0 else ad / vmax
    wish = [t_fast]
    if g > 0.0:
        if vy > 0.0:
            wish.append(vy / g)
        for yt in (face.y_hi, (face.y_lo + face.y_hi) / 2.0):
            disc = vy * vy - 2.0 * g * (yt - y0)
            if disc >= 0.0:
                r = math.sqrt(disc)
                wish += [(vy - r) / g, (vy + r) / g]
    out: List[Tuple[float, Phases]] = []
    seen: Set[float] = set()
    for t in wish:
        if t < t_fast - 1e-12 or t <= _EPS:
            continue
        key = round(t, 6)
        if key in seen:
            continue
        seen.add(key)
        if push > 0.0:
            u = s * (ad - push) / (t - t_p)
            out.append((t, _kick_phases(int(s), push, vmax, u)))
        else:
            out.append((t, [(INF, s * ad / t)]))
    return out


def _samples(fl: Flight, t: float, end: Sample) -> List[Sample]:
    return fl.samples(t, SAMPLE_STEP_PX, _MAX_SAMPLES, end)


def wall_jump_paths(
    grid: Sequence[Sequence[int]],
    p1: Sample,
    p2: Sample,
    profile: "GameProfile",
    field: GravityField,
    tile_size: float,
) -> Iterator[List[Sample]]:
    """Collision-free wall-jump paths from p1 to p2 (feet polylines).

    Yields nothing when the envelope rejects the move, when no wall is near,
    or when the bounded search finds no sequence of kicks.
    """
    if not grid or not grid[0]:
        return
    wj = profile.abilities.wall_jump
    phys = profile.physics
    vmax = phys.run_speed
    push = wj.push_px
    g_lo = field.g_min
    if not check_wall_jump(
        p1, p2, g_lo, wj.jump_v, wj.max_climb_px, push, phys.jump_v, vmax
    ):
        return
    ts = float(tile_size)
    hitbox = (profile.player.width_px, profile.player.height_px)
    body = profile.player.height_px
    reach = flat_jump_reach(g_lo, phys.jump_v, vmax) + push + ts
    climb = max_jump_height(g_lo, phys.jump_v) + wj.max_climb_px
    faces = wall_faces(
        grid, profile, ts,
        min(p1[0], p2[0]) - reach, max(p1[0], p2[0]) + reach,
        min(p1[1], p2[1]) - body, max(p1[1], p2[1]) + climb + body,
    )
    if not faces:
        return
    budget = [MAX_ARCS]
    no_land = (p2[0], -1e12)  # contact arcs get no landing tolerance

    def clear(samples: List[Sample], target: Sample) -> bool:
        budget[0] -= 1
        return not trajectory_hits(grid, samples, target, ts, hitbox)

    def contacts_from(
        start: Sample, vy: float, away: Optional[int], skip: Optional[WallFace]
    ) -> List[Tuple[WallFace, float, List[Sample]]]:
        """Wall contacts reachable by one arc from *start* (a kick if *away*)."""
        g_here = field.g_at(*start)
        found: List[Tuple[WallFace, float, List[Sample]]] = []
        for face in faces:
            if face is skip or budget[0] <= 0:
                continue
            d = face.x - start[0]
            if d == 0.0:
                continue
            direction = 1 if d > 0 else -1
            if face.away != -direction:
                continue  # must approach the face from the player's side
            if away is not None and direction != away:
                continue  # a kick goes away from the wall it leaves
            for t, phases in _arrivals(d, push if away else 0.0, vmax, vy, start[1], g_here, face):
                fl = Flight(field, start, vy, phases)
                yc = fl.y(t)
                if not face.y_lo - 1e-6 <= yc <= face.y_hi + 1e-6:
                    continue
                samples = _samples(fl, t, (face.x, yc))
                if budget[0] <= 0:
                    break
                if clear(samples, no_land):
                    found.append((face, yc, samples))
        found.sort(key=lambda f: -f[1])  # higher contacts first
        return found

    def land_from(face: WallFace, yc: float, vk: float) -> Optional[List[Sample]]:
        """A kick arc from the contact that lands on p2, or None."""
        start = (face.x, yc)
        dx = p2[0] - face.x
        s = face.away
        t_p = push / vmax if push > 0.0 and vmax > 0.0 else 0.0

        def phases_for(t: float) -> Optional[Phases]:
            if push > 0.0 and t <= t_p:
                if abs(face.x + s * vmax * t - p2[0]) <= _LAND_SNAP_PX:
                    return [(INF, s * vmax)]
                return None
            u = (dx - s * push) / (t - t_p) if push > 0.0 else dx / t
            if abs(u) > vmax + _EPS:
                return None
            return _kick_phases(s, push, vmax, u)

        x_lo = min(face.x, p2[0]) - push
        x_hi = max(face.x, p2[0]) + push
        if field.axis == "x" and field.uniform_g(x_lo, x_hi, 0.0, 0.0) is None:
            g_min = field.g_min
            disc = vk * vk + 2.0 * g_min * (yc - p2[1])
            if disc < 0.0:
                return None
            t_hi = (vk + math.sqrt(disc)) / g_min

            def f(t: float) -> float:
                ph = phases_for(t) or _kick_phases(s, push, vmax, s * vmax)
                return Flight(field, start, vk, ph).y(t) - p2[1]

            times = sorted(solve_times(f, max(t_p, 1e-6), t_hi), reverse=True)
        else:
            times = list(reversed(Flight(field, start, vk, [(INF, 0.0)]).roots_y(p2[1])))
        for t in times:
            if budget[0] <= 0:
                return None
            ph = phases_for(t)
            if ph is None:
                continue
            samples = _samples(Flight(field, start, vk, ph), t, p2)
            if clear(samples, p2):
                return samples
        return None

    visited: Set[Tuple[int, int, int]] = set()

    def search(
        face: WallFace, yc: float, path: List[Sample], kicks: int
    ) -> Iterator[List[Sample]]:
        if budget[0] <= 0:
            return
        key = (face.col, face.away, int(round(yc / 4.0)))
        if key in visited:
            return
        visited.add(key)
        vk = _kick_speed(profile, field.g_at(face.x, yc))
        landing = land_from(face, yc, vk)
        if landing is not None:
            yield path + landing[1:]
            return
        if kicks >= MAX_KICKS:
            return
        for nxt, y2, samples in contacts_from((face.x, yc), vk, face.away, face):
            yield from search(nxt, y2, path + samples[1:], kicks + 1)

    launches = [phys.jump_v * frac for frac in LAUNCH_FRACTIONS] + [0.0]
    for vy in launches:
        for face, yc, samples in contacts_from(p1, vy, None, None):
            yield from search(face, yc, samples, 1)
            if budget[0] <= 0:
                return
