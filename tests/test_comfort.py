"""Short hops and walk-first paths (v3 Part 20).

Covers the Game Profile's ``design`` comfort limits, the effort-based route cost
(``layout/route.py``), the comfort pass (``layout/comfort.py``) in generate and in
region edits, ``Section.style`` and densify's preference for comfortable positions.
Offline: FakeProvider answers and hand-made grids.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest
from fastapi.testclient import TestClient

import server
from edit.context import Rect, load_context
from edit.region import edit_region
from game.profile import Design, GameProfile, load_profile, save_profile
from layout import IREntity, TileValue, comfort
from layout.comfort import LONG_FACTOR, Move, golden_moves, moves_to_fix
from layout.route import (
    COMFORT_HOP_MAX,
    LONG_PENALTY,
    WALK_COST_PER_TILE,
    level_route,
    move_measure,
)
from llm import FakeProvider, LLMController
from pipeline import build_level
from spec import LevelSpec
from spec_builders import make_spec, node, section

ROOT = Path(__file__).resolve().parent.parent
PROFILE = GameProfile()


# ─────────────────────────────────────────────────────────────────────────────
# Game Profile
# ─────────────────────────────────────────────────────────────────────────────


def test_design_defaults_and_old_profiles_without_design(tmp_path):
    d = GameProfile().design
    assert (d.comfort_hop_tiles, d.comfort_rise_tiles, d.comfort_drop_tiles) == (4, 3, 6)
    old = tmp_path / "gldtk.game.json"  # a profile written before Part 20
    old.write_text(json.dumps({"version": 1, "name": "Old", "physics": {"gravity": 900}}),
                   encoding="utf-8")
    p = load_profile(old)
    assert p.name == "Old" and p.physics.gravity == 900 and p.design == Design()
    assert d.is_comfortable(4, 3, 0) and d.is_comfortable(4, 0, 6)
    assert not d.is_comfortable(5, 0, 0) and not d.is_comfortable(0, 4, 0)
    assert not d.is_comfortable(0, 0, 7)


def test_design_is_validated_and_in_the_prompt_summary(tmp_path):
    bad = tmp_path / "gldtk.game.json"
    bad.write_text(json.dumps({"design": {"comfort_hop_tiles": 0}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_profile(bad)
    bad.write_text(json.dumps({"design": {"comfort_hops": 3}}), encoding="utf-8")
    with pytest.raises(ValueError):  # strict: a typo is an error
        load_profile(bad)
    p = GameProfile(design=Design(comfort_hop_tiles=5, comfort_rise_tiles=2, comfort_drop_tiles=7))
    s = p.summary_for_prompt()
    assert "Comfortable moves" in s and "at most 5" in s and "at most 2" in s and "at most 7" in s


def test_profile_endpoint_round_trips_design(tmp_path):
    client = TestClient(server.app)
    ldtk = tmp_path / "level.ldtk"
    assert client.get("/profile", params={"path": str(ldtk)}).json()["design"] == {
        "comfort_hop_tiles": 4, "comfort_rise_tiles": 3, "comfort_drop_tiles": 6}
    body = GameProfile().model_dump()
    body["design"] = {"comfort_hop_tiles": 6, "comfort_rise_tiles": 2, "comfort_drop_tiles": 9}
    resp = client.put("/profile", params={"path": str(ldtk)}, json=body)
    assert resp.status_code == 200 and resp.json()["design"] == body["design"]
    assert client.get("/profile", params={"path": str(ldtk)}).json() == body
    assert load_profile(tmp_path / "gldtk.game.json").design.comfort_hop_tiles == 6
    body["design"]["comfort_rise_tiles"] = 0
    assert client.put("/profile", params={"path": str(ldtk)}, json=body).status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# Route cost
# ─────────────────────────────────────────────────────────────────────────────


def blank(w: int = 40, h: int = 20) -> List[List[int]]:
    return [[0] * w for _ in range(h)]


def platform(grid, row: int, c0: int, c1: int) -> None:
    for c in range(c0, c1 + 1):
        grid[row][c] = TileValue.SOLID


def ents(start, exit_) -> List[IREntity]:
    return [IREntity("Start", *start, {"iid": "s"}), IREntity("Exit", *exit_, {"iid": "e"})]


def test_route_takes_the_short_hops_not_one_long_jump():
    """A, B, C in a row with comfortable gaps; A → C is also one (long) plain jump."""
    grid = blank()
    platform(grid, 15, 0, 4)  # A
    platform(grid, 15, 8, 10)  # B: 3 empty columns on each side
    platform(grid, 15, 14, 18)  # C: A → C clears 9 columns, within physics reach
    from symbolic.extractor import extract_graph

    graph = extract_graph(grid, profile=PROFILE, collision=True)
    spans = {(n.metadata["row"], n.metadata["col_start"]): n.id for n in graph.nodes()}
    assert graph.nx_graph.has_edge(spans[(15, 0)], spans[(15, 14)])  # the long jump exists

    route = level_route(grid, ents((1, 14), (16, 14)))
    assert route.ok, route.error
    hops = [s for s in route.steps if s.kind != "walk"]
    assert len(hops) == 2 and all(s.kind == "jump" for s in hops)
    assert hops[0].from_cell[0] <= 4 and 8 <= hops[0].to_cell[0] <= 10  # A → B
    assert 8 <= hops[1].from_cell[0] <= 10 and 14 <= hops[1].to_cell[0] <= 18  # B → C


def test_cost_constants_keep_the_documented_order():
    # A long hop costs more than two (in fact three) comfortable hops plus W per tile.
    assert LONG_PENALTY > 2 * COMFORT_HOP_MAX
    assert 1 + 1 + LONG_PENALTY >= 3 * COMFORT_HOP_MAX  # and its effort term is > 1
    assert 0 < WALK_COST_PER_TILE < 1
    assert move_measure((15, 0, 4), (15, 8, 10)) == (3, 0, 0)
    assert move_measure((15, 8, 10), (12, 0, 4)) == (3, 3, 0)
    assert move_measure((10, 0, 4), (17, 2, 6)) == (0, 0, 7)


def test_mixed_sections_keep_one_long_jump_in_a_row():
    long_ = Move((15, 0, 3), (15, 10, 13), "jump", "mixed")  # gap 6 = 1.5 × 4: long
    huge = Move((15, 0, 3), (15, 16, 19), "jump", "mixed")  # gap 12: beyond long
    short = Move((15, 0, 3), (15, 6, 9), "jump", "mixed")  # gap 2: comfortable
    assert LONG_FACTOR == 1.5
    assert moves_to_fix([long_], PROFILE) == []
    assert moves_to_fix([long_, long_], PROFILE) == [long_]  # the second of two in a row
    assert moves_to_fix([long_, short, long_], PROFILE) == []
    assert moves_to_fix([huge], PROFILE) == [huge]
    walk = Move(long_.src, long_.dst, "jump", "walk")
    parkour = Move(huge.src, huge.dst, "jump", "parkour")
    assert moves_to_fix([walk], PROFILE) == [walk]
    assert moves_to_fix([parkour, parkour], PROFILE) == []


# ─────────────────────────────────────────────────────────────────────────────
# Comfort pass in generate
# ─────────────────────────────────────────────────────────────────────────────


def long_jump_spec(style=None) -> LevelSpec:
    """Start (cols 2-3) → Exit (cols 16-17) on one row: a 12-tile golden-path jump
    (centres 14 tiles apart, within the default plain jump's reach)."""
    sec = section("s1")
    sec["style"] = style
    return make_spec(
        [node("start", "Start", 2, 3, 2), node("exit", "Exit", 16, 3, 2)],
        [("start", "exit", "Jump")],
        sections=[sec], size=(30, 15),
    )


def build(spec: LevelSpec, profile: GameProfile = PROFILE, **kw):
    raw = json.dumps(spec.model_dump(by_alias=True))
    ctrl = LLMController(profile=profile, provider=FakeProvider([raw] * 3))
    return build_level("a level", ctrl, profile, seed=3, **kw)


def final_moves(built, profile: GameProfile = PROFILE) -> List[Move]:
    lay, rep = built.spec_layout, built.report
    return golden_moves(rep.graph, rep.start_span, rep.exit_spans, profile,
                        lambda c, r: lay.section_at(c, r).style)


@pytest.mark.parametrize("style", [None, "mixed", "walk"])
def test_a_12_tile_jump_gets_intermediate_platforms(style):
    built = build(long_jump_spec(style), densify=False)
    assert built.report.ok
    hops = [n for n in built.spec.nodes if n.id.startswith(comfort.HOP_PREFIX)]
    assert len(hops) == built.stats["comfort_added"] >= 1
    assert all(n.type == "Platform" and 3 * 16 <= n.w <= 5 * 16 for n in hops)
    moves = final_moves(built)
    assert moves and all(PROFILE.design.is_comfortable(*m.measure) for m in moves), \
        [m.describe() for m in moves]
    assert any(w.startswith("comfort: added") for w in built.warnings)
    assert not any("could not shorten" in w for w in built.warnings)
    assert set(built.spec_layout.added_nodes) >= {n.id for n in hops}


def test_a_parkour_section_keeps_the_long_jump():
    built = build(long_jump_spec("parkour"), densify=False)
    assert built.report.ok and built.stats["comfort_added"] == 0
    assert not any(n.id.startswith(comfort.HOP_PREFIX) for n in built.spec.nodes)
    moves = final_moves(built)
    assert [m.measure for m in moves] == [(12, 0, 0)] and moves[0].style == "parkour"
    assert not any(w.startswith("comfort") for w in built.warnings)


def test_an_impossible_fix_warns_and_never_fails(monkeypatch):
    monkeypatch.setattr(comfort, "find_stone", lambda *a, **k: None)
    built = build(long_jump_spec("walk"), densify=False)
    assert built.report.ok and built.stats["comfort_added"] == 0
    assert any("comfort: could not shorten the jump" in w and "12 tiles across" in w
               for w in built.warnings)


def test_stones_that_break_verification_are_removed(monkeypatch):
    real = comfort.verify_grid
    calls = []

    def failing(layout, profile, spec):
        calls.append(1)
        rep = real(layout, profile, spec)
        rep.ok = False
        return rep

    monkeypatch.setattr(comfort, "verify_grid", failing)
    built = build(long_jump_spec("walk"), densify=False)
    assert calls and built.report.ok and built.stats["comfort_added"] == 0
    assert not any(n.id.startswith(comfort.HOP_PREFIX) for n in built.spec.nodes)
    assert not any(n.startswith(comfort.HOP_PREFIX) for n in built.spec_layout.node_layouts)
    assert any("could not shorten" in w for w in built.warnings)


def test_wide_design_limits_add_nothing():
    wide = GameProfile(design=Design(comfort_hop_tiles=20, comfort_rise_tiles=20,
                                     comfort_drop_tiles=40))
    built = build(long_jump_spec("walk"), wide, densify=False)
    assert built.report.ok and built.stats["comfort_added"] == 0


def test_style_is_stored_in_gldtk_sections(tmp_path, monkeypatch):
    d = json.loads((ROOT / "tests/fixtures/llm/valid_two_sections.json").read_text(encoding="utf-8"))
    d["sections"][0]["style"] = "walk"
    d["sections"][1]["style"] = "parkour"
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([json.dumps(d)]))
    out = tmp_path / "level.ldtk"
    body = TestClient(server.app).post("/generate", json={
        "description": "a level", "output_path": str(out), "densify": False}).json()
    assert body["success"], body.get("error")
    level = json.loads(out.read_text(encoding="utf-8"))["levels"][0]
    fields = {f["__identifier"]: f for f in level["fieldInstances"]}
    sections = json.loads(fields["gldtk_sections"]["__value"])
    assert [s["style"] for s in sections] == ["walk", "parkour"]
    spec = json.loads(fields["gldtk_spec"]["__value"])
    assert [s["style"] for s in spec["sections"]] == ["walk", "parkour"]
    ctx = load_context(out)
    assert [b.raw.get("style") for b in ctx.bands] == ["walk", "parkour"]
    assert body["stats"]["comfort_added"] >= 1  # the 7-tile gap p2 → p3 starts in the walk meadow


def test_section_style_defaults_to_null_and_is_in_the_schema():
    spec = long_jump_spec()
    assert spec.sections[0].style is None
    props = LevelSpec.json_schema()["properties"]["sections"]["items"]["properties"]
    assert "style" in props
    with pytest.raises(Exception):
        long_jump_spec("sprint")


def test_prompt_explains_styles_and_short_hops():
    from llm import SYSTEM_PROMPT

    assert '"parkour"' in SYSTEM_PROMPT and '"walk"' in SYSTEM_PROMPT
    assert "short hops" in SYSTEM_PROMPT and "ONLY when the description asks for parkour" in SYSTEM_PROMPT
    start = SYSTEM_PROMPT.index('{"title"')
    example, _ = json.JSONDecoder().raw_decode(SYSTEM_PROMPT[start:])
    assert [s.style for s in LevelSpec.model_validate(example).sections] == ["walk", "mixed"]


# ─────────────────────────────────────────────────────────────────────────────
# Densify
# ─────────────────────────────────────────────────────────────────────────────


def test_densify_prefers_comfortable_positions():
    """Most fill platforms are within comfortable reach of another platform."""
    from test_densify import _sparse

    built = build(_sparse())
    lay = built.spec_layout
    fills = [n for n in lay.added_nodes if n.startswith("fill_")]
    assert fills
    others = [n for n in lay.node_layouts if lay.standable(n)]

    def comfy(n: str) -> bool:
        a = lay.rect(n)
        return any(PROFILE.design.is_comfortable(*move_measure(lay.rect(o), a))
                   for o in others if o != n)

    share = sum(comfy(n) for n in fills) / len(fills)
    assert share >= 0.75, share


# ─────────────────────────────────────────────────────────────────────────────
# Region edits
# ─────────────────────────────────────────────────────────────────────────────


WIDE = {"design": {"comfort_hop_tiles": 30, "comfort_rise_tiles": 30, "comfort_drop_tiles": 60}}


@pytest.fixture
def wide_level(tmp_path) -> Path:
    """The two-section level generated with wide limits: p2 (12-14, row 15) → p3
    (22-24, row 17) keeps its 7-tile gap; start → p1 is 3 tiles, p3 → exit 3 tiles."""
    (tmp_path / "gldtk.game.json").write_text(json.dumps(WIDE), encoding="utf-8")
    fx = (ROOT / "tests/fixtures/llm/valid_two_sections.json").read_text(encoding="utf-8")
    old = server.provider_factory
    server.provider_factory = lambda req: FakeProvider([fx])
    try:
        body = TestClient(server.app).post("/generate", json={
            "description": "meadow then dungeon", "output_path": str(tmp_path / "level.ldtk"),
            "densify": False}).json()
    finally:
        server.provider_factory = old
    assert body["success"], body.get("error")
    assert body["stats"]["comfort_added"] == 0
    save_profile(GameProfile(), tmp_path / "gldtk.game.json")  # back to the default limits
    return tmp_path / "level.ldtk"


def region_answer() -> str:
    return json.dumps({"nodes": [], "edges": [], "entities": [], "requirements": []})


def test_region_edit_applies_the_comfort_pass_inside_the_rect_only(wide_level):
    before = load_context(wide_level)
    rect = Rect(10, 10, 11, 10)  # columns 10..20, rows 10..19: p2 and the gap after it
    out = edit_region(str(wide_level), rect, "an empty stretch", FakeProvider([region_answer()]),
                      densify=False)
    assert out.success, out.error
    assert out.stats["comfort_added"] >= 1
    assert any(w.startswith("comfort: added") for w in out.warnings)
    after = load_context(wide_level)
    changed = [(c, r) for r, (a, b) in enumerate(zip(before.grid, after.grid))
               for c, (x, y) in enumerate(zip(a, b)) if int(x) != int(y)]
    added = [(c, r) for c, r in changed if int(after.grid[r][c]) == TileValue.SOLID]
    assert added and all(rect.contains(c, r) for c, r in changed)
    hops = [n for n in after.spec.nodes if n.id.startswith("hop_")]
    assert len(hops) == out.stats["comfort_added"]
    # the move out of the rect is now comfortable
    spans = after.extract(after.grid)
    start = next(e for e in after.entities if e.entity_type == "Start")
    exit_ = next(e for e in after.entities if e.entity_type == "Exit")
    moves = golden_moves(spans.graph, spans.under(start, 1), {spans.under(exit_, 1)}, PROFILE,
                         lambda c, r: None)
    assert all(PROFILE.design.is_comfortable(*m.measure) for m in moves), \
        [m.describe() for m in moves]


def test_region_edit_away_from_long_moves_adds_nothing(wide_level):
    rect = Rect(40, 2, 15, 6)  # empty sky in the dungeon, no golden-path move touches it
    out = edit_region(str(wide_level), rect, "nothing", FakeProvider([region_answer()]),
                      densify=False)
    assert out.success, out.error
    assert out.stats["comfort_added"] == 0
    assert not any(w.startswith("comfort") for w in out.warnings)
