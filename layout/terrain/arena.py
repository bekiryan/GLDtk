"""Arena — a closed room for a fight or a boss.

A wide room with a flat floor and ``ctx.jump_air`` + 2 empty rows above it,
closed by rock except the entry and exit doors.  A couple of floating
platforms (``ledge`` anchors) hang 3 rows over the floor, low stub pillars
(one tile, walkable as a step) stand on it, spike pits are cut into it when the
section asks for hazards, and ``arena_center`` is the middle of the floor.  When
the exit door is higher than the floor a column of ledges climbs to it; when it
is lower, stairs lead down to it.  In a vertical level the room sits at the
bottom of the band and a switchback climbs from it to the top.
"""

from __future__ import annotations

from typing import List

from .base import Canvas, Cell, TerrainContext, TerrainResult, carve_path, stair_line, zigzag
from .shaft import vertical_shaft

DOOR = 4


def _room(ctx: TerrainContext, cv: Canvas, res: TerrainResult, x0: int, x1: int, floor: int) -> List[Cell]:
    rng = ctx.rng
    top = max(ctx.inner_rows[0], floor - ctx.jump_air - 2)
    cv.carve_rect(x0, top, x1, floor - 1)
    cells = [(c, floor) for c in range(x0, x1 + 1)]
    for c, r in cells:
        cv.floor(c, r)
    width = x1 - x0 + 1
    # pits (jumpable: the room is tall)
    pit_cols = set()
    for k in range(int(ctx.param("pits", ctx.hazards_wanted))):
        x = x0 + int((k + 1) * width / (int(ctx.param("pits", ctx.hazards_wanted)) + 1)) - 1
        if x - 2 < x0 or x + 3 > x1 or abs(x - (x0 + x1) // 2) < 2 or floor + 1 > ctx.inner_rows[1]:
            continue
        for c in (x, x + 1):
            cv.carve(c, floor)
            cv.rock.discard((c, floor))
            cv.spike(c, floor + 1)
            pit_cols.add(c)
        res.hazards.append((floor + 1, x, x + 1))
    # floating platforms over the floor, 3 rows up
    ledges: List[Cell] = []
    if width >= 14:
        for frac in (0.3, 0.7):
            c = x0 + int(frac * width) - 1
            if any(abs(c - p) < 4 for p in pit_cols):
                continue
            for cc in range(c, c + 3):
                cv.floor(cc, floor - 3 - rng.randint(0, 1) * 0)
            ledges.append((c + 1, floor - 3))
    # stub pillars, one tile high
    if width >= 18:
        for frac in (0.15, 0.85):
            c = x0 + int(frac * width)
            if c in pit_cols or c + 1 in pit_cols or any(abs(c - lc) < 3 for lc, _ in ledges):
                continue
            cv.floor(c, floor - 1)
    res.add("ledge", ledges)
    walk = [cell for cell in cells if cell[0] not in pit_cols]
    centre = min(walk, key=lambda cell: abs(cell[0] - (x0 + x1) // 2))
    res.add("arena_center", [centre])
    res.add("room", [centre])
    return walk


def generate(ctx: TerrainContext, cv: Canvas) -> TerrainResult:
    rng = ctx.rng
    res = TerrainResult()
    lo, hi = ctx.inner_cols
    if ctx.orientation == "vertical":
        res = vertical_shaft(ctx, cv)
        chamber = res.stats.get("chamber")
        if chamber:  # widen the chamber floor into the arena, away from the stairwell
            cx0, cx1, floor, side = chamber
            x0, x1 = (lo, cx1) if side == "right" else (cx0, hi)
            if floor - ctx.jump_air - 2 >= ctx.inner_rows[0]:
                _room(ctx, cv, res, x0, x1, floor)
        return res
    fe, fx = ctx.entry.row, ctx.exit.row
    floor = fe
    margin = 2 if ctx.entry.kind != "end" else 0
    x0 = lo + margin + rng.randint(0, 1)
    x1 = hi - margin - rng.randint(0, 1)
    enter = stair_line((ctx.c0, fe), (x0, fe), rng)
    carve_path(cv, enter, DOOR)
    if fx < floor - 1 and x1 - x0 > 20:  # the exit is higher: a column of ledges at the right wall
        walk = _room(ctx, cv, res, x0, x1, floor)
        zx0 = x1 - 11
        ledges = zigzag(cv, zx0, floor, fx, "right", air=ctx.jump_air)
        leave = stair_line((x1, fx), (ctx.c1, fx), rng)
        res.add("ledge", ledges)
        exit_path = ledges + leave
    else:
        stairs = abs(fx - floor) * 2
        x1 = min(x1, hi - stairs) if fx != floor else x1
        walk = _room(ctx, cv, res, x0, x1, floor)
        leave = stair_line((x1, floor), (ctx.c1, fx), rng)
        exit_path = leave
    carve_path(cv, leave, DOOR)
    res.path = enter + walk + exit_path
    res.add("floor", enter + walk + leave)
    res.add("entry", enter[:3])
    res.add("exit", leave[-3:][::-1])
    return res
