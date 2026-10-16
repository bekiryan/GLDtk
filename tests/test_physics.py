"""Ability-aware physics oracle: hand-computed cases (g=980, v=600, run=200 —
the pre-Part-28 defaults, pinned with ``legacy_profile()``)."""

from __future__ import annotations

import time

import pytest

from game import GameProfile
from symbolic.physics import (
    best_ability,
    check_dash_jump,
    check_double_jump,
    check_jump_arc,
    check_wall_jump,
    gravity_for,
    reachable,
)

from conftest import legacy_profile  # hand-computed for the pre-Part-28 physics

G, V, VMAX = 980.0, 600.0, 200.0
APEX = V**2 / (2 * G)  # 183.67 px
REACH = VMAX * 2 * V / G  # 244.90 px
O = (0.0, 0.0)


def _profile(**enabled: bool) -> GameProfile:
    p = legacy_profile()
    for name, on in enabled.items():
        getattr(p.abilities, name).enabled = on
    return p


# ── plain jump ──────────────────────────────────────────────────────────────


def test_plain_jump_max_height():
    assert check_jump_arc(O, (0, APEX - 0.5), G, V, VMAX)
    assert not check_jump_arc(O, (0, APEX + 0.5), G, V, VMAX)


def test_plain_jump_flat_reach():
    assert check_jump_arc(O, (REACH - 0.5, 0), G, V, VMAX)
    assert not check_jump_arc(O, (REACH + 0.5, 0), G, V, VMAX)
    assert reachable(O, (REACH - 0.5, 0), legacy_profile())
    assert not reachable(O, (REACH + 0.5, 0), legacy_profile())


# ── double jump ─────────────────────────────────────────────────────────────


def test_double_jump_height_bounds():
    v2 = 500.0
    stacked = APEX + v2**2 / (2 * G)  # 311.2 px
    assert check_double_jump(O, (16, APEX + 50), G, V, v2, VMAX)  # above single apex
    assert check_double_jump(O, (0, stacked - 0.5), G, V, v2, VMAX)
    assert not check_double_jump(O, (0, stacked + 0.5), G, V, v2, VMAX)


def test_double_jump_extends_flat_reach_and_includes_plain():
    v2 = 500.0
    assert check_double_jump(O, (REACH + 100, 0), G, V, v2, VMAX)
    assert check_double_jump(O, (REACH - 10, 0), G, V, v2, VMAX)  # plain move still ok
    # closed form: T* = τ* + (v2 + √D)/g with gτ*−v = √((v²+v2²)/2) → reach ≈ 449.9 px
    assert check_double_jump(O, (449.0, 0), G, V, v2, VMAX)
    assert not check_double_jump(O, (451.0, 0), G, V, v2, VMAX)


def test_double_jump_is_fast():
    t0 = time.perf_counter()
    for i in range(2000):
        check_double_jump(O, (i % 500, (i % 300) - 50), G, V, 500.0, VMAX)
    assert (time.perf_counter() - t0) / 2000 < 1e-3


# ── dash ────────────────────────────────────────────────────────────────────


def test_dash_extends_flat_reach_by_distance():
    d = 64.0
    assert check_dash_jump(O, (REACH + d - 0.5, 0), G, V, VMAX, d)
    assert not check_dash_jump(O, (REACH + d + 0.5, 0), G, V, VMAX, d)
    assert check_dash_jump(O, (-(REACH + d - 0.5), 0), G, V, VMAX, d)  # leftwards
    assert check_dash_jump(O, (30, 0), G, V, VMAX, d)  # |Δx| < dash → clamped to 0
    assert not check_dash_jump(O, (0, APEX + 1), G, V, VMAX, d)  # no height bonus


def test_ground_only_dash_gives_no_gap_bonus():
    p = _profile(dash=True)
    p.abilities.dash.air = False
    assert not reachable(O, (REACH + 30, 0), p, ability="dash")


# ── wall jump ───────────────────────────────────────────────────────────────


def test_wall_jump_envelope():
    climb, push = 160.0, 48.0
    args = (G, 550.0, climb, push, V, VMAX)
    assert check_wall_jump(O, (100, APEX + climb - 1), *args)
    assert not check_wall_jump(O, (100, APEX + climb + 1), *args)
    assert check_wall_jump(O, (REACH + push - 1, 0), *args)
    assert not check_wall_jump(O, (REACH + push + 1, 0), *args)


# ── gravity scale ───────────────────────────────────────────────────────────


def test_gravity_scale_half_reaches_higher_and_farther():
    p = legacy_profile()
    assert gravity_for(p, 0.5) == pytest.approx(490.0)
    high, far = (0, APEX + 50), (REACH + 50, 0)
    assert not reachable(O, high, p, 1.0)
    assert not reachable(O, far, p, 1.0)
    assert reachable(O, high, p, 0.5)
    assert reachable(O, far, p, 0.5)


# ── best_ability ────────────────────────────────────────────────────────────


def test_best_ability_order_and_false():
    p = _profile(dash=True, double_jump=True, wall_jump=True)
    assert best_ability(O, (100, 50), p) is None  # plain jump
    assert best_ability(O, (280, 0), p) == "dash"  # dash before double_jump
    assert best_ability(O, (0, 250), p) == "double_jump"  # too high for dash
    assert best_ability(O, (0, 330), p) == "wall_jump"  # above double-jump stack
    assert best_ability(O, (0, 400), p) is False


def test_best_ability_respects_disabled_abilities():
    assert best_ability(O, (280, 0), legacy_profile()) is False
    assert best_ability(O, (280, 0), _profile(double_jump=True)) == "double_jump"
    assert not reachable(O, (280, 0), legacy_profile(), ability="dash")  # disabled
    with pytest.raises(ValueError):
        reachable(O, (10, 0), legacy_profile(), ability="fly")
