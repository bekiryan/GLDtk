"""Deterministic, seeded 2D noise and the tools built on it (v4 Part 25a).

Pure Python (numpy is not a dependency).  Everything is a function of the seed
and of *world* coordinates, so two chunks that share a border sample the same
field there: a chunk at offset ``(ox, oy)`` samples ``fbm(seed, ox + col, oy + row)``.

* :func:`value_noise` — value noise on the integer lattice, smoothstep
  (quintic) interpolation of hashed corner values: continuous everywhere, in
  ``[0, 1]``;
* :func:`fbm` — fractal Brownian motion: ``octaves`` layers of value noise,
  each ``lacunarity`` times the frequency and ``gain`` times the amplitude of
  the last, normalised back to ``[0, 1]``;
* :func:`warp` — domain warping: offsets a point by two independent fBm fields;
* :func:`smooth_ca` — cellular-automata smoothing with the 4-5 rule.
"""

from __future__ import annotations

import math
from typing import Callable, List, Optional, Sequence, Tuple

_MASK = 0xFFFFFFFFFFFFFFFF


def hash_ints(*values: int) -> int:
    """A 64-bit integer hash of *values* (splitmix64 chain); stable across runs and
    processes (unlike ``hash()``)."""
    h = 0x9E3779B97F4A7C15
    for v in values:
        h = (h ^ (int(v) & _MASK)) & _MASK
        h = (h + 0x9E3779B97F4A7C15) & _MASK
        z = h
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
        h = z ^ (z >> 31)
    return h


def hash01(*values: int) -> float:
    """:func:`hash_ints` mapped to ``[0, 1)``."""
    return (hash_ints(*values) >> 11) / float(1 << 53)


def _fade(t: float) -> float:
    return t * t * t * (t * (t * 6 - 15) + 10)


def value_noise(seed: int, x: float, y: float) -> float:
    """Value noise in ``[0, 1]`` at ``(x, y)``: lattice values hashed from *seed*."""
    x0, y0 = math.floor(x), math.floor(y)
    tx, ty = _fade(x - x0), _fade(y - y0)
    a = hash01(seed, x0, y0)
    b = hash01(seed, x0 + 1, y0)
    c = hash01(seed, x0, y0 + 1)
    d = hash01(seed, x0 + 1, y0 + 1)
    top = a + (b - a) * tx
    bottom = c + (d - c) * tx
    return top + (bottom - top) * ty


def fbm(
    seed: int,
    x: float,
    y: float,
    *,
    octaves: int = 4,
    frequency: float = 1.0 / 16.0,
    lacunarity: float = 2.0,
    gain: float = 0.5,
) -> float:
    """Fractal Brownian motion of :func:`value_noise`, normalised to ``[0, 1]``."""
    total, amp, norm, f = 0.0, 1.0, 0.0, frequency
    for i in range(max(1, octaves)):
        total += amp * value_noise(seed + 1013 * i, x * f, y * f)
        norm += amp
        amp *= gain
        f *= lacunarity
    return total / norm


def warp(seed: int, x: float, y: float, strength: float, frequency: float = 1.0 / 24.0
         ) -> Tuple[float, float]:
    """Domain warping: ``(x, y)`` moved by up to *strength* along two fBm fields."""
    if strength <= 0:
        return x, y
    dx = fbm(seed + 7919, x, y, octaves=2, frequency=frequency) - 0.5
    dy = fbm(seed + 104729, x, y, octaves=2, frequency=frequency) - 0.5
    return x + 2.0 * strength * dx, y + 2.0 * strength * dy


def sample_field(
    seed: int,
    width: int,
    height: int,
    origin: Tuple[int, int] = (0, 0),
    *,
    octaves: int = 4,
    frequency: float = 1.0 / 16.0,
    lacunarity: float = 2.0,
    gain: float = 0.5,
    warp_strength: float = 0.0,
) -> List[List[float]]:
    """``field[row][col]`` = fBm at world ``(origin[0] + col, origin[1] + row)``."""
    ox, oy = origin
    out: List[List[float]] = []
    for r in range(height):
        line: List[float] = []
        for c in range(width):
            x, y = warp(seed, ox + c, oy + r, warp_strength)
            line.append(fbm(seed, x, y, octaves=octaves, frequency=frequency,
                            lacunarity=lacunarity, gain=gain))
        out.append(line)
    return out


def smooth_ca(
    cells: Sequence[Sequence[bool]],
    iterations: int = 3,
    *,
    mutable: Optional[Callable[[int, int], bool]] = None,
    outside: bool = True,
) -> List[List[bool]]:
    """Cellular-automata smoothing, 4-5 rule: a cell is solid next round when at least
    5 of its 9 cells (itself and 8 neighbours) are solid, i.e. a solid cell stays with
    4+ solid neighbours and an empty one fills with 5+.

    *mutable(col, row)* False pins a cell to its current value (protected cells,
    cells outside the fill region); out-of-grid neighbours count as *outside*.
    """
    height = len(cells)
    width = len(cells[0]) if height else 0
    cur = [list(line) for line in cells]
    for _ in range(max(0, iterations)):
        nxt = [list(line) for line in cur]
        for r in range(height):
            for c in range(width):
                if mutable is not None and not mutable(c, r):
                    continue
                n = 0
                for dr in (-1, 0, 1):
                    rr = r + dr
                    for dc in (-1, 0, 1):
                        cc = c + dc
                        if 0 <= rr < height and 0 <= cc < width:
                            n += cur[rr][cc]
                        else:
                            n += outside
                nxt[r][c] = n >= 5
        cur = nxt
    return cur


__all__ = ["fbm", "hash01", "hash_ints", "sample_field", "smooth_ca", "value_noise", "warp"]
