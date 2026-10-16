"""Shaft — vertical travel, up or down.

A chamber ``ZIGZAG_WIDTH`` columns wide with 4-column ledges on alternating
walls, 3 rows apart (``base.zigzag``).  In a horizontal level the entry
corridor reaches the chamber floor (climbing up) or its top ledge (dropping
down) and the exit corridor leaves from the other end; in a vertical level the
chamber climbs from the band's bottom and a switchback finishes at the top.
Ledges are ``ledge`` anchors.
"""

from __future__ import annotations

from typing import List

from .base import (
    STOREY,
    ZIGZAG_LEDGE,
    ZIGZAG_WIDTH,
    Canvas,
    TerrainContext,
    TerrainResult,
    carve_path,
    stair_line,
    zigzag,
)
from .vertical import bottom_start, climb_to

CORRIDOR_AIR = 4


def chamber_x(ctx: TerrainContext, width: int) -> int:
    lo, hi = ctx.inner_cols
    span = hi - lo + 1
    if span <= width + 6:
        return max(lo, (lo + hi - width + 1) // 2)
    return ctx.rng.randint(lo + 3, hi - width - 2)


def horizontal_shaft(ctx: TerrainContext, cv: Canvas, ledge: int = ZIGZAG_LEDGE,
                     width: int = ZIGZAG_WIDTH) -> TerrainResult:
    res = TerrainResult()
    fe, fx = ctx.entry.row, ctx.exit.row
    x0 = chamber_x(ctx, width)
    x1 = x0 + width - 1
    up = fx <= fe
    low, high = (fe, fx) if up else (fx, fe)
    if low - high < 5:  # not much of a climb: the chamber floor is the low corridor
        high = min(high, low - 6)
        high = max(high, ctx.inner_rows[0] + ctx.jump_air + 1)
    high_side = "right" if up else "left"
    ledges = zigzag(cv, x0, low, high, high_side, ledge=ledge, width=width, air=ctx.jump_air,
                    clear_side="left" if up else "right")  # the floor-level corridor's wall
    if up:  # room to jump from the entry corridor onto a ledge by the left wall
        cv.carve_rect(x0 - 6, low - ctx.jump_air, x0 - 1, low - 1)
    if up:
        enter = stair_line((ctx.c0, fe), (x0, low), ctx.rng)
        leave = stair_line((x1, high), (ctx.c1, fx), ctx.rng)
        inside = [(c, low) for c in range(x0, x0 + 3)] + ledges
    else:
        enter = stair_line((ctx.c0, fe), (x0, high), ctx.rng)
        leave = stair_line((x1, low), (ctx.c1, fx), ctx.rng)
        inside = ledges[::-1] + [(c, low) for c in range(x1 - 2, x1 + 1)]
    carve_path(cv, enter, CORRIDOR_AIR)
    carve_path(cv, leave, CORRIDOR_AIR)
    res.path = enter + inside + leave
    res.add("floor", enter + [(c, low) for c in range(x0, x1 + 1)] + leave)
    res.add("ledge", ledges)
    res.add("entry", enter[:3])
    res.add("exit", leave[-3:][::-1])
    res.add("room", [((x0 + x1) // 2, low)])
    return res


def vertical_shaft(ctx: TerrainContext, cv: Canvas, ledge: int = ZIGZAG_LEDGE,
                   width: int = ZIGZAG_WIDTH) -> TerrainResult:
    """Bottom → chamber floor, zigzag up, then a switchback to the band's top end."""
    res = TerrainResult()
    s = bottom_start(ctx, cv)
    lo, hi = ctx.inner_cols
    # the walk to the top runs at least ctx.jump_air + 1 rows over the top ledge
    top_room = ctx.r0 + STOREY - 1 + ctx.jump_air + 1
    high = s[1] - 9
    while high - 3 >= top_room:
        high -= 3
    if high < top_room or s[1] - high < 6 or hi - lo + 1 < width + 4:
        from .vertical import vertical_walk

        return vertical_walk(ctx, cv)
    # the chamber at one end of the band, clear of the hole the player came up
    # through; a narrow stairwell (8+ columns) beside it climbs on to the top
    zone = (s[0] - 4, s[0] + 4)
    options = []
    if hi - width + 1 > zone[1]:  # chamber at the right end, stairwell on its left
        options.append((hi - width + 1, "left", (lo, hi - width - 1)))
    if lo + width - 1 < zone[0]:  # chamber at the left end, stairwell on its right
        options.append((lo, "right", (lo + width + 1, hi)))
    options = [o for o in options if o[2][1] - o[2][0] >= 7]
    if not options:
        from .vertical import vertical_walk

        return vertical_walk(ctx, cv)
    x0, side, well = max(options, key=lambda o: o[2][1] - o[2][0])
    ledges = zigzag(cv, x0, s[1], high, side, ledge=ledge, width=width, air=ctx.jump_air)
    target = min(max(s[0], x0), x0 + width - 1)
    enter = stair_line(s, (target, s[1]), ctx.rng)
    # the walk to the chamber is part of it: room to jump from anywhere on it
    cv.carve_rect(min(s[0], target), s[1] - ctx.jump_air, max(s[0], target), s[1] - 1)
    carve_path(cv, enter, CORRIDOR_AIR)
    top = ledges[-1]
    edge = well[0] + 1 if side == "right" else well[1] - 1
    out = stair_line(top, (edge, top[1]), ctx.rng)
    carve_path(cv, out, CORRIDOR_AIR)
    res.stats["chamber"] = (x0, x0 + width - 1, s[1], side)
    res.path = enter + ledges + out
    res.add("floor", enter + out)
    res.add("ledge", ledges)
    res.add("entry", enter[:3])
    climb_to(ctx, cv, out[-1], res, cols=well)
    res.add("exit", res.path[-3:][::-1])
    return res


def generate(ctx: TerrainContext, cv: Canvas) -> TerrainResult:
    if ctx.orientation == "vertical":
        return vertical_shaft(ctx, cv)
    return horizontal_shaft(ctx, cv)


__all__: List[str] = ["generate", "horizontal_shaft", "vertical_shaft", "chamber_x"]
