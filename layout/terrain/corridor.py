"""Corridor — walk and fight.

A continuous floor from the entry to the exit with 1-tile stairs where the
height changes (and a few up-and-down slopes, more with ``complexity``), a
ceiling ``ceiling_height`` (default 5) empty rows above the floor, columns
hanging from the ceiling (3 rows of clearance below them), alcoves (the
ceiling raised over a few columns: ``room`` anchors), spike pits 2–3 columns
wide to hop over and, with ``complexity``, 2-row ledges to hop up onto
(``ledge`` anchors).  Since v4 Part 28 a short hop fits under the default
5-row ceiling (``ctx.hop_air``); a pit only gets a small vault (``ctx.jump_air``
rows) so a full jump over it does not bump its head, and ledges need a ceiling
the profile's shortest hop fits under.
"""

from __future__ import annotations

from typing import List, Set

from .base import Canvas, Cell, TerrainContext, TerrainResult, carve_path, stair_line
from .vertical import vertical_walk


def floor_line(ctx: TerrainContext, a: Cell, b: Cell, undulations: int) -> List[Cell]:
    """Stairs from *a* to *b* with *undulations* extra up-and-down bumps on the way."""
    rng = ctx.rng
    n = abs(b[0] - a[0])
    if undulations <= 0 or n < 16:
        return stair_line(a, b, rng)
    # waypoints: a, bumps, b — each bump 1–2 rows off the straight line
    k = min(undulations, n // 8)
    xs = sorted(rng.sample(range(a[0] + 4, b[0] - 3), k)) if b[0] > a[0] else []
    lo, hi = ctx.inner_rows
    pts = [a]
    for x in xs:
        t = (x - a[0]) / max(1, n)
        base = round(a[1] + (b[1] - a[1]) * t)
        row = min(max(base + rng.choice((-2, -1, 1, 2)), lo + ctx.jump_air + 1), hi - 1)
        pts.append((x, row))
    pts.append(b)
    cells: List[Cell] = []
    for p, q in zip(pts, pts[1:]):
        seg = stair_line(p, q, rng)
        cells += seg if not cells else seg[1:]
    return cells


def generate(ctx: TerrainContext, cv: Canvas) -> TerrainResult:
    if ctx.orientation == "vertical":
        return vertical_walk(ctx, cv, air=4, alcoves=True)
    rng = ctx.rng
    res = TerrainResult()
    air = int(ctx.param("ceiling_height", 5))
    complexity = float(ctx.param("complexity", 0.5))
    a = (ctx.c0, ctx.entry.row)
    b = (ctx.c1, ctx.exit.row)
    path = floor_line(ctx, a, b, undulations=round(complexity * 3))
    carve_path(cv, path, air)

    # flat runs (same row for 7+ columns) host pits, pillars and alcoves
    flats: List[List[Cell]] = []
    run: List[Cell] = []
    for cell in path:
        if run and cell[1] == run[-1][1]:
            run.append(cell)
        else:
            if len(run) >= 7:
                flats.append(run)
            run = [cell]
    if len(run) >= 7:
        flats.append(run)
    used: Set[int] = set()
    c_lo, c_hi = ctx.inner_cols

    def free(cols: range) -> bool:
        return all(c not in used and c_lo + 2 <= c <= c_hi - 2 for c in cols)

    pits = int(ctx.param("pits", ctx.hazards_wanted))
    pit_cols: Set[int] = set()
    for _ in range(pits):
        pw = 3 if rng.random() < complexity else 2  # pit width, columns
        spots = [(run, i) for run in flats for i in range(3, len(run) - 3 - pw)
                 if free(range(run[i][0] - 3, run[i][0] + pw + 3))]
        if not spots:
            break
        run, i = rng.choice(spots)
        x, f = run[i]
        if f + 1 > ctx.inner_rows[1] or f - ctx.jump_air < ctx.inner_rows[0]:
            continue
        for c in range(x, x + pw):
            cv.rock.discard((c, f))  # the path laid a floor here: open it
            cv.carve(c, f)
            cv.spike(c, f + 1)
            pit_cols.add(c)
        if air < ctx.jump_air:
            cv.carve_rect(x - 1, f - ctx.jump_air, x + pw, f - 1)
        res.hazards.append((f + 1, x, x + pw - 1))
        used.update(range(x - 3, x + pw + 3))

    # ledges: a 3–5 column block 2 rows above the floor, hopped onto and off
    ledge_rows = {}
    if air >= ctx.hop_air:
        for _ in range(round(complexity * 2)):
            lw = rng.randint(3, 5)
            spots = [(run, i) for run in flats for i in range(3, len(run) - 3 - lw)
                     if free(range(run[i][0] - 3, run[i][0] + lw + 3))]
            if not spots:
                break
            run, i = rng.choice(spots)
            x, f = run[i]
            if f - 2 - air < ctx.inner_rows[0]:
                continue
            cv.carve_rect(x, f - 2 - air, x + lw - 1, f - 3)
            for c in range(x, x + lw):
                cv.floor(c, f - 1)
                cv.floor(c, f - 2)
                ledge_rows[c] = f - 2
            res.add("ledge", [(c, f - 2) for c in range(x, x + lw)])
            used.update(range(x - 3, x + lw + 3))

    # alcoves: the ceiling raised by 2–3 rows over 4–6 columns
    rooms: List[Cell] = []
    for _ in range(1 + round(complexity * 2)):
        spots = [(run, i) for run in flats for i in range(1, len(run) - 6)
                 if free(range(run[i][0], run[i][0] + 6))]
        if not spots:
            break
        run, i = rng.choice(spots)
        x, f = run[i]
        w = rng.randint(4, 6)
        extra = rng.randint(2, 3)
        if f - air - extra < ctx.inner_rows[0]:
            continue
        cv.carve_rect(x, f - air - extra, x + w - 1, f - 1)
        rooms.append((x + w // 2, f))
        used.update(range(x, x + w))

    # columns hanging from the ceiling, 3 rows of clearance under them
    if air >= 5:
        for run in flats:
            for i in range(2, len(run) - 2, rng.randint(9, 13)):
                x, f = run[i]
                if not free(range(x - 1, x + 2)) or rng.random() > 0.6:
                    continue
                for r in range(f - air, f - 3):
                    cv.floor(x, r)
                used.update(range(x - 1, x + 2))

    walk = [(c, ledge_rows.get(c, r)) for c, r in path if c not in pit_cols]
    res.path = walk
    res.add("floor", walk)
    res.add("room", rooms)
    res.add("entry", walk[:3])
    res.add("exit", walk[-3:][::-1])
    return res
