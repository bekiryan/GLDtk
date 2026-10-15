"""Regional editing + version history (Part 8a): ``edit/`` and the server routes.

Offline: every LLM answer is a FakeProvider fixture.  The base level is a real
``/generate`` output (``valid_two_sections.json``, 60×20, no densify):

    row 14   .......P.....P...............I.X......   coin1 (7,14), coin2 (13,14)
    row 15   ......###...###.............####......   p1 6-8, p2 12-14, exit 28-31
    row 16   .S.....................E..............   Start (1,16), skeleton (23,16)
    row 17   ###...................###.............   start 0-2, p3 22-24
    row 18   ..................^^^.................   spikes 18-20
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient

import server
from edit import history
from edit.check import verify_level
from edit.context import Rect, _bands, find_seams, load_context
from edit.entities import edit_entities
from edit.locate import locate
from edit.region import edit_region, edit_section, section_rect
from layout import IREntity, IRLevel, TileValue
from layout.ldtk_adapter import to_ldtk_dict
from layout.ldtk_reader import read_level
from llm import FakeProvider

ROOT = Path(__file__).resolve().parent.parent
REGION = Rect(10, 10, 11, 10)  # columns 10..20, rows 10..19: p2, coin2, the spikes


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures and helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def base_level(tmp_path_factory) -> bytes:
    fx = (ROOT / "tests/fixtures/llm/valid_two_sections.json").read_text(encoding="utf-8")
    out = tmp_path_factory.mktemp("base") / "level.ldtk"
    old = server.provider_factory
    server.provider_factory = lambda req: FakeProvider([fx])
    try:
        body = TestClient(server.app).post(
            "/generate", json={"description": "meadow then dungeon", "output_path": str(out),
                               "densify": False}).json()
    finally:
        server.provider_factory = old
    assert body["success"], body.get("error")
    return out.read_bytes()


@pytest.fixture
def level(tmp_path, base_level) -> Path:
    p = tmp_path / "level.ldtk"
    p.write_bytes(base_level)
    return p


@pytest.fixture
def client():
    return TestClient(server.app)


def region_answer(nodes, entities=(), requirements=(), edges=()) -> str:
    return json.dumps({"nodes": list(nodes), "edges": list(edges), "entities": list(entities),
                       "requirements": list(requirements)})


def platform(nid, col, row, w=3, kind="Platform"):
    return {"id": nid, "type": kind, "col": col, "row": row, "w": w}


def gem(eid="gem", anchor="mid", **extra):
    e = {"id": eid, "kind": "Pickup", "anchor": anchor, "section": None, "description": "a gem",
         "sprite": "gem", "pickup_kind": "gem", "value": 5}
    e.update(extra)
    return e


GOOD_REGION = region_answer(
    [platform("mid", 7, 6), platform("lava", 3, 9, 6, "Hazard")],
    [gem()],
    [{"id": "R1", "text": "There is a lava pit.", "category": "layout",
      "check": {"type": "hazard", "min_count": 1}}],
)


def doc_of(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def level_of(doc):
    return doc["levels"][0]


def layer(doc, kind: str, name: Optional[str] = None):
    return next(li for li in level_of(doc)["layerInstances"]
                if li["__type"] == kind and (name is None or li["__identifier"] == name))


def entities_by_iid(doc) -> Dict[str, Dict[str, Any]]:
    return {ei["iid"]: ei for li in level_of(doc)["layerInstances"] for ei in li["entityInstances"]}


def tiles_by_cell(doc, ts=16) -> Dict[tuple, list]:
    out: Dict[tuple, list] = {}
    for li in level_of(doc)["layerInstances"]:
        for t in li["gridTiles"]:
            out.setdefault((li["__identifier"], t["px"][0] // ts, t["px"][1] // ts), []).append(t)
    return out


def iid_of(doc, spec_id: str) -> str:
    for iid, ei in entities_by_iid(doc).items():
        if any(fi["__identifier"] == "spec_id" and fi["__value"] == spec_id for fi in ei["fieldInstances"]):
            return iid
    raise KeyError(spec_id)


def _schema_validator():
    jsonschema = pytest.importorskip("jsonschema")
    for base in [ROOT, *ROOT.parents]:
        p = base / "ldtk-src" / "docs" / "JSON_SCHEMA.json"
        if p.is_file():
            return jsonschema.Draft7Validator(json.loads(p.read_text(encoding="utf-8")))
    pytest.skip("ldtk-src/docs/JSON_SCHEMA.json not found")


def assert_schema_valid(doc) -> None:
    v = _schema_validator()
    errors = list(v.iter_errors(json.loads(json.dumps(doc))))
    assert not errors, [f"{list(e.absolute_path)}: {e.message[:120]}" for e in errors[:10]]


# ─────────────────────────────────────────────────────────────────────────────
# Context and seams
# ─────────────────────────────────────────────────────────────────────────────


def test_context_reads_sections_and_profile(level):
    ctx = load_context(level)
    assert (ctx.width, ctx.height) == (60, 20)
    assert [b.id for b in ctx.bands] == ["meadow", "dungeon"]
    assert ctx.band_at(1, 16).id == "meadow" and ctx.band_at(40, 3).id == "dungeon"
    assert ctx.spec is not None and ctx.profile_path.name == "gldtk.game.json"


def test_seams_lock_the_platform_the_path_uses(level):
    ctx = load_context(level)
    seams = find_seams(ctx, REGION, ctx.extract(ctx.grid))
    # p1 → p2 (jump in) and p2 → p3 (fall out) both use p2, which is inside.
    assert [(s.row, s.col_start, s.col_end, s.direction) for s in seams] == [(15, 12, 14, "both")]


def test_straddling_span_is_a_walk_seam(level):
    ctx = load_context(level)
    rect = Rect(13, 10, 8, 10)  # cuts p2 (12-14) in two
    seams = find_seams(ctx, rect, ctx.extract(ctx.grid))
    walk = [s for s in seams if s.kind == "walk"]
    assert walk and walk[0].side == "left" and (walk[0].col_start, walk[0].col_end) == (13, 14)


def test_vertical_section_bands_give_row_rects():
    bands = _bands([
        {"id": "base", "name": "Base", "orientation": "vertical", "col_start": 0, "col_end": 39,
         "row_start": 34, "row_end": 59},
        {"id": "top", "name": "Top", "orientation": "vertical", "col_start": 0, "col_end": 39,
         "row_start": 0, "row_end": 33},
        {"id": "old", "name": "Old", "col_start": 0, "col_end": 9},  # pre-vertical: all rows
    ], 40, 60)
    assert bands[0].rect == Rect(0, 34, 40, 26)
    assert bands[1].rect == Rect(0, 0, 40, 34)
    assert bands[2].rect == Rect(0, 0, 10, 60)


# ─────────────────────────────────────────────────────────────────────────────
# Region edits
# ─────────────────────────────────────────────────────────────────────────────


def test_region_edit_changes_nothing_outside(level):
    before = doc_of(level)
    out = edit_region(str(level), REGION, "a lava pit with a gem", FakeProvider([GOOD_REGION]))
    assert out.success, out.error
    after = doc_of(level)
    assert_schema_valid(after)

    w = 60
    old_csv = layer(before, "IntGrid")["intGridCsv"]
    new_csv = layer(after, "IntGrid")["intGridCsv"]
    for i, (a, b) in enumerate(zip(old_csv, new_csv)):
        if not REGION.contains(i % w, i // w):
            assert a == b, f"IntGrid cell {(i % w, i // w)} changed"
    assert new_csv[19 * w + 13] == TileValue.HAZARD  # the lava (region row 9, column 3)

    # Entities outside the rect: byte-identical JSON instances.
    old_e, new_e = entities_by_iid(before), entities_by_iid(after)
    for iid, ei in old_e.items():
        if not REGION.contains(*ei["__grid"]):
            assert new_e[iid] == ei
    assert iid_of(before, "coin2") not in new_e  # coin2 lived in the region
    assert any(ei["__identifier"] == "Pickup" and ei["__grid"] == [18, 15] for ei in new_e.values())

    # Tiles outside the rect + 1-tile margin are untouched; the margin re-autotiled.
    margin = REGION.expanded(1, 60, 20)
    old_t, new_t = tiles_by_cell(before), tiles_by_cell(after)
    for key, tiles in old_t.items():
        if not margin.contains(key[1], key[2]):
            assert new_t.get(key) == tiles
    for key in new_t:
        assert key in old_t or margin.contains(key[1], key[2])
    assert any((key[1], key[2]) == (17, 16) for key in new_t)  # the new 'mid' platform has art

    # Level fields: the region's requirement was added; the prompt is unchanged.
    back = read_level(after)
    assert back.prompt == "meadow then dungeon"
    assert back.requirements[-1] == "There is a lava pit."
    assert back.spec is not None and any(n.type == "Hazard" for n in back.spec.nodes)
    assert out.fidelity.items[-1].status == "satisfied"


def test_region_edit_keeps_seams_and_verifies(level):
    out = edit_region(str(level), REGION, "a lava pit", FakeProvider([GOOD_REGION]))
    assert out.success and out.verified
    ctx = load_context(level)
    for c in (12, 13, 14):
        assert ctx.grid[15][c] == TileValue.SOLID  # the locked seam
        assert ctx.grid[14][c] == TileValue.EMPTY and ctx.grid[13][c] == TileValue.EMPTY
    check = verify_level(ctx, ctx.grid, ctx.entities)
    assert check.ok, check.messages()


def test_repair_keeps_an_unreachable_ledge_with_a_stepping_stone(level):
    rect = Rect(10, 2, 11, 18)
    # A ledge 12 tiles above p2: out of jump reach, so the gem on it is unreachable.
    # Repair links the ledge (the prompt asked for it) instead of moving the gem off it.
    answer = region_answer([platform("far", 1, 1, 3)], [gem(anchor="far")])
    out = edit_region(str(level), rect, "a high ledge with a gem", FakeProvider([answer]))
    assert out.success, out.error
    assert out.stats["llm_attempts"] == 1 and out.stats["grid_repairs"] >= 1
    assert any("stepping stone" in w and "gem" in w for w in out.warnings)
    assert not any(w.startswith("moved gem") for w in out.warnings)
    ctx = load_context(level)
    assert verify_level(ctx, ctx.grid, ctx.entities).ok
    gem_ent = next(e for e in ctx.entities if e.metadata.get("spec_id") == "gem")
    assert (gem_ent.row, 11 <= gem_ent.col <= 13) == (2, True)  # still on the ledge (row 3)


def test_repair_moves_an_unreachable_entity_when_no_stone_fits(level, monkeypatch):
    import edit.region as region_mod

    monkeypatch.setattr(region_mod, "_insert_stone", lambda *a, **k: None)
    rect = Rect(10, 2, 11, 18)
    answer = region_answer([platform("far", 1, 1, 3)], [gem(anchor="far")])
    out = edit_region(str(level), rect, "a high ledge with a gem", FakeProvider([answer]))
    assert out.success, out.error
    assert any(w.startswith("moved gem") for w in out.warnings)
    ctx = load_context(level)
    assert verify_level(ctx, ctx.grid, ctx.entities).ok


def test_repair_bridges_a_gap_with_stepping_stones(tmp_path):
    p = tmp_path / "flat.ldtk"
    _v1_file(p)
    # The region cuts 26 tiles out of the ground; only the 3-tile seams stay, and
    # the 20-tile gap between them is wider than a jump.
    out = edit_region(str(p), Rect(10, 6, 26, 9), "remove the floor", FakeProvider([region_answer([])]))
    assert out.success, out.error
    assert out.stats["grid_repairs"] >= 1 and out.stats["llm_attempts"] == 1
    assert any("stepping stone" in w for w in out.warnings)
    ctx = load_context(p)
    assert verify_level(ctx, ctx.grid, ctx.entities).ok
    assert ctx.grid[14][10:13] == [TileValue.SOLID] * 3 and ctx.grid[14][33:36] == [TileValue.SOLID] * 3


# A wall over the whole height (its top row a hazard, so nobody stands on it).
WALL = region_answer([platform(f"w{r}", 0, r, 6) for r in range(1, 20)]
                     + [platform("top", 0, 0, 6, "Hazard")])


def test_unverifiable_region_is_not_written(level):
    raw = level.read_bytes()
    fake = FakeProvider([WALL, WALL])
    out = edit_region(str(level), Rect(15, 0, 6, 20), "a solid wall", fake)
    assert not out.success
    assert "not changed" in out.error and "SEAM_BROKEN" not in out.error
    assert "reachable" in out.error or "Exit" in out.error
    assert len(fake.calls) == 2  # exactly one re-prompt with the verification errors
    assert "LEVEL VERIFICATION FAILED" in fake.calls[1].messages[-1]["content"]
    assert level.read_bytes() == raw
    assert history.list_snapshots(level) == []


def test_region_prompt_has_the_seams_view_and_profile(level):
    fake = FakeProvider([GOOD_REGION])
    edit_region(str(level), REGION, "a lava pit", fake)
    call = fake.calls[0]
    text = call.messages[0]["content"]
    assert "REQUEST: a lava pit" in text
    assert "seam1" in text and "11 × 10 tiles" in text
    assert "GAME PROFILE" in text and "MAP" in text
    assert "coin2" in text  # the region's current entities are listed
    from edit.prompts import REGION_SYSTEM_PROMPT

    assert call.system == REGION_SYSTEM_PROMPT  # static, cacheable
    assert set(call.schema["properties"]) == {"nodes", "edges", "entities", "requirements"}


def test_invalid_answer_is_reprompted(level):
    fake = FakeProvider(["not json", GOOD_REGION])
    out = edit_region(str(level), REGION, "a lava pit", fake)
    assert out.success and out.stats["llm_attempts"] == 2
    assert "VALIDATION FAILED" in fake.calls[1].messages[-1]["content"]


def test_hand_edited_file_is_respected(level):
    doc = doc_of(level)
    coin1 = iid_of(doc, "coin1")
    skel = iid_of(doc, "skeleton")
    for ei in layer(doc, "Entities")["entityInstances"]:
        if ei["iid"] == coin1:  # moved by hand
            ei["__grid"] = [6, 14]
            ei["px"] = [6 * 16 + 8, 15 * 16]
            ei["__worldX"] = ei["px"][0]
        if ei["iid"] == skel:  # a field edited by hand
            for fi in ei["fieldInstances"]:
                if fi["__identifier"] == "health":
                    fi["__value"] = 99
                    fi["realEditorValues"] = [{"id": "V_Int", "params": [99]}]
    layer(doc, "IntGrid")["intGridCsv"][18 * 60 + 40] = 1  # a painted tile
    level.write_text(json.dumps(doc, indent="\t"), encoding="utf-8")
    edited = doc_of(level)

    out = edit_region(str(level), REGION, "a lava pit", FakeProvider([GOOD_REGION]))
    assert out.success, out.error
    after = doc_of(level)
    ents = entities_by_iid(after)
    assert ents[coin1]["__grid"] == [6, 14]
    assert ents[skel] == entities_by_iid(edited)[skel]
    assert layer(after, "IntGrid")["intGridCsv"][18 * 60 + 40] == 1
    assert read_level(after).entities  # still readable


def _v1_file(path: Path) -> None:
    ir = IRLevel.empty("Level_0", 40, 15)
    ir.fill_rect(0, 14, 40, 1, TileValue.SOLID)
    ir.entities = [IREntity("Start", 1, 13), IREntity("Exit", 38, 13)]
    doc = to_ldtk_dict(ir)
    lvl = doc["levels"][0]
    lvl["fieldInstances"] = []
    doc["defs"]["levelFields"] = []
    for li in lvl["layerInstances"]:
        for ei in li["entityInstances"]:
            ei["fieldInstances"] = []
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_v1_file_without_gldtk_fields(tmp_path):
    p = tmp_path / "old.ldtk"
    _v1_file(p)
    old_iids = {ei["iid"] for ei in entities_by_iid(doc_of(p)).values()}
    answer = region_answer([platform("step", 2, 3, 4)], [gem(anchor="step")])
    out = edit_region(str(p), Rect(15, 6, 10, 9), "a step with a gem", FakeProvider([answer]))
    assert out.success, out.error
    assert any("re-exported" in w for w in out.warnings)
    doc = doc_of(p)
    assert_schema_valid(doc)
    back = read_level(doc)
    assert back.version == "2" and back.sections and back.spec is not None
    assert old_iids <= set(entities_by_iid(doc))
    assert [e.entity_type for e in back.entities].count("Pickup") == 1
    ground = back.grid[14]
    assert ground[:18] == [TileValue.SOLID] * 18 and ground[22:] == [TileValue.SOLID] * 18  # seams


def test_section_edit_uses_the_band(level):
    ctx = load_context(level)
    band = section_rect(ctx, "dungeon")
    assert band.row == 0 and band.h == 20 and band.right == 59
    fake = FakeProvider([region_answer([], [])])
    out = edit_section(str(level), "dungeon", "keep it", fake)
    assert out.mode == "section"
    assert out.rect == band
    assert f"{band.w} × 20 tiles" in fake.calls[0].messages[0]["content"]
    assert out.success, out.error  # seams keep p3 and the exit platform; nothing else needed


def test_unknown_section_is_an_error(level):
    out = edit_section(str(level), "nope", "x", FakeProvider([]))
    assert not out.success and "meadow" in out.error


def test_requirement_turned_missing_is_reported(level):
    req = [{"id": "R1", "text": "A gem in the middle.", "category": "entity",
            "check": {"type": "entity_count", "entity_kind": "Pickup", "subkind": "gem",
                      "min_count": 1, "section": None}}]
    first = region_answer([platform("mid", 7, 6)], [gem()], req)
    assert edit_region(str(level), REGION, "a gem", FakeProvider([first])).success
    second = region_answer([platform("mid", 7, 6)], [])
    out = edit_region(str(level), REGION, "no gem", FakeProvider([second]))
    assert out.success
    assert any("no longer met" in w and "A gem in the middle." in w for w in out.warnings)
    assert out.fidelity.items[-1].status == "missing"


# ─────────────────────────────────────────────────────────────────────────────
# Entity edits
# ─────────────────────────────────────────────────────────────────────────────


def ops(*operations, requirements=()) -> str:
    return json.dumps({"operations": list(operations), "requirements": list(requirements)})


def fields(**kw) -> Dict[str, Any]:
    return kw


def test_entity_edit_only_touches_the_chosen_entities(level):
    before = doc_of(level)
    coin1 = iid_of(before, "coin1")
    fake = FakeProvider([ops({"op": "modify", "target": "E1", "anchor": None,
                              "entity": fields(value=10)})])
    out = edit_entities(str(level), "make this coin worth 10", fake, entity_iids=[coin1])
    assert out.success, out.error
    assert "E1: Pickup" in fake.calls[0].messages[0]["content"]
    after = doc_of(level)
    assert_schema_valid(after)
    old_e, new_e = entities_by_iid(before), entities_by_iid(after)
    assert set(old_e) == set(new_e)
    for iid in old_e:
        if iid != coin1:
            assert new_e[iid] == old_e[iid]
    value = next(fi for fi in new_e[coin1]["fieldInstances"] if fi["__identifier"] == "value")
    assert value["__value"] == 10
    assert new_e[coin1]["__grid"] == old_e[coin1]["__grid"]
    assert layer(after, "IntGrid") == layer(before, "IntGrid")
    assert tiles_by_cell(after) == tiles_by_cell(before)
    assert out.rect is None


def test_entity_edit_adds_and_removes_in_a_rect(level):
    before = doc_of(level)
    rect = Rect(4, 10, 12, 8)  # p1, p2, coin1, coin2
    answer = ops(
        {"op": "remove", "target": "E1", "anchor": None, "entity": None},
        *[{"op": "add", "target": None, "anchor": "P1",
           "entity": fields(kind="Pickup", pickup_kind="gem", description="gem", value=3)}
          for _ in range(2)],
    )
    fake = FakeProvider([answer])
    out = edit_entities(str(level), "swap the coin for 2 gems", fake, rect=rect)
    assert out.success, out.error
    text = fake.calls[0].messages[0]["content"]
    assert "P1: row 15, columns 6..8" in text and "P2: row 15, columns 12..14" in text
    after = doc_of(level)
    old_e, new_e = entities_by_iid(before), entities_by_iid(after)
    assert iid_of(before, "coin1") not in new_e
    gems = [ei for ei in new_e.values() if ei["__identifier"] == "Pickup"
            and any(fi["__value"] == "Gem" for fi in ei["fieldInstances"])]
    assert len(gems) == 2 and all(g["__grid"][1] == 14 and 6 <= g["__grid"][0] <= 8 for g in gems)
    assert len({tuple(g["__grid"]) for g in gems}) == 2
    for iid, ei in old_e.items():
        if iid != iid_of(before, "coin1"):
            assert new_e[iid] == ei
    assert out.rect == rect


def test_entity_edit_cannot_remove_the_start(level):
    raw = level.read_bytes()
    start = iid_of(doc_of(level), "player")
    bad = ops({"op": "remove", "target": "E1", "anchor": None, "entity": None})
    out = edit_entities(str(level), "remove the player", FakeProvider([bad, bad, bad]),
                        entity_iids=[start])
    assert not out.success and "Start" in out.error
    assert level.read_bytes() == raw


def test_removed_link_target_becomes_a_warning(level):
    doc = doc_of(level)
    chest, coin1 = iid_of(doc, "chest"), iid_of(doc, "coin1")
    # Link the chest to coin1 by hand, then remove coin1 with an entity edit.
    for ei in layer(doc, "Entities")["entityInstances"]:
        if ei["iid"] == chest:
            for fi in ei["fieldInstances"]:
                if fi["__identifier"] == "linked_to":
                    ent_layer = layer(doc, "Entities")
                    fi["__value"] = {"entityIid": coin1, "layerIid": ent_layer["iid"],
                                     "levelIid": level_of(doc)["iid"], "worldIid": doc["dummyWorldIid"]}
                    fi["realEditorValues"] = [{"id": "V_String", "params": [coin1]}]
    level.write_text(json.dumps(doc), encoding="utf-8")
    out = edit_entities(str(level), "remove it",
                        FakeProvider([ops({"op": "remove", "target": "E1", "anchor": None, "entity": None})]),
                        entity_iids=[coin1])
    assert out.success, out.error
    assert any("linked_to" in w and "cleared" in w for w in out.warnings)
    linked = next(fi for fi in entities_by_iid(doc_of(level))[chest]["fieldInstances"]
                  if fi["__identifier"] == "linked_to")
    assert linked["__value"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Locate
# ─────────────────────────────────────────────────────────────────────────────


def test_locate_returns_a_rect_and_writes_nothing(level):
    raw = level.read_bytes()
    fake = FakeProvider([json.dumps({"col": 15, "row": 12, "w": 9, "h": 8,
                                     "explanation": "The spike pit before the skeleton."})])
    out = locate(str(level), "the spikes before the skeleton", fake)
    assert out.success and out.rect == Rect(15, 12, 9, 8)
    assert out.explanation.startswith("The spike pit")
    text = fake.calls[0].messages[0]["content"]
    assert "LOCATION: the spikes before the skeleton" in text and "MAP" in text
    assert level.read_bytes() == raw and history.list_snapshots(level) == []


def test_locate_clamps_into_the_level(level):
    fake = FakeProvider([json.dumps({"col": 55, "row": 18, "w": 20, "h": 9, "explanation": "edge"})])
    out = locate(str(level), "the far right", fake)
    assert out.rect == Rect(40, 11, 20, 9)


# ─────────────────────────────────────────────────────────────────────────────
# History
# ─────────────────────────────────────────────────────────────────────────────


def test_undo_restores_byte_for_byte_and_walks_back(level):
    v0 = level.read_bytes()
    assert edit_region(str(level), REGION, "a lava pit", FakeProvider([GOOD_REGION])).success
    v1 = level.read_bytes()
    second = region_answer([platform("mid", 2, 6)], [])
    assert edit_region(str(level), REGION, "a ledge", FakeProvider([second])).success
    assert level.read_bytes() not in (v0, v1)

    snaps = history.list_snapshots(level)
    assert [s.label for s in snaps] == ["region: a lava pit", "region: a ledge"]
    res = history.undo(level)
    assert res.success and res.remaining == 1 and level.read_bytes() == v1
    res = history.undo(level)
    assert res.success and res.remaining == 0 and level.read_bytes() == v0
    res = history.undo(level)
    assert not res.success and "nothing to undo" in res.error


def test_snapshots_are_capped(tmp_path):
    p = tmp_path / "x.ldtk"
    for i in range(history.MAX_SNAPSHOTS + 5):
        p.write_text(str(i))
        history.snapshot(p, f"edit {i}")
    snaps = history.list_snapshots(p)
    assert len(snaps) == history.MAX_SNAPSHOTS
    assert snaps[0].label == "edit 5" and snaps[-1].label == f"edit {history.MAX_SNAPSHOTS + 4}"
    assert snaps[0].file.read_text() == "5"
    assert [s.timestamp for s in snaps] == sorted(s.timestamp for s in snaps)
    index = json.loads((history.history_dir(p) / "index.json").read_text())
    assert len(index) == history.MAX_SNAPSHOTS


def test_label_shortens_long_prompts():
    label = history.make_label("region", "word " * 40)
    assert label.startswith("region: word") and label.endswith("…") and len(label) < 80


# ─────────────────────────────────────────────────────────────────────────────
# Server routes
# ─────────────────────────────────────────────────────────────────────────────


def test_routes_region_history_undo(client, level, monkeypatch):
    v0 = level.read_bytes()
    seen = {}

    def factory(req):
        seen["provider"], seen["model"] = req.provider, req.model
        return FakeProvider([GOOD_REGION])

    monkeypatch.setattr(server, "provider_factory", factory)
    body = client.post("/edit/region", json={
        "path": str(level), "prompt": "a lava pit", "provider": "gemini", "seed": 1,
        "rect": REGION.as_dict()}).json()
    assert body["success"], body["error"]
    assert seen == {"provider": "gemini", "model": None}
    assert body["edit"]["mode"] == "region" and body["edit"]["region"] == REGION.as_dict()
    assert Path(body["edit"]["snapshot"]).is_file()
    assert body["verified"] and body["output_path"] == str(level.resolve())
    assert body["sections"] == ["Grassy Meadow", "Dungeon"]
    assert body["fidelity"]["items"][-1]["text"] == "There is a lava pit."
    assert body["stats"]["entities"]["Pickup"] == 2 and "preview_ascii" in body

    hist = client.get("/history", params={"path": str(level)}).json()
    assert len(hist["snapshots"]) == 1
    assert hist["snapshots"][0]["label"] == "region: a lava pit"
    assert hist["snapshots"][0]["timestamp"].startswith("20")

    undo = client.post("/undo", json={"path": str(level)}).json()
    assert undo["success"] and undo["remaining"] == 0 and undo["restored_from"]
    assert level.read_bytes() == v0
    again = client.post("/undo", json={"path": str(level)}).json()
    assert again == {"success": False, "restored_from": None, "remaining": 0,
                     "error": again["error"]} and again["error"]
    assert client.get("/history", params={"path": str(level)}).json() == {"snapshots": []}


def test_route_failure_is_http_200(client, level, monkeypatch):
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([WALL, WALL]))
    resp = client.post("/edit/region", json={
        "path": str(level), "prompt": "wall", "provider": "gemini", "seed": 1,
        "rect": {"col": 15, "row": 0, "w": 6, "h": 20}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False and body["error"]
    assert body["edit"]["snapshot"] is None


def test_routes_section_entities_locate(client, level, monkeypatch):
    answers = iter([
        region_answer([], []),
        ops({"op": "add", "target": None, "anchor": "P1",
             "entity": fields(kind="Pickup", pickup_kind="coin", description="coin", value=1)}),
        json.dumps({"col": 1, "row": 2, "w": 5, "h": 5, "explanation": "start"}),
    ])
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([next(answers)]))
    sec = client.post("/edit/section", json={"path": str(level), "prompt": "x", "provider": "gemini",
                                             "seed": 1, "section_id": "dungeon"}).json()
    assert sec["success"], sec["error"]
    assert sec["edit"]["mode"] == "section" and sec["edit"]["region"]["h"] == 20
    ent = client.post("/edit/entities", json={"path": str(level), "prompt": "a coin",
                                              "provider": "gemini", "seed": 1}).json()
    assert ent["success"], ent["error"]
    assert ent["edit"] == {"mode": "entities", "region": None, "snapshot": ent["edit"]["snapshot"]}
    loc = client.post("/edit/locate", json={"path": str(level), "text": "the start",
                                            "provider": "gemini"}).json()
    assert loc == {"success": True, "rect": {"col": 1, "row": 2, "w": 5, "h": 5},
                   "explanation": "start", "error": None, "cancelled": False}
    assert len(client.get("/history", params={"path": str(level)}).json()["snapshots"]) == 2


def test_missing_file_is_a_clean_error(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([]))
    body = client.post("/edit/region", json={"path": str(tmp_path / "nope.ldtk"), "prompt": "x",
                                             "rect": {"col": 0, "row": 0, "w": 3, "h": 3}}).json()
    assert body["success"] is False and "does not exist" in body["error"]


def test_generate_snapshots_before_overwriting(client, tmp_path, monkeypatch, load_fixture):
    monkeypatch.setattr(server, "provider_factory",
                        lambda req: FakeProvider([load_fixture("valid_minimal.json")]))
    out = tmp_path / "g.ldtk"
    req = {"description": "tiny", "output_path": str(out), "densify": False}
    assert client.post("/generate", json=req).json()["success"]
    assert history.list_snapshots(out) == []  # nothing to snapshot the first time
    first = out.read_bytes()
    assert client.post("/generate", json=req).json()["success"]
    snaps = history.list_snapshots(out)
    assert len(snaps) == 1 and snaps[0].label == "generate: tiny"
    assert snaps[0].file.read_bytes() == first


def _move_start_into_the_air(path: Path) -> None:
    """A hand edit that breaks the level: the Start no longer stands on anything."""
    doc = doc_of(path)
    start = next(e for e in layer(doc, "Entities")["entityInstances"] if e["__identifier"] == "Start")
    start["__grid"] = [start["__grid"][0], 1]
    start["px"] = [start["px"][0], 1 * 16 + 15]
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_edit_on_an_already_broken_level_explains_and_skips_the_llm(level):
    _move_start_into_the_air(level)
    raw = level.read_bytes()
    for run in (
        lambda fake: edit_region(str(level), REGION, "a lava pit", fake),
        lambda fake: edit_section(str(level), "dungeon", "a lava pit", fake),
        lambda fake: edit_entities(str(level), "add a coin", fake),
    ):
        fake = FakeProvider([GOOD_REGION])
        out = run(fake)
        assert not out.success
        assert out.error.startswith("This level cannot be beaten as it is now")
        assert "Start" in out.error and "Undo" in out.error
        assert fake.calls == []  # no quota spent
    assert level.read_bytes() == raw
