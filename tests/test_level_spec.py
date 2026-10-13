"""LevelSpec schema, round-trip and graph conversion."""

from __future__ import annotations

import json

import pytest

from spec import LevelSpec
from symbolic.schema import EdgeType, LevelEdge, LevelNode, NodeType, Vec2


def _walk(node):
    """Yield every dict / key in a nested JSON structure."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_json_schema_is_inlined_and_strict():
    schema = LevelSpec.json_schema()
    text = json.dumps(schema)
    assert "$ref" not in text
    assert "$defs" not in text
    assert "anyOf" not in text
    assert schema["required"] == [
        "title", "size", "sections", "nodes", "edges", "entities", "requirements"
    ]
    for obj in _walk(schema):
        if obj.get("type") == "object":
            assert obj["additionalProperties"] is False
            assert set(obj["required"]) == set(obj["properties"])


def test_json_schema_optionals_are_nullable():
    ent = LevelSpec.json_schema()["properties"]["entities"]["items"]["properties"]
    assert ent["facing"]["type"] == ["string", "null"]
    assert None in ent["facing"]["enum"]
    assert ent["starting_items"]["type"] == ["array", "null"]
    assert ent["kind"]["type"] == "string"
    edge = LevelSpec.json_schema()["properties"]["edges"]["items"]["properties"]
    assert "from" in edge and "from_" not in edge


def test_valid_fixture_round_trips(valid_spec_dict):
    spec = LevelSpec.model_validate(valid_spec_dict)
    dumped = json.loads(spec.model_dump_json(by_alias=True))
    again = LevelSpec.model_validate(dumped)
    assert again == spec
    assert dumped["edges"][0]["from"] == "start"
    assert spec.entities[0].starting_weapon == "sword"
    assert spec.entities[1].facing is None


def test_to_graph_derives_deltas_and_sections(valid_spec_dict):
    # Poison any LLM-provided dx/dy: they must be ignored.
    valid_spec_dict["edges"][0]["dx"] = 9999
    spec = LevelSpec.model_validate(valid_spec_dict)
    graph = spec.to_graph()
    assert len(graph.nodes()) == 6
    edge = graph.get_edge("start", "p1")
    # start surface (24, 32) → p1 surface (120, 64)
    assert edge.dx == pytest.approx(96.0)
    assert edge.dy == pytest.approx(32.0)
    assert edge.edge_type == EdgeType.JUMP
    assert edge.v_launch == (82.0, 600.0)
    assert graph.get_node("spikes").metadata["section"] == "dungeon"
    assert graph.get_node("start").node_type == NodeType.START


def test_with_graph_assigns_sections_to_added_nodes(valid_spec_dict):
    spec = LevelSpec.model_validate(valid_spec_dict)
    graph = spec.to_graph()
    # Simulate a SteppingStonePatch-style insertion between p3 (dungeon) and exit.
    stone = LevelNode("stone_x", NodeType.PLATFORM, Vec2(400, 48), Vec2(48, 16))
    graph.remove_edge("p3", "exit")
    graph.add_node(stone)
    graph.add_edge(LevelEdge("p3", "stone_x", EdgeType.JUMP, 48, 16, (40.0, 600.0)))
    graph.add_edge(LevelEdge("stone_x", "exit", EdgeType.JUMP, 48, 16, (40.0, 600.0)))
    # An isolated added Start-type node falls back to the first section.
    graph.add_node(LevelNode("auto_x", NodeType.START, Vec2(0, 0), Vec2(48, 16)))

    patched = spec.with_graph(graph)
    by_id = {n.id: n for n in patched.nodes}
    assert by_id["stone_x"].section == "dungeon"
    assert by_id["auto_x"].section == "meadow"
    assert by_id["p1"].section == "meadow"
    assert {(e.from_, e.to) for e in patched.edges} >= {("p3", "stone_x"), ("stone_x", "exit")}
    # Everything else is preserved; the original spec is untouched.
    assert patched.entities == spec.entities
    assert patched.requirements == spec.requirements
    assert len(spec.nodes) == 6


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["size"].update(width_tiles=10),
        lambda d: d["size"].update(height_tiles=500),
        lambda d: d.update(sections=[]),
        lambda d: d["sections"][0].update(gravity_scale=5.0),
        lambda d: d["entities"][0].update(kind="Dragon"),
        lambda d: d["nodes"][0].update(type="Lava"),
    ],
)
def test_pydantic_rejects_out_of_contract_values(valid_spec_dict, mutate):
    mutate(valid_spec_dict)
    with pytest.raises(Exception):
        LevelSpec.model_validate(valid_spec_dict)
