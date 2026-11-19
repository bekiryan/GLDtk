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


def test_failed_retype_falls_back_to_the_old_structure_before_the_llm(monkeypatch):
    """A section rebuilt for the prompt that breaks verification is laid out again
    with its old structure; no LLM repair is spent on it."""
    import dataclasses

    import pipeline.level as pl
    from game import GameProfile
    from pipeline.chunks import _FixedController

    real = pl._layout_and_verify

    def no_parkour(spec, *a, **kw):
        sl, rep, applied = real(spec, *a, **kw)
        if any(s.structure == "parkour" for s in spec.sections):
            rep = dataclasses.replace(rep, ok=False)
        return sl, rep, applied

    monkeypatch.setattr(pl, "_layout_and_verify", no_parkour)
    spec = _spec(["open", "arena"], ["Hall", "Pit"])
    ctrl = _FixedController(spec)
    repairs = []
    ctrl.repair_spec = lambda *a, **kw: repairs.append(1) or spec  # type: ignore[method-assign]
    built = pl.build_level("a parkour run, then an arena", ctrl, GameProfile(), densify=False)
    assert built.report.ok and repairs == []
    assert _built(built.spec) == ["open", "arena"]
    assert any("kept their old structure" in w for w in built.warnings)


def _vertical_tower_spec() -> LevelSpec:
    """Two-section homogeneous stone tower spec (climb_bottom + climb_top)."""
    return LevelSpec.model_validate({
        "title": "Tower Climb",
        "size": {"width_tiles": 30, "height_tiles": 40},
        "orientation": "vertical",
        "sections": [
            {
                "id": "climb_bottom",
                "name": "Tower Base",
                "theme": "stone tower",
                "difficulty": "easy",
                "gravity_scale": 1.0,
                "description": "The base of the tower with the player start.",
                "order": 0,
            },
            {
                "id": "climb_top",
                "name": "Tower Peak",
                "theme": "stone tower",
                "difficulty": "medium",
                "gravity_scale": 1.0,
                "description": "The peak of the tower with the exit.",
                "order": 1,
            },
        ],
        "nodes": [
            {"id": "n_start", "type": "Start", "x": 80, "y": 32, "w": 64, "h": 16, "section": "climb_bottom"},
            {"id": "n_mid1", "type": "Platform", "x": 160, "y": 80, "w": 64, "h": 16, "section": "climb_bottom"},
            {"id": "n_mid2", "type": "Platform", "x": 80, "y": 128, "w": 64, "h": 16, "section": "climb_bottom"},
            {"id": "n_mid3", "type": "Platform", "x": 160, "y": 176, "w": 64, "h": 16, "section": "climb_top"},
            {"id": "n_mid4", "type": "Platform", "x": 80, "y": 224, "w": 64, "h": 16, "section": "climb_top"},
            {"id": "n_exit", "type": "Exit", "x": 160, "y": 272, "w": 64, "h": 16, "section": "climb_top"},
        ],
        "edges": [
            {"from": "n_start", "to": "n_mid1", "type": "Jump", "vx": 150, "vy": 500},
            {"from": "n_mid1", "to": "n_mid2", "type": "Jump", "vx": -150, "vy": 500},
            {"from": "n_mid2", "to": "n_mid3", "type": "Jump", "vx": 150, "vy": 500},
            {"from": "n_mid3", "to": "n_mid4", "type": "Jump", "vx": -150, "vy": 500},
            {"from": "n_mid4", "to": "n_exit", "type": "Jump", "vx": 150, "vy": 500},
        ],
        "entities": [
            {"id": "player", "kind": "Start", "anchor": "n_start", "section": "climb_bottom",
             "description": "player start", "sprite": "player", "facing": "right", "starting_items": []},
            {"id": "tower_exit", "kind": "Exit", "anchor": "n_exit", "section": "climb_top",
             "description": "tower exit", "sprite": "portal", "facing": "right", "requires_key": False},
        ],
        "requirements": [
            {"id": "R1", "text": "The level is a tall vertical tower.", "category": "size",
             "check": {"type": "size", "min_width_tiles": 30, "min_height_tiles": 40}},
            {"id": "R2", "text": "There are platforms zig-zagging upward.", "category": "layout", "check": None},
        ],
    })


def test_single_tower_collapses_unrequested_homogeneous_sections():
    import pipeline.level as pl
    from game import GameProfile
    from pipeline.chunks import _FixedController

    prompt = ("A tall vertical tower to climb: the player starts at the bottom and the exit is at "
              "the very top, with platforms zig-zagging upward.")
    spec = _vertical_tower_spec()

    res = from_prompt(spec, prompt)
    assert "P_single_tower" in res.added
    assert any("merged unrequested extra tower sections" in n for n in res.notes)
    assert [s.id for s in res.spec.sections] == ["climb_bottom"]
    assert res.spec.sections[0].order == 0
    assert all(n.section == "climb_bottom" for n in res.spec.nodes)
    assert all(e.section == "climb_bottom" for e in res.spec.entities)

    comp = ensure_complete(spec, prompt=prompt)
    assert [s.id for s in comp.spec.sections] == ["climb_bottom"]
    assert not comp.report.missing

    built = pl.build_level(prompt, _FixedController(spec), GameProfile(), densify=False)
    assert built.report.ok
    assert [s.id for s in built.spec.sections] == ["climb_bottom"]
    assert all(n.section == "climb_bottom" for n in built.spec.nodes)
    assert all(e.section == "climb_bottom" for e in built.spec.entities)


def test_multi_section_vertical_prompts_keep_all_sections():
    # structure-tower: 4 distinct structures & section/structure requirements
    st_prompt = ("A tall bell tower to climb: stairs at the bottom guarded by rats, a climbing shaft, "
                 "a maze of storerooms and a gargoyle boss in the belfry at the top.")
    st_reqs = [
        {"id": "R1", "text": "The level is a tall bell tower to climb.", "category": "size",
         "check": {"type": "size", "min_width_tiles": 30, "min_height_tiles": 60}},
        {"id": "R2", "text": "There are stairs at the bottom section.", "category": "section",
         "check": {"type": "section", "section": "s0", "structure": "corridor"}},
        {"id": "R3", "text": "There is a climbing shaft section.", "category": "section",
         "check": {"type": "structure", "section": "s1", "structure": "shaft"}},
        {"id": "R4", "text": "There is a maze of storerooms.", "category": "section",
         "check": {"type": "structure", "section": "s2", "structure": "labyrinth"}},
        {"id": "R5", "text": "There is a belfry at the top.", "category": "section",
         "check": {"type": "section", "section": "s3", "structure": "arena"}},
    ]
    st_spec = _spec(["corridor", "shaft", "labyrinth", "arena"],
                    ["Tower Base Stairs", "Climbing Shaft", "Storeroom Maze", "The Belfry"],
                    requirements=st_reqs)
    st_spec = st_spec.model_copy(update={"orientation": "vertical"})
    st_res = from_prompt(st_spec, st_prompt)
    assert len(st_res.spec.sections) == 4
    assert "P_single_tower" not in st_res.added

    # Explicit two-section vertical prompt even if the LLM emitted homogeneous sections
    mine_prompt = ("A deep mine shaft that goes up in two sections: a damp lower mine with 4 gems "
                   "and a crystal cavern on top with the exit.")
    mine_spec = _vertical_tower_spec()
    mine_res = from_prompt(mine_spec, mine_prompt)
    assert len(mine_res.spec.sections) == 2
    assert "P_single_tower" not in mine_res.added

    # Even with "tower" in the prompt, explicit "two sections" keeps all sections
    two_tower_prompt = "A tall stone tower in two sections: a lower hall and an upper spire."
    two_tower_res = from_prompt(mine_spec, two_tower_prompt)
    assert len(two_tower_res.spec.sections) == 2
    assert "P_single_tower" not in two_tower_res.added


def test_single_tower_guards_and_order():
    prompt = ("A tall vertical tower to climb: the player starts at the bottom and the exit is at "
              "the very top, with platforms zig-zagging upward.")
    base = _vertical_tower_spec()

    # Non-homogeneous theme/structure/gravity keeps all sections
    diff_theme = base.model_copy(update={
        "sections": [base.sections[0], base.sections[1].model_copy(update={"theme": "ice tower"})]
    })
    assert len(from_prompt(diff_theme, prompt).spec.sections) == 2

    diff_grav = base.model_copy(update={
        "sections": [base.sections[0], base.sections[1].model_copy(update={"gravity_scale": 0.5})]
    })
    assert len(from_prompt(diff_grav, prompt).spec.sections) == 2

    # Horizontal orientation keeps all sections
    horiz = base.model_copy(update={"orientation": "horizontal"})
    assert len(from_prompt(horiz, prompt).spec.sections) == 2

    # Requirement referencing a section keeps all sections
    from spec import Requirement
    sec_req = base.model_copy(update={
        "requirements": [
            *base.requirements,
            Requirement.model_validate({
                "id": "R3", "text": "Reach the peak.", "category": "entity",
                "check": {"type": "entity_count", "entity_kind": "Exit", "section": "climb_top"},
            }),
        ]
    })
    assert len(from_prompt(sec_req, prompt).spec.sections) == 2

    # Mission with keys/locks keeps all sections
    locked = LevelSpec.model_validate({
        **base.model_dump(by_alias=True),
        "mission": {"rules": ["lock_key_cycle"], "keys": 1, "locks": None},
    })
    assert len(from_prompt(locked, prompt).spec.sections) == 2

    # Out-of-order sections still keep the lowest-order section and normalize order to 0
    reordered = base.model_copy(update={
        "sections": [
            base.sections[1].model_copy(update={"order": 2}),
            base.sections[0].model_copy(update={"order": 1}),
        ]
    })
    res = from_prompt(reordered, prompt)
    assert [s.id for s in res.spec.sections] == ["climb_bottom"]
    assert res.spec.sections[0].order == 0
