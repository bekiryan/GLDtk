"""Parkour — a gap run over spikes (or, with ``vertical``, a narrow-ledged shaft).

A tall chamber whose bottom is a bed of spikes; rock pillars rise out of it,
2–3 columns wide, their tops (``ledge`` anchors) 3–5 columns apart and within
3 rows of each other, from the entry corridor to the exit corridor.  The
chamber keeps ``JUMP_AIR`` empty rows above the highest pillar.  Moves here may
be longer than the comfort limits (Part 20's ``parkour`` style).
"""

from __future__ import annotations

from typing import List

from .base import JUMP_AIR, Canvas, Cell, TerrainContext, TerrainResult, carve_path, stair_line
from .shaft import horizontal_shaft, vertical_shaft

CORRIDOR_AIR = 4


def generate(ctx: TerrainContext, cv: Canvas) -> TerrainResult:
    if ctx.orientation == "vertical":
        return vertical_shaft(ctx, cv, ledge=3)
    if ctx.param("vertical", None) in ("up", "down"):
        return horizontal_shaft(ctx, cv, ledge=3)
    rng = ctx.rng
    res = TerrainResult()
    complexity = float(ctx.param("complexity", 0.5))
    lo, hi = ctx.inner_cols
    top_row, bottom_row = ctx.inner_rows
    fe, fx = ctx.entry.row, ctx.exit.row
    x0 = lo + min(4, max(2, (hi - lo) // 8)) if ctx.entry.kind != "end" else lo + 3
    x1 = hi - min(4, max(2, (hi - lo) // 8)) if ctx.exit.kind != "end" else hi - 3
    if x1 - x0 < 8:
        x0, x1 = lo + 1, hi - 1
    pit = min(bottom_row, max(fe, fx) + 3)  # spike row
    high_limit = top_row + JUMP_AIR + 1
    enter = stair_line((ctx.c0, fe), (x0, fe), rng)
    leave = stair_line((x1, fx), (ctx.c1, fx), rng)
    carve_path(cv, enter, CORRIDOR_AIR)
    carve_path(cv, leave, CORRIDOR_AIR)

    # pillars from x0+gap to x1-gap
    tops: List[Cell] = []
    x = x0 + 1
    row = fe
    max_gap = 4 + round(complexity)
    while True:
        gap = rng.randint(2, max_gap)
        w = rng.randint(2, 3)
        start = x + gap
        if start + w - 1 > x1 - 3:
            break
        t = (start - x0) / max(1, x1 - x0)
        aim = round(fe + (fx - fe) * t)
        row = min(max(aim + rng.randint(-2, 2), row - 3, high_limit), row + 3, pit - 2)
        tops += [(c, row) for c in range(start, start + w)]
        x = start + w - 1
    # make the last hop to the exit corridor short enough
    if tops and (abs(tops[-1][1] - fx) > 3 or x1 - tops[-1][0] > max_gap + 1):
        mid = (tops[-1][0] + x1) // 2
        r = (tops[-1][1] + fx) // 2
        if mid - 1 > tops[-1][0] + 2:
            tops += [(mid - 1, r), (mid, r)]
    if not tops:  # band too narrow: a plain corridor
        path = stair_line((ctx.c0, fe), (ctx.c1, fx), rng)
        carve_path(cv, path, CORRIDOR_AIR)
        res.path = path
        res.add("floor", path)
        return res
    highest = min([r for _c, r in tops] + [fe, fx])
    cv.carve_rect(x0 + 1, highest - JUMP_AIR, x1 - 1, pit)
    cv.carve_rect(x0 - 2, highest - JUMP_AIR, x0, fe - 1)
    cv.carve_rect(x1, highest - JUMP_AIR, x1 + 2, fx - 1)
    for c, r in tops:
        for rr in range(r, pit + 1):
            cv.floor(c, rr)
    top_cols = {c for c, _r in tops}
    run = []
    for c in range(x0 + 1, x1):
        if c in top_cols:
            if run:
                res.hazards.append((pit, run[0], run[-1]))
                run = []
            continue
        cv.spike(c, pit)
        run.append(c)
    if run:
        res.hazards.append((pit, run[0], run[-1]))
    res.path = enter + tops + leave
    res.add("floor", enter + leave)
    res.add("ledge", tops)
    res.add("entry", enter[:3])
    res.add("exit", leave[-3:][::-1])
    return res
