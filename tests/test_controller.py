"""LLMController generate → validate → patch → repair loop (FakeProvider)."""

from __future__ import annotations

import pytest

from llm import (
    SYSTEM_PROMPT,
    FakeProvider,
    LLMController,
    MaxRetriesExceeded,
)
from llm.providers import FakeProviderExhausted
from spec import LevelSpec
from symbolic.schema import AbstractLevelGraph


def _controller(responses, max_retries=3) -> tuple[LLMController, FakeProvider]:
    fake = FakeProvider(responses)
    return LLMController(provider=fake, max_retries=max_retries), fake


def test_valid_on_first_try(load_fixture):
    ctrl, fake = _controller([load_fixture("valid_two_sections.json")])
    spec = ctrl.generate_spec("A meadow then a dungeon.")
    assert isinstance(spec, LevelSpec)
    assert [s.id for s in spec.sections] == ["meadow", "dungeon"]
    assert len(fake.calls) == 1
    assert ctrl.stats.attempts == 1
    assert ctrl.stats.success and not ctrl.stats.patched
    assert fake.calls[0].system == SYSTEM_PROMPT
    [msg] = fake.calls[0].messages
    assert msg["role"] == "user"
    assert msg["content"].startswith("A meadow then a dungeon.\n\nGAME PROFILE")


def test_invalid_is_patched_without_second_call(load_fixture):
    ctrl, fake = _controller([load_fixture("patchable_jump_down_no_start_entity.json")])
    spec = ctrl.generate_spec("A meadow then a dungeon.")
    assert len(fake.calls) == 1
    assert ctrl.stats.patched
    assert "EdgeReclassifyPatch" in ctrl.stats.patches_applied
    assert "TerminalEntityPatch" in ctrl.stats.patches_applied
    edge = next(e for e in spec.edges if (e.from_, e.to) == ("p2", "p3"))
    assert edge.type == "Fall"
    assert sum(e.kind == "Start" for e in spec.entities) == 1


def test_invalid_json_triggers_repair_with_error_text(fixtures_dir):
    ctrl, fake = _controller(fixtures_dir / "repair_sequence")
    spec = ctrl.generate_spec("A tiny field.")
    assert spec.title == "Tiny"
    assert len(fake.calls) == 2
    repair_messages = fake.calls[1].messages
    assert repair_messages[0] == fake.calls[0].messages[0]
    assert repair_messages[0]["content"].startswith("A tiny field.")
    assert repair_messages[1]["role"] == "assistant"
    assert "oops" in repair_messages[1]["content"]  # echoes the bad output
    assert repair_messages[2]["role"] == "user"
    assert "Could not parse your output as JSON" in repair_messages[2]["content"]
    assert ctrl.stats.attempts == 2
    assert ctrl.stats.error_codes_per_attempt == [["INVALID_JSON"], []]


def test_unpatchable_error_is_sent_back(load_fixture):
    ctrl, fake = _controller(
        [load_fixture("invalid_unknown_section.json"), load_fixture("valid_two_sections.json")]
    )
    ctrl.generate_spec("A meadow then a dungeon.")
    assert len(fake.calls) == 2
    assert "castle" in fake.calls[1].messages[-1]["content"]


def test_exhaustion_raises_max_retries(load_fixture):
    bad = load_fixture("invalid_unknown_section.json")
    ctrl, fake = _controller([bad, bad, bad], max_retries=3)
    with pytest.raises(MaxRetriesExceeded) as info:
        ctrl.generate_spec("A meadow then a dungeon.")
    assert len(fake.calls) == 3
    assert info.value.stats.final_error_codes == ["UNKNOWN_SECTION"]
    assert ctrl.stats.attempts == 3 and not ctrl.stats.success
    assert "UNKNOWN_SECTION" in str(info.value)


def test_schema_is_passed_on_every_call(load_fixture):
    bad = load_fixture("invalid_not_json.txt")
    ctrl, fake = _controller([bad, bad, load_fixture("valid_minimal.json")])
    ctrl.generate_spec("x")
    assert len(fake.calls) == 3
    expected = LevelSpec.json_schema()
    assert all(call.schema == expected for call in fake.calls)


def test_generate_compat_returns_graph(load_fixture):
    ctrl, _ = _controller([load_fixture("valid_minimal.json")])
    graph = ctrl.generate("x")
    assert isinstance(graph, AbstractLevelGraph)
    assert graph.is_solvable()


def test_constraints_reach_the_prompt(load_fixture):
    ctrl, fake = _controller([load_fixture("valid_minimal.json")])
    ctrl.generate_spec("x", constraints={"difficulty": "hard", "width_tiles": 120})
    content = fake.calls[0].messages[0]["content"]
    assert "Difficulty: hard" in content and "width_tiles=120" in content


def test_fake_provider_raises_when_exhausted():
    fake = FakeProvider([])
    with pytest.raises(FakeProviderExhausted):
        fake.complete_json("s", [], {})
