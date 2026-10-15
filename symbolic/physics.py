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
picks the cheapest way to make a move.  Every closed-form check takes one
*effective* gravity ``g = gravity · gravity_scale``.

Cross-section moves (v3)
------------------------
Sections may scale gravity, and a move can cross a section border.  The
entry points take an optional :class:`~symbolic.gravity.GravityField`; when the
move stays inside one region of it, the closed forms above are used with that
region's gravity (the fast path).  Otherwise the trajectory is integrated
piecewise: the gravity is the one of the section holding the hitbox centre,
switching exactly at the border (``symbolic/gravity.py``).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Iterator, List, Optional, Sequence, Tuple, Union

from .gravity import INF, Flight, GravityField

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


def _move_box(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    profile: "GameProfile",
    field: GravityField,
    ability: Optional[str],
) -> Tuple[float, float, float, float]:
    """Feet box ``(xlo, xhi, ylo, yhi)`` a move with *ability* can sweep.

    The rise uses the weakest gravity of the field, so the box is safe.
    """
    phys = profile.physics
    ab = profile.abilities
    v2 = phys.jump_v**2
    if ability == "double_jump":
        v2 += ab.double_jump.jump_v**2
    rise = v2 / (2.0 * field.g_min)
    extra = ab.dash.distance_px if ability == "dash" else 0.0
    return (
        min(p1[0], p2[0]) - extra,
        max(p1[0], p2[0]) + extra,
        min(p1[1], p2[1]),
        max(p1[1], p2[1]) + rise,
    )


def move_gravity(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    profile: "GameProfile",
    gravity_scale: float = 1.0,
    ability: Optional[str] = None,
    gravity_field: Optional[GravityField] = None,
) -> Optional[float]:
    """The single gravity a move feels, or None if it may cross a border.

    Without a field this is ``gravity_for(profile, gravity_scale)``.  With one,
    it is the gravity of the region holding the whole move (fast path), or
    None when the move's box spans regions with different gravity.
    """
    if gravity_field is None:
        return gravity_for(profile, gravity_scale)
    box = _move_box(p1, p2, profile, gravity_field, ability)
    return gravity_field.uniform_g(*box)


def reachable(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    profile: "GameProfile",
    gravity_scale: float = 1.0,
    ability: Optional[str] = None,
    gravity_field: Optional[GravityField] = None,
) -> bool:
    """Can the player move from p1 to p2 under *profile*?

    ``ability=None`` is a plain jump; otherwise the named ability is used —
    and it must be enabled in the profile (a disabled ability never helps).
    Gravity is ``gravity_for(profile, gravity_scale)``; ``run_speed`` caps |vx|.

    With *gravity_field*, a move inside one gravity region uses the closed
    forms with that region's gravity; a move that may cross a border is
    integrated piecewise (any physically valid candidate trajectory, ignoring
    tiles).  Wall jumps then use the envelope with the weakest gravity — the
    exact wall-jump check needs the tile grid (:func:`trajectory_clear`).
    """
    if ability is not None and not ability_enabled(profile, ability):
        return False
    if gravity_field is not None:
        g_move = move_gravity(p1, p2, profile, gravity_scale, ability, gravity_field)
        if g_move is None:
            if ability == "wall_jump":
                box = _move_box(p1, p2, profile, gravity_field, ability)
                g_lo = gravity_field.g_min_over(box[0], box[1], box[2], INF)
                ab = profile.abilities
                return check_wall_jump(
                    p1, p2, g_lo, ab.wall_jump.jump_v, ab.wall_jump.max_climb_px,
                    ab.wall_jump.push_px, profile.physics.jump_v, profile.physics.run_speed,
                )
            for _ in trajectory_candidates(
                [[0]], p1, p2, profile, gravity_scale, ability, gravity_field
            ):
                return True
            return False
        gravity_scale = g_move / profile.physics.gravity
    g = gravity_for(profile, gravity_scale)
    phys = profile.physics
    vmax = phys.run_speed
    if ability is None:
        return check_jump_arc(p1, p2, g, phys.jump_v, vmax)
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
    gravity_field: Optional[GravityField] = None,
) -> Union[str, None, bool]:
    """Cheapest way to make the move.

    Returns ``None`` if a plain jump works, else the first *enabled* ability
    in :data:`ABILITY_ORDER` (dash → double_jump → wall_jump) that works, else
    ``False`` (impossible with this profile).  *gravity_field* as in
    :func:`reachable`.
    """
    if reachable(p1, p2, profile, gravity_scale, None, gravity_field):
        return None
    for ability in ABILITY_ORDER:
        if ability_enabled(profile, ability) and reachable(
            p1, p2, profile, gravity_scale, ability, gravity_field
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
    p1: Sample,
    p2: Sample,
    g: float,
    jump_v: float,
    vmax: float,
    dash_px: float,
    field: Optional[GravityField] = None,
) -> Iterator[List[Sample]]:
    """Plain arc to the dash-shortened target, with the dash segment inserted.

    The dash is the same instantaneous horizontal shift as
    :func:`check_dash_jump`; here its segment is sampled at a few points of the
    arc (middle first) and must be clear too.  With a row-band *field* the
    arcs are the piecewise ones (exact: their vertical motion ignores x).
    """
    dx = p2[0] - p1[0]
    sign = 1.0 if dx >= 0 else -1.0
    dash = min(dash_px, abs(dx))
    reduced_target = (p2[0] - sign * dash, p2[1])
    arcs = (
        _plain_trajectories(p1, reduced_target, g, jump_v, vmax)
        if field is None
        else _field_plain_trajectories(p1, reduced_target, field, jump_v, vmax)
    )
    for arc in arcs:
        last = len(arc) - 1
        for frac in _DASH_SPLITS:
            k = round(frac * last)
            xk, yk = arc[k]
            seg = _segment((xk, yk), (xk + sign * dash, yk))
            post = [(x + sign * dash, y) for x, y in arc[k + 1 :]]
            path = arc[:k] + seg + post
            path[-1] = p2
            yield path


# ── Piecewise gravity (moves that may cross a section border) ─────────────────


def _flight_samples(fl: Flight, t_end: float, end: Optional[Sample] = None) -> List[Sample]:
    return fl.samples(t_end, SAMPLE_STEP_PX, _MAX_SAMPLES, end)


def _landing_times(field: GravityField, p1: Sample, dx: float, dy: float, v: float) -> List[float]:
    """Flight times (> 0, latest first) of a launch at vy=v with constant vx
    that lands Δy higher and Δx away, under *field*.

    Column bands: exact closed form with the path-weighted gravity
    G = 2∫₀¹(1−σ)·g(x1+σΔx)dσ.  Row bands: exact roots of the piecewise y(t).
    """
    if field.axis == "y":
        times = Flight(field, p1, v, [(INF, 0.0)]).roots_y(p1[1] + dy)
        return sorted((t for t in times if t > _EPS), reverse=True)
    big_g = 2.0 * field.path_moment(p1[0], dx, 0.0, 1.0, 1.0)
    disc = v * v - 2.0 * big_g * dy
    if disc < 0.0:
        return []
    root = math.sqrt(disc)
    return [t for t in ((v + root) / big_g, (v - root) / big_g) if t > _EPS]


def _field_plain_trajectories(
    p1: Sample, p2: Sample, field: GravityField, jump_v: float, vmax: float
) -> Iterator[List[Sample]]:
    """:func:`_plain_trajectories` under piecewise gravity (same candidates)."""
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    launches: List[float] = [0.0] if dy < 0.0 else []
    launches += [jump_v * frac for frac in LAUNCH_FRACTIONS]
    for v in launches:
        for t in _landing_times(field, p1, dx, dy, v):
            vx = dx / t
            if abs(vx) > vmax + _EPS:
                continue
            yield _flight_samples(Flight(field, p1, v, [(INF, vx)]), t, p2)


def _field_double_jump_trajectories(
    p1: Sample, p2: Sample, field: GravityField, v1: float, v2: float, vmax: float
) -> Iterator[List[Sample]]:
    """:func:`_double_jump_trajectories` under piecewise gravity.

    Column bands: the second jump happens at a fraction φ of the flight; then
    y(T) − y1 = a·T − c·T² with a = v1·φ + v2·(1−φ) and
    c = ∫₀^φ(φ−σ)g dσ + ∫_φ¹(1−σ)g dσ, solved exactly for T.  Row bands: τ is
    sampled over the first arc (until it is back at launch height) and the
    second arc's landing times are exact roots.
    """
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    steps = _DOUBLE_JUMP_TAU_STEPS
    plans: List[Tuple[float, float]] = []  # (tau, total time)
    if field.axis == "y":
        first = Flight(field, p1, v1, [(INF, 0.0)])
        back = first.roots_y(p1[1])
        if not back:
            return
        tau_max = back[-1]
        for k in range(1, steps + 1):
            tau = tau_max * k / steps
            mid = (0.0, first.y(tau))
            for s in sorted(Flight(field, mid, v2, [(INF, 0.0)]).roots_y(p2[1]), reverse=True):
                if s > _EPS:
                    plans.append((tau, tau + s))
    else:
        for k in range(1, steps + 1):
            phi = k / (steps + 1)
            a = v1 * phi + v2 * (1.0 - phi)
            c = field.path_moment(p1[0], dx, 0.0, phi, phi) + field.path_moment(
                p1[0], dx, phi, 1.0, 1.0
            )
            disc = a * a - 4.0 * c * dy
            if c <= 0.0 or disc < 0.0:
                continue
            root = math.sqrt(disc)
            for t in ((a + root) / (2.0 * c), (a - root) / (2.0 * c)):
                if t > _EPS:
                    plans.append((phi * t, t))
    for tau, total in plans:
        vx = dx / total
        if abs(vx) > vmax + _EPS:
            continue
        f1 = Flight(field, p1, v1, [(INF, vx)])
        mid = f1.point(tau)
        f2 = Flight(field, mid, v2, [(INF, vx)])
        yield _flight_samples(f1, tau) + _flight_samples(f2, total - tau, p2)[1:]


def _field_dash_trajectories(
    p1: Sample, p2: Sample, field: GravityField, jump_v: float, vmax: float, dash_px: float
) -> Iterator[List[Sample]]:
    """:func:`_dash_trajectories` under piecewise gravity.

    Row bands reuse the plain piecewise arcs (vertical motion ignores x).
    Column bands: the dash at fraction f of the flight shifts every later x by
    the dash, so G = 2[∫₀^f(1−σ)g(x1+σD)dσ + ∫_f¹(1−σ)g(x1+dash+σD)dσ] with
    D the dash-shortened Δx; exact per split.
    """
    if field.axis != "x":
        yield from _dash_trajectories(p1, p2, field.g_min, jump_v, vmax, dash_px, field)
        return
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    sign = 1.0 if dx >= 0 else -1.0
    dash = min(dash_px, abs(dx))
    shift = sign * dash
    reduced = dx - shift
    launches: List[float] = [0.0] if dy < 0.0 else []
    launches += [jump_v * frac for frac in LAUNCH_FRACTIONS]
    for frac in _DASH_SPLITS:
        big_g = 2.0 * (
            field.path_moment(p1[0], reduced, 0.0, frac, 1.0)
            + field.path_moment(p1[0] + shift, reduced, frac, 1.0, 1.0)
        )
        for v in launches:
            disc = v * v - 2.0 * big_g * dy
            if disc < 0.0:
                continue
            root = math.sqrt(disc)
            for t in ((v + root) / big_g, (v - root) / big_g):
                if t <= _EPS:
                    continue
                vx = reduced / t
                if abs(vx) > vmax + _EPS:
                    continue
                tk = frac * t
                f1 = Flight(field, p1, v, [(INF, vx)])
                xk, yk = f1.point(tk)
                pre = _flight_samples(f1, tk) if tk > 0.0 else [p1]
                seg = _segment((xk, yk), (xk + shift, yk))
                f2 = Flight(field, (xk + shift, yk), f1.vy(tk), [(INF, vx)])
                post = _flight_samples(f2, t - tk, p2)
                yield pre[:-1] + seg + post[1:]


# ── Walls and wall jumps ─────────────────────────────────────────────────────

WALL_TILE_EXCLUDED = (0, 2)
"""Tile values that are never part of a wall: empty and hazard."""


def find_walls(
    grid: Sequence[Sequence[int]],
    p1: Sample,
    p2: Sample,
    profile: "GameProfile",
    tile_size: float,
    min_height_tiles: Optional[int] = None,
) -> List[Tuple[int, int, int]]:
    """Solid vertical runs (``col, row_top, row_bottom``) usable for a wall jump.

    A wall is ≥ ``min_height_tiles`` (default: the player's height in tiles)
    stacked solid tiles in one column (hazards do not count), within
    ``push_px`` (plus a tile) of the hop's horizontal span, overlapping the
    hop's height range (plus the player's height).  Sorted by distance to the
    hop's midpoint.  This is only an existence test; ``trajectory_clear`` with
    ``ability="wall_jump"`` decides whether the walls can actually be used.
    """
    height = len(grid)
    if height == 0:
        return []
    width = len(grid[0])
    if min_height_tiles is None:
        min_height_tiles = profile.player_tiles()[1]
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
            if grid[r][c] in WALL_TILE_EXCLUDED:
                r += 1
                continue
            top = r
            while r + 1 < height and grid[r + 1][c] not in WALL_TILE_EXCLUDED:
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
    field: GravityField,
    tile_size: float,
) -> Iterator[List[Sample]]:
    """Exact wall-jump paths on the grid (see :mod:`symbolic.walljump`)."""
    from .walljump import wall_jump_paths

    yield from wall_jump_paths(grid, p1, p2, profile, field, tile_size)


def trajectory_candidates(
    grid: Sequence[Sequence[int]],
    p1: Sample,
    p2: Sample,
    profile: "GameProfile",
    gravity_scale: float = 1.0,
    ability: Optional[str] = None,
    gravity_field: Optional[GravityField] = None,
) -> Iterator[List[Sample]]:
    """Every candidate path (feet polylines) for the move.

    ``ability=None`` is a plain move (walk-off drop or jump at 100/85/70 % of
    jump_v, both flight-time roots).  An ability adds its own candidates on top
    of the plain ones; a disabled ability adds nothing.  Plain, dash and
    double-jump candidates are physics only; wall-jump candidates come from a
    search over the grid's walls (``symbolic/walljump.py``) and each one it
    yields is already collision-free.

    *gravity_field* (optional) gives the gravity per section; a move that may
    cross a border is integrated piecewise.  Without it the gravity is
    ``gravity_for(profile, gravity_scale)`` everywhere.
    """
    phys = profile.physics
    vmax = phys.run_speed
    g = move_gravity(p1, p2, profile, gravity_scale, None, gravity_field)
    if g is not None:
        yield from _plain_trajectories(p1, p2, g, phys.jump_v, vmax)
    elif gravity_field is not None:
        yield from _field_plain_trajectories(p1, p2, gravity_field, phys.jump_v, vmax)
    if ability is None or not ability_enabled(profile, ability):
        return
    ab = profile.abilities
    if ability == "wall_jump":
        field = gravity_field or GravityField.uniform(gravity_for(profile, gravity_scale))
        yield from _wall_jump_trajectories(grid, p1, p2, profile, field, float(profile.tile_size))
        return
    g = move_gravity(p1, p2, profile, gravity_scale, ability, gravity_field)
    if ability == "double_jump":
        if g is not None:
            yield from _double_jump_trajectories(
                p1, p2, g, phys.jump_v, ab.double_jump.jump_v, vmax
            )
        elif gravity_field is not None:
            yield from _field_double_jump_trajectories(
                p1, p2, gravity_field, phys.jump_v, ab.double_jump.jump_v, vmax
            )
    elif ability == "dash" and ab.dash.air:
        if g is not None:
            yield from _dash_trajectories(p1, p2, g, phys.jump_v, vmax, ab.dash.distance_px)
        elif gravity_field is not None:
            yield from _field_dash_trajectories(
                p1, p2, gravity_field, phys.jump_v, vmax, ab.dash.distance_px
            )


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
    gravity_field: Optional[GravityField] = None,
) -> bool:
    """True if some physically valid trajectory from p1 to p2 is collision-free.

    p1/p2 are feet points (Y-up px) on the source / target surfaces.  The player
    hitbox (``profile.player``) is swept along every candidate of
    :func:`trajectory_candidates`; the move is clear if any candidate touches no
    solid or hazard tile (landing on the target surface is allowed).
    *gravity_field* as in :func:`trajectory_candidates`.
    """
    ts = float(tile_size or profile.tile_size)
    hitbox = (profile.player.width_px, profile.player.height_px)
    for samples in trajectory_candidates(
        grid, p1, p2, profile, gravity_scale, ability, gravity_field
    ):
        if not trajectory_hits(grid, samples, p2, ts, hitbox):
            return True
    return False
