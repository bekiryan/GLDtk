"""Golden route (v3 Part 13): ``layout/route.py``, ``GET /route`` and the response fields.

Offline: the levels are FakeProvider ``/generate`` outputs or small hand-made grids.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

import server
from edit.context import Spans, load_context
from game.profile import GameProfile
from layout import IREntity, TileValue
from layout.route import MOVE_COST, Route, _edge_kind, file_route, level_route
from llm import FakeProvider
from symbolic.extractor import extract_graph

ROOT = Path(__file__).resolve().parent.parent
TS = 16
FIXTURES = ["valid_two_sections.json", "valid_minimal.json", "completeness_fill.json"]


@pytest.fixture
def client():
    return TestClient(server.app)


def generate(client, monkeypatch, tmp_path, fixture: str, densify: bool = False) -> Dict[str, Any]:
    fx = (ROOT / "tests/fixtures/llm" / fixture).read_text(encoding="utf-8")
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([fx]))
    out = tmp_path / "level.ldtk"
    body = client.post("/generate", json={"description": "a level", "output_path": str(out),
                                          "densify": densify}).json()
    assert body["success"], body.get("error")
    return body


def check_points(route: Route, grid: List[List[Any]]) -> None:
    """Every feet point (sampled along the polyline every px) is inside the level, in empty air."""
    h, w = len(grid), len(grid[0])
    for step in route.steps:
        for (x0, y0), (x1, y1) in zip(step.points, step.points[1:]):
            n = max(1, math.ceil(math.dist((x0, y0), (x1, y1))))
            for i in range(n + 1):
                x, y = x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n
                assert 0 <= x <= w * TS and 0 <= y <= h * TS, (step.kind, x, y)
                col, row = int(x // TS), int((y - 1) // TS)  # the cell the feet are in
                if 0 <= col < w and 0 <= row < h:
                    assert int(grid[row][col]) == TileValue.EMPTY, (step.kind, x, y, col, row)


def check_kinds(route: Route, grid, profile, spans: Spans) -> None:
    """Each move step matches the kind of the graph edge between its spans."""
    g = spans.graph.nx_graph
    for step in route.steps:
        if step.kind == "walk":
            continue
        u = spans.at(*step.from_cell)
        v = spans.at(*step.to_cell)
        assert u is not None and v is not None, step
        kind, ability = _edge_kind(g.edges[u, v]["data"])
        assert (step.kind, step.ability) == (kind, ability)


# ─────────────────────────────────────────────────────────────────────────────
# Generated levels
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fixture", FIXTURES)
@pytest.mark.parametrize("densify", [False, True])
def test_route_of_generated_levels(client, monkeypatch, tmp_path, fixture, densify):
    body = generate(client, monkeypatch, tmp_path, fixture, densify)
    route = file_route(tmp_path / "level.ldtk")
    assert route.ok, route.error
    assert route.steps and route.length_px > 0
    assert route.unreachable_required == []

    ctx = load_context(tmp_path / "level.ldtk")
    start = next(e for e in ctx.entities if e.entity_type == "Start")
    exit_ = next(e for e in ctx.entities if e.entity_type == "Exit")
    first, last = route.steps[0].points[0], route.steps[-1].points[-1]
    assert first == ((start.col + 0.5) * TS, (start.row + 1) * TS)
    assert last[0] == (exit_.col + 0.5) * TS and last[1] == (exit_.row + 1) * TS
    for a, b in zip(route.steps, route.steps[1:]):
        assert a.points[-1] == b.points[0]  # one continuous polyline

    check_points(route, ctx.grid)
    spans = ctx.extract(ctx.grid)
    check_kinds(route, ctx.grid, ctx.profile, spans)
    assert {s.kind for s in route.steps} <= {"walk", "jump", "fall", "double_jump", "dash",
                                             "wall_jump"}
    # reachable spans are the spans the verifier reaches from the Start
    assert len(route.reachable_spans) == len(spans.reach(spans.under(start, depth=1)))

    # the generate response carries the same route
    assert body["route"]["success"] is True
    assert len(body["route"]["steps"]) == len(route.steps)
    assert body["route"]["level_iid"] == ctx.level.level_iid
    step0 = body["route"]["steps"][0]
    assert set(step0) >= {"kind", "from", "to", "points"}


def test_arcs_are_sampled_about_every_4px(client, monkeypatch, tmp_path):
    generate(client, monkeypatch, tmp_path, "valid_two_sections.json")
    route = file_route(tmp_path / "level.ldtk")
    arcs = [s for s in route.steps if s.kind != "walk"]
    assert arcs
    for s in arcs:
        gaps = [math.dist(a, b) for a, b in zip(s.points, s.points[1:])]
        assert max(gaps) <= 4.0 + 1e-6
        assert len(s.points) >= 3 and len(s.points) >= math.dist(s.points[0], s.points[-1]) / 4.0
    walks = [s for s in route.steps if s.kind == "walk"]
    assert all(len(s.points) == 2 and s.points[0][1] == s.points[1][1] for s in walks)


# ─────────────────────────────────────────────────────────────────────────────
# Hand-made grids: falls, abilities, cost order
# ─────────────────────────────────────────────────────────────────────────────


def blank(w: int = 40, h: int = 20) -> List[List[int]]:
    return [[0] * w for _ in range(h)]


def platform(grid, row: int, c0: int, c1: int) -> None:
    for c in range(c0, c1 + 1):
        grid[row][c] = TileValue.SOLID


def ents(start, exit_, *more) -> List[IREntity]:
    out = [IREntity("Start", *start, {"iid": "start-iid"}), IREntity("Exit", *exit_, {"iid": "exit-iid"})]
    return out + list(more)


def test_fall_step():
    grid = blank()
    platform(grid, 8, 0, 4)
    platform(grid, 15, 8, 14)
    route = level_route(grid, ents((1, 7), (12, 14)))
    assert route.ok, route.error
    kinds = [s.kind for s in route.steps]
    assert "fall" in kinds and "jump" not in kinds
    fall = next(s for s in route.steps if s.kind == "fall")
    assert fall.points[-1][1] == 15 * TS  # lands on row 15
    check_points(route, grid)


def test_dash_step_when_the_gap_is_too_wide():
    grid = blank()
    platform(grid, 15, 0, 4)
    platform(grid, 15, 22, 30)
    plain = level_route(grid, ents((1, 14), (28, 14)))
    assert not plain.ok and "Exit" in plain.error
    assert plain.unreachable_required == ["exit-iid"]
    assert plain.reachable_spans == [(0, 15, 5)]

    profile = GameProfile()
    profile.abilities.dash.enabled = True
    route = level_route(grid, ents((1, 14), (28, 14)), profile)
    assert route.ok, route.error
    dash = [s for s in route.steps if s.kind == "dash"]
    assert len(dash) == 1 and dash[0].ability == "dash"
    check_points(route, grid)
    spans = Spans(extract_graph(grid, profile=profile, collision=True))
    check_kinds(route, grid, profile, spans)


def test_plain_moves_are_preferred_over_abilities():
    grid = blank()
    platform(grid, 15, 0, 4)
    platform(grid, 15, 12, 16)  # stepping stone, a plain jump away
    platform(grid, 15, 26, 30)
    profile = GameProfile()
    profile.abilities.dash.enabled = True
    route = level_route(grid, ents((1, 14), (28, 14)), profile)
    assert route.ok
    assert [s.kind for s in route.steps if s.kind != "walk"] == ["jump", "jump"]
    assert MOVE_COST["walk"] < MOVE_COST["fall"] < MOVE_COST["jump"] < MOVE_COST["ability"]


def test_unreachable_required_entities_and_signs():
    grid = blank()
    platform(grid, 15, 0, 10)
    platform(grid, 3, 30, 34)  # far up and away
    coin = IREntity("Pickup", 32, 2, {"iid": "coin-iid"})
    sign = IREntity("Interactive", 33, 2, {"iid": "sign-iid", "interactive_kind": "sign"})
    enemy = IREntity("Enemy", 31, 2, {"iid": "enemy-iid"})
    route = level_route(grid, ents((1, 14), (8, 14), coin, sign, enemy))
    assert route.ok
    assert route.unreachable_required == ["coin-iid"]


def test_no_start_and_start_in_the_air():
    grid = blank()
    platform(grid, 15, 0, 10)
    assert level_route(grid, [IREntity("Exit", 8, 14)]).error == "the level has no Start entity"
    r = level_route(grid, ents((1, 5), (8, 14)))
    assert not r.ok and "Start" in r.error and "no platform" in r.error


# ─────────────────────────────────────────────────────────────────────────────
# Server
# ─────────────────────────────────────────────────────────────────────────────


def test_route_endpoint(client, monkeypatch, tmp_path):
    body = generate(client, monkeypatch, tmp_path, "valid_two_sections.json")
    resp = client.get("/route", params={"path": str(tmp_path / "level.ldtk")})
    assert resp.status_code == 200
    assert resp.json() == body["route"]


def test_route_endpoint_respects_a_hand_edit_that_breaks_the_path(client, monkeypatch, tmp_path):
    generate(client, monkeypatch, tmp_path, "valid_two_sections.json")
    path = tmp_path / "level.ldtk"
    doc = json.loads(path.read_text(encoding="utf-8"))
    level = doc["levels"][0]
    grid_layer = next(li for li in level["layerInstances"] if li["__type"] == "IntGrid")
    cw = grid_layer["__cWid"]
    csv = grid_layer["intGridCsv"]
    for c in range(cw):  # remove the platforms on row 15 (p1, p2 and the Exit's platform)
        csv[15 * cw + c] = 0
    path.write_text(json.dumps(doc), encoding="utf-8")

    body = client.get("/route", params={"path": str(path)}).json()
    assert body["success"] is False
    assert "Exit" in body["error"]
    assert body["steps"] == []
    assert body["reachable_spans"]  # the Start's own platform is still shaded


def test_post_route_uses_the_unsaved_project(client, monkeypatch, tmp_path):
    body = generate(client, monkeypatch, tmp_path, "valid_two_sections.json")
    path = tmp_path / "level.ldtk"
    raw = path.read_bytes()
    doc = json.loads(raw)
    same = client.post("/route", json={"path": str(path), "project": doc}).json()
    assert same == body["route"]

    grid_layer = next(li for li in doc["levels"][0]["layerInstances"] if li["__type"] == "IntGrid")
    cw = grid_layer["__cWid"]
    for c in range(cw):
        grid_layer["intGridCsv"][15 * cw + c] = 0
    broken = client.post("/route", json={"path": str(path), "project": doc}).json()
    assert broken["success"] is False and "Exit" in broken["error"]
    assert path.read_bytes() == raw  # the file on disk is untouched …
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".ldtk") == ["level.ldtk"]  # … no temp left

    bad = client.post("/route", json={"path": str(path), "project": {"levels": []}}).json()
    assert bad["success"] is False and bad["error"]
    assert "gldtk-route" not in bad["error"]


def test_route_endpoint_on_a_broken_file_is_a_clean_error(client, tmp_path):
    bad = tmp_path / "bad.ldtk"
    bad.write_text("{not json", encoding="utf-8")
    body = client.get("/route", params={"path": str(bad)}).json()
    assert body["success"] is False and "not valid LDtk JSON" in body["error"]
    body = client.get("/route", params={"path": str(tmp_path / "missing.ldtk")}).json()
    assert body["success"] is False and "does not exist" in body["error"]


def test_start_moved_into_the_air_by_hand(client, monkeypatch, tmp_path):
    generate(client, monkeypatch, tmp_path, "valid_two_sections.json")
    path = tmp_path / "level.ldtk"
    doc = json.loads(path.read_text(encoding="utf-8"))
    for li in doc["levels"][0]["layerInstances"]:
        for e in li["entityInstances"]:
            if e["__identifier"] == "Start":
                e["__grid"] = [e["__grid"][0], 1]
                e["px"] = [e["px"][0], 1 * 16 + 15]
    path.write_text(json.dumps(doc), encoding="utf-8")
    body = client.get("/route", params={"path": str(path)}).json()
    assert body == {**body, "success": False, "steps": []}
    assert "Start" in body["error"]


def test_undo_response_has_the_route(client, monkeypatch, tmp_path):
    generate(client, monkeypatch, tmp_path, "valid_minimal.json")
    generate(client, monkeypatch, tmp_path, "valid_two_sections.json")
    body = client.post("/undo", json={"path": str(tmp_path / "level.ldtk")}).json()
    assert body["success"]
    assert body["route"]["success"] is True and body["route"]["steps"]


def test_edit_response_has_the_route(client, monkeypatch, tmp_path):
    generate(client, monkeypatch, tmp_path, "valid_two_sections.json")
    answer = json.dumps({
        "nodes": [{"id": "mid", "type": "Platform", "col": 7, "row": 6, "w": 3}],
        "edges": [], "entities": [], "requirements": []})
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([answer]))
    path = tmp_path / "level.ldtk"
    body = client.post("/edit/region", json={
        "path": str(path), "prompt": "a ledge",
        "rect": {"col": 10, "row": 10, "w": 11, "h": 10}}).json()
    assert body["success"], body["error"]
    assert body["route"]["success"] is True
    assert body["route"] == client.get("/route", params={"path": str(path)}).json()
