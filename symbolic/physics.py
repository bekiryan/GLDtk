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
"""

from __future__ import annotations

import math
from typing import Optional, Tuple


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
    discriminant: float = jump_v ** 2 - 2.0 * gravity * dy

    if discriminant < 0.0:
        # Apex is below the target height; physically unreachable.
        return False

    sqrt_d: float = math.sqrt(discriminant)

    # Two candidate flight times from the quadratic formula.
    t1: float = (jump_v - sqrt_d) / gravity   # earlier (ascending phase)
    t2: float = (jump_v + sqrt_d) / gravity   # later   (descending phase)

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

    discriminant: float = jump_v ** 2 - 2.0 * gravity * dy
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
    discriminant: float = initial_vy ** 2 - 2.0 * gravity * dy
    if discriminant < 0.0:
        return None

    sqrt_d: float = math.sqrt(discriminant)
    t1: float = (initial_vy - sqrt_d) / gravity
    t2: float = (initial_vy + sqrt_d) / gravity

    positives = [t for t in (t1, t2) if t > 0.0]
    return min(positives) if positives else None
