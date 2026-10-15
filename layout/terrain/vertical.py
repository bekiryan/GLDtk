"""Vertical travel shared by the generators: the ends of a section in a vertical
level and the switchback stairwell.

In a vertical level sections are row bands, bottom to top, joined by **links**
(``base.link``): a 2-column hole in the upper band's bottom row (its floor) over
a pedestal in the lower band.  A section starts on its bottom slab beside the
hole it came through and ends on the pedestal under the hole to the next band.

The **switchback** walks between two floor cells of a band: traverses of
stairs (one row per two columns, steeper when it must) running wall to wall,
and at each wall a link up one storey (5 rows).  The number of turns and the
climb of each traverse are planned so the walk ends exactly at the target.
Everything is walkable both ways, so a level climbed upward can also be
descended.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .base import (
    LINK_RISE,
    STOREY,
    Canvas,
    Cell,
    TerrainContext,
    TerrainResult,
    carve_path,
    link,
    stair_line,
)

MAX_TURNS = 12


def bottom_start(ctx: TerrainContext, cv: Canvas) -> Cell:
    """The floor cell a section in a vertical level starts on (carving its hole)."""
    p = ctx.entry
    lo, hi = ctx.inner_cols
    if p.kind == "hole":
        slab = ctx.r1
        for c in (p.col, p.col + 1):
            cv.hole(c, slab)
        cv.carve_rect(p.col - 2, slab - 4, p.col + 3, slab - 1)
        left_room = p.col - lo
        right_room = hi - (p.col + 1)
        return (p.col - 1, slab) if left_room > right_room else (p.col + 2, slab)
    if p.kind == "open":
        cv.carve_rect(p.col - 1, p.row - 4, p.col + 2, ctx.r1)
        return (p.col, min(p.row, ctx.r1 - 1))
    return (min(max(p.col, lo), hi), min(p.row, ctx.inner_rows[1]))


def top_target(ctx: TerrainContext, cv: Canvas,
               cols: Optional[Tuple[int, int]] = None) -> Tuple[Cell, List[Cell]]:
    """The cell the walk must reach near the top, and the pedestal cells after it
    (the level's last cell is kept inside *cols* when given)."""
    p = ctx.exit
    lo, hi = cols or ctx.inner_cols
    if p.kind == "hole":
        pedestal, _ = link(cv, p.row, p.col)
        floor_row = p.row + STOREY
        if p.col - 1 - lo >= hi - (p.col + 2):
            return (p.col - 1, floor_row), pedestal
        return (p.col + 2, floor_row), pedestal[::-1]
    if p.kind == "open":
        cv.carve_rect(p.col - 1, ctx.r0, p.col + 2, p.row - 1)
        return (p.col, p.row), []
    return (min(max(p.col, lo), hi), max(p.row, ctx.inner_rows[0] + 5)), []


def _plan(
    s: Cell, a: Cell, lo: int, hi: int
) -> Optional[Tuple[int, int, List[Tuple[int, int, int]]]]:
    """(turns, first heading, traverses [(c_from, c_to, rise)]) or None."""
    rise = s[1] - a[1]
    best = None
    for t in range(0, MAX_TURNS + 1):
        q = rise - STOREY * t
        if q < 0:
            break
        for h0 in (1, -1):
            if t == 0:
                if (a[0] - s[0]) * h0 < 0 or (a[0] == s[0] and h0 == -1):
                    continue
                spans = [(s[0], a[0], abs(a[0] - s[0]))]
                caps = [abs(a[0] - s[0])]
            else:
                spans = []
                caps = []
                start = s[0]
                h = h0
                ok = True
                for k in range(t):
                    w = hi if h == 1 else lo
                    length = (w - start) * h
                    if length < 3:
                        ok = False
                        break
                    spans.append((start, w, length))
                    caps.append(length - 3)
                    start = w - 2 * h
                    h = -h
                last = (a[0] - start) * h
                if not ok or last < 0:
                    continue
                spans.append((start, a[0], last))
                caps.append(last)
            if q > sum(caps):
                continue
            score = (t, -sum(caps))
            if best is None or score < best[0]:
                best = (score, t, h0, spans, caps, q)
        if best is not None and best[1] == t:
            break
    if best is None:
        return None
    _score, t, h0, spans, caps, q = best
    total = sum(caps) or 1
    rises = [int(q * c / total) for c in caps]
    left = q - sum(rises)
    for i in sorted(range(len(caps)), key=lambda i: -(caps[i] - rises[i])):
        if left <= 0:
            break
        add = min(left, caps[i] - rises[i])
        rises[i] += add
        left -= add
    return t, h0, [(c0, c1, r) for (c0, c1, _l), r in zip(spans, rises)]


def switchback(ctx: TerrainContext, cv: Canvas, s: Cell, a: Cell, air: int = 4,
               cols: Optional[Tuple[int, int]] = None) -> Tuple[List[Cell], List[Cell]]:
    """Walk from *s* (lower) to *a* (upper), turning inside *cols* (default: the
    band).  Returns (path cells, turn landings)."""
    if a[1] > s[1]:
        s, a = a, s
    lo, hi = cols or ctx.inner_cols
    plan = _plan(s, a, lo, hi)
    if plan is None:  # not enough room: straight stairs, verification will judge
        cells = stair_line(s, a, ctx.rng)
        carve_path(cv, cells, air)
        return cells, []
    t, h0, traverses = plan
    path: List[Cell] = []
    landings: List[Cell] = []
    row = s[1]
    for k, (c_from, c_to, climb) in enumerate(traverses):
        turn = k < len(traverses) - 1
        n = abs(c_to - c_from)
        if turn:
            # climb on the first n-3 columns, the last 3 flat (2 hold the pedestal)
            mid = c_to - 3 * (1 if c_to > c_from else -1)
            seg = stair_line((c_from, row), (mid, row - climb), ctx.rng) if n > 3 else [(c_from, row)]
            end_row = seg[-1][1]
            d = 1 if c_to > c_from else -1
            seg += [(mid + d * i, end_row) for i in range(1, 4)]
        else:
            seg = stair_line((c_from, row), (c_to, row - climb), ctx.rng)
        carve_path(cv, seg, air)
        path += seg if not path else seg[1:] if path[-1] == seg[0] else seg
        row = seg[-1][1]
        if turn:
            w = c_to
            d = 1 if c_to > c_from else -1
            hole_col = min(w, w - d)
            ped, _ = link(cv, row - STOREY, hole_col)
            path += ped if d == 1 else ped[::-1]
            row -= STOREY
            landings.append((w - 2 * d, row))
    return path, landings


def vertical_walk(ctx: TerrainContext, cv: Canvas, air: int = 4, alcoves: bool = False) -> TerrainResult:
    """A section of a vertical level made of one switchback stairwell."""
    res = TerrainResult()
    s = bottom_start(ctx, cv)
    a, pedestal = top_target(ctx, cv)
    path, landings = switchback(ctx, cv, s, a, air)
    path += pedestal
    res.path = path
    res.add("floor", path)
    res.add("room", landings)
    res.add("entry", path[:3])
    res.add("exit", path[-3:][::-1])
    return res


def climb_to(ctx: TerrainContext, cv: Canvas, s: Cell, res: TerrainResult, air: int = 4,
             cols: Optional[Tuple[int, int]] = None) -> None:
    """Finish a vertical section: walk from *s* to the section's top end."""
    a, pedestal = top_target(ctx, cv, cols)
    path, landings = switchback(ctx, cv, s, a, air, cols)
    res.path += path + pedestal
    res.add("floor", path + pedestal)
    res.add("room", landings)


__all__ = ["bottom_start", "top_target", "switchback", "vertical_walk", "climb_to", "LINK_RISE"]
