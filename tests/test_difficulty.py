"""Difficulty metrics (v3 Part 15a): ``fidelity/difficulty.py``, ``GET /difficulty``, eval aggregation.

Offline: hand-made grids and FakeProvider ``/generate`` outputs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

import server
from eval.metrics import aggregate, difficulty_by_label, render_report, summarize
from fidelity import FidelityItem, FidelityReport
from fidelity.difficulty import apply_to_fidelity, measure_difficulty
from game.profile import GameProfile
from layout import IREntity, TileValue
from llm import FakeProvider

ROOT = Path(__file__).resolve().parent.parent
E, S, H = int(TileValue.EMPTY), int(TileValue.SOLID), int(TileValue.HAZARD)


def side_scroller(widths: List[int], gaps: List[int], *, hazards: bool = False, enemies: int = 0,
                  health: int = 1, step: int = 0, height: int = 16):
    """Platforms of the given widths separated by gaps (the floor row is 12, +step per platform)."""
    total = 2 + sum(widths) + sum(gaps) + 2
    grid = [[E] * total for _ in range(height)]
    col, row, spans = 2, 12, []
    for i, w in enumerate(widths):
        for r in range(row, height):
            for c in range(col, col + w):
                grid[r][c] = S
        spans.append((col, row, w))
        if i < len(gaps):
            if hazards:
                for c in range(col + w, col + w + gaps[i]):
                    grid[height - 1][c] = H
            col += w + gaps[i]
            row -= step
    ents = [IREntity("Start", spans[0][0] + 1, spans[0][1] - 1, {}),
            IREntity("Exit", spans[-1][0] + spans[-1][2] - 2, spans[-1][1] - 1, {})]
    for k in range(enemies):
        c, r, w = spans[min(k + 1, len(spans) - 1)]
        ents.append(IREntity("Enemy", c + w // 2, r - 1, {"archetype": "walker", "health": health, "damage": 1}))
    return grid, ents


def one_section(grid, label: str, **extra) -> List[Dict[str, Any]]:
    return [{"id": "s", "name": "Level", "difficulty": label, "col_start": 0, "col_end": len(grid[0]) - 1,
             "row_start": 0, "row_end": len(grid) - 1, **extra}]


def score_of(grid, ents, label="medium", **extra) -> float:
    rep = measure_difficulty(grid, ents, one_section(grid, label, **extra), GameProfile())
    assert rep.route_ok, "the fixture must be beatable"
    return rep.sections[0].score


# ─── synthetic grids ─────────────────────────────────────────────────────────


def test_hard_scores_higher_than_easy():
    easy = side_scroller([12, 12, 12], [3, 3])
    hard = side_scroller([3, 3, 3, 3], [13, 13, 13], hazards=True, enemies=3, health=4)
    e, h = score_of(*easy), score_of(*hard)
    assert e < 4 and h > 6.5 and h > e + 3, (e, h)


def test_each_pressure_raises_the_score():
    base = score_of(*side_scroller([8, 8, 8], [6, 6]))
    assert score_of(*side_scroller([8, 8, 8], [6, 6], hazards=True)) > base
    assert score_of(*side_scroller([8, 8, 8], [6, 6], enemies=2, health=3)) > base
    assert score_of(*side_scroller([8, 8, 8], [13, 13])) > base
    assert score_of(*side_scroller([3, 3, 3], [6, 6])) > base


def test_recovery_lowers_the_score():
    grid, ents = side_scroller([8, 8, 8], [6, 6], hazards=True, enemies=2, health=3)
    plain = score_of(grid, ents)
    ents2 = ents + [IREntity("Checkpoint", 12, 11, {}), IREntity("Pickup", 13, 11, {"pickup_kind": "health"})]
    assert score_of(grid, ents2) < plain


def test_metrics():
    grid, ents = side_scroller([6, 6, 6], [6, 6], hazards=True, enemies=1, health=3)
    rep = measure_difficulty(grid, ents, one_section(grid, "easy"), GameProfile())
    m = rep.sections[0].metrics
    assert m["moves"] == 2 and 5 <= m["mean_gap"] <= 7 and m["max_rise"] == 0
    assert m["hazard_tiles"] == 12 and m["hazard_per_100"] == pytest.approx(100 * 12 / len(grid[0]), abs=0.1)
    assert m["enemies"] == 1 and m["enemy_threat"] == 3 and m["bosses"] == 0
    assert 0 <= rep.sections[0].score <= 10 and rep.overall == rep.sections[0].score


def test_boss_threat_is_capped():
    grid, ents = side_scroller([8, 8], [6])
    ents.append(IREntity("Enemy", 12, 11, {"archetype": "boss", "health": 99, "damage": 9}))
    rep = measure_difficulty(grid, ents, one_section(grid, "hard"), GameProfile())
    m = rep.sections[0].metrics
    assert m["bosses"] == 1 and m["enemy_threat"] == 12


def test_sections_split_the_level():
    """Two column bands over one level: the harder half scores higher."""
    grid, ents = side_scroller([12, 12, 3, 3, 3], [3, 13, 13, 13], hazards=True)
    mid = 2 + 12 + 3 + 12 + 6  # the first big gap (13 tiles) starts after the second platform
    secs = [
        {"id": "a", "name": "A", "difficulty": "easy", "col_start": 0, "col_end": mid - 1,
         "row_start": 0, "row_end": 15},
        {"id": "b", "name": "B", "difficulty": "hard", "col_start": mid, "col_end": len(grid[0]) - 1,
         "row_start": 0, "row_end": 15},
    ]
    rep = measure_difficulty(grid, ents, secs, GameProfile())
    a, b = rep.sections
    assert [s.id for s in rep.sections] == ["a", "b"]
    assert b.score > a.score and not a.warnings


def vertical_tower(rise: int, hazards: bool = False, enemies: int = 0, ledge: int = 10, width: int = 24):
    """A tower of ledges every *rise* rows; the sections are row bands (the first at the bottom)."""
    height = 4 + rise * 6
    grid = [[E] * width for _ in range(height)]
    row, side, ents = height - 3, 0, []
    ledges = []
    for _ in range(6):
        c0 = 1 if side == 0 else width - 1 - ledge
        for r in range(row, min(height, row + 2)):
            for c in range(c0, c0 + ledge):
                grid[r][c] = S
        ledges.append((c0, row))
        row -= rise
        side ^= 1
    if hazards:
        for r in range(height - 2, height):
            for c in range(width):
                if grid[r][c] == E:
                    grid[r][c] = H
    ents.append(IREntity("Start", ledges[0][0] + 1, ledges[0][1] - 1, {}))
    ents.append(IREntity("Exit", ledges[-1][0] + 1, ledges[-1][1] - 1, {}))
    for k in range(enemies):
        c0, r = ledges[1 + k]
        ents.append(IREntity("Enemy", c0 + ledge // 2, r - 1, {"archetype": "walker", "health": 3, "damage": 1}))
    half = height // 2
    secs = [
        {"id": "low", "name": "Low", "difficulty": "easy", "orientation": "vertical", "col_start": 0,
         "col_end": width - 1, "row_start": half, "row_end": height - 1},
        {"id": "high", "name": "High", "difficulty": "hard", "orientation": "vertical", "col_start": 0,
         "col_end": width - 1, "row_start": 0, "row_end": half - 1},
    ]
    return grid, ents, secs


def test_vertical_sections_are_row_bands():
    grid, ents, secs = vertical_tower(rise=4)
    rep = measure_difficulty(grid, ents, secs, GameProfile())
    assert rep.route_ok
    low, high = rep.sections
    assert low.metrics["length"] == high.metrics["length"] == len(grid) // 2
    assert low.metrics["moves"] > 0 and high.metrics["moves"] > 0  # moves counted by their start row
    assert low.metrics["length"] == len(grid) // 2


def test_vertical_hard_scores_higher_than_easy():
    easy = vertical_tower(rise=5)
    hard = vertical_tower(rise=9, hazards=True, enemies=3, ledge=4, width=16)
    se = measure_difficulty(*easy[:2], easy[2], GameProfile())
    sh = measure_difficulty(*hard[:2], hard[2], GameProfile())
    assert se.route_ok and sh.route_ok
    assert sh.overall > se.overall + 2, (se.overall, sh.overall)


# ─── checks ──────────────────────────────────────────────────────────────────


def test_mismatch_warnings():
    easy_grid = side_scroller([12, 12, 12], [3, 3])
    hard_grid = side_scroller([3, 3, 3, 3], [13, 13, 13], hazards=True, enemies=3, health=4)
    ok_easy = measure_difficulty(easy_grid[0], easy_grid[1], one_section(easy_grid[0], "easy"), GameProfile())
    ok_hard = measure_difficulty(hard_grid[0], hard_grid[1], one_section(hard_grid[0], "hard"), GameProfile())
    assert not ok_easy.sections[0].warnings and not ok_hard.sections[0].warnings

    bad_hard = measure_difficulty(easy_grid[0], easy_grid[1], one_section(easy_grid[0], "hard"), GameProfile())
    assert "labelled hard" in bad_hard.sections[0].warnings[0]
    bad_easy = measure_difficulty(hard_grid[0], hard_grid[1], one_section(hard_grid[0], "easy"), GameProfile())
    assert "labelled easy" in bad_easy.sections[0].warnings[0]
    # medium never mismatches
    med = measure_difficulty(easy_grid[0], easy_grid[1], one_section(easy_grid[0], "medium"), GameProfile())
    assert not med.sections[0].warnings


def test_fidelity_item_becomes_approximated():
    grid, ents = side_scroller([12, 12, 12], [3, 3])
    sec = [{"id": "boss_lair", "name": "Boss Lair", "difficulty": "hard", "col_start": 0,
            "col_end": len(grid[0]) - 1, "row_start": 0, "row_end": len(grid) - 1}]
    rep = measure_difficulty(grid, ents, sec, GameProfile())
    assert rep.sections[0].mismatch

    fid = FidelityReport(items=[
        FidelityItem("R1", "A hard boss lair section.", "section", "satisfied", "section exists"),
        FidelityItem("R2", "Some coins.", "entity", "satisfied", "ok"),
    ])
    assert apply_to_fidelity(rep, fid) == 1
    assert fid.items[0].status == "approximated" and "scores" in fid.items[0].reason
    assert fid.items[1].status == "satisfied"

    # no requirement names it, but the prompt states it: an item is added
    fid2 = FidelityReport(items=[FidelityItem("R1", "Some coins.", "entity", "satisfied", "ok")])
    assert apply_to_fidelity(rep, fid2, "Make a very difficult Boss Lair at the end.") == 1
    assert fid2.items[-1].status == "approximated" and fid2.items[-1].id == "D-boss_lair"
    # the prompt does not state it: nothing changes (warning only)
    fid3 = FidelityReport(items=[FidelityItem("R1", "Some coins.", "entity", "satisfied", "ok")])
    assert apply_to_fidelity(rep, fid3, "A level with a lair.") == 0 and len(fid3.items) == 1


def test_gets_harder_curve_warning():
    easy = side_scroller([12, 12], [3])
    hard = side_scroller([3, 3, 3], [13, 13], hazards=True, enemies=2, health=4)
    # hard half first, easy half second: the curve goes down
    n = len(hard[0][0])
    grid = [hard[0][r] + [E] * 2 + easy[0][r] for r in range(len(hard[0]))]
    off = n + 2
    ents = hard[1][:1] + [IREntity(e.entity_type, e.col + off, e.row, e.metadata) for e in easy[1][1:]]
    # a Start on the hard half, the Exit at the end of the easy half; bridge the join with a floor
    for r in range(12, 16):
        for c in range(n - 2, n + 4):
            grid[r][c] = S
    secs = [
        {"id": "a", "name": "First", "difficulty": "hard", "col_start": 0, "col_end": n,
         "row_start": 0, "row_end": 15},
        {"id": "b", "name": "Second", "difficulty": "easy", "col_start": n + 1,
         "col_end": len(grid[0]) - 1, "row_start": 0, "row_end": 15},
    ]
    rep = measure_difficulty(grid, ents, secs, GameProfile(), prompt="It gets harder as you go.")
    assert rep.sections[0].score > rep.sections[1].score + 1
    assert rep.warnings and "gets harder" in rep.warnings[0]
    assert not measure_difficulty(grid, ents, secs, GameProfile(), prompt="A relaxing walk.").warnings
    assert not rep.sections[0].warnings and not rep.sections[1].warnings  # the curve is a level warning


def test_no_route_still_measures():
    grid, ents = side_scroller([6, 6], [30])
    rep = measure_difficulty(grid, ents, one_section(grid, "easy"), GameProfile())
    assert not rep.route_ok and rep.sections[0].metrics["moves"] == 0


# ─── eval aggregation ────────────────────────────────────────────────────────


def rec(pid: str, *secs: tuple, **kw) -> Dict[str, Any]:
    return {"id": pid, "provider": "p", "model": "m", "verified": True,
            "difficulty": {"sections": [{"label": l, "score": s} for l, s in secs], "overall": 1.0}, **kw}


def test_eval_mean_score_per_label():
    records = [rec("a", ("easy", 2.0), ("hard", 8.0)), rec("b", ("easy", 3.0)), {"id": "c", "provider": "p"}]
    by = difficulty_by_label(records)
    assert list(by) == ["easy", "hard"]
    assert by["easy"] == {"n": 2, "mean": 2.5, "min": 2.0, "max": 3.0}
    assert by["hard"]["mean"] == 8.0
    assert aggregate(records)["difficulty_by_label"] == by
    text = render_report(records, summarize(records))
    assert "Difficulty by section label" in text and "| p/m | easy | 2 | 2.5 |" in text
    assert difficulty_by_label([]) == {}


def test_eval_record_has_difficulty():
    from eval.run import run_prompt
    from llm.validator import LLMController

    from eval.providers import CallBudget, CountingProvider

    fx = (ROOT / "tests/fixtures/llm/valid_two_sections.json").read_text(encoding="utf-8")
    prov = CountingProvider(FakeProvider([fx]), CallBudget(None, 0.0))
    r = run_prompt({"id": "x", "prompt": "two sections"}, prov, GameProfile())
    assert r["verified"]
    d = r["difficulty"]
    assert [s["label"] for s in d["sections"]] and all(0 <= s["score"] <= 10 for s in d["sections"])


# ─── API ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    return TestClient(server.app)


def generate(client, monkeypatch, tmp_path, fixture: str, prompt: str = "a level") -> Dict[str, Any]:
    fx = (ROOT / "tests/fixtures/llm" / fixture).read_text(encoding="utf-8")
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([fx]))
    out = tmp_path / "level.ldtk"
    body = client.post("/generate", json={"description": prompt, "output_path": str(out)}).json()
    assert body["success"], body.get("error")
    return body


def test_generate_response_and_get_difficulty(client, monkeypatch, tmp_path):
    body = generate(client, monkeypatch, tmp_path, "valid_two_sections.json")
    d = body["difficulty"]
    assert d["success"] and d["error"] is None and len(d["sections"]) == 2
    assert set(d["sections"][0]) == {"id", "name", "label", "score", "metrics", "warnings"}
    assert all(0 <= s["score"] <= 10 for s in d["sections"]) and 0 <= d["overall"] <= 10
    assert "mean_gap" in d["sections"][0]["metrics"]

    got = client.get("/difficulty", params={"path": str(tmp_path / "level.ldtk")}).json()
    assert got == d  # same numbers when read back from the file


def test_get_difficulty_errors(client, tmp_path):
    body = client.get("/difficulty", params={"path": str(tmp_path / "missing.ldtk")}).json()
    assert body["success"] is False and "does not exist" in body["error"] and body["sections"] == []


def test_edit_and_undo_responses_carry_difficulty(client, monkeypatch, tmp_path):
    generate(client, monkeypatch, tmp_path, "valid_two_sections.json")
    path = str(tmp_path / "level.ldtk")
    # undo with no history fails and keeps its v2 shape
    res = client.post("/undo", json={"path": path}).json()
    assert res["success"] is False and res.get("difficulty") is None
