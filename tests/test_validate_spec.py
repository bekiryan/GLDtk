"""Semantic LevelSpec validation — every new code fires on a minimal bad spec."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict

import pytest

from llm import PhysicsParams, TerminalEntityPatch, parse_and_validate_spec
from spec import LevelSpec


def _codes(d: Dict[str, Any]) -> list[str]:
    result = parse_and_validate_spec(json.dumps(d), PhysicsParams())
    return [e.code for e in result.errors]


def test_valid_fixtures_pass(valid_spec_dict, minimal_spec_dict):
    assert _codes(valid_spec_dict) == []
    assert _codes(minimal_spec_dict) == []


def _dup_node(d):
    d["nodes"].append(dict(d["nodes"][1]))


def _dup_entity(d):
    d["entities"].append(dict(d["entities"][1]))


def _dup_section(d):
    d["sections"].append(dict(d["sections"][0], order=5))


def _dup_requirement(d):
    d["requirements"].append(dict(d["requirements"][0]))


def _unknown_node_section(d):
    d["nodes"][1]["section"] = "castle"


def _unknown_entity_section(d):
    d["entities"][1]["section"] = "castle"


def _unknown_anchor(d):
    d["entities"][1]["anchor"] = "nowhere"


def _anchor_on_hazard(d):
    d["entities"][3]["anchor"] = "spikes"


def _unknown_linked_to(d):
    d["entities"][4]["linked_to"] = "ghost_door"


def _unknown_opens(d):
    d["entities"].append(
        {"id": "key1", "kind": "Key", "anchor": "p2", "section": "meadow",
         "description": "a key", "sprite": "", "opens": "vault"}
    )


def _out_of_bounds(d):
    d["nodes"][-1]["x"] = 60 * 16 + 1  # exit just past the right edge


def _negative_y(d):
    d["nodes"][1]["y"] = -16
    d["nodes"][2]["y"] = -16


def _no_start_entity(d):
    d["entities"] = [e for e in d["entities"] if e["kind"] != "Start"]


def _two_start_entities(d):
    d["entities"].append(dict(d["entities"][0], id="player2"))


def _start_entity_off_start(d):
    d["entities"][0]["anchor"] = "p1"


def _no_exit_entity(d):
    d["entities"] = [e for e in d["entities"] if e["kind"] != "Exit"]


def _exit_entity_off_exit(d):
    d["entities"][-1]["anchor"] = "p3"


def _no_requirements(d):
    d["requirements"] = []


def _unknown_edge_node(d):
    d["edges"].append({"from": "p3", "to": "ghost", "type": "Walk", "vx": 200, "vy": 0})


def _bad_size(d):
    d["size"]["width_tiles"] = 5


@pytest.mark.parametrize(
    "mutate, code",
    [
        (_dup_node, "DUPLICATE_ID"),
        (_dup_entity, "DUPLICATE_ID"),
        (_dup_section, "DUPLICATE_ID"),
        (_dup_requirement, "DUPLICATE_ID"),
        (_unknown_node_section, "UNKNOWN_SECTION"),
        (_unknown_entity_section, "UNKNOWN_SECTION"),
        (_unknown_anchor, "UNKNOWN_ANCHOR"),
        (_anchor_on_hazard, "ANCHOR_ON_HAZARD"),
        (_unknown_linked_to, "UNKNOWN_ENTITY_REF"),
        (_unknown_opens, "UNKNOWN_ENTITY_REF"),
        (_out_of_bounds, "NODE_OUT_OF_BOUNDS"),
        (_negative_y, "NODE_OUT_OF_BOUNDS"),
        (_no_start_entity, "START_ENTITY_MISMATCH"),
        (_two_start_entities, "START_ENTITY_MISMATCH"),
        (_start_entity_off_start, "START_ENTITY_MISMATCH"),
        (_no_exit_entity, "EXIT_ENTITY_MISMATCH"),
        (_exit_entity_off_exit, "EXIT_ENTITY_MISMATCH"),
        (_no_requirements, "NO_REQUIREMENTS"),
        (_unknown_edge_node, "UNKNOWN_EDGE_NODE"),
        (_bad_size, "INVALID_SPEC"),
    ],
)
def test_validation_code_fires(
    valid_spec_dict, mutate: Callable[[Dict[str, Any]], None], code: str
):
    mutate(valid_spec_dict)
    assert code in _codes(valid_spec_dict)


def test_graph_codes_still_fire(valid_spec_dict):
    valid_spec_dict["edges"][2]["type"] = "Jump"  # p2 → p3 goes down
    assert "JUMP_GOES_DOWN" in _codes(valid_spec_dict)


def test_parse_never_raises_and_reports_pydantic_text():
    for raw in ["", "not json", "[1, 2]", '{"title": 3}', "{" * 50]:
        result = parse_and_validate_spec(raw, PhysicsParams())
        assert not result.valid
        assert result.errors[0].code in {"INVALID_JSON", "INVALID_SPEC"}
    result = parse_and_validate_spec('{"title": "x"}', PhysicsParams())
    assert result.errors[0].code == "INVALID_SPEC"
    assert "size" in result.errors[0].message  # pydantic names the missing field


def test_markdown_fenced_output_is_accepted(load_fixture):
    raw = "```json\n" + load_fixture("valid_minimal.json") + "\n```"
    assert parse_and_validate_spec(raw, PhysicsParams()).valid


def test_terminal_entity_patch_fixes_start_and_exit(valid_spec_dict):
    _no_start_entity(valid_spec_dict)
    _exit_entity_off_exit(valid_spec_dict)
    spec = LevelSpec.model_validate(valid_spec_dict)
    patched = TerminalEntityPatch().apply_to_spec(spec)
    starts = [e for e in patched.entities if e.kind == "Start"]
    assert len(starts) == 1 and starts[0].anchor == "start"
    assert starts[0].section == "meadow"
    assert any(e.kind == "Exit" and e.anchor == "exit" for e in patched.entities)
    assert parse_and_validate_spec(
        patched.model_dump_json(by_alias=True), PhysicsParams()
    ).valid


def test_terminal_entity_patch_drops_extra_starts(valid_spec_dict):
    _two_start_entities(valid_spec_dict)
    patched = TerminalEntityPatch().apply_to_spec(LevelSpec.model_validate(valid_spec_dict))
    assert [e.id for e in patched.entities if e.kind == "Start"] == ["player"]
