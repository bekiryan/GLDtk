"""Requirements the prompt states outright (fidelity/prompt.py) and the structure fill."""

from __future__ import annotations

import json
from typing import List, Optional

from fidelity.prompt import from_prompt, prompt_structures, prompt_wants_lock
from mission import mission_of
from pipeline.completeness import ensure_complete
from spec import LevelSpec
from test_terrain import terrain_spec

CRYPT = ("A crypt: a long corridor with three skeletons, then a parkour shaft climbing up, a small "
         "labyrinth of catacombs with two treasure chests in its dead ends, and finally a boss arena "
         "where the lich waits by the exit.")
METROIDVANIA = ("A small metroidvania ruin: a central hub room, a locked door to the east whose key "
                "lies in a western cave labyrinth, a double-jump boots pickup in a shaft that opens a "
                "high ledge back in the hub, and a shortcut loop back to the start after the boss arena.")


def _spec(structures: List[str], names: List[str], descriptions: Optional[List[str]] = None,
          requirements: Optional[list] = None) -> LevelSpec:
    d = json.loads(terrain_spec(structures).model_dump_json(by_alias=True))
    for i, sec in enumerate(d["sections"]):
        sec["name"] = names[i]
        sec["description"] = (descriptions or names)[i]
    d["requirements"] = requirements or []
    return LevelSpec.model_validate(d)


def _built(spec: LevelSpec) -> List[str]:
    return [s.structure or "open" for s in sorted(spec.sections, key=lambda s: s.order)]


def test_prompt_structures_reads_the_named_types_in_order():
    assert prompt_structures(CRYPT) == ["corridor", "parkour", "labyrinth", "arena"]
    assert prompt_structures(METROIDVANIA) == ["hub", "labyrinth", "shaft", "arena"]
    assert prompt_structures("A hedge maze labyrinth with dead ends") == ["labyrinth"]
    assert prompt_structures("an open-air arena") == ["arena"]
    assert prompt_structures("A tall tower to climb through a cave") == []  # loose words: the LLM's call
    assert prompt_wants_lock(METROIDVANIA)
    assert not prompt_wants_lock("a key moment in a forest")


def test_no_structure_or_key_in_the_prompt_changes_nothing():
    spec = _spec(["corridor", "arena"], ["Hall", "Pit"])
    assert from_prompt(spec, "a sunny forest with three coins").spec is spec
    assert from_prompt(spec, None).spec is spec
    out = ensure_complete(spec, prompt="a sunny forest with three coins")
    assert out.spec == spec and out.filled == []


def test_metroidvania_sections_are_retyped_and_a_lock_added():
    spec = _spec(
        ["corridor", "labyrinth", "corridor", "corridor"],
        ["Central Hub Room", "Western Cave Labyrinth", "Shaft with Double-Jump Boots", "Boss Arena"],
        ["A central hub room with a locked door to the east.",
         "A labyrinthine cave with a key to unlock the eastern door.",
         "A vertical shaft containing a double-jump boots pickup.",
         "A challenging boss arena."],
    )
    calls = []
    out = ensure_complete(spec, reprompt=lambda s, m: calls.append(m) or s, prompt=METROIDVANIA)
    assert calls == []  # all deterministic
    assert _built(out.spec) == ["hub", "labyrinth", "shaft", "arena"]
    assert len(out.spec.sections) == 4
    lock = next(r for r in out.spec.requirements if r.id == "P_lock")
    assert (lock.check.section, lock.check.target_section) == ("s1", "s0")
    assert "lock_key_cycle" in mission_of(out.spec).rules
    assert not out.report.missing


def test_llm_structure_requirement_follows_the_prompt():
    """'a parkour shaft' asked as shaft becomes parkour; the section is rebuilt."""
    req = {"id": "R3", "text": "There is a parkour shaft climbing up.", "category": "layout",
           "check": {"type": "structure", "structure": "shaft", "section": "s1"}}
    spec = _spec(["corridor", "shaft", "labyrinth", "arena"],
                 ["Crypt Corridor", "Parkour Shaft", "Catacombs", "Boss Arena"], requirements=[req])
    out = ensure_complete(spec, prompt=CRYPT)
    assert _built(out.spec) == ["corridor", "parkour", "labyrinth", "arena"]
    assert next(r for r in out.spec.requirements if r.id == "R3").check.structure == "parkour"


def test_positional_fallback_keeps_asked_structures():
    """No name matches: the section at the structure's place is retyped, never the
    only section of another asked-for structure, and no section is added."""
    spec = _spec(["open", "open", "arena"], ["Alpha", "Beta", "Gamma"])
    out = ensure_complete(spec, prompt="a corridor, then a labyrinth and an arena")
    assert _built(out.spec) == ["corridor", "labyrinth", "arena"]
    small = _spec(["arena", "open"], ["Alpha", "Beta"])
    out = ensure_complete(small, prompt="an arena, a corridor and a labyrinth")
    assert len(out.spec.sections) == 2 and _built(out.spec)[0] == "arena"
    assert [i.id for i in out.report.missing] == ["P_labyrinth"]  # left for the re-prompt
