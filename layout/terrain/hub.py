"""Hub — connected rooms with side rooms (a key, a secret).

Horizontal levels: a chain of 2–4 rooms of different heights (4–6 empty rows)
along a floor with stairs, separated by walls hanging from the ceiling with a
3-row door under them.  ``branches`` (default 1) side rooms sit above a room
(or below it when there is no room above), joined by a link (a hole in the
slab over a pedestal): their floors are the first ``room`` anchors, where keys
and secrets go.  Vertical levels: the switchback stairwell, with a side room
off one of its landings.
"""

from __future__ import annotations

from typing import List

from .base import STOREY, Canvas, Cell, TerrainContext, TerrainResult, carve_path, link
from .corridor import floor_line
from .vertical import vertical_walk


def generate(ctx: TerrainContext, cv: Canvas) -> TerrainResult:
    if ctx.orientation == "vertical":
        return vertical_walk(ctx, cv, air=4)
    rng = ctx.rng
    res = TerrainResult()
    complexity = float(ctx.param("complexity", 0.5))
    branches = int(ctx.param("branches", 1))
    lo, hi = ctx.inner_cols
    top_row, bottom_row = ctx.inner_rows
    path = floor_line(ctx, (ctx.c0, ctx.entry.row), (ctx.c1, ctx.exit.row), round(complexity * 2))
    n_rooms = min(4, max(2, (hi - lo) // 12))
    bounds = [lo + round(k * (hi - lo) / n_rooms) for k in range(n_rooms + 1)]
    heights = [rng.randint(4, 6) for _ in range(n_rooms)]

    def room_of(c: int) -> int:
        for k in range(n_rooms):
            if c <= bounds[k + 1]:
                return k
        return n_rooms - 1

    for c, r in path:
        air = heights[room_of(c)]
        for k in range(1, air + 1):
            cv.carve(c, r - k)
        cv.floor(c, r)
    # walls between rooms, hanging down to a 3-row door
    by_col = {c: r for c, r in path}
    for b in bounds[1:-1]:
        r = by_col.get(b)
        if r is None:
            continue
        for rr in range(r - 7, r - 3):
            cv.floor(b, rr)
    centres: List[Cell] = []
    for k in range(n_rooms):
        mid = (bounds[k] + bounds[k + 1]) // 2
        if mid in by_col:
            centres.append((mid, by_col[mid]))

    # side rooms joined by links: above (slab = 5 rows over the floor) or below
    side_rooms: List[Cell] = []
    flat = [i for i in range(3, len(path) - 8)
            if all(path[i + d][1] == path[i][1] for d in range(-3, 8))]
    rng.shuffle(flat)
    used: List[int] = []
    for i in flat:
        if len(side_rooms) >= branches:
            break
        x, f = path[i]
        # a side room (columns x - 3 .. x + 8) never takes in a wall between rooms
        if any(abs(x - u) < 12 for u in used) or any(x - 4 <= b <= x + 9 for b in bounds[1:-1]):
            continue
        if f - STOREY - 5 >= top_row:  # room above: the floor's ceiling becomes its slab
            slab = f - STOREY
            for c in range(x - 3, x + 6):
                for rr in range(slab + 1, f):
                    cv.carve(c, rr)
                cv.floor(c, slab)
            cv.carve_rect(x - 3, slab - 4, x + 8, slab - 1)
            for c in range(x - 3, x + 9):
                cv.floor(c, slab)
            link(cv, slab, x)
            side_rooms.append((x + 6, slab))
            used.append(x)
        elif f + STOREY <= bottom_row:  # cellar: a hole in the floor
            link(cv, f, x, landing=False)
            cv.carve_rect(x - 3, f + 1, x + 6, f + STOREY - 1)
            for c in range(x - 3, x + 7):
                cv.floor(c, f + STOREY)
            side_rooms.append((x - 2, f + STOREY))
            used.append(x)
    res.path = path
    res.add("floor", path)
    res.add("room", side_rooms + centres)
    res.add("entry", path[:3])
    res.add("exit", path[-3:][::-1])
    res.stats["side_rooms"] = len(side_rooms)
    return res


__all__ = ["generate", "carve_path"]
