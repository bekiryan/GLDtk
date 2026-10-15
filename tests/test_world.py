"""Multi-level worlds (v3 Part 17a): outline call, export, links, per-level edits.

Offline: the outline and the three levels are FakeProvider fixtures in
``tests/fixtures/llm/world_campaign`` (outline, forest, cave with a key, castle
with the boss), answered in that order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

import server
from api import jobs
from layout.ldtk_reader import read_level
from layout.world import level_identifier, link_world
from llm import FakeProvider
from pipeline.world import mentions_levels, wants_outline
from spec.world import WorldOutline

ROOT = Path(__file__).resolve().parent.parent
WORLD_FX = ROOT / "tests" / "fixtures" / "llm" / "world_campaign"
PROMPT = "A 3-level campaign: a forest, then a cave, then a castle with the final boss."


def _validator():
    jsonschema = pytest.importorskip("jsonschema")
    for base in [ROOT, *ROOT.parents]:
        p = base / "ldtk-src" / "docs" / "JSON_SCHEMA.json"
        if p.is_file():
            return jsonschema.Draft7Validator(json.loads(p.read_text(encoding="utf-8")))
    pytest.skip("ldtk-src/docs/JSON_SCHEMA.json not found")


def assert_schema_valid(doc: Dict[str, Any]) -> None:
    errors = list(_validator().iter_errors(json.loads(json.dumps(doc))))
    assert not errors, [f"{list(e.absolute_path)}: {e.message[:120]}" for e in errors[:10]]


def doc_of(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def level_text(doc: Dict[str, Any], i: int) -> str:
    """Level *i* as the file holds it (the file is ``json.dumps(doc, indent=2)``)."""
    return json.dumps(doc["levels"][i], indent=2)


def instances(lvl: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    return [ei for li in lvl["layerInstances"] for ei in li["entityInstances"]
            if ei["__identifier"] == kind]


def field(ei: Dict[str, Any], name: str) -> Any:
    return next(fi["__value"] for fi in ei["fieldInstances"] if fi["__identifier"] == name)


def assert_linked(doc: Dict[str, Any]) -> None:
    levels = doc["levels"]
    for k, lvl in enumerate(levels):
        for ex in instances(lvl, "Exit"):
            if k + 1 < len(levels):
                start = instances(levels[k + 1], "Start")[0]
                ref = field(ex, "leads_to")
                assert ref["entityIid"] == start["iid"]
                assert ref["levelIid"] == levels[k + 1]["iid"]
                assert ref["worldIid"] == doc["dummyWorldIid"]
                assert field(ex, "final") is False
            else:
                assert field(ex, "leads_to") is None
                assert field(ex, "final") is True


@pytest.fixture
def client():
    return TestClient(server.app)


def generate_world(client, monkeypatch, out: Path, **extra) -> Dict[str, Any]:
    fake = FakeProvider(WORLD_FX)
    monkeypatch.setattr(server, "provider_factory", lambda req: fake)
    body = client.post("/generate", json={"description": PROMPT, "output_path": str(out),
                                          "densify": False, **extra}).json()
    assert body["success"], body.get("error")
    body["_calls"] = fake.calls
    return body


@pytest.fixture(scope="module")
def world_bytes(tmp_path_factory) -> bytes:
    out = tmp_path_factory.mktemp("world") / "world.ldtk"
    old = server.provider_factory
    server.provider_factory = lambda req: FakeProvider(WORLD_FX)
    try:
        body = TestClient(server.app).post("/generate", json={
            "description": PROMPT, "output_path": str(out), "densify": False}).json()
    finally:
        server.provider_factory = old
    assert body["success"], body.get("error")
    return out.read_bytes()


@pytest.fixture
def world(tmp_path, world_bytes) -> Path:
    p = tmp_path / "world.ldtk"
    p.write_bytes(world_bytes)
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Detection and the outline
# ─────────────────────────────────────────────────────────────────────────────


def test_detection_rule():
    assert mentions_levels(PROMPT)
    assert mentions_levels("two stages: ice then lava")
    assert mentions_levels("a short campaign")
    assert not mentions_levels("a level")
    assert not mentions_levels("A meadow then a dungeon.")
    assert not wants_outline(PROMPT, 1)
    assert wants_outline("a forest", 3)
    assert not wants_outline("a forest", None)


def test_outline_schema_is_strict():
    schema = WorldOutline.json_schema()
    assert schema["additionalProperties"] is False
    level = schema["properties"]["levels"]["items"]
    assert set(level["required"]) == {"id", "title", "prompt", "size_hint", "difficulty", "theme"}
    assert "$defs" not in json.dumps(schema)


# ─────────────────────────────────────────────────────────────────────────────
# Generate
# ─────────────────────────────────────────────────────────────────────────────


def test_three_level_world_exports_validates_and_links(client, monkeypatch, tmp_path):
    out = tmp_path / "w" / "world.ldtk"
    jid = "world-job"
    body = generate_world(client, monkeypatch, out, job_id=jid)
    calls = body["_calls"]
    assert len(calls) == 4  # the outline + one call per level
    assert "levels" in calls[0].schema["properties"]  # the WorldOutline schema
    assert "LEVELS" not in calls[0].messages[0]["content"]  # count left to the outline
    assert calls[2].messages[0]["content"].startswith("A dark cave where a key lies")
    assert "Difficulty: medium" in calls[2].messages[0]["content"]

    doc = doc_of(out)
    assert_schema_valid(doc)
    assert doc["worldLayout"] == "LinearHorizontal"
    ids = [lv["identifier"] for lv in doc["levels"]]
    assert ids == ["Whispering_Forest_1", "Dark_Cave_2", "Castle_Keep_3"]
    assert len({lv["iid"] for lv in doc["levels"]}) == 3
    assert all(lv["worldX"] == -1 and lv["worldY"] == -1 for lv in doc["levels"])
    assert_linked(doc)
    assert link_world(doc) == 0  # already linked: relinking changes nothing

    # every level has the same layer stack as defs.layers (its own theme sheet in the slot)
    def_ids = [d["identifier"] for d in doc["defs"]["layers"]]
    for lvl in doc["levels"]:
        assert [li["__identifier"] for li in lvl["layerInstances"]] == def_ids
    sheets = [next(li["overrideTilesetUid"] for li in lvl["layerInstances"]
                   if li["__type"] == "Tiles") for lvl in doc["levels"]]
    assert len(set(sheets)) == 3  # forest, cave, castle
    used = {int(t["uid"]) for t in doc["defs"]["tilesets"]}
    assert set(sheets) <= used
    for t in doc["defs"]["tilesets"]:
        assert (out.parent / t["relPath"]).is_file()

    # each level keeps its own gldtk_* fields; the outline sits on the first level
    reads = [read_level(doc, i) for i in range(3)]
    assert [r.prompt for r in reads] == [lv["prompt"] for lv in reads[0].world["outline"]["levels"]]
    assert reads[0].world["identifiers"] == ids
    assert reads[1].world is None and reads[2].world is None
    assert [r.spec.title for r in reads] == ["Whispering Forest", "Dark Cave", "Castle Keep"]

    # response: the first level in the v2 fields, every level in `levels`, the world report
    assert [lv["identifier"] for lv in body["levels"]] == ids
    assert [lv["current"] for lv in body["levels"]] == [True, False, False]
    assert all(lv["verified"] and lv["score"] == 1.0 for lv in body["levels"])
    assert body["verified"] and body["route"]["success"]
    assert body["route"]["level_iid"] == doc["levels"][0]["iid"]
    assert body["requirements"] == ["There is a gold coin."]
    world = body["world"]
    assert world["title"] == "The Long Road"
    status = {i["id"]: i["status"] for i in world["items"]}
    assert status == {"W1": "satisfied", "W2": "satisfied", "W3": "satisfied", "W4": "unchecked"}
    assert world["score"] == 1.0 and world["verified"] is True
    assert [lv["iid"] for lv in world["levels"]] == [lv["iid"] for lv in doc["levels"]]

    # each level verifies on its own (route by identifier and by iid)
    for lvl in doc["levels"]:
        for key in (lvl["identifier"], lvl["iid"]):
            route = client.get("/route", params={"path": str(out), "level": key}).json()
            assert route["success"] and route["level_iid"] == lvl["iid"]
        diff = client.get("/difficulty", params={"path": str(out), "level": lvl["iid"]}).json()
        assert diff["success"] and len(diff["sections"]) == 1

    events = [e["message"] for e in jobs.registry.progress(jid)["events"]]
    assert "Planning the levels" in events
    assert "Level 2/3: Dark Cave" in events
    assert any(m.startswith("Level 2/3: Laying out") for m in events)
    assert any(m.startswith("Level 3/3: Asking") for m in events)


def test_levels_1_skips_the_outline_call(client, monkeypatch, tmp_path, load_fixture):
    fake = FakeProvider([load_fixture("valid_minimal.json")])
    monkeypatch.setattr(server, "provider_factory", lambda req: fake)
    out = tmp_path / "one.ldtk"
    body = client.post("/generate", json={"description": PROMPT, "output_path": str(out),
                                          "levels": 1, "densify": False}).json()
    assert body["success"], body.get("error")
    assert len(fake.calls) == 1 and "levels" not in fake.calls[0].schema["properties"]
    doc = doc_of(out)
    assert doc["worldLayout"] == "Free" and len(doc["levels"]) == 1
    assert body["world"] is None
    assert body["levels"] == [{"index": 0, "id": None, "identifier": "Level_0",
                               "iid": doc["levels"][0]["iid"], "title": "Tiny", "score": None,
                               "verified": True, "current": True}]


def test_an_outline_of_one_level_falls_back_to_one_level(client, monkeypatch, tmp_path,
                                                          load_fixture):
    outline = json.loads((WORLD_FX / "00_outline.json").read_text(encoding="utf-8"))
    outline["levels"] = outline["levels"][:1]
    outline["requirements"] = []
    fake = FakeProvider([json.dumps(outline), load_fixture("valid_minimal.json")])
    monkeypatch.setattr(server, "provider_factory", lambda req: fake)
    out = tmp_path / "one.ldtk"
    body = client.post("/generate", json={"description": "a few levels of forest",
                                          "output_path": str(out), "densify": False}).json()
    assert body["success"], body.get("error")
    assert len(fake.calls) == 2
    assert fake.calls[1].messages[0]["content"].startswith("a few levels of forest")
    assert len(doc_of(out)["levels"]) == 1


def test_a_requested_count_is_enforced(client, monkeypatch, tmp_path):
    files = sorted(WORLD_FX.iterdir())
    outline = json.loads(files[0].read_text(encoding="utf-8"))
    short = dict(outline, levels=outline["levels"][:2])
    answers = [json.dumps(short), files[0].read_text(encoding="utf-8")]
    answers += [f.read_text(encoding="utf-8") for f in files[1:]]
    fake = FakeProvider(answers)
    monkeypatch.setattr(server, "provider_factory", lambda req: fake)
    out = tmp_path / "w.ldtk"
    body = client.post("/generate", json={"description": "forest, cave, castle", "levels": 3,
                                          "output_path": str(out), "densify": False}).json()
    assert body["success"], body.get("error")
    assert "LEVELS: 3" in fake.calls[0].messages[0]["content"]
    assert "exactly 3 levels" in fake.calls[1].messages[-1]["content"]  # re-prompted once
    assert len(doc_of(out)["levels"]) == 3


def test_a_failing_level_fails_the_world_and_writes_nothing(client, monkeypatch, tmp_path,
                                                             load_fixture):
    files = sorted(WORLD_FX.iterdir())
    bad = load_fixture("invalid_unknown_section.json")
    fake = FakeProvider([files[0].read_text(encoding="utf-8"), files[1].read_text(encoding="utf-8"),
                         bad])
    monkeypatch.setattr(server, "provider_factory", lambda req: fake)
    out = tmp_path / "w.ldtk"
    body = client.post("/generate", json={"description": PROMPT, "output_path": str(out),
                                          "max_retries": 1, "densify": False}).json()
    assert not body["success"]
    assert body["error"].startswith("Level 2/3 (Dark Cave):")
    assert not out.exists()


def test_level_identifier():
    assert level_identifier("Whispering Forest", 0) == "Whispering_Forest_1"
    assert level_identifier("3rd tower!", 2) == "L3rd_Tower_3"
    assert level_identifier("", 1) == "Level_2"
    assert level_identifier("Cave", 1, ["Cave_2"]) == "Cave_2_2"


# ─────────────────────────────────────────────────────────────────────────────
# Edits on one level
# ─────────────────────────────────────────────────────────────────────────────


def ops(*operations) -> str:
    return json.dumps({"operations": list(operations), "requirements": []})


def test_edit_on_level_2_leaves_levels_1_and_3_byte_identical(client, monkeypatch, world):
    before = doc_of(world)
    cave = before["levels"][1]
    exit_iid = instances(cave, "Exit")[0]["iid"]
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([ops(
        {"op": "modify", "target": "E1", "anchor": None,
         "entity": {"description": "a heavy stone door"}})]))
    body = client.post("/edit/entities", json={
        "path": str(world), "prompt": "make the exit a stone door", "level": "Dark_Cave_2",
        "entity_iids": [exit_iid]}).json()
    assert body["success"], body["error"]
    after = doc_of(world)
    assert_schema_valid(after)
    assert level_text(after, 0) == level_text(before, 0)
    assert level_text(after, 2) == level_text(before, 2)
    assert level_text(after, 1) != level_text(before, 1)
    new_exit = instances(after["levels"][1], "Exit")[0]
    assert field(new_exit, "description") == "a heavy stone door"
    assert new_exit["__worldX"] is None
    assert_linked(after)  # the modified Exit still leads to the castle

    # the response is about level 2
    assert body["route"]["level_iid"] == cave["iid"]
    assert [lv["current"] for lv in body["levels"]] == [False, True, False]
    assert [lv["title"] for lv in body["levels"]] == ["Whispering Forest", "Dark Cave", "Castle Keep"]
    assert body["levels"][1]["verified"] is True and body["levels"][0]["score"] is None

    # undo restores the whole file (snapshots are per file)
    undo = client.post("/undo", json={"path": str(world), "level": cave["iid"]}).json()
    assert undo["success"] and undo["route"]["level_iid"] == cave["iid"]
    assert [lv["current"] for lv in undo["levels"]] == [False, True, False]
    assert doc_of(world) == before


def test_region_edit_on_level_2_by_iid(client, monkeypatch, world):
    before = doc_of(world)
    cave = before["levels"][1]
    answer = json.dumps({"nodes": [{"id": "ledge", "type": "Platform", "col": 2, "row": 6, "w": 4}],
                         "edges": [], "entities": [], "requirements": []})
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([answer]))
    rect = {"col": 20, "row": 0, "w": 10, "h": 12}
    body = client.post("/edit/region", json={"path": str(world), "prompt": "a ledge", "seed": 1,
                                             "rect": rect, "level": cave["iid"]}).json()
    assert body["success"], body["error"]
    after = doc_of(world)
    assert_schema_valid(after)
    assert level_text(after, 0) == level_text(before, 0)
    assert level_text(after, 2) == level_text(before, 2)
    grid = read_level(after, cave["iid"]).grid
    assert grid[6][22] != 0 and read_level(before, 1).grid[6][22] == 0
    assert_linked(after)


def test_unknown_level_is_a_clean_error(client, monkeypatch, world):
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([]))
    body = client.post("/edit/entities", json={"path": str(world), "prompt": "x",
                                               "level": "Nowhere_9"}).json()
    assert not body["success"] and "Nowhere_9" in body["error"] and "Dark_Cave_2" in body["error"]
    route = client.get("/route", params={"path": str(world), "level": "Nowhere_9"}).json()
    assert route["success"] is False and "Nowhere_9" in route["error"]


def test_unsaved_route_of_a_level(client, world):
    doc = doc_of(world)
    body = client.post("/route", json={"path": str(world), "project": doc,
                                       "level": "Castle_Keep_3"}).json()
    assert body["success"] and body["level_iid"] == doc["levels"][2]["iid"]


# ─────────────────────────────────────────────────────────────────────────────
# Regenerate one level
# ─────────────────────────────────────────────────────────────────────────────


def test_regenerate_one_level_keeps_the_others_and_the_links(client, monkeypatch, world):
    before = doc_of(world)
    cave = before["levels"][1]
    fake = FakeProvider([(WORLD_FX / "02_cave.json").read_text(encoding="utf-8")])
    monkeypatch.setattr(server, "provider_factory", lambda req: fake)
    body = client.post("/generate", json={"description": "", "output_path": str(world),
                                          "level": cave["iid"], "densify": False}).json()
    assert body["success"], body.get("error")
    assert len(fake.calls) == 1
    assert fake.calls[0].messages[0]["content"].startswith("A dark cave where a key lies")
    assert "Theme: cave" in fake.calls[0].messages[0]["content"]

    after = doc_of(world)
    assert_schema_valid(after)
    assert [lv["identifier"] for lv in after["levels"]] == [lv["identifier"] for lv in before["levels"]]
    assert [lv["iid"] for lv in after["levels"]] == [lv["iid"] for lv in before["levels"]]
    assert after["levels"][1]["uid"] == cave["uid"]
    assert level_text(after, 2) == level_text(before, 2)
    # level 1 changed only in its Exit's link to the new Start
    assert instances(after["levels"][1], "Start")[0]["iid"] != instances(cave, "Start")[0]["iid"]
    assert_linked(after)
    first_before, first_after = before["levels"][0], after["levels"][0]
    for ex in instances(first_after, "Exit"):
        ex["fieldInstances"] = [fi for fi in ex["fieldInstances"] if fi["__identifier"] != "leads_to"]
    for ex in instances(first_before, "Exit"):
        ex["fieldInstances"] = [fi for fi in ex["fieldInstances"] if fi["__identifier"] != "leads_to"]
    assert first_after == first_before
    assert read_level(after, 0).world is not None  # the outline stays on the first level

    assert [lv["current"] for lv in body["levels"]] == [False, True, False]
    assert body["route"]["level_iid"] == cave["iid"] and body["verified"]
    hist = client.get("/history", params={"path": str(world), "level": cave["iid"]}).json()
    assert hist["snapshots"][0]["label"].startswith("regenerate Dark_Cave_2")


def test_regenerate_unknown_level(client, monkeypatch, world):
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([]))
    body = client.post("/generate", json={"description": "x", "output_path": str(world),
                                          "level": "Nope"}).json()
    assert not body["success"] and "Nope" in body["error"]


# ─────────────────────────────────────────────────────────────────────────────
# Catalog compatibility
# ─────────────────────────────────────────────────────────────────────────────


def test_exit_world_fields_do_not_shift_older_field_uids():
    from spec.catalog import KINDS

    exit_kind = KINDS["Exit"]
    assert exit_kind.field_uid("requires_key") == 1100
    assert exit_kind.field_uid("description") == 1101  # unchanged since v2
    assert exit_kind.field_uid("sprite_tile") == 1107
    assert (exit_kind.field_uid("leads_to"), exit_kind.field_uid("final")) == (1108, 1109)


def test_editing_the_exit_of_a_file_without_world_fields(client, monkeypatch, tmp_path,
                                                         load_fixture):
    fake = FakeProvider([load_fixture("valid_minimal.json")])
    monkeypatch.setattr(server, "provider_factory", lambda req: fake)
    out = tmp_path / "old.ldtk"
    assert client.post("/generate", json={"description": "tiny", "output_path": str(out),
                                          "densify": False}).json()["success"]
    doc = doc_of(out)  # strip the Part 17 fields: a file written before them
    exit_def = next(d for d in doc["defs"]["entities"] if d["identifier"] == "Exit")
    exit_def["fieldDefs"] = [f for f in exit_def["fieldDefs"] if f["uid"] < 1108]
    ex = instances(doc["levels"][0], "Exit")[0]
    ex["fieldInstances"] = [f for f in ex["fieldInstances"] if f["defUid"] < 1108]
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([ops(
        {"op": "modify", "target": "E1", "anchor": None, "entity": {"description": "gate"}})]))
    body = client.post("/edit/entities", json={"path": str(out), "prompt": "a gate",
                                               "entity_iids": [ex["iid"]]}).json()
    assert body["success"], body["error"]
    new_ex = instances(doc_of(out)["levels"][0], "Exit")[0]
    assert field(new_ex, "description") == "gate"
    assert {f["defUid"] for f in new_ex["fieldInstances"]} == {f["uid"] for f in exit_def["fieldDefs"]}
    assert_schema_valid(doc_of(out))
