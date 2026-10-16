"""Decorative background noise (v4 Part 29c).

A purely visual ``Background`` layer: seeded noise (``layout/noise.py``) draws
caves, strata and stalactite shapes, using the section theme's tiles.  It reads
the gameplay grid but never writes it, and it is exported below ``Collisions``
with no collision or gameplay meaning.  Cells where the gameplay grid is SOLID
stay empty (the rock hides them anyway).  Everything is a function of the seed
and the *world* cell, so chunks that share a border line up (``origin``).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from layout.ir import IRLevel, TileValue
from layout.noise import fbm, hash01, hash_ints, value_noise

from .themes import Theme, TileRole

CAVE, STRATA, STALACTITE = 0, 1, 2
KINDS = 3
_SALT = 0xB6D


def _stalactites(grid, width: int, height: int, seed: int, ox: int) -> set:
    """Cells hanging 1-4 tiles below a ceiling (the underside of solid rock)."""
    out = set()
    for c in range(width):
        for r in range(height - 1):
            if grid[r][c] == TileValue.SOLID and grid[r + 1][c] != TileValue.SOLID:
                x = ox + c
                if hash01(seed, x // 2, 1) < 0.45:
                    n = 1 + int(hash01(seed, x // 2, 2) * 4)
                    for k in range(1, n + 1):
                        if r + k < height:
                            out.add((c, r + k))
    return out


def background_values(
    ir: IRLevel,
    theme_for_cell: Callable[[int, int], Theme],
    themes: List[Theme],
    seed: int,
    origin: Tuple[int, int] = (0, 0),
) -> List[List[int]]:
    """``values[row][col]`` = 0 or ``1 + kind + KINDS * themes.index(theme)``.

    *themes* grows with every theme met.  *ir* is only read."""
    w, h = ir.width_tiles, ir.height_tiles
    ox, oy = origin
    s = hash_ints(seed, _SALT) & 0x7FFFFFFF
    hang = _stalactites(ir.grid, w, h, s, ox)
    out: List[List[int]] = []
    for r in range(h):
        line: List[int] = []
        for c in range(w):
            if ir.grid[r][c] == TileValue.SOLID:
                line.append(0)
                continue
            x, y = ox + c, oy + r
            kind: Optional[int] = None
            if (c, r) in hang:
                kind = STALACTITE
            elif fbm(s + 1, x, y, octaves=3, frequency=1.0 / 11.0) > 0.52:
                kind = CAVE
            elif value_noise(s + 2, x / 36.0, y / 3.0) > 0.6:
                kind = STRATA
            if kind is None:
                line.append(0)
                continue
            th = theme_for_cell(c, r)
            if th not in themes:
                themes.append(th)
            line.append(1 + kind + KINDS * themes.index(th))
        out.append(line)
    return out


def background_tile(theme: Theme, kind: int) -> Tuple[int, int]:
    """Source ``(x, y)`` in the theme sheet for a background *kind*."""
    if kind == STRATA:
        a = theme.tile(TileRole.FILL)
    elif kind == STALACTITE:
        a = theme.tile(TileRole.EDGE_B)
    else:
        a = theme.tile(TileRole.CENTER)
    return a.src_x, a.src_y
