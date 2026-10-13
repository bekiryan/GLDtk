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
    assert "gravity=980" in s and "jump_v=600" in s and "run_speed=200" in s
    assert "184 px" in s and "245 px" in s  # derived apex and flat reach
    for name in ("double_jump", "dash", "wall_jump"):
        assert name not in s

    p.abilities.dash.enabled = True
    s = p.summary_for_prompt()
    assert '"dash"' in s and "309 px" in s
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
    assert "GAME PROFILE" in SYSTEM_PROMPT and '"ability"' in SYSTEM_PROMPT
