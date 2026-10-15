"""Shared pieces of the terrain generators: the canvas, ports and carving primitives.

Coordinates are grid cells ``(col, row)``, row 0 at the top.  A *floor cell* is
the solid tile the player stands on (the player occupies the rows above it).

Physics the primitives are built around (default Game Profile, checked by the
extractor's collision-aware arcs):

* the lowest jump (70 % of jump_v) still rises about 5.6 tiles, so the hitbox
  top goes about 7.1 tiles above the launch surface: **a jump needs 8 empty
  rows above it**.  Under a low ceiling the player only walks;
* 1-tile steps are walkable in terrain sections (``extract_graph(step_at=...)``),
  so stairs and slopes are runs of 1-row steps (2 columns per step when there
  is room);
* a **link** climbs 4 rows through a 2-column hole in a 1-tile slab: a 1-tile
  pedestal under the hole, 3 empty rows above it, and at least 4 empty rows
  above the slab.  It works up (jump through the hole) and down (drop
  through it), and is how mazes and stairwells change storeys;
* a **zigzag** climbs a chamber 12 columns wide with 4-column ledges on
  alternating walls, 3 rows apart.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from layout.ir import TileValue

Cell = Tuple[int, int]  # (col, row)

AIR = TileValue.EMPTY
ROCK = TileValue.SOLID
SPIKE = TileValue.HAZARD

JUMP_AIR = 8  # empty rows a jump needs above its launch surface
LINK_RISE = 4  # rows a link climbs (pedestal surface → slab)
STOREY = 5  # floor-to-floor pitch of a link (4 empty rows + the slab)
ZIGZAG_WIDTH = 12  # inner columns of a zigzag chamber
ZIGZAG_LEDGE = 4
ZIGZAG_STEP = 3
DOOR_AIR = 4  # empty rows above a door between two sections


@dataclass
class Port:
    """Where a section meets its neighbour (or the end of the level).

    * ``door``: horizontal levels — the floor row ``row`` at the seam column
      ``col`` (the first column of the right-hand band);
    * ``hole``: vertical levels — a 2-column hole at ``col``/``col + 1`` in the
      slab ``row`` (the upper band's bottom row); the lower section ends on a
      pedestal under it, the upper one starts on the slab beside it;
    * ``end``: the start or the end of the level (no neighbour): ``col``/``row``
      is a suggested floor cell;
    * ``open``: the neighbour is an open-air section: ``col``/``row`` is the
      floor cell to meet it at.
    """

    kind: str
    col: int
    row: int


@dataclass
class TerrainContext:
    section_id: str
    structure: str
    rect: Tuple[int, int, int, int]  # c0, r0, c1, r1 (inclusive)
    level_size: Tuple[int, int]  # width, height in tiles
    orientation: str
    entry: Port
    exit: Port
    rng: random.Random
    params: Dict[str, object] = field(default_factory=dict)
    hazards_wanted: int = 0
    player_h: int = 2

    @property
    def c0(self) -> int:
        return self.rect[0]

    @property
    def r0(self) -> int:
        return self.rect[1]

    @property
    def c1(self) -> int:
        return self.rect[2]

    @property
    def r1(self) -> int:
        return self.rect[3]

    def param(self, name: str, default):  # type: ignore[no-untyped-def]
        v = self.params.get(name)
        return default if v is None else v

    @property
    def inner_cols(self) -> Tuple[int, int]:
        """Columns that may hold air (inside the band and the level frame)."""
        w, _h = self.level_size
        return max(self.c0, 1), min(self.c1, w - 2)

    @property
    def inner_rows(self) -> Tuple[int, int]:
        _w, h = self.level_size
        return max(self.r0, 1), min(self.r1, h - 2)


@dataclass
class TerrainResult:
    """What a generator made: anchors for entities and the intended path."""

    path: List[Cell] = field(default_factory=list)  # main-path floor cells, in travel order
    anchors: Dict[str, List[Cell]] = field(default_factory=dict)  # name → floor cells
    hazards: List[Tuple[int, int, int]] = field(default_factory=list)  # (row, c0, c1) spike runs
    stats: Dict[str, object] = field(default_factory=dict)

    def add(self, name: str, cells: Sequence[Cell]) -> None:
        self.anchors.setdefault(name, []).extend(cells)


class Canvas:
    """Carving operations on one section band, applied in order air → rock → spikes.

    Everything starts as rock; air is only carved inside the band and inside
    the level frame (the outermost rows and columns stay solid).  Rock wins
    over air so a floor carved by one feature survives another feature's air.
    """

    def __init__(self, ctx: TerrainContext) -> None:
        self.ctx = ctx
        self.air: Set[Cell] = set()
        self.rock: Set[Cell] = set()
        self.spikes: Set[Cell] = set()
        self.unrock: Set[Cell] = set()  # holes forced open after rock (link holes)

    def inside(self, c: int, r: int) -> bool:
        c0, c1 = self.ctx.inner_cols
        r0, r1 = self.ctx.inner_rows
        return c0 <= c <= c1 and r0 <= r <= r1

    def in_band(self, c: int, r: int) -> bool:
        x = self.ctx
        return x.c0 <= c <= x.c1 and x.r0 <= r <= x.r1

    def carve(self, c: int, r: int) -> None:
        if self.inside(c, r):
            self.air.add((c, r))

    def carve_rect(self, ca: int, ra: int, cb: int, rb: int) -> None:
        for r in range(min(ra, rb), max(ra, rb) + 1):
            for c in range(min(ca, cb), max(ca, cb) + 1):
                self.carve(c, r)

    def floor(self, c: int, r: int) -> None:
        if self.in_band(c, r):
            self.rock.add((c, r))

    def spike(self, c: int, r: int) -> None:
        if self.inside(c, r):
            self.spikes.add((c, r))

    def hole(self, c: int, r: int) -> None:
        if self.inside(c, r):
            self.unrock.add((c, r))

    def apply(self, grid: List[List[TileValue]]) -> None:
        x = self.ctx
        for r in range(x.r0, x.r1 + 1):
            for c in range(x.c0, x.c1 + 1):
                grid[r][c] = ROCK
        for c, r in self.air:
            grid[r][c] = AIR
        for c, r in self.rock:
            grid[r][c] = ROCK
        for c, r in self.unrock:
            grid[r][c] = AIR
        for c, r in self.spikes:
            grid[r][c] = SPIKE

    def is_air(self, c: int, r: int) -> bool:
        return ((c, r) in self.air or (c, r) in self.unrock) and (c, r) not in self.rock


# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────


def step_columns(n_cols: int, steps: int, rng: random.Random, margin: int = 1) -> List[int]:
    """*steps* distinct offsets in ``[margin, n_cols - margin)`` spread out, 2 apart when
    there is room (2-column treads)."""
    if steps <= 0:
        return []
    lo, hi = margin, max(margin, n_cols - margin)
    slots = list(range(lo, hi))
    if not slots:
        slots = list(range(0, max(1, n_cols)))
    gap = 2 if len(slots) >= 2 * steps else 1
    if gap == 2:
        # one step per pair of columns, chosen among evenly spread pairs
        pairs = list(range(lo, hi - 1, 2))
        chosen = sorted(rng.sample(pairs, min(steps, len(pairs))))
        while len(chosen) < steps:  # not enough pairs: fill the remaining odd columns
            rest = [c for c in slots if c not in chosen]
            chosen = sorted(chosen + rest[: steps - len(chosen)])
        return chosen
    return sorted(slots[:steps]) if len(slots) >= steps else slots


def stair_line(a: Cell, b: Cell, rng: random.Random, margin: int = 1) -> List[Cell]:
    """Floor cells from *a* to *b* (any horizontal direction), changing row by one at
    a time (steps), as evenly as the columns allow.  Excess rise is not reachable:
    the line ends at ``b``'s column on the nearest row it can reach."""
    (ca, ra), (cb, rb) = a, b
    d = 1 if cb >= ca else -1
    n = abs(cb - ca)
    dr = rb - ra
    steps = min(abs(dr), n)
    offs = set(step_columns(n, steps, rng, margin=min(margin, max(0, (n - steps) // 2))))
    cells = [(ca, ra)]
    row = ra
    for i in range(1, n + 1):
        if (i - 1) in offs:
            row += 1 if dr > 0 else -1
        cells.append((ca + d * i, row))
    return cells


def carve_path(cv: Canvas, cells: Sequence[Cell], air: int) -> None:
    """Floor under every cell, *air* empty rows above it."""
    for c, r in cells:
        for k in range(1, air + 1):
            cv.carve(c, r - k)
    for c, r in cells:
        cv.floor(c, r)


def link(cv: Canvas, slab_row: int, col: int, landing: bool = True) -> Tuple[List[Cell], Cell]:
    """A 2-column hole in *slab_row* at ``col``/``col + 1`` over a pedestal.

    Returns the pedestal floor cells (surface ``slab_row + 4``) and the slab
    cell next to the hole on the left (callers carve the storeys).
    """
    for c in (col, col + 1):
        cv.hole(c, slab_row)
        for r in range(slab_row + 1, slab_row + LINK_RISE):
            cv.carve(c, r)
        cv.floor(c, slab_row + LINK_RISE)
        cv.floor(c, slab_row + LINK_RISE + 1)
    if landing:
        for c in range(col - 2, col + 4):  # room to land beside the hole
            for r in range(slab_row - 4, slab_row):
                cv.carve(c, r)
    return [(col, slab_row + LINK_RISE), (col + 1, slab_row + LINK_RISE)], (col - 1, slab_row)


def zigzag(
    cv: Canvas, x0: int, low_row: int, high_row: int, high_side: str,
    ledge: int = ZIGZAG_LEDGE, step: int = ZIGZAG_STEP, width: int = ZIGZAG_WIDTH,
) -> List[Cell]:
    """Ledges on alternating walls of the chamber ``x0 .. x0 + width - 1`` from
    *low_row* (the chamber floor) up to *high_row* (the top ledge, on
    *high_side*).  Carves the chamber air (``JUMP_AIR`` rows above the top
    ledge).  Returns the ledge floor cells from the bottom up."""
    x1 = x0 + width - 1
    cv.carve_rect(x0, high_row - JUMP_AIR - 1, x1, low_row - 1)
    rows = []
    r = high_row
    while low_row - r >= 2:
        rows.append(r)
        r += step
    # the lowest ledge must sit at least 2 rows above the chamber floor
    if rows and low_row - rows[-1] > step + 1:
        rows.append(rows[-1] + step)
    cells: List[Cell] = []
    side = high_side
    for row in rows:
        cols = range(x0, x0 + ledge) if side == "left" else range(x1 - ledge + 1, x1 + 1)
        for c in cols:
            cv.floor(c, row)
        cells.append((cols[0] if side == "left" else cols[-1], row))
        side = "right" if side == "left" else "left"
    for c in range(x0, x1 + 1):
        cv.floor(c, low_row)
    if rows:  # the lowest ledge stands on a pillar, so the floor run ends beside it
        last = rows[-1]
        side = cells[-1][0]
        cols = range(x0, x0 + ledge) if side == x0 else range(x1 - ledge + 1, x1 + 1)
        for c in cols:
            for r in range(last, low_row):
                cv.floor(c, r)
    return list(reversed(cells))


def ledge_cells(c0: int, c1: int, row: int) -> List[Cell]:
    return [(c, row) for c in range(min(c0, c1), max(c0, c1) + 1)]
