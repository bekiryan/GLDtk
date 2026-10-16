"""Level complexity target (v4 Part 24): ``pipeline/complexity.py``, the closed loop, the check type."""

from __future__ import annotations

import json

import pytest

from fidelity import check_level
from fidelity.difficulty import measure_difficulty
from game.profile import GameProfile
from llm import FakeProvider, LLMController
from llm.prompt import build_generate_messages
from pipeline import build_level
from pipeline import complexity as cx
from spec import LevelSpec

FIXTURES = ["terrain_crypt.json", "valid_two_sections.json"]


def build(fixtures_dir, name, c=None, **kw):
    ctrl = LLMController(provider=FakeProvider([(fixtures_dir / name).read_text(encoding="utf-8")]),
                         max_retries=1)
    return build_level("x", ctrl, GameProfile(), complexity=c, **kw)


def overall(built):
    rep = measure_difficulty(built.spec_layout.ir.grid, built.spec_layout.ir.entities,
                             built.spec_layout.sections, GameProfile())
    return rep.overall


def test_targets_are_monotonic_in_c():
    cs = [i / 10 for i in range(11)]
    ts = [cx.targets(c) for c in cs]
    up = ["comfort_scale", "long_move_share", "hazard_per_100", "enemies_per_100", "threat_per_100",
          "labyrinth_complexity", "branches", "pits", "arena_platforms", "score_min", "score_max"]
    for f in up:
        vals = [getattr(t, f) for t in ts]
        assert vals == sorted(vals) and vals[0] < vals[-1], f
    spacing = [t.checkpoint_spacing for t in ts]
    assert spacing == sorted(spacing, reverse=True) and spacing[0] > spacing[-1]
    assert cx.targets(-1).complexity == 0 and cx.targets(2).complexity == 1
    assert cx.from_scale(1) == 0 and cx.from_scale(10) == 1 and cx.to_scale(0.5) == 5.5


def test_prompt_block():
    msgs = build_generate_messages("a level", {"complexity": 0.8})
    text = msgs[0]["content"]
    assert "Complexity 8.2/10" in text and "expected difficulty score" in text
    assert "Complexity" not in build_generate_messages("a level", {"theme": "x"})[0]["content"]


@pytest.mark.parametrize("name", FIXTURES)
def test_null_is_byte_identical(fixtures_dir, name):
    a = build(fixtures_dir, name)
    b = build(fixtures_dir, name, None)
    assert a.spec.model_dump_json() == b.spec.model_dump_json()
    assert "complexity" not in json.loads(a.spec.model_dump_json())
    assert "complexity" not in a.stats
    assert a.spec_layout.ir.grid == b.spec_layout.ir.grid
    assert a.warnings == b.warnings


@pytest.mark.parametrize("name", FIXTURES)
def test_measured_score_increases_with_c(fixtures_dir, name):
    scores = [overall(build(fixtures_dir, name, c)) for c in (0.0, 0.5, 1.0)]
    assert scores[0] < scores[1] <= scores[2] or scores[0] <= scores[1] < scores[2], scores
    assert scores[2] - scores[0] >= 1.5, scores


def test_closed_loop_moves_a_section_towards_its_target(fixtures_dir):
    base = build(fixtures_dir, "valid_two_sections.json")
    hard = build(fixtures_dir, "valid_two_sections.json", 1.0)
    assert hard.stats["complexity"]["rounds"] >= 1
    assert any(w.startswith("complexity: added enemy") for w in hard.warnings)
    t = cx.targets(1.0)

    def dist(built):
        rep = measure_difficulty(built.spec_layout.ir.grid, built.spec_layout.ir.entities,
                                 built.spec_layout.sections, GameProfile())
        return sum(abs(cx.miss(s.score, t)) for s in rep.sections)

    assert dist(hard) < dist(base)
    assert hard.report.ok


def test_adjust_section_directions(fixtures_dir):
    spec = LevelSpec.model_validate_json((fixtures_dir / "terrain_crypt.json").read_text(encoding="utf-8"))
    n = sum(e.kind == "Enemy" for e in spec.entities if e.section == "maze")
    harder, notes = cx.adjust_section(spec, "maze", +1)
    assert notes and sum(e.kind == "Enemy" for e in harder.entities if e.section == "maze") == n + 1
    maze = next(s for s in harder.sections if s.id == "maze")
    assert maze.params is not None and maze.params.complexity > 0.5
    easier, notes = cx.adjust_section(harder, "maze", -1)
    assert any("removed enemy" in x for x in notes)
    assert cx.adjust_section(spec, "nope", 1) == (spec, [])


def test_apply_targets_keeps_llm_params(fixtures_dir):
    spec = LevelSpec.model_validate_json((fixtures_dir / "terrain_crypt.json").read_text(encoding="utf-8"))
    out = cx.apply_targets(spec.model_copy(update={"complexity": 1.0}))
    hall = next(s for s in out.sections if s.id == "hall")
    assert hall.params.complexity == 0.6  # the LLM's value wins
    assert hall.params.pits == 6  # unset: from the targets
    maze = next(s for s in out.sections if s.id == "maze")
    assert maze.params.complexity == cx.targets(1.0).labyrinth_complexity
    # a section override beats the level's
    sec = [s.model_copy(update={"complexity": 0.0}) if s.id == "maze" else s for s in spec.sections]
    low = cx.effective(spec.model_copy(update={"complexity": 1.0, "sections": sec}), sec[2])
    assert low == 0.0


def test_comfort_scale_shrinks_the_limits():
    p = cx.scaled_profile(GameProfile(), 0.5)
    assert p.design.comfort_hop_tiles < GameProfile().design.comfort_hop_tiles
    assert cx.scaled_profile(GameProfile(), 1.0).design == GameProfile().design


def test_complexity_check_type(fixtures_dir):
    spec = LevelSpec.model_validate_json((fixtures_dir / "valid_two_sections.json").read_text(encoding="utf-8"))
    reqs = list(spec.requirements)
    mk = lambda i, **ck: type(reqs[0]).model_validate(  # noqa: E731
        {"id": i, "text": "difficulty", "category": "other", "check": {"type": "complexity", **ck}})
    spec = spec.model_copy(update={"requirements": reqs + [
        mk("R90", target_difficulty=3.0), mk("R91", target_difficulty=9.0),
        mk("R92", target_difficulty=3.0, section="meadow")]})
    built = build(fixtures_dir, "valid_two_sections.json")
    ir = built.spec_layout.ir
    items = {i.id: i for i in check_level(spec, ir, GameProfile(), scores={"*": 3.5, "meadow": 2.0}).items}
    assert items["R90"].status == "satisfied"
    assert items["R91"].status == "approximated" and "9" in items["R91"].reason
    assert items["R92"].status == "satisfied"
    assert check_level(spec, ir, GameProfile()).items[-3].status == "unchecked"


def test_build_scores_the_complexity_check(fixtures_dir):
    raw = json.loads((fixtures_dir / "valid_two_sections.json").read_text(encoding="utf-8"))
    raw["requirements"].append({"id": "R99", "text": "Difficulty 9/10.", "category": "other",
                                "check": {"type": "complexity", "target_difficulty": 9.0}})
    ctrl = LLMController(provider=FakeProvider([json.dumps(raw)]), max_retries=1)
    built = build_level("x", ctrl, GameProfile())
    item = next(i for i in built.fidelity.items if i.id == "R99")
    assert item.status == "approximated" and "scores" in item.reason


def test_request_models_accept_complexity():
    from api.models import GenerateRequest, SectionEditRequest

    assert GenerateRequest(description="x", complexity=0.4).complexity == 0.4
    assert GenerateRequest(description="x").complexity is None
    assert SectionEditRequest(path="p", section_id="s", prompt="x", complexity=1).complexity == 1
    with pytest.raises(ValueError):
        GenerateRequest(description="x", complexity=1.5)


def test_json_schema_has_complexity():
    schema = LevelSpec.json_schema()
    assert "complexity" in schema["properties"]
    assert "complexity" in schema["properties"]["sections"]["items"]["properties"]
