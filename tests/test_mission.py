"""Mission graph and space graph (v4 Part 23): ``mission/`` and its hooks.

Offline: the grammar on its own, two terrain fixtures built through
``build_level`` with a FakeProvider ("a crypt with a key", "a hub with 2
keys"), and a small open-air spec whose Exit needs a double jump.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import networkx as nx
import pytest

import server
from api.models import GenerateRequest
from game import GameProfile
from layout.spec_entities import place_spec_entities
from layout.spec_layout import layout_spec
from layout.verify import verify_grid
from llm import FakeProvider, LLMController
from mission import map_space, mission_of, plan_graph, requests_from, rewrite, verify_mission
from mission.grammar import RULES
from mission.graph import MissionGraph
from pipeline.level import build_level
from spec import LevelSpec
from spec.level_spec import MissionSpec


def _build(load_fixture, name: str, profile=None):
    ctrl = LLMController(provider=FakeProvider([load_fixture(name)]))
    return build_level("x", ctrl, profile=profile, seed=0)


def _feat(built, kind: str):
    return [f for f in built.mission["features"] if f["kind"] == kind]


def _ents(built) -> Dict[str, Any]:
    return {e.metadata["spec_id"]: e for e in built.spec_layout.ir.entities if "spec_id" in e.metadata}


# ─────────────────────────────────────────────────────────────────────────────
# Grammar
# ─────────────────────────────────────────────────────────────────────────────


ALL = MissionSpec(rules=["lock_key_cycle", "lock_key_cycle", "ability_gate", "treasure_branch",
                         "shortcut_loop", "boss_before_exit"], ability="dash")


def test_grammar_is_deterministic_and_solvable_after_every_rule():
    reqs, warns = requests_from(ALL, ["dash"])
    assert warns == [] and [r for r, _ in reqs] == [
        "lock_key_cycle", "lock_key_cycle", "ability_gate", "treasure_branch", "shortcut_loop",
        "boss_before_exit"]
    for n in range(1, len(reqs) + 1):
        g, w = rewrite(reqs[:n], seed=7)
        assert w == [] and g.solvable(), (n, g.to_dict())
        assert g.to_dict() == rewrite(reqs[:n], seed=7)[0].to_dict()
    kinds = sorted(n.kind for n in g.nodes.values())
    assert kinds == sorted(["start", "exit", "key", "lock", "key", "lock", "ability", "gate",
                            "treasure", "shortcut", "boss"])
    line = g.main_line()
    assert line[0] == "start" and line[-2:] == ["boss", "exit"]
    # every key's branch leaves the main line before its lock
    for lock in g.of_kind("lock", "gate"):
        origin = g.branch_origin(g.item_node(lock).id)
        assert line.index(origin) < line.index(lock.id)


def test_each_rule_keeps_the_graph_solvable_and_an_unsolvable_graph_is_detected():
    for name, rule in RULES.items():
        g = MissionGraph.initial()
        RULES["lock_key_cycle"](g, None, {})  # so shortcut_loop has a lock
        params = {"ability": "dash"} if name == "ability_gate" else {}
        assert rule(g, None, params) and g.solvable(), name
    g = MissionGraph.initial()
    g.add("lock9", "lock", "nokey")  # a lock whose key does not exist
    g.unlink("start", "exit")
    g.link("start", "lock9")
    g.link("lock9", "exit")
    assert not g.solvable()


def test_ability_gate_needs_an_enabled_ability():
    reqs, warns = requests_from(MissionSpec(rules=["ability_gate"]), [])
    assert reqs == [] and "enables" in warns[0]
    reqs, warns = requests_from(MissionSpec(rules=["ability_gate"], ability="dash"), ["double_jump"])
    assert reqs == [] and "dash" in warns[0]


# ─────────────────────────────────────────────────────────────────────────────
# A crypt with a key
# ─────────────────────────────────────────────────────────────────────────────


def test_crypt_key_comes_before_its_door_and_the_item_check_passes(load_fixture):
    built = _build(load_fixture, "mission_crypt_key.json")
    assert built.report.ok and built.report.softlock.ok
    assert built.mission["realised"], built.mission
    lock = _feat(built, "lock")[0]
    assert (lock["key_section"], lock["lock_section"]) == ("maze", "boss")
    ents = _ents(built)
    key, door = ents[lock["key"]], ents[lock["lock"]]
    assert key.metadata["opens"] == door.metadata["spec_id"]
    assert door.metadata["linked_to"] == key.metadata["spec_id"]
    assert key.col < door.col  # horizontal level: the key comes first along the path
    items = built.report.softlock.items
    assert any(f"key:{lock['key']}" in keys for keys in items.doors.values())
    by_text = {i.text: i for i in built.fidelity.items}
    assert by_text["A key in the catacombs opens the lich chamber gate."].status == "satisfied"


def test_moving_the_key_behind_its_door_is_reported_by_the_softlock_check(load_fixture):
    built = _build(load_fixture, "mission_crypt_key.json")
    lay = built.spec_layout
    lock = _feat(built, "lock")[0]
    ents = _ents(built)
    key, door, exit_ = ents[lock["key"]], ents[lock["lock"]], ents["door"]
    # put the key in the boss arena, behind its own door
    key.col, key.row = exit_.col - 3, exit_.row
    key.metadata["anchor"] = exit_.metadata["anchor"]
    rep = verify_grid(lay, GameProfile(), built.spec)
    assert not rep.ok and "SOFTLOCK" in [e.code for e in rep.errors]
    assert any(t.kind == "key_behind_door" for t in rep.softlock.traps)
    assert door.metadata["spec_id"] in {e.metadata.get("spec_id") for e in lay.ir.entities}


# ─────────────────────────────────────────────────────────────────────────────
# A hub with two keys and a shortcut loop
# ─────────────────────────────────────────────────────────────────────────────


def test_hub_with_two_keys_realises_both_locks_and_the_loop(load_fixture):
    built = _build(load_fixture, "mission_hub_two_keys.json")
    assert built.report.ok and built.mission["realised"], built.mission
    locks = _feat(built, "lock")
    assert [(f["key_section"], f["lock_section"]) for f in locks] == [("hub", "maze"), ("maze", "boss")]
    ents = _ents(built)
    for f in locks:
        assert ents[f["key"]].col < ents[f["lock"]].col
    statuses = {i.id: i.status for i in built.fidelity.items}
    assert statuses["R8"] == statuses["R9"] == statuses["R10"] == "satisfied"
    assert set(built.mission["rules"]) == {"lock_key_cycle", "shortcut_loop", "boss_before_exit"}
    assert {"nodes", "edges"} <= set(built.mission)


def test_shortcut_is_a_cycle_back_to_the_hub_in_the_movement_graph(load_fixture):
    built = _build(load_fixture, "mission_hub_two_keys.json")
    loop = _feat(built, "loop")[0]
    assert loop["ok"] and loop["section"] == "hub" and loop["from_section"] == "maze"
    lay, rep = built.spec_layout, built.report
    from layout.verify import SpanIndex, _anchor_span

    index = SpanIndex(rep.graph, lay)
    ents = _ents(built)
    a = _anchor_span(ents[loop["door"]], lay, index)
    b = _anchor_span(ents[loop["hub_door"]], lay, index)
    g = rep.graph.nx_graph
    cycle = nx.shortest_path(g, a, b) + nx.shortest_path(g, b, a)[1:]
    hub = next(s for s in lay.sections if s.id == "hub")
    cols = [rep.graph.get_node(s).metadata["col_start"] for s in cycle]
    assert cycle[0] == cycle[-1] and any(hub.col_start <= c <= hub.col_end for c in cols)


def test_generate_response_returns_the_mission_graph(tmp_path, load_fixture):
    raw = load_fixture("mission_hub_two_keys.json")
    old = server.provider_factory
    server.provider_factory = lambda req: FakeProvider([raw])
    try:
        out = tmp_path / "keep" / "level.ldtk"
        res = server.generate(GenerateRequest(description="x", output_path=str(out), seed=0))
    finally:
        server.provider_factory = old
    assert res.success and res.verified, res.error
    assert res.mission and res.mission["realised"]
    kinds = [n["kind"] for n in res.mission["nodes"]]
    assert kinds.count("key") == 2 and kinds.count("lock") == 2 and "shortcut" in kinds
    assert all(set(e) == {"from", "to", "kind"} for e in res.mission["edges"])
    data = json.loads(out.read_text())
    level = data["levels"][0]
    ents = [e for li in level["layerInstances"] for e in li.get("entityInstances", [])]
    doors = [e for e in ents if e["__identifier"] == "Interactive"
             and any(f["__identifier"] == "linked_to" and f["__value"] for f in e["fieldInstances"])]
    assert len(doors) >= 3  # two locked doors and the shortcut door


# ─────────────────────────────────────────────────────────────────────────────
# Ability gate
# ─────────────────────────────────────────────────────────────────────────────


def _gate_spec() -> Dict[str, Any]:
    secs = [{"id": sid, "name": sid, "theme": "forest", "difficulty": "easy", "gravity_scale": 1.0,
             "description": "", "order": k} for k, sid in enumerate(("low", "high"))]
    return {
        "title": "Gate", "size": {"width_tiles": 40, "height_tiles": 30}, "sections": secs,
        "nodes": [
            {"id": "start", "type": "Start", "x": 16, "y": 32, "w": 96, "h": 16, "section": "low"},
            {"id": "p1", "type": "Platform", "x": 160, "y": 32, "w": 96, "h": 16, "section": "low"},
            # 88 px up: above the plain apex (69 px), below the double jump's (102 px)
            {"id": "exit", "type": "Exit", "x": 288, "y": 120, "w": 96, "h": 16, "section": "high"},
        ],
        "edges": [{"from": "start", "to": "p1", "type": "Walk", "vx": 200, "vy": 0},
                  {"from": "p1", "to": "exit", "type": "Jump", "vx": 50, "vy": 500,
                   "ability": "double_jump"}],
        "entities": [
            {"id": "player", "kind": "Start", "anchor": "start", "section": "low",
             "description": "", "sprite": ""},
            {"id": "door", "kind": "Exit", "anchor": "exit", "section": "high",
             "description": "", "sprite": ""},
        ],
        "requirements": [],
        "mission": {"rules": ["ability_gate"], "keys": None, "ability": "double_jump", "locks": None},
    }


def test_ability_gate_is_only_passable_with_the_ability():
    profile = GameProfile()
    profile.abilities.double_jump.enabled = True
    spec = LevelSpec.model_validate(_gate_spec())
    graph, warns = plan_graph(mission_of(spec), profile.abilities.enabled_names())
    assert warns == [] and graph.of_kind("gate")
    plan = map_space(spec, graph)
    pickup = next(e for e in plan.spec.entities if e.grants_ability)
    assert pickup.section == "low" and pickup.kind == "Pickup"
    lay = layout_spec(plan.spec, profile, seed=0)
    place_spec_entities(plan.spec, lay)
    rep = verify_grid(lay, profile, plan.spec)
    assert rep.ok, rep.error_messages()
    assert any(d["data"].metadata.get("ability") == "double_jump"
               for _u, _v, d in rep.graph.nx_graph.edges(data=True))
    check = verify_mission(lay, rep, plan)
    assert check.realised, check.problems
    assert check.features[0]["kind"] == "gate" and check.features[0]["ok"]
    # without the ability's moves, the Exit cannot be reached
    g = rep.graph.nx_graph
    plain = nx.DiGraph([(u, v) for u, v, d in g.edges(data=True)
                        if not d["data"].metadata.get("ability")])
    plain.add_nodes_from(g.nodes)
    assert not any(nx.has_path(plain, rep.start_span, x) for x in rep.exit_spans)


# ─────────────────────────────────────────────────────────────────────────────
# Compatibility, fidelity, fallback
# ─────────────────────────────────────────────────────────────────────────────


def test_old_specs_without_a_mission_are_unchanged(load_fixture):
    raw = json.loads(load_fixture("terrain_crypt.json"))
    spec = LevelSpec.model_validate(raw)
    dumped = spec.model_dump(by_alias=True)
    assert "mission" not in dumped
    assert all("grants_ability" not in e for e in dumped["entities"])
    assert all("target_section" not in (r["check"] or {}) for r in dumped["requirements"])
    assert mission_of(spec) is None
    built = _build(load_fixture, "terrain_crypt.json")
    assert built.mission is None and "mission" not in built.stats
    assert not any(e.id.startswith("m_") for e in built.spec.entities)


def test_mission_check_reports_the_wrong_sections_and_missing_mission(load_fixture):
    built = _build(load_fixture, "mission_crypt_key.json")
    from fidelity.checker import check_level

    spec = built.spec
    req = spec.requirements[-1].model_copy(deep=True)
    req.check.section, req.check.target_section = "hall", "boss"
    spec2 = spec.model_copy(update={"requirements": [req]})
    rep = check_level(spec2, built.spec_layout.ir, mission=built.mission)
    assert rep.items[0].status == "approximated"
    assert check_level(spec2, built.spec_layout.ir).items[0].status == "missing"


def test_too_many_locks_fall_back_to_a_locked_exit():
    spec = LevelSpec.model_validate({**_gate_spec(), "mission": {
        "rules": ["lock_key_cycle"], "keys": 2, "ability": None, "locks": None}})
    graph, _ = plan_graph(mission_of(spec))
    plan = map_space(spec, graph)
    assert plan is not None and plan.locked_exit == "lock2"
    exit_ = next(e for e in plan.spec.entities if e.kind == "Exit")
    assert exit_.requires_key
    assert any(e.kind == "Key" and e.opens == exit_.id for e in plan.spec.entities)


@pytest.mark.parametrize("attempt", [0, 1, 2])
def test_space_mapping_keeps_keys_before_their_locks(load_fixture, attempt):
    spec = LevelSpec.model_validate(json.loads(load_fixture("mission_hub_two_keys.json")))
    graph, _ = plan_graph(mission_of(spec))
    plan = map_space(spec, graph, attempt=attempt, seed=3)
    order = {s.id: s.order for s in spec.sections}
    for lock in plan.graph.of_kind("lock"):
        key = plan.graph.item_node(lock)
        assert order[plan.sections[key.id]] < order[plan.sections[lock.id]]
