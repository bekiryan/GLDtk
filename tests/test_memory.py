"""Design memory backend (v4 Part 27a): recording, hand-edit diff, statistics, prompt block,
nudges and the /memory API.  Offline; ``conftest.py`` points GLDTK_MEMORY_DIR at a temp dir."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from game.profile import GameProfile
from llm import FakeProvider
from llm.prompt import build_generate_messages
from memory import analyze, handedit, prefs, store

ROOT = Path(__file__).resolve().parent.parent
WIDE = {"design": {"comfort_hop_tiles": 30, "comfort_rise_tiles": 30, "comfort_drop_tiles": 60}}


@pytest.fixture
def client():
    return TestClient(server.app)


@pytest.fixture
def generated(client, monkeypatch, tmp_path):
    fx = (ROOT / "tests/fixtures/llm/valid_two_sections.json").read_text(encoding="utf-8")
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([fx]))
    (tmp_path / "gldtk.game.json").write_text(json.dumps(WIDE), encoding="utf-8")
    out = tmp_path / "level.ldtk"
    body = client.post("/generate", json={"description": "meadow then dungeon",
                                          "output_path": str(out), "densify": False,
                                          "difficulty": "hard"}).json()
    assert body["success"], body.get("error")
    return out


def events(kind=None):
    return [e for e in store.read_events() if kind is None or e["type"] == kind]


def edit_file(path: Path, fn) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc["levels"][0])
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def erase_tile(lvl, col, row):
    layer = next(li for li in lvl["layerInstances"] if li["__type"] == "IntGrid")
    w = lvl["pxWid"] // layer["__gridSize"]
    assert layer["intGridCsv"][row * w + col] != 0
    layer["intGridCsv"][row * w + col] = 0


# ── recording ───────────────────────────────────────────────────────────────


def test_memory_dir_is_the_temp_dir():
    assert os.environ["GLDTK_MEMORY_DIR"] in str(store.memory_dir())
    assert "Application Support" not in str(store.memory_dir())


def test_generate_and_undo_are_recorded(client, generated):
    ev = events("generated")
    assert len(ev) == 1
    assert ev[0]["mode"] == "generate" and ev[0]["difficulty"] == "hard"
    assert ev[0]["prompt"] == "meadow then dungeon"
    assert [s["name"] for s in ev[0]["sections"]] == ["Grassy Meadow", "Dungeon"]
    assert "levels" not in json.dumps(ev[0])  # compact: no level JSON
    assert handedit.last_output_path(generated).is_file()
    # nothing but the memory events and the last-output copy was added to the history folder
    assert not list(generated.parent.glob(".gldtk_history/*/*.ldtk"))

    # a second generate snapshots the first, so undo has something to restore
    fx = (ROOT / "tests/fixtures/llm/valid_two_sections.json").read_text(encoding="utf-8")
    server.provider_factory = lambda req: FakeProvider([fx])
    client.post("/generate", json={"description": "again", "output_path": str(generated),
                                   "densify": False})
    assert client.post("/undo", json={"path": str(generated)}).json()["success"]
    undone = events("undone")
    assert undone and undone[0]["mode"] == "generate"


def test_events_rotate(monkeypatch):
    monkeypatch.setattr(store, "ROTATE_BYTES", 200)
    for i in range(10):
        store.record("generated", prompt="x" * 50, n=i)
    assert (store.memory_dir() / store.OLD_EVENTS_FILE).is_file()
    assert 0 < len(store.read_events()) <= 10


def test_disabled_records_and_uses_nothing(client, generated):
    client.put("/memory", json={"enabled": False})
    n = len(events())
    store.record("generated", prompt="nope")
    assert len(events()) == n
    assert handedit.scan(generated) == {"edited": False, "levels": 0}
    assert prefs.preferences_block() == ""


# ── hand edits ──────────────────────────────────────────────────────────────


def test_hand_edit_diff(client, generated):
    def hand(lvl):
        erase_tile(lvl, 12, 15)  # p2 loses its left tile: the gap before it widens
        ent = next(e for e in lvl["layerInstances"][0]["entityInstances"]
                   if e["__identifier"] == "Enemy")
        ent["px"] = [ent["px"][0] + 48, ent["px"][1]]
        ent["__grid"] = [ent["__grid"][0] + 3, ent["__grid"][1]]
        lvl_fields = ent["fieldInstances"]
        for fi in lvl_fields:
            if fi["__identifier"] == "health":
                fi["__value"] = 5
                fi["realEditorValues"] = [{"id": "V_Int", "params": [5]}]

    edit_file(generated, hand)
    res = client.post("/memory/scan", json={"path": str(generated)}).json()
    assert res["edited"] and res["levels"] == 1
    (ev,) = events("hand_edit")
    assert ev["moved"] == [{"kind": "Enemy", "dist": 3.0}]
    assert {"kind": "Enemy", "field": "health", "from": 3, "to": 5} in ev["changed"]
    t = ev["tiles"]
    assert t["erased"] == 1 and t["painted"] == 0
    assert t["platforms_before"] == t["platforms_after"]
    assert t["gaps_created"] and max(t["gaps_created"]) > min(t["gaps_closed"] or [0])
    # scanned once: the copy is refreshed, so a second scan finds nothing
    assert client.post("/memory/scan", json={"path": str(generated)}).json()["edited"] is False
    assert len(events("hand_edit")) == 1


def test_scan_runs_before_the_next_request(client, monkeypatch, generated):
    edit_file(generated, lambda lvl: erase_tile(lvl, 12, 15))
    client.post("/undo", json={"path": str(generated)})  # lazily scans first (undo may fail)
    assert len(events("hand_edit")) == 1


def test_entity_added_and_removed(generated):
    def hand(lvl):
        ents = lvl["layerInstances"][0]["entityInstances"]
        ents.remove(next(e for e in ents if e["__identifier"] == "Enemy"))

    edit_file(generated, hand)
    handedit.scan(generated)
    (ev,) = events("hand_edit")
    assert [r["kind"] for r in ev["removed"]] == ["Enemy"]


# ── statistics and rules ────────────────────────────────────────────────────


def synthetic():
    ev = []
    for _ in range(4):
        ev.append({"type": "hand_edit", "tiles": {
            "painted": 2, "erased": 0, "platforms_before": 10, "platforms_after": 12,
            "gaps_before": [2, 3, 3], "gaps_after": [2, 5, 6], "gaps_created": [5, 6]},
            "enemy_spacing": 8.0, "removed": [{"kind": "Enemy", "context": "s1"}],
            "changed": [{"kind": "Enemy", "field": "health", "from": None, "to": 3}]})
    ev += [{"type": "edited", "mode": "region"}] * 4
    ev += [{"type": "undone", "mode": "region"}] * 3
    ev += [{"type": "regenerated", "sections": [{"name": "A", "style": "vertical"}]}] * 3
    return ev


def test_statistics():
    s = analyze.compute_stats(synthetic())
    assert s["gap"]["user_median"] == 5.5 and s["gap"]["gldtk_median"] == 3
    assert s["enemy_spacing_median"] == 8.0
    assert s["platform_ratio_median"] == 1.2
    assert s["removed_kinds"] == {"Enemy": 4}
    assert s["field_values"]["Enemy"]["health"] == {"3": 4}
    assert s["undo_rate"]["region"] == 0.75
    assert s["regenerated_structures"] == {"vertical": 3}
    ids = {r["id"] for r in analyze.stat_rules(s)}
    assert {"gap", "enemy_spacing", "removes_Enemy", "field_Enemy_health", "undo_region",
            "regen_vertical"} <= ids


def seed_events():
    for e in synthetic():
        e = dict(e)
        store.record(e.pop("type"), **e)


def test_rules_and_api(client):
    seed_events()
    body = client.get("/memory").json()
    assert body["enabled"] and body["stats"]["hand_edits"] == 4
    rule = next(r for r in body["rules"] if r["id"] == "gap")
    assert rule["confidence"] >= 0.7
    # edit one, delete one; deleted stat rules do not come back on the next event
    client.put("/memory", json={"rules": [{"id": "gap", "text": "Gaps of 5."}], "delete": ["undo_region"]})
    store.record("generated", prompt="p")
    rules = {r["id"]: r for r in client.get("/memory").json()["rules"]}
    assert rules["gap"]["text"] == "Gaps of 5." and rules["gap"]["source"] == "user"
    assert "undo_region" not in rules
    assert client.put("/memory", json={"enabled": False}).json()["enabled"] is False
    cleared = client.delete("/memory").json()
    assert cleared["rules"] == [] and cleared["event_count"] == 0 and cleared["enabled"] is False


def test_llm_summary_with_fake_provider():
    seed_events()
    fake = FakeProvider([json.dumps({"rules": [{"text": f"rule {i}", "confidence": 0.8}
                                               for i in range(14)]})])
    assert not analyze.maybe_summarize(fake)  # not due yet (events_since_summary < N)
    p = store.load_prefs()
    p["events_since_summary"] = store.SUMMARY_EVERY
    store.save_prefs(p)
    assert analyze.maybe_summarize(fake)
    llm = [r for r in store.load_prefs()["rules"] if r["source"] == "llm"]
    assert len(llm) == analyze.MAX_RULES
    assert store.load_prefs()["events_since_summary"] == 0


# ── use: prompt block ───────────────────────────────────────────────────────


def user_content(messages):
    return messages[0]["content"]


def test_prompt_block_present_and_absent(client):
    assert "DESIGNER PREFERENCES" not in user_content(build_generate_messages("a level"))
    seed_events()
    content = user_content(build_generate_messages("a level"))
    assert "DESIGNER PREFERENCES" in content and "Prefers wider gaps" in content
    block = prefs.preferences_block()
    assert len(block) <= prefs.BLOCK_MAX_CHARS
    # only high-confidence rules
    client.put("/memory", json={"rules": [{"id": "gap", "confidence": 0.3}]})
    assert "Prefers wider gaps" not in prefs.preferences_block()
    client.put("/memory", json={"enabled": False})
    assert "DESIGNER PREFERENCES" not in user_content(build_generate_messages("a level"))


def test_block_is_capped():
    p = store.load_prefs()
    p["rules"] = [{"id": f"r{i}", "text": "x" * 150, "confidence": 0.9, "source": "user"}
                  for i in range(10)]
    store.save_prefs(p)
    assert len(prefs.preferences_block()) <= prefs.BLOCK_MAX_CHARS


# ── use: nudges ─────────────────────────────────────────────────────────────


def test_nudges_are_bounded():
    seed_events()
    base = GameProfile()
    nudged = prefs.nudge_profile(base)
    assert base.design.comfort_hop_tiles == 4  # the original is untouched
    assert nudged.design.comfort_hop_tiles == 6  # median 5.5 -> 6, within +-2 of 4
    p = store.load_prefs()
    p["stats"]["gap"]["user_median"] = 40
    p["stats"]["platform_ratio_median"] = 9.0
    store.save_prefs(p)
    assert prefs.nudge_profile(base).design.comfort_hop_tiles == 4 + prefs.COMFORT_MAX_SHIFT
    assert prefs.density_factor() == prefs.DENSITY_RANGE[1]
    p["stats"]["platform_ratio_median"] = 0.1
    store.save_prefs(p)
    assert prefs.density_factor() == prefs.DENSITY_RANGE[0]


def test_generate_applies_nudges_and_logs_them(client, monkeypatch, tmp_path):
    seed_events()
    fx = (ROOT / "tests/fixtures/llm/valid_two_sections.json").read_text(encoding="utf-8")
    fake = FakeProvider([fx])
    monkeypatch.setattr(server, "provider_factory", lambda req: fake)
    body = client.post("/generate", json={"description": "meadow", "densify": False,
                                          "output_path": str(tmp_path / "n.ldtk")}).json()
    assert body["success"], body.get("error")
    warns = " ".join(body["warnings"])
    assert "Design memory: comfortable hop 4 -> 6" in warns
    assert "Design memory: platform density" in warns
    assert "DESIGNER PREFERENCES" in fake.calls[0].messages[0]["content"]
