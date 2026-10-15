"""Jump Reachability Oracle — the deterministic physics judge.

All equations use a Y-up coordinate system.
Gravity is a positive scalar (magnitude of downward acceleration, e.g. 9.8 px/s²).
jump_v is the initial vertical speed at launch (positive = upward).

Kinematic model
---------------
  x(t) = vx * t
  y(t) = jump_v * t  −  ½ * g * t²

To land at (Δx, Δy) we need t* s.t.:

  Δy = jump_v * t* − ½ * g * t*²            … (1)
  Δx = vx * t*                               … (2)

From (1): ½g * t*² − jump_v * t* + Δy = 0
  discriminant D = jump_v² − 2 * g * Δy

  D < 0  → apex below Δy → unreachable
  D ≥ 0  → two candidate times; take the positive ones

Ability-aware checks (GLDtk v2)
-------------------------------
The player's abilities live in the per-project Game Profile
(``game/profile.py``).  :func:`check_double_jump`, :func:`check_dash_jump` and
:func:`check_wall_jump` extend the plain arc; :func:`reachable` is the single
entry point used by the validator and the extractor, and :func:`best_ability`
picks the cheapest way to make a move.  Every check takes the *effective*
gravity ``g = gravity · gravity_scale`` of the section the move starts in.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Iterator, List, Optional, Sequence, Tuple, Union

if TYPE_CHECKING:  # pragma: no cover
    from game.profile import GameProfile


def check_jump_arc(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    gravity: float,
    jump_v: float,
    max_horizontal_speed: Optional[float] = None,
) -> bool:
    """Return True if a jump from p1 can physically land at p2.

    Parameters
    ----------
    p1 : (x, y) launch position (surface standing point).
    p2 : (x, y) landing position (surface standing point).
    gravity : downward acceleration magnitude (must be > 0).
    jump_v : initial vertical speed at launch (must be ≥ 0).
    max_horizontal_speed : optional cap on |vx|.  When None, horizontal
        speed is unconstrained — only vertical reachability is checked.

    Returns
    -------
    bool
        True if at least one valid flight time t* > 0 exists where the
        parabolic arc passes through p2 and the required horizontal speed
        is within the given limit.
    """
    if gravity <= 0:
        raise ValueError(f"gravity must be positive, got {gravity}")
    if jump_v < 0:
        raise ValueError(f"jump_v must be non-negative, got {jump_v}")

    dx: float = p2[0] - p1[0]
    dy: float = p2[1] - p1[1]

    # Discriminant of the vertical quadratic.
    discriminant: float = jump_v**2 - 2.0 * gravity * dy

    if discriminant < 0.0:
        # Apex is below the target height; physically unreachable.
        return False

    sqrt_d: float = math.sqrt(discriminant)

    # Two candidate flight times from the quadratic formula.
    t1: float = (jump_v - sqrt_d) / gravity  # earlier (ascending phase)
    t2: float = (jump_v + sqrt_d) / gravity  # later   (descending phase)

    for t in (t1, t2):
        if t <= 0.0:
            # Must be a future moment.
            continue

        if max_horizontal_speed is None:
            # No speed cap → any positive flight time suffices.
            return True

        # Check whether the required horizontal speed is achievable.
        required_vx: float = dx / t
        if abs(required_vx) <= max_horizontal_speed + 1e-9:
            return True

    return False


def required_launch_velocity(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    gravity: float,
    jump_v: float,
) -> Optional[Tuple[float, float]]:
    """Return the (vx, vy) needed to arc from p1 to p2, or None if impossible.

    When two valid flight times exist the descending-phase time is preferred
    (longer flight → lower required |vx|, which is friendlier to slower
    characters).

    Parameters
    ----------
    p1, p2, gravity, jump_v : same semantics as :func:`check_jump_arc`.

    Returns
    -------
    (vx, vy) tuple or None.
    """
    if gravity <= 0:
        raise ValueError(f"gravity must be positive, got {gravity}")
    if jump_v < 0:
        raise ValueError(f"jump_v must be non-negative, got {jump_v}")

    dx: float = p2[0] - p1[0]
    dy: float = p2[1] - p1[1]

    discriminant: float = jump_v**2 - 2.0 * gravity * dy
    if discriminant < 0.0:
        return None

    sqrt_d: float = math.sqrt(discriminant)
    t1: float = (jump_v - sqrt_d) / gravity
    t2: float = (jump_v + sqrt_d) / gravity

    # Prefer the later (descending) time; fall back to the earlier one.
    chosen_t: Optional[float] = None
    for t in (t2, t1):
        if t > 0.0:
            chosen_t = t
            break

    if chosen_t is None:
        return None

    vx: float = dx / chosen_t
    return (vx, jump_v)


def fall_time(
    dy: float,
    gravity: float,
    initial_vy: float = 0.0,
) -> Optional[float]:
    """Time to fall a vertical displacement dy (negative = downward).

    Solves: dy = initial_vy * t − ½ * g * t²  for t > 0.

    Returns None if the displacement is not reachable under gravity.
    """
    if gravity <= 0:
        raise ValueError(f"gravity must be positive, got {gravity}")

    # ½g*t² − initial_vy*t + dy = 0
    discriminant: float = initial_vy**2 - 2.0 * gravity * dy
    if discriminant < 0.0:
        return None

    sqrt_d: float = math.sqrt(discriminant)
    t1: float = (initial_vy - sqrt_d) / gravity
    t2: float = (initial_vy + sqrt_d) / gravity

    positives = [t for t in (t1, t2) if t > 0.0]
    return min(positives) if positives else None


# ─────────────────────────────────────────────────────────────────────────────
# Derived limits
# ─────────────────────────────────────────────────────────────────────────────

_EPS = 1e-9


def max_jump_height(gravity: float, jump_v: float) -> float:
    """Apex of a plain jump:  h_max = v² / 2g."""
    return jump_v**2 / (2.0 * gravity)


def flat_jump_reach(gravity: float, jump_v: float, max_horizontal_speed: float) -> float:
    """Horizontal reach of a plain jump that lands at launch height.

    Flight time back to Δy = 0 is t = 2v / g, so  reach = vmax · 2v / g.
    """
    return max_horizontal_speed * 2.0 * jump_v / gravity


def gravity_for(profile: "GameProfile", gravity_scale: float = 1.0) -> float:
    """Effective gravity of a section:  g_eff = profile.gravity · gravity_scale."""
    if gravity_scale <= 0:
        raise ValueError(f"gravity_scale must be positive, got {gravity_scale}")
    return profile.physics.gravity * gravity_scale


# ─────────────────────────────────────────────────────────────────────────────
# Double jump
# ─────────────────────────────────────────────────────────────────────────────


def double_jump_max_time(
    dy: float, gravity: float, v1: float, v2: float
) -> Optional[float]:
    """Longest flight time of a double jump that lands at height Δy, or None.

    Model (exact, closed form)
    --------------------------
    First jump at v1; at time τ ∈ [0, 2·v1/g] (before the first arc falls
    back to launch height) the second jump resets the vertical velocity to v2:

      h(τ)  = v1·τ − ½g·τ²                       height when jumping again
      y(s)  = h(τ) + v2·s − ½g·s²                s = time since 2nd jump

    Landing at Δy on the descending branch:

      s(τ)  = (v2 + √D(τ)) / g,   D(τ) = v2² + 2g·(h(τ) − Δy)
      T(τ)  = τ + s(τ)                           total flight time

    Using 2g·h(τ) = v1² − (gτ − v1)²,  D(τ) = v1² + v2² − 2gΔy − (gτ − v1)².
    T is unimodal; T′(τ) = 1 + (v1 − gτ)/√D = 0 gives

      gτ* − v1 = √((v1² + v2² − 2gΔy) / 2)

    and D(τ*) = (gτ* − v1)² ≥ 0.  τ* is clipped to 2·v1/g; on [v1/g, τ*] D
    only grows towards the apex, so the clipped D is still ≥ 0.

    Δy is reachable at all iff  v1²/2g + v2²/2g ≥ Δy  (both apexes stacked).
    """
    if gravity <= 0:
        raise ValueError(f"gravity must be positive, got {gravity}")
    if v1 < 0 or v2 < 0:
        raise ValueError(f"jump speeds must be non-negative, got {v1}, {v2}")
    total = v1**2 + v2**2 - 2.0 * gravity * dy
    if total < 0.0:
        return None
    tau = min((v1 + math.sqrt(total / 2.0)) / gravity, 2.0 * v1 / gravity)
    d = v1**2 + v2**2 - 2.0 * gravity * dy - (gravity * tau - v1) ** 2
    if d < 0.0:  # numerical guard — analytically D ≥ 0 here
        d = 0.0
    return tau + (v2 + math.sqrt(d)) / gravity


def check_double_jump(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    gravity: float,
    v1: float,
    v2: float,
    max_horizontal_speed: Optional[float] = None,
) -> bool:
    """Return True if a jump at v1 followed by one mid-air jump at v2 lands at p2.

    The second jump is optional, so anything a plain jump reaches is also
    reachable.  Otherwise the move is reachable iff Δy ≤ v1²/2g + v2²/2g and
    the longest flight time T* (:func:`double_jump_max_time`) satisfies
    |Δx| ≤ vmax · T* — any slower horizontal speed is also allowed, so the
    longest flight is the most permissive.  Exact and O(1).
    """
    if check_jump_arc(p1, p2, gravity, v1, max_horizontal_speed):
        return True
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    t_max = double_jump_max_time(dy, gravity, v1, v2)
    if t_max is None or t_max <= 0.0:
        return False
    if max_horizontal_speed is None:
        return True
    return abs(dx) <= max_horizontal_speed * t_max + _EPS


# ─────────────────────────────────────────────────────────────────────────────
# Dash
# ─────────────────────────────────────────────────────────────────────────────


def check_dash_jump(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    gravity: float,
    jump_v: float,
    max_horizontal_speed: Optional[float],
    dash_px: float,
) -> bool:
    """Return True if a plain jump plus one horizontal dash lands at p2.

    Simplification: the dash is instantaneous and keeps the current height —
    it shifts the landing point by ``dash_px`` towards the target and costs no
    flight time.  So the move is reachable iff the plain arc reaches

      |Δx|′ = max(0, |Δx| − dash_px)   at the same Δy.
    """
    if dash_px < 0:
        raise ValueError(f"dash_px must be non-negative, got {dash_px}")
    dx = p2[0] - p1[0]
    reduced = max(0.0, abs(dx) - dash_px)
    return check_jump_arc(
        p1, (p1[0] + reduced, p2[1]), gravity, jump_v, max_horizontal_speed
    )


# ─────────────────────────────────────────────────────────────────────────────
# Wall jump
# ─────────────────────────────────────────────────────────────────────────────


def check_wall_jump(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    gravity: float,
    v_wall: float,
    max_climb: float,
    push_px: float,
    normal_v: float,
    max_horizontal_speed: Optional[float] = None,
) -> bool:
    """Graph-level envelope of a move that uses wall jumps.

    Approximation: by kicking off walls the player gains at most
    ``max_climb`` extra height and ``push_px`` extra horizontal reach:

      Δy   ≤ normal_v² / 2g + max_climb
      |Δx| ≤ vmax · 2·normal_v / g + push_px      (no cap when vmax is None)

    This does NOT check that a wall exists — that needs the tile grid, and
    Part 3 verifies it on the final grid.  ``v_wall`` (the kick speed) is not
    needed by this envelope; it is validated here and kept in the signature
    for the grid-level check.
    """
    if gravity <= 0:
        raise ValueError(f"gravity must be positive, got {gravity}")
    if v_wall <= 0:
        raise ValueError(f"v_wall must be positive, got {v_wall}")
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    if dy > max_jump_height(gravity, normal_v) + max_climb + _EPS:
        return False
    if max_horizontal_speed is None:
        return True
    reach = flat_jump_reach(gravity, normal_v, max_horizontal_speed) + push_px
    return abs(dx) <= reach + _EPS


# ─────────────────────────────────────────────────────────────────────────────
# Profile-level entry points
# ─────────────────────────────────────────────────────────────────────────────

ABILITY_ORDER: Tuple[str, ...] = ("dash", "double_jump", "wall_jump")
"""Abilities in "cheapest first" order — used by :func:`best_ability`."""


def ability_enabled(profile: "GameProfile", ability: str) -> bool:
    """True if *ability* exists in the profile and is enabled."""
    if ability not in ABILITY_ORDER:
        raise ValueError(f"unknown ability {ability!r}; expected one of {ABILITY_ORDER}")
    return bool(getattr(profile.abilities, ability).enabled)


def reachable(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    profile: "GameProfile",
    gravity_scale: float = 1.0,
    ability: Optional[str] = None,
) -> bool:
    """Can the player move from p1 to p2 under *profile*?

    ``ability=None`` is a plain jump; otherwise the named ability is used —
    and it must be enabled in the profile (a disabled ability never helps).
    Gravity is ``gravity_for(profile, gravity_scale)``; ``run_speed`` caps |vx|.
    """
    g = gravity_for(profile, gravity_scale)
    phys = profile.physics
    vmax = phys.run_speed
    if ability is None:
        return check_jump_arc(p1, p2, g, phys.jump_v, vmax)
    if not ability_enabled(profile, ability):
        return False
    ab = profile.abilities
    if ability == "dash":
        if not ab.dash.air:
            # A ground-only dash cannot extend a jump across a gap.
            return check_jump_arc(p1, p2, g, phys.jump_v, vmax)
        return check_dash_jump(p1, p2, g, phys.jump_v, vmax, ab.dash.distance_px)
    if ability == "double_jump":
        return check_double_jump(p1, p2, g, phys.jump_v, ab.double_jump.jump_v, vmax)
    return check_wall_jump(
        p1,
        p2,
        g,
        ab.wall_jump.jump_v,
        ab.wall_jump.max_climb_px,
        ab.wall_jump.push_px,
        phys.jump_v,
        vmax,
    )


def best_ability(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    profile: "GameProfile",
    gravity_scale: float = 1.0,
) -> Union[str, None, bool]:
    """Cheapest way to make the move.

    Returns ``None`` if a plain jump works, else the first *enabled* ability
    in :data:`ABILITY_ORDER` (dash → double_jump → wall_jump) that works, else
    ``False`` (impossible with this profile).
    """
    if reachable(p1, p2, profile, gravity_scale, None):
        return None
    for ability in ABILITY_ORDER:
        if ability_enabled(profile, ability) and reachable(
            p1, p2, profile, gravity_scale, ability
        ):
            return ability
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Collision-aware arcs on a tile grid (GLDtk v2, Part 3)
# ─────────────────────────────────────────────────────────────────────────────
#
# Grid convention (same as symbolic/extractor.py): ``grid[row][col]``, row 0 is
# the visual top, 0 = empty, anything else (solid / hazard / start / exit) is
# blocking.  A platform tile on row r has its standing surface at
#
#     y = (H − 1 − r) · tile_size          (Y-up px)
#
# so tile row r covers y ∈ [(H − 2 − r)·ts, (H − 1 − r)·ts).  Points are the
# player's *feet centre*; the hitbox is ``player.width_px × player.height_px``
# above it.
#
# Sampling: every candidate trajectory is a polyline of feet positions spaced
# at most ``SAMPLE_STEP_PX`` (6 px) apart; the hitbox is tested against the
# grid at every sample.  Consecutive hitboxes (12 × 24 px by default) overlap,
# so the swept area is contiguous and a 16 px tile cannot slip between two
# samples; only corner clips shallower than a few px can be missed.  Contacts within
# ``_CONTACT_EPS`` are not collisions (standing on, or brushing, a surface).
# The left/right level edges and the area below the bottom row block; the area
# above the top row is open.

Sample = Tuple[float, float]
SAMPLE_STEP_PX = 6.0
LAUNCH_FRACTIONS: Tuple[float, ...] = (1.0, 0.85, 0.7)
"""Launch speeds tried, as fractions of jump_v (variable jump height)."""
LAND_TOLERANCE_PX = 4.0
"""Hitting the target's surface row is allowed while the feet are this close to it."""
_CONTACT_EPS = 0.01
_MAX_SAMPLES = 2000
_DOUBLE_JUMP_TAU_STEPS = 8
_DASH_SPLITS: Tuple[float, ...] = (0.5, 0.0, 1.0, 0.25, 0.75)


def _parabola(
    p1: Sample, vx: float, vy: float, g: float, t_end: float, end: Optional[Sample] = None
) -> List[Sample]:
    """Feet positions of a ballistic arc from p1 for ``t ∈ [0, t_end]``."""
    vmax_y = max(abs(vy), abs(vy - g * t_end))
    length = (abs(vx) + vmax_y) * t_end
    n = max(2, min(_MAX_SAMPLES, math.ceil(length / SAMPLE_STEP_PX)))
    pts = []
    for i in range(n + 1):
        t = t_end * i / n
        pts.append((p1[0] + vx * t, p1[1] + vy * t - 0.5 * g * t * t))
    if end is not None:
        pts[-1] = end
    return pts


def _segment(a: Sample, b: Sample) -> List[Sample]:
    """Straight polyline from a to b (both included)."""
    n = max(1, math.ceil(math.hypot(b[0] - a[0], b[1] - a[1]) / SAMPLE_STEP_PX))
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]


def _plain_trajectories(
    p1: Sample, p2: Sample, g: float, jump_v: float, vmax: float
) -> Iterator[List[Sample]]:
    """Walk-off-ledge drop (downward moves), then jumps at each launch speed.

    Every candidate respects |vx| ≤ vmax — downward moves included.
    """
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    if dy < 0.0:
        t = fall_time(dy, g, 0.0)
        if t is not None and abs(dx / t) <= vmax + _EPS:
            yield _parabola(p1, dx / t, 0.0, g, t, p2)
    for frac in LAUNCH_FRACTIONS:
        v = jump_v * frac
        disc = v * v - 2.0 * g * dy
        if disc < 0.0:
            continue
        root = math.sqrt(disc)
        for t in ((v + root) / g, (v - root) / g):  # descending branch first
            if t <= _EPS:
                continue
            if abs(dx / t) > vmax + _EPS:
                continue
            yield _parabola(p1, dx / t, v, g, t, p2)


def _double_jump_trajectories(
    p1: Sample, p2: Sample, g: float, v1: float, v2: float, vmax: float
) -> Iterator[List[Sample]]:
    """Jump at v1, second jump at v2 after τ (τ sampled over the first arc)."""
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    tau_max = 2.0 * v1 / g
    taus = [tau_max * k / _DOUBLE_JUMP_TAU_STEPS for k in range(1, _DOUBLE_JUMP_TAU_STEPS + 1)]
    total = v1**2 + v2**2 - 2.0 * g * dy
    if total >= 0.0:  # the closed-form optimum of double_jump_max_time
        taus.insert(0, min((v1 + math.sqrt(total / 2.0)) / g, tau_max))
    for tau in taus:
        h = v1 * tau - 0.5 * g * tau * tau
        disc = v2 * v2 + 2.0 * g * (h - dy)
        if disc < 0.0:
            continue
        root = math.sqrt(disc)
        for s in ((v2 + root) / g, (v2 - root) / g):
            if s <= _EPS:
                continue
            vx = dx / (tau + s)
            if abs(vx) > vmax + _EPS:
                continue
            first = _parabola(p1, vx, v1, g, tau)
            second = _parabola(first[-1], vx, v2, g, s, p2)
            yield first + second[1:]


def _dash_trajectories(
    p1: Sample, p2: Sample, g: float, jump_v: float, vmax: float, dash_px: float
) -> Iterator[List[Sample]]:
    """Plain arc to the dash-shortened target, with the dash segment inserted.

    The dash is the same instantaneous horizontal shift as
    :func:`check_dash_jump`; here its segment is sampled at a few points of the
    arc (middle first) and must be clear too.
    """
    dx = p2[0] - p1[0]
    sign = 1.0 if dx >= 0 else -1.0
    dash = min(dash_px, abs(dx))
    reduced_target = (p2[0] - sign * dash, p2[1])
    for arc in _plain_trajectories(p1, reduced_target, g, jump_v, vmax):
        last = len(arc) - 1
        for frac in _DASH_SPLITS:
            k = round(frac * last)
            xk, yk = arc[k]
            seg = _segment((xk, yk), (xk + sign * dash, yk))
            post = [(x + sign * dash, y) for x, y in arc[k + 1 :]]
            path = arc[:k] + seg + post
            path[-1] = p2
            yield path


def find_walls(
    grid: Sequence[Sequence[int]],
    p1: Sample,
    p2: Sample,
    profile: "GameProfile",
    tile_size: float,
    min_height_tiles: int = 3,
) -> List[Tuple[int, int, int]]:
    """Solid vertical runs (``col, row_top, row_bottom``) usable for a wall jump.

    A wall is ≥ ``min_height_tiles`` stacked non-empty tiles in one column, within
    ``push_px`` (plus a tile) of the horizontal span of the hop, overlapping the
    hop's height range (plus the player's height).  Sorted by distance to the
    hop's midpoint.
    """
    height = len(grid)
    if height == 0:
        return []
    width = len(grid[0])
    push = profile.abilities.wall_jump.push_px
    lo_x = min(p1[0], p2[0]) - push - tile_size
    hi_x = max(p1[0], p2[0]) + push + tile_size
    lo_y = min(p1[1], p2[1])
    hi_y = max(p1[1], p2[1]) + profile.player.height_px
    c0 = max(0, int(math.floor(lo_x / tile_size)))
    c1 = min(width - 1, int(math.floor(hi_x / tile_size)))
    mid = (p1[0] + p2[0]) / 2.0
    walls: List[Tuple[int, int, int]] = []
    for c in range(c0, c1 + 1):
        r = 0
        while r < height:
            if grid[r][c] == 0:
                r += 1
                continue
            top = r
            while r + 1 < height and grid[r + 1][c] != 0:
                r += 1
            bottom = r
            r += 1
            if bottom - top + 1 < min_height_tiles:
                continue
            wall_hi = (height - 1 - top) * tile_size
            wall_lo = (height - 2 - bottom) * tile_size
            if wall_hi > lo_y and wall_lo < hi_y:
                walls.append((c, top, bottom))
    walls.sort(key=lambda w: (abs((w[0] + 0.5) * tile_size - mid), w[0], w[1]))
    return walls


def _wall_jump_trajectories(
    grid: Sequence[Sequence[int]],
    p1: Sample,
    p2: Sample,
    profile: "GameProfile",
    g: float,
    tile_size: float,
) -> Iterator[List[Sample]]:
    """Approximate wall-jump paths: to the wall face, climb it, over to the target.

    Requires the graph-level envelope (:func:`check_wall_jump`) and a real wall
    (:func:`find_walls`).  The path is a polyline p1 → wall face → up the face to
    just above the target height → across → down onto p2.
    """
    wj = profile.abilities.wall_jump
    phys = profile.physics
    if not check_wall_jump(
        p1, p2, g, wj.jump_v, wj.max_climb_px, wj.push_px, phys.jump_v, phys.run_speed
    ):
        return
    half = profile.player.width_px / 2.0 + _CONTACT_EPS * 2
    top_y = max(p1[1], p2[1]) + LAND_TOLERANCE_PX
    for col, _top, _bottom in find_walls(grid, p1, p2, profile, tile_size):
        left_face = col * tile_size - half
        right_face = (col + 1) * tile_size + half
        faces = (left_face, right_face) if p1[0] <= col * tile_size else (right_face, left_face)
        for face in faces:
            start_y = p1[1] + _CONTACT_EPS * 10
            path = _segment(p1, (face, start_y))
            path += _segment((face, start_y), (face, top_y))[1:]
            path += _segment((face, top_y), (p2[0], top_y))[1:]
            path += _segment((p2[0], top_y), p2)[1:]
            yield path


def trajectory_candidates(
    grid: Sequence[Sequence[int]],
    p1: Sample,
    p2: Sample,
    profile: "GameProfile",
    gravity_scale: float = 1.0,
    ability: Optional[str] = None,
) -> Iterator[List[Sample]]:
    """Every candidate path (feet polylines) for the move, physics only.

    ``ability=None`` is a plain move (walk-off drop or jump at 100/85/70 % of
    jump_v, both flight-time roots).  An ability adds its own candidates on top
    of the plain ones; a disabled ability adds nothing.
    """
    g = gravity_for(profile, gravity_scale)
    phys = profile.physics
    vmax = phys.run_speed
    yield from _plain_trajectories(p1, p2, g, phys.jump_v, vmax)
    if ability is None or not ability_enabled(profile, ability):
        return
    ab = profile.abilities
    if ability == "double_jump":
        yield from _double_jump_trajectories(p1, p2, g, phys.jump_v, ab.double_jump.jump_v, vmax)
    elif ability == "dash":
        if ab.dash.air:
            yield from _dash_trajectories(p1, p2, g, phys.jump_v, vmax, ab.dash.distance_px)
    else:
        yield from _wall_jump_trajectories(grid, p1, p2, profile, g, float(profile.tile_size))


def trajectory_hits(
    grid: Sequence[Sequence[int]],
    samples: Sequence[Sample],
    p2: Sample,
    tile_size: float,
    hitbox: Tuple[float, float],
    first_only: bool = True,
) -> List[Tuple[int, int]]:
    """Grid cells ``(col, row)`` the hitbox overlaps along *samples*.

    ``(-1, -1)`` stands for leaving the level (sides or bottom).  Cells on the
    target's surface row are ignored while the feet are within
    ``LAND_TOLERANCE_PX`` of the target surface (landing).
    """
    height = len(grid)
    width = len(grid[0]) if height else 0
    w, h = hitbox
    ts = tile_size
    target_row = height - 1 - int(math.floor(p2[1] / ts + 0.5))
    land_y = p2[1] - LAND_TOLERANCE_PX
    hits: List[Tuple[int, int]] = []
    seen = set()
    half = w / 2.0
    eps = _CONTACT_EPS
    max_x = width * ts
    base = height - 2
    last_row = height - 1
    for x, y in samples:
        left = x - half + eps
        right = x + half - eps
        r_bottom = base - int((y + eps) // ts)
        if left < 0.0 or right > max_x or r_bottom > last_row:
            if first_only:
                return [(-1, -1)]
            hits.append((-1, -1))
            continue
        r_top = base - int((y + h - eps) // ts)
        c0 = int(left // ts)
        c1 = int(right // ts)
        if c1 > width - 1:
            c1 = width - 1
        for r in range(r_top if r_top > 0 else 0, r_bottom + 1):
            row = grid[r]
            for c in range(c0, c1 + 1):
                if row[c] == 0:
                    continue
                if r == target_row and y >= land_y:
                    continue
                if first_only:
                    return [(c, r)]
                if (c, r) not in seen:
                    seen.add((c, r))
                    hits.append((c, r))
    return hits


def trajectory_clear(
    grid: Sequence[Sequence[int]],
    p1: Sample,
    p2: Sample,
    profile: "GameProfile",
    gravity_scale: float = 1.0,
    ability: Optional[str] = None,
    tile_size: Optional[float] = None,
) -> bool:
    """True if some physically valid trajectory from p1 to p2 is collision-free.

    p1/p2 are feet points (Y-up px) on the source / target surfaces.  The player
    hitbox (``profile.player``) is swept along every candidate of
    :func:`trajectory_candidates`; the move is clear if any candidate touches no
    solid or hazard tile (landing on the target surface is allowed).
    """
    ts = float(tile_size or profile.tile_size)
    hitbox = (profile.player.width_px, profile.player.height_px)
    for samples in trajectory_candidates(grid, p1, p2, profile, gravity_scale, ability):
        if not trajectory_hits(grid, samples, p2, ts, hitbox):
            return True
    return False
