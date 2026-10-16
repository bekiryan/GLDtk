"""Stamp a retrieved pattern into a terrain section (v4 Part 26b).

A *stamp* is a real level fragment from the pattern index (Part 26) carved into
a section instead of the procedural generator.  Only its geometry is used
(SOLID, HAZARD, EMPTY); its entities never are.  ``layout/`` stays offline: the
pipeline retrieves the candidates and passes them in as plain dicts
``{"id", "grid" | "ascii", ...}``.

Fitting (:func:`fit_pattern`, deterministic):

* **width**: columns are repeated (pattern too narrow) or removed (too wide)
  only where the pattern has a flat walkable run (a column with a floor cell
  identical to a neighbour), nearest the middle first, so a jump arc is never
  cut or widened.  Removing more than ``MAX_RESIZE`` of the width rejects the
  pattern;
* **height**: rock rows are added on top, or top rows are cropped (the floor is
  at the bottom); more than ``MAX_RESIZE`` of the inner height rejects it;
* **ports**: the entry and exit floors are joined to the nearest standable
  pattern cell with ``stair_line`` / ``carve_path``, so the seams always match
  the neighbours.  A pattern whose ports end up in different air regions is
  rejected.

Only horizontal levels are stamped; vertical ones use the generators.
"""

from __future__ import annotations

import random
from collections import deque
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from layout.ir import TileValue

from .base import AIR, DOOR_AIR, ROCK, SPIKE, Canvas, Cell, TerrainContext, TerrainResult, carve_path, stair_line

MAX_RESIZE = 0.25  # share of a dimension that may be padded / cropped
_CHARS = {"#": ROCK, "^": SPIKE}

Grid = List[List[TileValue]]


def pattern_grid(rec: Dict[str, Any]) -> Optional[Grid]:
    """The tile grid of a pattern record: its ``grid`` or its ASCII (entity letters are air);
    None for ``kind="note"`` records and records without geometry."""
    if rec.get("kind", "pattern") == "note":
        return None
    raw = rec.get("grid")
    if raw:
        try:
            g = [[TileValue(int(v)) if int(v) in (0, 1, 2) else ROCK for v in row] for row in raw]
        except (TypeError, ValueError):
            return None
    else:
        lines = [ln for ln in str(rec.get("ascii") or "").splitlines() if ln]
        if not lines:
            return None
        w = max(len(ln) for ln in lines)
        g = [[_CHARS.get(ch, AIR) for ch in ln.ljust(w, "#")] for ln in lines]
    if not g or not g[0] or len({len(r) for r in g}) != 1:
        return None
    return g


def _col(g: Grid, c: int) -> Tuple[TileValue, ...]:
    return tuple(row[c] for row in g)


def _standable(g: Grid, c: int, r: int, head: int) -> bool:
    if g[r][c] != ROCK or r - head < 0:
        return False
    return all(g[r - k][c] == AIR for k in range(1, head + 1))


def _safe_cols(g: Grid, head: int) -> List[int]:
    """Columns inside a flat walkable run: equal to a neighbour and with a floor cell."""
    w = len(g[0])
    out = []
    for c in range(1, w - 1):
        col = _col(g, c)
        if col != _col(g, c - 1) and col != _col(g, c + 1):
            continue
        if any(_standable(g, c, r, head) for r in range(len(g))):
            out.append(c)
    return out


def _by_middle(cols: Sequence[int], w: int) -> List[int]:
    return sorted(cols, key=lambda c: (abs(2 * c - w), c))


def _fit_width(g: Grid, width: int, head: int) -> Optional[Grid]:
    w = len(g[0])
    if w == width:
        return [list(r) for r in g]
    if w > width:
        if w - width > MAX_RESIZE * w:
            return None
        out = [list(r) for r in g]
        for _ in range(w - width):  # one flat column at a time, nearest the middle
            safe = _by_middle(_safe_cols(out, head), len(out[0]))
            if not safe:
                return None
            c = safe[0]
            for row in out:
                del row[c]
        return out
    safe = _by_middle(_safe_cols(g, head), w)
    if not safe:
        return None
    extra: Dict[int, int] = {}
    for i in range(width - w):  # round robin over the flat columns, middle first
        c = safe[i % len(safe)]
        extra[c] = extra.get(c, 0) + 1
    return [[v for c, v in enumerate(row) for _ in range(1 + extra.get(c, 0))] for row in g]


def _fit_height(g: Grid, height: int) -> Optional[Grid]:
    h = len(g)
    if abs(h - height) > MAX_RESIZE * height:
        return None
    if h >= height:
        return [list(r) for r in g[h - height:]]
    return [[ROCK] * len(g[0]) for _ in range(height - h)] + [list(r) for r in g]


def _nearest_floor(floors: List[Cell], port: Cell, toward: int) -> Optional[Cell]:
    """The standable cell a stair from *port* can reach (rise ≤ run), nearest first."""
    pc, pr = port
    best = None
    for c, r in floors:
        dc = (c - pc) * toward
        if dc < 1 or abs(r - pr) > dc:
            continue
        key = (dc + abs(r - pr), dc, r)
        if best is None or key < best[0]:
            best = (key, (c, r))
    return best[1] if best else None


def _air_connected(cv: Canvas, a: Cell, b: Cell) -> bool:
    if not (cv.is_air(*a) and cv.is_air(*b)):
        return False
    seen: Set[Cell] = {a}
    todo = deque([a])
    while todo:
        c, r = todo.popleft()
        if (c, r) == b:
            return True
        for n in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
            if n not in seen and cv.is_air(*n) and n not in cv.spikes:
                seen.add(n)
                todo.append(n)
    return False


def _merge(cv: Canvas, work: Canvas) -> None:
    cv.air |= work.air
    cv.rock |= work.rock
    cv.spikes |= work.spikes
    cv.unrock |= work.unrock


def fit_pattern(grid: Grid, ctx: TerrainContext, cv: Optional[Canvas] = None) -> Optional[TerrainResult]:
    """Fit *grid* to the section of *ctx* and carve it into *cv* (a new canvas when None).
    None when the pattern does not fit (the canvas is then left untouched)."""
    if ctx.orientation == "vertical" or not grid or not grid[0]:
        return None
    c_lo, c_hi = ctx.inner_cols
    r_lo, r_hi = ctx.inner_rows
    head = max(1, ctx.player_h)
    g = _fit_height(grid, r_hi - r_lo + 1)
    g = _fit_width(g, c_hi - c_lo + 1, head) if g is not None else None
    if g is None:
        return None
    work = Canvas(ctx)
    floors: List[Cell] = []
    hazards: List[Tuple[int, int, int]] = []
    for i, row in enumerate(g):
        r = r_lo + i
        run: Optional[List[int]] = None
        for j, v in enumerate(row + [AIR]):
            c = c_lo + j
            if j < len(row) and v == AIR:
                work.carve(c, r)
            if j < len(row) and v == SPIKE:
                work.spike(c, r)
                run = [run[0], c] if run else [c, c]
                continue
            if run:
                hazards.append((r, run[0], run[1]))
                run = None
        for j in range(len(row)):
            if _standable(g, j, i, head):
                floors.append((c_lo + j, r))
    air = max(DOOR_AIR, ctx.hop_air)
    a = (ctx.c0, ctx.entry.row)
    b = (ctx.c1, ctx.exit.row)
    ta = _nearest_floor(floors, a, 1)
    tb = _nearest_floor(floors, b, -1)
    if ta is None or tb is None or ta[0] > tb[0]:
        return None
    rng = random.Random(f"{ctx.section_id}:{ctx.c0}:stamp")  # pure: never draws from ctx.rng
    entry_path = stair_line(a, ta, rng)
    exit_path = list(reversed(stair_line(b, tb, rng)))
    carve_path(work, entry_path, air)
    carve_path(work, exit_path, air)
    ea = (max(a[0], c_lo), a[1] - 1)  # the air cell above each port floor, inside the frame
    eb = (min(b[0], c_hi), b[1] - 1)
    if not _air_connected(work, ea, eb):
        return None
    # main path: entry stair, then one floor cell per column (nearest the last row), exit stair
    by_col: Dict[int, List[int]] = {}
    for c, r in floors:
        by_col.setdefault(c, []).append(r)
    path: List[Cell] = list(entry_path)
    row = ta[1]
    for c in range(ta[0] + 1, tb[0]):
        if c in by_col:
            row = min(by_col[c], key=lambda r: (abs(r - row), r))
            path.append((c, row))
    path += exit_path
    if cv is not None:
        _merge(cv, work)
    res = TerrainResult(path=path, hazards=hazards)
    res.add("entry", entry_path[: max(2, len(entry_path) // 2)])
    res.add("exit", exit_path[-max(2, len(exit_path) // 2):])
    res.add("floor", floors)
    res.stats.update({"stamp_width": len(grid[0]), "stamp_height": len(grid)})
    return res


def stamp_section(
    ctx: TerrainContext, cv: Canvas, candidates: Sequence[Dict[str, Any]], index: int,
) -> Tuple[Optional[TerrainResult], int]:
    """``(result, fits)``: carve the *index*-th candidate that fits (in order) into *cv*
    (``result`` None when fewer than ``index + 1`` fit) and the number that fit.
    The result's ``stats["pattern"]`` is the stamped pattern id."""
    chosen: Optional[TerrainResult] = None
    fits = 0
    for rec in candidates:
        g = pattern_grid(rec)
        if g is None:
            continue
        work = Canvas(ctx)
        res = fit_pattern(g, ctx, work)
        if res is None:
            continue
        if fits == index:
            _merge(cv, work)
            res.stats["pattern"] = str(rec["id"])
            chosen = res
        fits += 1
    return chosen, fits


__all__ = ["MAX_RESIZE", "fit_pattern", "pattern_grid", "stamp_section"]
