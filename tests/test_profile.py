"""Game Profile: file I/O and the GAME PROFILE prompt block."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from game import (
    PROFILE_FILENAME,
    GameProfile,
    load_profile,
    profile_path_for,
    save_profile,
)
from llm import build_generate_messages


def test_round_trip(tmp_path):
    p = GameProfile(name="Moon Hopper")
    p.physics.gravity = 500
    p.abilities.double_jump.enabled = True
    p.player.description = "a small astronaut"
    path = save_profile(p, tmp_path / "sub" / PROFILE_FILENAME)
    assert load_profile(path) == p
    assert json.loads(path.read_text())["abilities"]["double_jump"]["enabled"] is True


def test_missing_file_gives_defaults(tmp_path):
    assert load_profile(tmp_path / "nope.json") == GameProfile()


def test_invalid_json_is_a_clear_error(tmp_path):
    bad = tmp_path / PROFILE_FILENAME
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_profile(bad)


def test_invalid_values_are_a_clear_error(tmp_path):
    bad = tmp_path / PROFILE_FILENAME
    bad.write_text(json.dumps({"physics": {"gravity": -1}}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        load_profile(bad)


def test_profile_path_for():
    assert profile_path_for("/a/b/level.ldtk") == Path("/a/b") / PROFILE_FILENAME


def test_summary_lists_enabled_abilities_only():
    p = GameProfile()
    s = p.summary_for_prompt()
    # v4 Part 28 defaults: a ~4-tile jump
    assert "gravity=1800" in s and "jump_v=500" in s and "run_speed=220" in s
    assert "69 px (~4.3 tiles)" in s and "122 px (~7.6 tiles)" in s  # derived apex and flat reach
    for name in ("double_jump", "dash", "wall_jump"):
        assert name not in s

    p.abilities.dash.enabled = True
    s = p.summary_for_prompt()
    assert '"dash"' in s and "186 px" in s  # 122 px plain reach + 64 px dash
    assert "double_jump" not in s and "wall_jump" not in s


def test_generate_messages_carry_profile_block():
    p = GameProfile()
    p.abilities.wall_jump.enabled = True
    [msg] = build_generate_messages("A tower.", None, p)
    assert msg["content"].startswith("A tower.")
    assert "GAME PROFILE" in msg["content"] and "wall_jump" in msg["content"]


def test_system_prompt_is_profile_independent():
    from llm import SYSTEM_PROMPT

    assert "980" not in SYSTEM_PROMPT and "183" not in SYSTEM_PROMPT
    assert "1800" not in SYSTEM_PROMPT and "69 px" not in SYSTEM_PROMPT
    assert "GAME PROFILE" in SYSTEM_PROMPT and '"ability"' in SYSTEM_PROMPT


# ─────────────────────────────────────────────────────────────────────────────
# v4 Part 28: tighter default physics (~4-tile jump)
# ─────────────────────────────────────────────────────────────────────────────


def test_default_physics_is_a_short_hop():
    from symbolic.physics import double_jump_max_time, flat_jump_reach, max_jump_height

    p = GameProfile()
    ph, ab, ts = p.physics, p.abilities, p.tile_size
    g, v, run = ph.gravity, ph.jump_v, ph.run_speed
    h = max_jump_height(g, v) / ts
    reach = flat_jump_reach(g, v, run) / ts
    assert 4.0 < h < 4.5  # a full jump: about 4 tiles up
    assert 7.0 < reach < 8.0  # about 7 tiles across
    hop = max_jump_height(g, 0.7 * v) / ts  # the shortest hop the verifier tries
    assert 1.8 < hop < 2.4
    # the shortest hop fits under 4 empty rows, a full jump under 6
    body = p.player.height_px / ts
    assert hop + body < 4 < h + body < 6
    # each ability adds a sensible amount
    assert 1.8 < max_jump_height(g, ab.double_jump.jump_v) / ts < 2.4  # about +2 tiles up
    assert ab.dash.distance_px / ts == 4  # +4 tiles across
    assert double_jump_max_time(0.0, g, v, ab.double_jump.jump_v) * run / ts > 12
    assert ab.wall_jump.max_climb_px / ts == 4 and ab.wall_jump.push_px / ts == 2
    # the comfort limits stay inside the physics
    d = p.design
    assert d.comfort_rise_tiles < h and d.comfort_hop_tiles < reach


def test_saved_profiles_keep_their_physics(tmp_path):
    old = {"version": 1, "name": "Old game",
           "physics": {"gravity": 980.0, "jump_v": 600.0, "run_speed": 200.0, "walk_step_px": 8.0},
           "abilities": {"double_jump": {"enabled": True, "jump_v": 500.0}}}
    path = tmp_path / PROFILE_FILENAME
    path.write_text(json.dumps(old), encoding="utf-8")
    p = load_profile(path)
    assert (p.physics.gravity, p.physics.jump_v, p.physics.run_speed) == (980.0, 600.0, 200.0)
    assert p.abilities.double_jump.jump_v == 500.0
    # only missing fields take the new defaults
    assert p.abilities.wall_jump == GameProfile().abilities.wall_jump
    partial = tmp_path / "partial" / PROFILE_FILENAME
    partial.parent.mkdir()
    partial.write_text(json.dumps({"physics": {"gravity": 980.0}}), encoding="utf-8")
    q = load_profile(partial)
    assert q.physics.gravity == 980.0 and q.physics.jump_v == GameProfile().physics.jump_v
    assert load_profile(tmp_path / "missing.json") == GameProfile()  # a new project
