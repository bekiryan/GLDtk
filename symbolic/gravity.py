"""Piecewise gravity across section borders (GLDtk v3, Part 11).

A level's sections may scale gravity (``Section.gravity_scale``).  Sections are
column bands in horizontal levels and row bands in vertical ones, so the
effective gravity is piecewise constant along one axis.  :class:`GravityField`
holds those pieces; :class:`Flight` integrates a trajectory through them.

Rule: at every moment the player feels the gravity of the section that holds
the centre of the hitbox.  Points here are feet points (Y-up px, as in
``symbolic/physics.py``); the hitbox centre is ``(x, y + height/2)``, so a row
border at centre height ``b`` is crossed when the feet reach ``b − height/2``.

Within one region a flight is an exact parabola.  :class:`Flight` chains those
parabolas, splitting at every border crossing (exact crossing times from the
linear x(t) or the quadratic y(t)), so the integration is exact for piecewise
constant gravity; there is no time-step error.

Horizontal motion is a list of phases with constant vx.  For column bands
(axis ``"x"``) with one constant vx the vertical motion over a flight of length
T is a parabola in T with a *weighted* gravity

    y(T) − y0 = v·T − ½·G·T²,    G = 2·∫₀¹ (1 − σ)·g(x0 + σ·Δx) dσ

(σ = t/T), because the border crossing times scale with T.  That makes plain
jumps across a column border closed form again (:func:`path_moment`).  For row
bands (axis ``"y"``) the vertical motion does not depend on x at all, so the
landing times come from :meth:`Flight.roots_y`.
"""

from __future__ import annotations

import bisect
import math
from typing import TYPE_CHECKING, Callable, Iterable, List, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from game.profile import GameProfile

INF = math.inf
_EPS_T = 1e-9

Sample = Tuple[float, float]


# ─────────────────────────────────────────────────────────────────────────────
# Field
# ─────────────────────────────────────────────────────────────────────────────


class GravityField:
    """Effective gravity (px/s²) as a function of the player's feet point.

    ``axis`` is None (uniform), ``"x"`` (column bands: gravity depends on the
    feet / centre x) or ``"y"`` (row bands: gravity depends on the hitbox
    centre y).  ``borders`` are sorted coordinates along the axis, in *feet*
    coordinates (for ``"y"`` already shifted down by half the hitbox height);
    ``gs`` holds one effective gravity per region (``len(borders) + 1``).
    Adjacent regions with equal gravity are merged.
    """

    __slots__ = ("axis", "borders", "gs", "g_min", "g_max")

    def __init__(
        self,
        gravities: Sequence[float],
        borders: Sequence[float] = (),
        axis: Optional[str] = None,
    ) -> None:
        if len(gravities) != len(borders) + 1:
            raise ValueError("need exactly one gravity per region")
        if any(g <= 0 for g in gravities):
            raise ValueError(f"gravity must be positive, got {list(gravities)}")
        if axis not in (None, "x", "y"):
            raise ValueError(f"axis must be None, 'x' or 'y', got {axis!r}")
        gs: List[float] = [float(gravities[0])]
        bs: List[float] = []
        for b, g in zip(borders, gravities[1:]):
            if abs(g - gs[-1]) <= 1e-12:
                continue
            bs.append(float(b))
            gs.append(float(g))
        if any(b2 <= b1 for b1, b2 in zip(bs, bs[1:])):
            raise ValueError("borders must be strictly increasing")
        self.axis = axis if bs else None
        self.borders = bs
        self.gs = gs
        self.g_min = min(gs)
        self.g_max = max(gs)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def uniform(cls, gravity: float) -> "GravityField":
        return cls([gravity])

    @classmethod
    def from_columns(
        cls, gravity: float, col_scales: Sequence[float], tile_size: float
    ) -> "GravityField":
        """Column bands: ``col_scales[c]`` is the gravity scale of column c."""
        gs = [gravity * s for s in col_scales]
        borders = [c * tile_size for c in range(1, len(gs))]
        return cls(gs, borders, "x")

    @classmethod
    def from_rows(
        cls,
        gravity: float,
        row_scales: Sequence[float],
        tile_size: float,
        player_height: float,
    ) -> "GravityField":
        """Row bands: ``row_scales[r]`` is the gravity scale of grid row r (0 = top).

        Row r holds hitbox centres with ``y ∈ [(H−2−r)·ts, (H−1−r)·ts)``, the
        convention of ``symbolic/physics.py``.
        """
        height = len(row_scales)
        # ascending y: bottom row (H-1) first
        gs = [gravity * row_scales[r] for r in range(height - 1, -1, -1)]
        half = player_height / 2.0
        # border between row r (below) and r-1 (above) at centre y = (H-1-r)·ts
        borders = [(height - 1 - r) * tile_size - half for r in range(height - 1, 0, -1)]
        return cls(gs, borders, "y")

    @classmethod
    def from_cells(
        cls,
        profile: "GameProfile",
        width: int,
        height: int,
        scale_at: Callable[[int, int], float],
    ) -> "GravityField":
        """Build from a ``(col, row) -> gravity_scale`` function.

        The scales must form column bands or row bands (as GLDtk sections do);
        anything else is approximated by column bands, using each column's
        most common scale.
        """
        g = profile.physics.gravity
        ts = float(profile.tile_size)
        if width <= 0 or height <= 0:
            return cls.uniform(g)
        cells = [[float(scale_at(c, r)) for c in range(width)] for r in range(height)]
        first = cells[0][0]
        if all(v == first for row in cells for v in row):
            return cls.uniform(g * first)
        if all(cells[r][c] == cells[0][c] for r in range(height) for c in range(width)):
            return cls.from_columns(g, cells[0], ts)
        if all(row[c] == row[0] for row in cells for c in range(width)):
            return cls.from_rows(g, [row[0] for row in cells], ts, profile.player.height_px)
        col_scales = []
        for c in range(width):
            vals = [cells[r][c] for r in range(height)]
            col_scales.append(max(set(vals), key=vals.count))
        return cls.from_columns(g, col_scales, ts)

    @classmethod
    def from_sections(
        cls,
        profile: "GameProfile",
        sections: Iterable[object],
        width: int,
        height: int,
    ) -> "GravityField":
        """Build from section bands (``SectionBand`` objects or their dicts).

        Each band has ``col_start/col_end/row_start/row_end`` (inclusive),
        ``gravity_scale`` and ``orientation`` (``"vertical"`` = row bands).
        """
        bands = [b if isinstance(b, dict) else b.as_dict() for b in sections]  # type: ignore[attr-defined]
        g = profile.physics.gravity
        ts = float(profile.tile_size)
        if not bands:
            return cls.uniform(g)
        if str(bands[0].get("orientation", "horizontal")) == "vertical":
            scales = [float(bands[0]["gravity_scale"])] * height
            # bands cover rows; later bands win nothing — sections do not overlap
            for b in bands:
                for r in range(max(0, int(b["row_start"])), min(height - 1, int(b["row_end"])) + 1):
                    scales[r] = float(b["gravity_scale"])
            # rows outside every band: nearest band along the axis
            return cls.from_rows(g, scales, ts, profile.player.height_px)
        ordered = sorted(bands, key=lambda b: int(b["col_start"]))
        scales = [float(ordered[0]["gravity_scale"])] * width
        for b in ordered:
            for c in range(max(0, int(b["col_start"])), min(width - 1, int(b["col_end"])) + 1):
                scales[c] = float(b["gravity_scale"])
        return cls.from_columns(g, scales, ts)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def is_uniform(self) -> bool:
        return self.axis is None

    def _coord(self, x: float, y: float) -> float:
        return x if self.axis == "x" else y

    def region(self, x: float, y: float) -> int:
        if self.axis is None:
            return 0
        return bisect.bisect_right(self.borders, self._coord(x, y))

    def g_at(self, x: float, y: float) -> float:
        """Effective gravity at a feet point."""
        return self.gs[self.region(x, y)]

    def _span(self, lo: float, hi: float) -> Tuple[int, int]:
        return (
            bisect.bisect_right(self.borders, lo),
            bisect.bisect_right(self.borders, hi),
        )

    def uniform_g(self, xlo: float, xhi: float, ylo: float, yhi: float) -> Optional[float]:
        """The gravity if the feet box lies inside one region, else None."""
        if self.axis is None:
            return self.gs[0]
        lo, hi = (xlo, xhi) if self.axis == "x" else (ylo, yhi)
        a, b = self._span(lo, hi)
        return self.gs[a] if a == b else None

    def g_min_over(self, xlo: float, xhi: float, ylo: float, yhi: float) -> float:
        """Smallest gravity felt anywhere in the feet box (for safe pruning)."""
        if self.axis is None:
            return self.gs[0]
        lo, hi = (xlo, xhi) if self.axis == "x" else (ylo, yhi)
        a, b = self._span(lo, hi)
        return min(self.gs[a : b + 1])

    def path_moment(self, x0: float, slope: float, a: float, b: float, w: float) -> float:
        """``∫ₐᵇ (w − σ) · g(x0 + σ·slope) dσ`` for column bands (exact).

        Used for flights with constant vx: σ is the fraction of the flight time,
        x(σ) = x0 + σ·slope.  For a uniform or row-band field it uses the
        gravity at x0 (row bands never call it).
        """
        if b <= a:
            return 0.0
        cuts = [a, b]
        if self.axis == "x" and slope != 0.0:
            for border in self.borders:
                s = (border - x0) / slope
                if a < s < b:
                    cuts.append(s)
            cuts.sort()
        total = 0.0
        for s0, s1 in zip(cuts, cuts[1:]):
            if s1 <= s0:
                continue
            mid = (s0 + s1) / 2.0
            g = self.g_at(x0 + mid * slope, 0.0) if self.axis == "x" else self.gs[0]
            total += g * (w * (s1 - s0) - (s1 * s1 - s0 * s0) / 2.0)
        return total


# ─────────────────────────────────────────────────────────────────────────────
# Flight
# ─────────────────────────────────────────────────────────────────────────────


class Flight:
    """A ballistic flight through a :class:`GravityField`.

    ``phases`` is a list of ``(t_end, vx)``: vx holds until ``t_end`` (absolute
    time since launch); the last phase should end at ``INF``.  The vertical
    motion is built lazily as exact parabola pieces ``(t0, y0, v0, g, t1)``
    split at every border crossing.
    """

    __slots__ = ("field", "x0", "y0", "vy0", "phases", "_px", "_pieces", "_t0s", "_state")

    def __init__(
        self,
        field: GravityField,
        p0: Sample,
        vy0: float,
        phases: Sequence[Tuple[float, float]],
    ) -> None:
        self.field = field
        self.x0, self.y0 = float(p0[0]), float(p0[1])
        self.vy0 = float(vy0)
        ph = [(float(t), float(v)) for t, v in phases if t > 0.0 or t == INF]
        if not ph or ph[-1][0] != INF:
            last_v = ph[-1][1] if ph else (phases[-1][1] if phases else 0.0)
            ph.append((INF, last_v))
        self.phases = ph
        xs = [self.x0]
        t_prev = 0.0
        for t_end, vx in ph[:-1]:
            xs.append(xs[-1] + vx * (t_end - t_prev))
            t_prev = t_end
        self._px = xs  # x at the start of each phase
        self._pieces: List[Tuple[float, float, float, float, float]] = []
        self._t0s: List[float] = []
        self._state = (0.0, self.y0, self.vy0)  # next piece starts here

    # -- horizontal -----------------------------------------------------

    def _phase(self, t: float) -> int:
        for i, (t_end, _vx) in enumerate(self.phases):
            if t < t_end:
                return i
        return len(self.phases) - 1

    def x(self, t: float) -> float:
        i = self._phase(t)
        t_start = self.phases[i - 1][0] if i > 0 else 0.0
        return self._px[i] + self.phases[i][1] * (t - t_start)

    def max_abs_vx(self) -> float:
        return max(abs(v) for _t, v in self.phases)

    def time_at_x(self, xt: float, t_min: float = 0.0) -> Optional[float]:
        """First time ≥ t_min at which x(t) = xt, or None."""
        t_start = 0.0
        for i, (t_end, vx) in enumerate(self.phases):
            xa = self._px[i]
            if vx != 0.0:
                t = t_start + (xt - xa) / vx
                if t_start - _EPS_T <= t <= t_end and t >= t_min - _EPS_T:
                    return max(t, t_min)
            elif abs(xa - xt) < 1e-9 and t_end >= t_min:
                return max(t_start, t_min)
            t_start = t_end
        return None

    # -- vertical -------------------------------------------------------

    def _region(self, t: float, y: float, v: float) -> int:
        f = self.field
        if f.axis is None:
            return 0
        if f.axis == "x":
            x = self.x(t)
            vx = self.phases[self._phase(t)][1]
            r = bisect.bisect_right(f.borders, x)
            if vx < 0 and r > 0 and abs(x - f.borders[r - 1]) < 1e-9:
                r -= 1
            return r
        r = bisect.bisect_right(f.borders, y)
        if v <= 0 and r > 0 and abs(y - f.borders[r - 1]) < 1e-9:
            r -= 1
        return r

    def _next_event(self, t: float, y: float, v: float, reg: int, g: float) -> float:
        f = self.field
        best = INF
        # phase change (vx changes the x-border crossing times)
        if f.axis == "x":
            i = self._phase(t)
            if self.phases[i][0] > t:
                best = self.phases[i][0]
            vx = self.phases[i][1]
            if vx > 0 and reg < len(f.borders):
                best = min(best, t + (f.borders[reg] - self.x(t)) / vx)
            elif vx < 0 and reg > 0:
                best = min(best, t + (self.x(t) - f.borders[reg - 1]) / -vx)
        elif f.axis == "y":
            if reg < len(f.borders) and v > 0:
                up = f.borders[reg] - y
                disc = v * v - 2.0 * g * up
                if disc >= 0.0:
                    best = min(best, t + (v - math.sqrt(disc)) / g)
            if reg > 0:
                down = y - f.borders[reg - 1]
                best = min(best, t + (v + math.sqrt(v * v + 2.0 * g * max(0.0, down))) / g)
        if best <= t + _EPS_T:
            best = t + _EPS_T * 10
        return best

    def _extend(self, t_until: float) -> None:
        guard = 0
        while (not self._pieces or self._pieces[-1][4] < t_until) and guard < 10000:
            guard += 1
            t, y, v = self._state
            reg = self._region(t, y, v)
            g = self.field.gs[reg]
            t1 = self._next_event(t, y, v, reg, g)
            self._pieces.append((t, y, v, g, t1))
            self._t0s.append(t)
            if t1 == INF:
                break
            dt = t1 - t
            self._state = (t1, y + v * dt - 0.5 * g * dt * dt, v - g * dt)

    def _piece(self, t: float) -> Tuple[float, float, float, float, float]:
        self._extend(t)
        i = bisect.bisect_right(self._t0s, t) - 1
        return self._pieces[max(0, i)]

    def y(self, t: float) -> float:
        t0, y0, v0, g, _t1 = self._piece(t)
        dt = t - t0
        return y0 + v0 * dt - 0.5 * g * dt * dt

    def vy(self, t: float) -> float:
        t0, _y0, v0, g, _t1 = self._piece(t)
        return v0 - g * (t - t0)

    def point(self, t: float) -> Sample:
        return (self.x(t), self.y(t))

    def roots_y(self, yt: float) -> List[float]:
        """Every time t > 0 at which y(t) = yt (ascending order).

        The search ends once the flight is surely below yt: with the weakest
        gravity g_min the height is at most y0 + v0·t − ½·g_min·t².
        """
        g_lo = self.field.g_min
        disc = self.vy0 * self.vy0 + 2.0 * g_lo * (self.y0 - yt)
        if disc < 0.0:
            return []
        t_max = (self.vy0 + math.sqrt(disc)) / g_lo + 1e-6
        self._extend(t_max)
        out: List[float] = []
        for t0, y0, v0, g, t1 in self._pieces:
            if t0 > t_max:
                break
            dur = min(t1, t_max) - t0
            d = v0 * v0 - 2.0 * g * (yt - y0)
            if d < 0.0:
                continue
            r = math.sqrt(d)
            for s in ((v0 - r) / g, (v0 + r) / g):
                if -1e-9 <= s <= dur + 1e-9:
                    t = t0 + max(0.0, s)
                    if t > _EPS_T and not any(abs(t - o) < 1e-7 for o in out):
                        out.append(t)
        out.sort()
        return out

    def samples(
        self,
        t_end: float,
        step: float,
        max_samples: int,
        end: Optional[Sample] = None,
        t_start: float = 0.0,
    ) -> List[Sample]:
        """Feet points every ≤ *step* px from t_start to t_end (both included).

        vy only decreases (gravity is positive), so the largest |vy| on the
        interval is at one of its ends — the same bound as a single parabola.
        """
        span = max(0.0, t_end - t_start)
        self._extend(t_end)
        v_hi = max(abs(self.vy(t_start)), abs(self.vy(t_end)))
        length = (self.max_abs_vx() + v_hi) * span
        n = max(2, min(max_samples, math.ceil(length / step)))
        pieces = self._pieces
        phases = self.phases
        px = self._px
        k = max(0, bisect.bisect_right(self._t0s, t_start) - 1)
        j = 0
        pts: List[Sample] = []
        for i in range(n + 1):
            t = t_start + span * i / n
            while k + 1 < len(pieces) and pieces[k + 1][0] <= t:
                k += 1
            while j + 1 < len(phases) and phases[j][0] <= t:
                j += 1
            t0, y0, v0, g, _t1 = pieces[k]
            dt = t - t0
            ph_start = phases[j - 1][0] if j > 0 else 0.0
            pts.append((px[j] + phases[j][1] * (t - ph_start), y0 + v0 * dt - 0.5 * g * dt * dt))
        if end is not None:
            pts[-1] = end
        return pts


def solve_times(
    f: Callable[[float], float], t_lo: float, t_hi: float, n: int = 32, iters: int = 40
) -> List[float]:
    """Roots of a continuous f on [t_lo, t_hi]: a scan for sign changes, then bisection."""
    if t_hi <= t_lo:
        return []
    roots: List[float] = []
    prev_t = t_lo
    prev_f = f(t_lo)
    if prev_f == 0.0:
        roots.append(t_lo)
    for k in range(1, n + 1):
        t = t_lo + (t_hi - t_lo) * k / n
        ft = f(t)
        if ft == 0.0:
            roots.append(t)
        elif prev_f * ft < 0.0:
            a, b, fa = prev_t, t, prev_f
            for _ in range(iters):
                m = (a + b) / 2.0
                fm = f(m)
                if fa * fm <= 0.0:
                    b = m
                else:
                    a, fa = m, fm
            roots.append((a + b) / 2.0)
        prev_t, prev_f = t, ft
    return roots
