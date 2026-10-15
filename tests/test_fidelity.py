"""Fidelity report (checker, score), completeness fill and the completeness loop."""

from __future__ import annotations

import json

import pytest

from fidelity import FidelityItem, FidelityReport, check_level, check_spec, fill_missing
from game import GameProfile
from llm import FakeProvider, LLMController, build_completeness_messages, validate_spec
from pipeline import build_level
from pipeline.completeness import ensure_complete
from spec import LevelSpec, Requirement
from spec_builders import entity, make_spec, node, section


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _req(rid: str, text: str = "req", category: str = "entity", **check) -> dict:
    return {"id": rid, "text": text, "category": category, "check": check or None}


def _spec(requirements, entities=None, sections=None, size=(40, 15)) -> LevelSpec:
    """Two sections (a: start, pa, spikes; b: pb, exit) plus the given requirements."""
    nodes = [
        node("start", "Start", 1, 2, 3, "a"),
        node("pa", "Platform", 6, 2, 4, "a"),
        node("spikes", "Hazard", 11, 0, 2, "a"),
        node("pb", "Platform", 14, 2, 3, "b"),
        node("exit", "Exit", 19, 2, 3, "b"),
    ]
    d = make_spec(
        nodes,
        [("start", "pa", "Jump"), ("pa", "pb", "Jump"), ("pb", "exit", "Jump")],
        entities,
        sections or [section("a", 0), section("b", 1)],
        size,
        as_dict=True,
    )
    d["requirements"] = requirements
    return LevelSpec.model_validate(d)


def _status(report: FidelityReport, rid: str) -> str:
    return next(i.status for i in report.items if i.id == rid)


def _item(report: FidelityReport, rid: str) -> FidelityItem:
    return next(i for i in report.items if i.id == rid)


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────


def test_requirement_check_schema_is_strict_and_nullable():
    req = LevelSpec.json_schema()["properties"]["requirements"]["items"]
    assert "check" in req["required"]
    check = req["properties"]["check"]
    assert check["type"] == ["object", "null"]
    assert check["additionalProperties"] is False
    assert set(check["required"]) == set(check["properties"]) >= {
        "type", "entity_kind", "subkind", "min_count", "section", "ability",
        "min_width_tiles", "min_height_tiles", "gravity_scale",
    }
    assert check["properties"]["type"]["type"] == "string"  # the only non-null field
    assert None in check["properties"]["entity_kind"]["enum"]


def test_requirements_without_check_still_parse(valid_spec_dict):
    spec = LevelSpec.model_validate(valid_spec_dict)
    assert all(r.check is None for r in spec.requirements)
    report = check_spec(spec)
    assert {i.status for i in report.items} == {"unchecked"}
    assert report.score is None


def test_check_round_trips(load_fixture):
    spec = LevelSpec.model_validate_json(load_fixture("completeness_fill.json"))
    again = LevelSpec.model_validate(json.loads(spec.model_dump_json(by_alias=True)))
    assert again == spec
    assert spec.requirements[2].check.min_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# Checks and statuses
# ─────────────────────────────────────────────────────────────────────────────


def test_entity_count_statuses():
    ents = [
        entity("player", "Start", "start", "a", facing="right"),
        entity("door", "Exit", "exit", "b", requires_key=False),
        entity("c1", "Pickup", "pa", "a", pickup_kind="coin"),
        entity("c2", "Pickup", "pa", "a", pickup_kind="coin"),
        entity("bat", "Enemy", "pb", "b", archetype="flyer"),
    ]
    spec = _spec(
        [
            _req("R1", type="entity_count", entity_kind="Pickup", subkind="coin", min_count=2),
            _req("R2", type="entity_count", entity_kind="Pickup", subkind="coins", min_count=3),
            _req("R3", type="entity_count", entity_kind="Pickup", subkind="coin", section="b"),
            _req("R4", type="entity_count", entity_kind="Enemy", subkind="flyer", section="b"),
            _req("R5", type="entity_count", entity_kind="Enemy", section="nowhere"),
            _req("R6", type="entity_count", entity_kind="Key"),
            _req("R7", type="entity_count"),  # no kind → cannot be checked
        ],
        ents,
    )
    report = check_spec(spec)
    assert _status(report, "R1") == "satisfied"
    assert _status(report, "R2") == "approximated"  # 2 of 3: partly met
    assert "only 2 of 3" in _item(report, "R2").reason
    assert _status(report, "R3") == "missing"  # coins exist, but not in section b
    assert _status(report, "R4") == "satisfied"
    assert _status(report, "R5") == "missing"
    assert "nowhere" in _item(report, "R5").reason
    assert _status(report, "R6") == "missing"
    assert _status(report, "R7") == "unchecked"


def test_unknown_subkind_is_approximated_by_description():
    ents = [
        entity("player", "Start", "start", "a", facing="right"),
        entity("door", "Exit", "exit", "b", requires_key=False),
        entity("spook", "Enemy", "pa", "a", archetype="flyer"),
        entity("skel", "Enemy", "pb", "b", archetype="walker"),
    ]
    ents[2]["description"] = "a ghost haunting the hall"
    spec = _spec(
        [
            _req("R1", type="entity_count", entity_kind="Enemy", subkind="ghost"),
            _req("R2", type="entity_count", entity_kind="Enemy", subkind="ghost", min_count=2),
            _req("R3", type="entity_count", entity_kind="Enemy", subkind="dragon"),
        ],
        ents,
    )
    report = check_spec(spec)
    assert _status(report, "R1") == "approximated"
    assert "not a valid Enemy archetype" in _item(report, "R1").reason
    assert _status(report, "R2") == "approximated"  # partly met
    assert _status(report, "R3") == "missing"  # the walker is not described as a dragon


def test_start_item_section_size_gravity_and_ability_checks():
    ents = [
        entity("player", "Start", "start", "a", facing="right", starting_weapon="Sword",
               starting_items=["torch"]),
        entity("door", "Exit", "exit", "b", requires_key=False),
    ]
    sections = [section("a", 0), section("b", 1, gravity=0.5)]
    profile = GameProfile.model_validate(
        {"name": "Moon", "abilities": {"dash": {"enabled": True}}}
    )
    spec = _spec(
        [
            _req("R1", type="start_item", subkind="sword"),
            _req("R2", type="start_item", subkind="torch"),
            _req("R3", type="start_item", subkind="shield"),
            _req("R4", "section", "section", type="section", section="b"),
            _req("R5", "section", "section", type="section", section="lava"),
            _req("R6", "size", "size", type="size", min_width_tiles=40),
            _req("R7", "size", "size", type="size", min_width_tiles=80, min_height_tiles=20),
            _req("R8", "size", "size", type="size", min_width_tiles=500),
            _req("R9", "gravity", "other", type="gravity", gravity_scale=0.5),
            _req("R10", "gravity", "other", type="gravity", gravity_scale=0.5, section="a"),
            _req("R11", "gravity", "other", type="gravity", gravity_scale=3.0),
            _req("R12", "ability", "ability", type="ability", ability="dash"),
            _req("R13", "ability", "ability", type="ability", ability="double jump"),
            _req("R14", "ability", "ability", type="ability", ability="fly"),
            _req("R15", "style", "style", type="none"),
            _req("R16", "style", "style"),  # check null
        ],
        ents,
        sections,
    )
    report = check_spec(spec, profile)
    expected = {
        "R1": "satisfied", "R2": "satisfied", "R3": "missing",
        "R4": "satisfied", "R5": "missing",
        "R6": "satisfied", "R7": "missing", "R8": "impossible",
        "R9": "satisfied", "R10": "missing", "R11": "impossible",
        "R12": "satisfied", "R13": "impossible", "R14": "impossible",
        "R15": "unchecked", "R16": "unchecked",
    }
    assert {i.id: i.status for i in report.items} == expected
    assert "'Moon'" in _item(report, "R13").reason  # names the profile
    assert "double_jump" in _item(report, "R13").reason
    assert "not an ability GLDtk supports" in _item(report, "R14").reason


def test_score_formula():
    def item(status):
        return FidelityItem("R", "t", "entity", status, "")

    report = FidelityReport(
        [item("satisfied"), item("approximated"), item("missing"), item("impossible"),
         item("unchecked"), item("unchecked")]
    )
    # (1 + 0.5 + 0 + 0) / 4 checkable — unchecked items are left out
    assert report.score == pytest.approx(1.5 / 4)
    assert FidelityReport([item("unchecked")]).score is None
    assert FidelityReport([item("satisfied"), item("approximated")]).score == pytest.approx(0.75)
    d = report.to_dict()
    assert d["score"] == pytest.approx(0.375)
    assert d["counts"] == {"satisfied": 1, "approximated": 1, "missing": 1, "impossible": 1,
                           "unchecked": 2}


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic fill
# ─────────────────────────────────────────────────────────────────────────────


def test_fill_adds_entities_spread_over_non_hazard_nodes():
    spec = _spec(
        [
            _req("R1", "Five coins in the first area.", type="entity_count", entity_kind="Pickup",
                 subkind="coin", min_count=5, section="a"),
            _req("R2", "Two spooky ghosts.", type="entity_count", entity_kind="Enemy",
                 subkind="ghost", min_count=2),
            _req("R3", "A second exit.", type="entity_count", entity_kind="Exit", min_count=2),
        ]
    )
    result = fill_missing(spec)
    assert result.filled == ["R1", "R2"]
    assert result.entities_added == 7
    new = [e for e in result.spec.entities if e.id.startswith("auto_")]
    coins = [e for e in new if e.kind == "Pickup"]
    assert len(coins) == 5
    assert all(e.pickup_kind == "coin" and e.section == "a" for e in coins)
    assert all(e.description == "Five coins in the first area." for e in coins)
    # spread: never on the hazard, the platform first, then alternating
    anchors = [e.anchor for e in coins]
    assert "spikes" not in anchors
    assert anchors.count("pa") == 3 and anchors.count("start") == 2
    ghosts = [e for e in new if e.kind == "Enemy"]
    # anywhere in the level: the least loaded node is pb (section b), a Platform
    # beats the Exit node on a tie
    assert [e.anchor for e in ghosts] == ["pb", "pb"]
    assert {e.section for e in ghosts} == {"b"}
    assert all(e.archetype is None and e.sprite == "ghost" for e in ghosts)
    assert len({e.id for e in result.spec.entities}) == len(result.spec.entities)
    assert validate_spec(result.spec).valid

    report = check_spec(result.spec, auto_filled=result.filled)
    assert _status(report, "R1") == "satisfied" and _item(report, "R1").auto_filled
    assert _status(report, "R2") == "approximated"  # ghost → nearest kind
    assert _status(report, "R3") == "approximated"  # 1 of 2: Exits are never filled
    assert not _item(report, "R3").auto_filled
    assert spec.entities != result.spec.entities  # the input is untouched
    assert fill_missing(result.spec).filled == []  # idempotent


def test_fill_start_item_and_size():
    spec = _spec(
        [
            _req("R1", type="start_item", subkind="bow"),
            _req("R2", type="start_item", subkind="lantern"),
            _req("R3", "big", "size", type="size", min_width_tiles=90, min_height_tiles=25),
            _req("R4", "huge", "size", type="size", min_width_tiles=900),
        ]
    )
    result = fill_missing(spec)
    start = next(e for e in result.spec.entities if e.kind == "Start")
    assert start.starting_weapon == "bow"
    assert start.starting_items == ["lantern"]
    assert (result.spec.size.width_tiles, result.spec.size.height_tiles) == (90, 25)
    report = check_spec(result.spec, auto_filled=result.filled)
    assert [i.status for i in report.items] == ["satisfied", "satisfied", "satisfied", "impossible"]


def test_ensure_complete_reprompts_once_for_unfillable_items():
    spec = _spec([_req("R1", "lava", "section", type="section", section="lava")])
    calls = []

    def reprompt(current, missing):
        calls.append(missing)
        return current  # the LLM did not fix it

    out = ensure_complete(spec, reprompt=reprompt)
    assert len(calls) == 1 and out.reprompts == 1
    assert calls[0][0].startswith('R1 "lava": section \'lava\' does not exist')
    assert _status(out.report, "R1") == "missing"
    assert any("still missing" in n for n in out.notes)

    def failing(current, missing):
        raise RuntimeError("quota")

    out = ensure_complete(spec, reprompt=failing)
    assert out.spec == spec and out.reprompts == 1
    assert any("quota" in n for n in out.notes)


def test_system_prompt_example_carries_checks_and_layout_guidance():
    from llm import SYSTEM_PROMPT

    start = SYSTEM_PROMPT.index('{"title"')
    example, _ = json.JSONDecoder().raw_decode(SYSTEM_PROMPT[start:])
    spec = LevelSpec.model_validate(example)  # null fields omitted there → defaults
    types = {r.check.type for r in spec.requirements if r.check}
    assert {"entity_count", "section", "ability"} <= types
    report = check_spec(spec)  # the example meets its own entity requirements
    assert _status(report, "R3") == "satisfied" and _status(report, "R6") == "satisfied"
    assert "LAYOUT GUIDANCE" in SYSTEM_PROMPT and "3 and 10 tiles" in SYSTEM_PROMPT
    assert "min_count" in SYSTEM_PROMPT


def test_completeness_message():
    msgs = build_completeness_messages(
        "a castle", '{"title":"x"}', ['R1 "castle": missing'], [{"role": "user", "content": "a"}]
    )
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1]["content"] == '{"title":"x"}'
    assert "COMPLETENESS CHECK FAILED" in msgs[2]["content"]
    assert '1. R1 "castle": missing' in msgs[2]["content"]


# ─────────────────────────────────────────────────────────────────────────────
# build_level (FakeProvider fixtures)
# ─────────────────────────────────────────────────────────────────────────────


def _controller(responses):
    fake = FakeProvider(responses)
    return LLMController(provider=fake, max_retries=3), fake


def test_build_level_fills_coins_and_boss_without_llm_calls(load_fixture):
    ctrl, fake = _controller([load_fixture("completeness_fill.json")])
    built = build_level("3 coins in a meadow, a boss in the dungeon", ctrl, GameProfile())
    assert len(fake.calls) == 1  # no completeness re-prompt
    assert built.report.ok
    assert built.stats["completeness_reprompts"] == 0
    assert built.stats["entities_filled"] == 4
    report = built.fidelity
    assert report is not None and report.verified is True
    statuses = {i.id: i.status for i in report.items}
    assert statuses == {"R1": "satisfied", "R2": "satisfied", "R3": "satisfied",
                        "R4": "satisfied", "R5": "satisfied", "R6": "unchecked"}
    assert report.score == 1.0
    assert {i.id for i in report.items if i.auto_filled} == {"R3", "R4"}
    placed = [e for e in built.spec_layout.ir.entities if e.metadata.get("spec_id", "").startswith("auto_")]
    assert sorted(e.metadata.get("pickup_kind") or e.metadata.get("archetype") for e in placed) == [
        "boss", "coin", "coin", "coin"
    ]
    assert any("completeness: added 3 Pickup" in w for w in built.warnings)


def test_build_level_reprompts_once_for_a_missing_section(fixtures_dir):
    ctrl, fake = _controller(fixtures_dir / "completeness_reprompt")
    built = build_level("meadow, dungeon, castle", ctrl, GameProfile())
    assert len(fake.calls) == 2  # exactly one extra provider call
    assert built.stats["completeness_reprompts"] == 1
    assert built.stats["llm_attempts"] == 2
    msgs = fake.calls[1].messages
    assert msgs[1]["role"] == "assistant"
    assert "COMPLETENESS CHECK FAILED" in msgs[-1]["content"]
    assert "section 'castle' does not exist" in msgs[-1]["content"]
    assert _status(built.fidelity, "R7") == "satisfied"
    assert [s.id for s in built.spec.sections] == ["meadow", "dungeon", "castle"]


def test_build_level_never_reprompts_twice(fixtures_dir):
    first = (fixtures_dir / "completeness_reprompt" / "01_missing_castle.json").read_text()
    ctrl, fake = _controller([first, first, first])
    built = build_level("meadow, dungeon, castle", ctrl, GameProfile())
    assert len(fake.calls) == 2
    assert built.report.ok  # the level is still built
    assert _status(built.fidelity, "R7") == "missing"
    # R1–R6 of this fixture carry no check; R7 is the only checkable item
    assert built.fidelity.score == 0.0
    assert any("still missing after one re-prompt" in w for w in built.warnings)


def test_check_level_counts_placed_entities(load_fixture):
    ctrl, _ = _controller([load_fixture("completeness_fill.json")])
    built = build_level("x", ctrl, GameProfile())
    ir = built.spec_layout.ir
    # Placement dropped two coins and the boss: the level report disagrees with the spec.
    coins = [e for e in ir.entities if e.metadata.get("pickup_kind") == "coin"]
    ir.entities = [
        e for e in ir.entities if e not in coins[:2] and e.metadata.get("archetype") != "boss"
    ]
    report = check_level(built.spec, ir, GameProfile(), verified=True)
    assert _status(report, "R3") == "approximated"
    assert "only 1 of 3" in _item(report, "R3").reason and "in the level" in _item(report, "R3").reason
    assert _status(report, "R4") == "missing"
    assert _status(check_spec(built.spec), "R4") == "satisfied"


# ─────────────────────────────────────────────────────────────────────────────
# Hazard requirements (M2 bug 1)
# ─────────────────────────────────────────────────────────────────────────────


def _gap_spec(requirements, orientation=None) -> LevelSpec:
    """Section a has a 6-tile gap between pa and pb; section b a 2-tile one."""
    nodes = [
        node("start", "Start", 1, 2, 3, "a"),
        node("pa", "Platform", 5, 3, 3, "a"),
        node("pb", "Platform", 14, 3, 3, "a"),
        node("pc", "Platform", 19, 3, 3, "b"),
        node("exit", "Exit", 24, 3, 3, "b"),
    ]
    d = make_spec(
        nodes,
        [("start", "pa", "Jump"), ("pa", "pb", "Jump"), ("pb", "pc", "Jump"), ("pc", "exit", "Jump")],
        None, [section("a", 0), section("b", 1)], (40, 15), as_dict=True,
    )
    d["requirements"] = requirements
    d["orientation"] = orientation
    return LevelSpec.model_validate(d)


def test_hazard_check_counts_hazard_nodes_per_section():
    spec = _spec([
        _req("R1", "spikes in a", "layout", type="hazard", section="a"),
        _req("R2", "spikes in b", "layout", type="hazard", section="b"),
        _req("R3", "two spike strips", "layout", type="hazard", min_count=2),
        _req("R4", "lava in c", "layout", type="hazard", section="c"),
    ])
    report = check_spec(spec)
    assert _status(report, "R1") == "satisfied"
    assert _status(report, "R2") == "missing"
    assert _status(report, "R3") == "approximated"
    assert _status(report, "R4") == "missing"
    assert "does not exist" in _item(report, "R4").reason


def test_entity_count_naming_a_hazard_is_checked_as_a_hazard():
    # The M2 demo: "a spike pit in the cave" typed as an Enemy with subkind "Hazard".
    spec = _spec([
        _req("R1", "Spike pit in a.", "entity", type="entity_count", entity_kind="Enemy",
             subkind="Hazard", min_count=1, section="a"),
        _req("R2", "A lava pool in b.", "entity", type="entity_count", entity_kind="Enemy",
             min_count=1, section="b"),
        _req("R3", "Coins near the lava.", "entity", type="entity_count", entity_kind="Pickup",
             subkind="coin", min_count=1),
        _req("R4", "A spike trap in a.", "entity", type="entity_count", entity_kind="Interactive",
             subkind="spikes", section="a"),
    ])
    report = check_spec(spec)
    assert _status(report, "R1") == "satisfied"  # the spec's own Hazard node in a
    assert "checked as a hazard" in _item(report, "R1").reason
    assert _status(report, "R2") == "missing" and "hazard" in _item(report, "R2").reason
    assert _status(report, "R3") == "missing"  # a valid coin requirement stays an entity count
    assert "Pickup coin" in _item(report, "R3").reason
    assert _status(report, "R4") == "satisfied"

    fill = fill_missing(spec)
    # never an Enemy (or Interactive) for a hazard; the coin is still filled
    added = [e for e in fill.spec.entities if e.id.startswith("auto_")]
    assert [e.kind for e in added] == ["Pickup"]
    assert "R2" not in fill.filled  # section b has no gap wide enough: left to the re-prompt
    assert fill.nodes_added == 0


def test_fill_adds_hazard_node_under_a_gap_and_the_level_still_builds():
    reqs = [_req("R1", "a spike pit in a", "layout", type="entity_count", entity_kind="Enemy",
                 subkind="spikes", section="a")]
    spec = _gap_spec(reqs)
    assert _status(check_spec(spec), "R1") == "missing"
    fill = fill_missing(spec)
    assert fill.filled == ["R1"] and fill.nodes_added == 1 and fill.entities_added == 0
    hz = [n for n in fill.spec.nodes if n.type == "Hazard"]
    assert len(hz) == 1 and hz[0].section == "a" and hz[0].id.startswith("auto_hazard_r1")
    # on the floor, inside the pa..pb gap with a tile of margin on both sides
    assert hz[0].y == 0 and 9 * 16 <= hz[0].x and hz[0].x + hz[0].w <= 13 * 16
    assert not any(e.from_ == hz[0].id for e in fill.spec.edges)
    assert validate_spec(fill.spec).valid
    assert _status(check_spec(fill.spec), "R1") == "satisfied"

    ctrl, _ = _controller([json.dumps(spec.model_dump(by_alias=True))])
    built = build_level("a spike pit", ctrl, GameProfile())
    assert built.report.ok
    assert _status(built.fidelity, "R1") == "satisfied" and _item(built.fidelity, "R1").auto_filled
    assert not any(e.kind == "Enemy" for e in built.spec.entities)
    ir = built.spec_layout.ir
    assert any(v == 2 for row in ir.grid for v in row)  # HAZARD tiles on the grid


def test_vertical_hazard_fill_uses_the_lower_neighbours_height():
    spec = _gap_spec([_req("R1", "lava", "layout", type="hazard", section="a")],
                     orientation="vertical")
    hz = next(n for n in fill_missing(spec).spec.nodes if n.type == "Hazard")
    assert hz.y == 3 * 16  # y = 0 would lie in the bottom band, not in section a


def test_system_prompt_documents_hazard_checks_and_orientation():
    from llm import SYSTEM_PROMPT

    start = SYSTEM_PROMPT.index('{"title"')
    example, _ = json.JSONDecoder().raw_decode(SYSTEM_PROMPT[start:])
    spec = LevelSpec.model_validate(example)
    hazard = [r for r in spec.requirements if r.check and r.check.type == "hazard"]
    assert hazard and _status(check_spec(spec), hazard[0].id) == "satisfied"
    assert '"hazard"' in SYSTEM_PROMPT and "NEVER entities" in SYSTEM_PROMPT
    assert "BOTTOM → TOP" in SYSTEM_PROMPT and '"orientation"' in SYSTEM_PROMPT
