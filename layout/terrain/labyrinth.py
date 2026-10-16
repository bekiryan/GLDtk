"""Labyrinth — a maze to find the way through.

A recursive-backtracker maze on a coarse grid of cells.  A cell is
``corridor_width`` (default 4) columns of air plus one wall column, and 4 rows
of air plus one floor row (the player is 2 tiles tall: a passage has 2 rows of
headroom to spare).  Neighbours on a maze row are joined through an opening in
the wall column; neighbours on maze rows above each other through a **link**
(a 2-column hole in the floor over a pedestal, ``base.link``), which the player
drops down or jumps up.  The maze is a tree, so the main path from the entry
cell to the exit cell is unique; every other leaf is a **dead end** (``dead_end``
anchors, where pickups go; spikes guard the back of some when the section has
hazards).

Horizontal levels: the entry door is on the left at the entry floor row, the
exit door on the far (right) side, stairs leading to the exit row.  Vertical
levels: the maze's top row is the band's top storey (its exit cell holds the
pedestal under the hole to the next band) and stairs lead from the band's
bottom into its bottom row.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from .base import (
    STOREY,
    Canvas,
    Cell,
    TerrainContext,
    TerrainResult,
    carve_path,
    link,
    stair_line,
)
from .vertical import bottom_start, top_target

MazeCell = Tuple[int, int]  # (maze row, maze col); row 0 = top
DOOR = 4


class Maze:
    def __init__(self, floors: List[int], x0: int, cols: int, cw: int) -> None:
        self.floors = floors  # floor row of each maze row, top first
        self.x0 = x0
        self.cols = cols
        self.cw = cw
        self.edges: Set[Tuple[MazeCell, MazeCell]] = set()

    @property
    def rows(self) -> int:
        return len(self.floors)

    def x(self, j: int) -> int:
        return self.x0 + j * (self.cw + 1)

    def neighbours(self, cell: MazeCell) -> List[MazeCell]:
        i, j = cell
        out = []
        for di, dj in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            a, b = i + di, j + dj
            if 0 <= a < self.rows and 0 <= b < self.cols:
                out.append((a, b))
        return out

    def connected(self, a: MazeCell, b: MazeCell) -> bool:
        return (a, b) in self.edges or (b, a) in self.edges

    def degree(self, cell: MazeCell) -> int:
        return sum(1 for n in self.neighbours(cell) if self.connected(cell, n))

    def carve_tree(self, start: MazeCell, rng) -> None:  # type: ignore[no-untyped-def]
        seen = {start}
        stack = [start]
        while stack:
            cur = stack[-1]
            todo = [n for n in self.neighbours(cur) if n not in seen]
            if not todo:
                stack.pop()
                continue
            # prefer horizontal moves: a side-view maze reads better with long passages
            weights = [2 if n[0] == cur[0] else 1 for n in todo]
            nxt = rng.choices(todo, weights=weights)[0]
            self.edges.add((cur, nxt))
            seen.add(nxt)
            stack.append(nxt)

    def path(self, a: MazeCell, b: MazeCell) -> List[MazeCell]:
        prev: Dict[MazeCell, Optional[MazeCell]] = {a: None}
        todo = [a]
        while todo:
            cur = todo.pop(0)
            if cur == b:
                break
            for n in self.neighbours(cur):
                if n not in prev and self.connected(cur, n):
                    prev[n] = cur
                    todo.append(n)
        out = []
        cur: Optional[MazeCell] = b
        while cur is not None:
            out.append(cur)
            cur = prev.get(cur)
        return out[::-1]

    def floor_cells(self, cell: MazeCell) -> List[Cell]:
        i, j = cell
        return [(c, self.floors[i]) for c in range(self.x(j), self.x(j) + self.cw)]

    def carve(self, cv: Canvas) -> Dict[MazeCell, Set[str]]:
        """Carve cells and passages; returns, per cell, how it is entered."""
        sides: Dict[MazeCell, Set[str]] = {}
        for i, f in enumerate(self.floors):
            for j in range(self.cols):
                x = self.x(j)
                cv.carve_rect(x, f - 4, x + self.cw - 1, f - 1)
                for c in range(x, x + self.cw):
                    cv.floor(c, f)
        for a, b in self.edges:
            (i1, j1), (i2, j2) = sorted((a, b))
            if i1 == i2:  # wall opening
                wall = self.x(j1) + self.cw
                f = self.floors[i1]
                cv.carve_rect(wall, f - 4, wall, f - 1)
                cv.floor(wall, f)
                sides.setdefault((i1, j1), set()).add("right")
                sides.setdefault((i2, j2), set()).add("left")
            else:  # link down through the upper cell's floor, alternating sides by row
                # so a pedestal never stands on the hole of the link below it
                col = self.x(j1) + self.cw - 2 if i1 % 2 == 0 else self.x(j1)
                link(cv, self.floors[i1], col, landing=False)
                sides.setdefault((i1, j1), set()).add("down")
                sides.setdefault((i2, j2), set()).add("up")
        return sides


def _floors(around: int, lo: int, hi: int, count: int, rng) -> List[int]:  # type: ignore[no-untyped-def]
    """Up to *count* floor rows STOREY apart including *around*, within [lo, hi]."""
    floors = [around]
    up, down = around - STOREY, around + STOREY
    while len(floors) < count:
        options = []
        if up >= lo:
            options.append("up")
        if down <= hi:
            options.append("down")
        if not options:
            break
        if rng.choice(options) == "up":
            floors.insert(0, up)
            up -= STOREY
        else:
            floors.append(down)
            down += STOREY
    return floors


def _finish(res: TerrainResult, maze: Maze, sides: Dict[MazeCell, Set[str]],
            entry: MazeCell, exit_: MazeCell, cv: Canvas, hazards: int) -> List[Cell]:
    main = maze.path(entry, exit_)
    main_cells: List[Cell] = []
    for cell in main:
        main_cells += maze.floor_cells(cell)
    dead = [
        (i, j) for i in range(maze.rows) for j in range(maze.cols)
        if (i, j) not in (entry, exit_) and maze.degree((i, j)) == 1
    ]
    dead_cells: List[Cell] = []
    for k, cell in enumerate(dead):
        i, j = cell
        x, f = maze.x(j), maze.floors[i]
        how = sides.get(cell, set())
        if "right" in how:
            back, spot = x, x + maze.cw - 2
        else:
            back, spot = x + maze.cw - 1, x + 1
        dead_cells.append((spot, f))
        if k < hazards and ("left" in how or "right" in how) and "down" not in how:
            cv.rock.discard((back, f))
            cv.spike(back, f)
            res.hazards.append((f, back, back))
    res.add("dead_end", dead_cells)
    res.add("room", dead_cells)
    res.stats.update(
        maze_rows=maze.rows, maze_cols=maze.cols, cells=maze.rows * maze.cols,
        passages=len(maze.edges), dead_ends=len(dead), main_path_cells=len(main),
    )
    return main_cells


def generate(ctx: TerrainContext, cv: Canvas) -> TerrainResult:
    rng = ctx.rng
    res = TerrainResult()
    cw = max(5, int(ctx.param("corridor_width", 5)))
    pitch = cw + 1
    complexity = float(ctx.param("complexity", 0.5))
    lo, hi = ctx.inner_cols
    top_row, bottom_row = ctx.inner_rows
    hazards = int(ctx.hazards_wanted)

    if ctx.orientation == "vertical":
        s = bottom_start(ctx, cv)
        a, pedestal = top_target(ctx, cv)
        f_top = a[1]
        floors = [f_top]
        while floors[-1] + STOREY <= s[1] - 0 and len(floors) < 8:
            floors.append(floors[-1] + STOREY)
        rise = s[1] - floors[-1]
        strip = 2 * rise + 3
        if s[0] - lo > hi - s[0]:  # entry stairs on the right of the start, maze to its left
            m_lo, m_hi = lo, s[0] - strip
        else:
            m_lo, m_hi = s[0] + strip, hi
        ped_col = pedestal[0][0] if pedestal else a[0]
        cols = max(1, (m_hi - m_lo + 2) // pitch)
        x0 = m_lo
        connector: List[Cell] = []
        if m_lo <= ped_col and ped_col + 1 <= m_hi:
            # shift the maze so the exit pedestal lies at a cell's left edge (the
            # top row's links down use the right side, never under the pedestal)
            for shift in range(pitch):
                if (ped_col - (m_lo + shift)) % pitch == 0 and \
                        m_lo + shift + (cols - 1) * pitch + cw - 1 <= m_hi:
                    x0 = m_lo + shift
                    break
            else:
                cols = max(1, cols - 1)
                for shift in range(pitch):
                    if (ped_col - (m_lo + shift)) % pitch == 0:
                        x0 = m_lo + shift
                        break
            maze = Maze(floors, x0, cols, cw)
            ex_j = min(max((ped_col - x0) // pitch, 0), cols - 1)
        else:  # the pedestal is over the entry stairs: a passage from the top row to it
            maze = Maze(floors, x0, cols, cw)
            ex_j = cols - 1 if ped_col > x0 else 0
            edge = maze.x(ex_j) + cw - 1 if ex_j == cols - 1 else maze.x(0)
            connector = stair_line((edge, f_top), (a[0], f_top), rng)
            carve_path(cv, connector, DOOR)
        en_j = 0 if s[0] < x0 else cols - 1
        entry, exit_ = (len(floors) - 1, en_j), (0, ex_j)
        maze.carve_tree(entry, rng)
        sides = maze.carve(cv)
        door_x = maze.x(en_j) if en_j == 0 else maze.x(en_j) + cw - 1
        enter = stair_line(s, (door_x, floors[-1]), rng)
        carve_path(cv, enter, DOOR)
        main = _finish(res, maze, sides, entry, exit_, cv, hazards) + connector
        res.path = enter + main + pedestal
        res.add("floor", enter + main + pedestal)
        res.add("entry", enter[:3])
        res.add("exit", (pedestal or main[-1:])[::-1])
        return res

    fe, fx = ctx.entry.row, ctx.exit.row
    count = 2 + round(complexity * 3)
    floors = _floors(fe, top_row + 4, bottom_row - 1, count, rng)
    ie = floors.index(fe)
    ix = min(range(len(floors)), key=lambda i: (abs(floors[i] - fx), -i))
    reserve = max(3, 2 * abs(fx - floors[ix]) + 2)
    m_lo = lo + 2
    cols = max(2, (hi - reserve - m_lo + 1) // pitch)
    maze = Maze(floors, m_lo, cols, cw)
    entry, exit_ = (ie, 0), (ix, cols - 1)
    maze.carve_tree(entry, rng)
    sides = maze.carve(cv)
    enter = stair_line((ctx.c0, fe), (m_lo, fe), rng)
    right = maze.x(cols - 1) + cw - 1
    leave = stair_line((right, floors[ix]), (ctx.c1, fx), rng)
    carve_path(cv, enter, DOOR)
    carve_path(cv, leave, DOOR)
    main = _finish(res, maze, sides, entry, exit_, cv, hazards)
    res.path = enter + main + leave
    res.add("floor", enter + main + leave)
    res.add("entry", enter[:3])
    res.add("exit", leave[-3:][::-1])
    return res
