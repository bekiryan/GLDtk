"""Deterministic normalisation of slightly-off LLM specs (local-model output).

Complexity on a 0-10 scale, Walk edges with tall steps, out-of-bounds nodes,
dangling entity references, entities on hazards, abilities the profile lacks,
and partial patching (keep the fixes that work, re-prompt with the rest).
"""

from __future__ import annotations

import json

import pytest

from game.profile import GameProfile
from llm import FakeProvider, LLMController
from llm.patcher import SpecNormalizePatch
from llm.validator import validate_spec
from spec import LevelSpec


def _codes(spec: LevelSpec) -> set:
    return {e.code for e in validate_spec(spec, GameProfile()).errors}


# ── complexity scale ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("given, expected", [(0.7, 0.7), (1, 1), (5, 0.5), (3, 0.3), (80, 0.8),
                                             (1000, 1.0), (-1, 0.0)])
def test_complexity_is_rescaled_to_unit_range(valid_spec_dict, given, expected):
    valid_spec_dict["complexity"] = given
    valid_spec_dict["sections"][0]["complexity"] = given
    valid_spec_dict["sections"][1]["params"] = {"complexity": given}
    spec = LevelSpec.model_validate(valid_spec_dict)
    assert spec.complexity == pytest.approx(expected)
    assert spec.sections[0].complexity == pytest.approx(expected)
    assert spec.sections[1].params.complexity == pytest.approx(expected)


def test_valid_spec_is_untouched_by_normalisation(valid_spec_dict):
    spec = LevelSpec.model_validate(valid_spec_dict)
    same, fixes = SpecNormalizePatch().apply_to_spec(spec)
    assert fixes == [] and same is spec


# ── spec-level fixes ─────────────────────────────────────────────────────────


def test_out_of_bounds_nodes_are_clamped(valid_spec_dict):
    valid_spec_dict["nodes"][-1]["x"] = 60 * 16 + 40
    valid_spec_dict["nodes"][-1]["y"] = 20 * 16 + 32
    valid_spec_dict["nodes"][1]["y"] = -16
    spec, fixes = SpecNormalizePatch().apply_to_spec(LevelSpec.model_validate(valid_spec_dict))
    assert len(fixes) == 2
    exit_node = next(n for n in spec.nodes if n.id == "exit")
    assert exit_node.x == 60 * 16 - exit_node.w and exit_node.y == 20 * 16 - 3 * 16
    assert next(n for n in spec.nodes if n.id == "p1").y == 0
    assert "NODE_OUT_OF_BOUNDS" not in _codes(spec)


def test_unknown_entity_refs_are_resolved_or_dropped(valid_spec_dict):
    ents = valid_spec_dict["entities"]
    ents.append({"id": "key1", "kind": "Key", "anchor": "p2", "section": "meadow",
                 "opens": "vault"})
    next(e for e in ents if e["id"] == "chest")["linked_to"] = "key0, key1"
    spec, _ = SpecNormalizePatch().apply_to_spec(LevelSpec.model_validate(valid_spec_dict))
    by_id = {e.id: e for e in spec.entities}
    assert by_id["chest"].linked_to == "key1"
    assert by_id["key1"].opens is None
    assert "UNKNOWN_ENTITY_REF" not in _codes(spec)


def test_entity_on_hazard_moves_to_nearest_safe_node(valid_spec_dict):
    next(e for e in valid_spec_dict["entities"] if e["id"] == "skeleton")["anchor"] = "spikes"
    spec, _ = SpecNormalizePatch().apply_to_spec(LevelSpec.model_validate(valid_spec_dict))
    skel = next(e for e in spec.entities if e.id == "skeleton")
    assert skel.anchor == "p3" and skel.section == "dungeon"  # same section, closest
    assert "ANCHOR_ON_HAZARD" not in _codes(spec)


# ── controller: patches that fix everything ──────────────────────────────────


def _run(responses, profile=None):
    fake = FakeProvider([json.dumps(r) if isinstance(r, dict) else r for r in responses])
    ctrl = LLMController(provider=fake, profile=profile, max_retries=3)
    return ctrl.generate_spec("x"), ctrl, fake


def test_walk_with_tall_step_is_retyped(valid_spec_dict):
    walk = next(e for e in valid_spec_dict["edges"] if e["type"] == "Walk")  # p1 → p2
    next(n for n in valid_spec_dict["nodes"] if n["id"] == "p2")["y"] = 96  # 32 px up
    spec, ctrl, fake = _run([valid_spec_dict])
    assert len(fake.calls) == 1 and "EdgeReclassifyPatch" in ctrl.stats.patches_applied
    edge = next(e for e in spec.edges if (e.from_, e.to) == (walk["from"], walk["to"]))
    assert edge.type == "Jump"


def test_walk_drop_becomes_fall(valid_spec_dict):
    next(n for n in valid_spec_dict["nodes"] if n["id"] == "p2")["y"] = 32  # 32 px down
    spec, _, fake = _run([valid_spec_dict])
    assert len(fake.calls) == 1
    assert next(e for e in spec.edges if (e.from_, e.to) == ("p1", "p2")).type == "Fall"


def test_ability_the_profile_lacks_is_dropped(valid_spec_dict):
    valid_spec_dict["edges"][0]["ability"] = "double_jump"
    spec, ctrl, fake = _run([valid_spec_dict], profile=GameProfile())
    assert len(fake.calls) == 1 and "AbilityAssignPatch" in ctrl.stats.patches_applied
    assert spec.edges[0].ability is None


def test_missing_exit_entity_and_hazard_anchor_fixed_together(valid_spec_dict):
    d = valid_spec_dict
    d["entities"] = [e for e in d["entities"] if e["kind"] != "Exit"]
    next(e for e in d["entities"] if e["id"] == "skeleton")["anchor"] = "spikes"
    d["complexity"] = 5
    spec, ctrl, fake = _run([d])
    assert len(fake.calls) == 1
    assert {"SpecNormalizePatch", "TerminalEntityPatch"} <= set(ctrl.stats.patches_applied)
    assert any(e.kind == "Exit" and e.anchor == "exit" for e in spec.entities)
    assert spec.complexity == 0.5


# ── controller: partial patching ─────────────────────────────────────────────


def test_partial_patch_reprompts_with_only_the_remaining_errors(valid_spec_dict):
    bad = json.loads(json.dumps(valid_spec_dict))
    bad["edges"][2]["type"] = "Jump"  # p2 → p3 goes down: patchable
    bad["entities"] = [e for e in bad["entities"] if e["kind"] != "Start"]  # patchable
    bad["nodes"][1]["section"] = "castle"  # not patchable
    spec, ctrl, fake = _run([bad, valid_spec_dict])
    assert len(fake.calls) == 2
    previous = json.loads(fake.calls[1].messages[-2]["content"])  # the assistant turn
    assert next(e for e in previous["edges"] if e["from"] == "p2")["type"] == "Fall"
    assert sum(e["kind"] == "Start" for e in previous["entities"]) == 1
    request = fake.calls[1].messages[-1]["content"]
    assert "castle" in request
    assert "Change the edge type" not in request and "'Start' entit" not in request
    assert ctrl.stats.success


def test_one_unfixable_jump_keeps_the_other_patches(valid_spec_dict, monkeypatch):
    from llm.patcher import SteppingStonePatch
    from llm.validator import parse_and_validate_spec

    monkeypatch.setattr(SteppingStonePatch, "MAX_STONES", 0)  # no jump can be split
    d = valid_spec_dict
    next(n for n in d["nodes"] if n["id"] == "exit")["x"] = 800  # p3 → exit: impossible
    next(e for e in d["edges"] if e["from"] == "start")["type"] = "Fall"  # goes up: fixable
    d["entities"] = [e for e in d["entities"] if e["kind"] != "Start"]  # fixable
    result = parse_and_validate_spec(json.dumps(d), GameProfile())
    assert {"FALL_GOES_UP", "IMPOSSIBLE_JUMP"} <= {e.code for e in result.errors}

    ctrl = LLMController(provider=FakeProvider([]), profile=GameProfile())
    spec, applied, after = ctrl._patch_spec(result)
    assert "EdgeReclassifyPatch" in applied and "TerminalEntityPatch" in applied
    assert next(e for e in spec.edges if e.from_ == "start").type == "Jump"
    assert {e.code for e in after.errors} == {"IMPOSSIBLE_JUMP"}
