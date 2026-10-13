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
from typing import TYPE_CHECKING, Optional, Tuple, Union

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
