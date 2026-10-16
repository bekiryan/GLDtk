"""Ability-aware validation, patching and the edge ability round trip."""

from __future__ import annotations

import json
from typing import Any, Dict

from game import GameProfile
from llm import FakeProvider, LLMController, SymbolicPatcher, validate_graph, validate_spec
from spec import LevelSpec

from conftest import legacy_profile  # hand-computed for the pre-Part-28 physics


def _high_exit(minimal: Dict[str, Any], ability=None) -> Dict[str, Any]:
    """Exit 200 px above Start: above the plain apex (184 px), below the
    double-jump stack (311 px)."""
    d = json.loads(json.dumps(minimal))
    d["nodes"][1]["y"] = 232
    d["edges"] = [
        {"from": "start", "to": "exit", "type": "Jump", "vx": 50, "vy": 600, "ability": ability}
    ]
    return d


def _double_jump_profile() -> GameProfile:
    p = legacy_profile()
    p.abilities.double_jump.enabled = True
    return p


def _codes(spec: LevelSpec, profile: GameProfile) -> list[str]:
    return [e.code for e in validate_spec(spec, profile).errors]


def test_edge_ability_round_trips_through_graph(minimal_spec_dict):
    spec = LevelSpec.model_validate(_high_exit(minimal_spec_dict, "double_jump"))
    graph = spec.to_graph()
    edge = graph.get_edge("start", "exit")
    assert edge.metadata == {"ability": "double_jump", "gravity_scale": 1.0}
    assert spec.with_graph(graph).edges[0].ability == "double_jump"
    assert "ability" in LevelSpec.json_schema()["properties"]["edges"]["items"]["required"]


def test_impossible_with_defaults_valid_with_double_jump(minimal_spec_dict):
    plain = LevelSpec.model_validate(_high_exit(minimal_spec_dict))
    result = validate_spec(plain, legacy_profile())
    [err] = result.errors
    assert err.code == "IMPOSSIBLE_JUMP"
    assert "no abilities" in err.message

    hint = validate_spec(plain, _double_jump_profile()).errors[0].message
    assert '"double_jump"' in hint  # names the ability that would work

    with_ability = LevelSpec.model_validate(_high_exit(minimal_spec_dict, "double_jump"))
    assert _codes(with_ability, _double_jump_profile()) == []


def test_ability_not_in_profile(minimal_spec_dict):
    spec = LevelSpec.model_validate(_high_exit(minimal_spec_dict, "double_jump"))
    result = validate_spec(spec, legacy_profile())
    [err] = result.errors
    assert err.code == "ABILITY_NOT_IN_PROFILE"
    assert "double_jump" in err.message and "without" in err.message


def test_section_gravity_scale_is_honoured(minimal_spec_dict):
    d = _high_exit(minimal_spec_dict)
    d["sections"][0]["gravity_scale"] = 0.5
    spec = LevelSpec.model_validate(d)
    assert spec.to_graph().get_edge("start", "exit").metadata["gravity_scale"] == 0.5
    assert _codes(spec, legacy_profile()) == []


def test_run_speed_caps_horizontal_reach(minimal_spec_dict):
    d = json.loads(json.dumps(minimal_spec_dict))
    d["nodes"][1]["x"] = 300  # 300 px flat gap > 245 px reach at run_speed 200
    d["edges"][0]["type"] = "Jump"
    assert _codes(LevelSpec.model_validate(d), legacy_profile()) == ["IMPOSSIBLE_JUMP"]


def test_patcher_assigns_ability_without_llm_call(minimal_spec_dict):
    fake = FakeProvider([json.dumps(_high_exit(minimal_spec_dict))])
    ctrl = LLMController(provider=fake, profile=_double_jump_profile(), max_retries=1)
    spec = ctrl.generate_spec("A tall jump.")
    assert len(fake.calls) == 1
    assert "AbilityAssignPatch" in ctrl.stats.patches_applied
    assert "SteppingStonePatch" not in ctrl.stats.patches_applied
    assert [n.id for n in spec.nodes] == ["start", "exit"]  # no stepping stone
    assert spec.edges[0].ability == "double_jump"
    assert "double_jump" in fake.calls[0].messages[0]["content"]  # GAME PROFILE block


def test_patcher_clears_missing_ability_and_falls_back(minimal_spec_dict):
    # dash is not in the profile; without it the jump needs a stepping stone.
    spec = LevelSpec.model_validate(_high_exit(minimal_spec_dict, "dash"))
    graph = spec.to_graph()
    profile = legacy_profile()
    result = SymbolicPatcher(profile).patch(graph, validate_graph(graph, profile).errors)
    assert result.success
    assert result.applied[-1] == "SteppingStonePatch" and "AbilityAssignPatch" in result.applied
    assert all("ability" not in e.metadata for e in result.graph.edges())
    # the original graph is untouched (copies, not in-place edits)
    assert graph.get_edge("start", "exit").metadata["ability"] == "dash"


def test_patcher_swaps_missing_ability_for_an_enabled_one(minimal_spec_dict):
    spec = LevelSpec.model_validate(_high_exit(minimal_spec_dict, "wall_jump"))
    graph = spec.to_graph()
    profile = _double_jump_profile()
    result = SymbolicPatcher(profile).patch(graph, validate_graph(graph, profile).errors)
    assert result.success
    assert result.graph.get_edge("start", "exit").metadata["ability"] == "double_jump"
