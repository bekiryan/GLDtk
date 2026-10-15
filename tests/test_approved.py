"""v4 Part 26c: approved levels feed the pattern library, whole-level retrieval, chunk jobs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import pipeline.chunks as chunks_mod
import server
from api import jobs
from llm import FakeProvider
from memory import store
from patterns.store import PatternStore
from tests.test_memory import WIDE

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client():
    return TestClient(server.app)


@pytest.fixture
def make(client, monkeypatch, tmp_path):
    fx = (ROOT / "tests/fixtures/llm/valid_two_sections.json").read_text(encoding="utf-8")
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([fx]))
    (tmp_path / "gldtk.game.json").write_text(json.dumps(WIDE), encoding="utf-8")
    out = tmp_path / "level.ldtk"

    def gen():
        body = client.post("/generate", json={"description": "meadow then dungeon",
                                              "output_path": str(out), "densify": False}).json()
        assert body["success"], body.get("error")
        return out

    return gen


def approved():
    return [r for r in PatternStore() if r["source"] == "approved"]


def levels():
    return [r for r in approved() if r.get("kind") == "level"]


def test_kept_level_is_ingested_once(client, make):
    out = make()
    assert not approved()  # nothing has confirmed it was kept yet
    client.post("/memory/scan", json={"path": str(out)})
    assert not approved()  # unchanged file: still pending
    # the next GLDtk request on the file finds it was not undone
    server.memory_hooks.before(str(out))
    recs = approved()
    assert recs and all(r["license"] == "user" and r["label"] is None for r in recs)
    assert len(levels()) == 1
    lines = levels()[0]["ascii"].splitlines()
    assert len(lines) <= 20 and max(map(len, lines)) <= 60
    n = len(PatternStore())
    server.memory_hooks.before(str(out))
    make()  # regenerated with identical content: deduplicated
    server.memory_hooks.before(str(out))
    assert len(PatternStore()) == n


def test_undone_level_is_not_ingested(client, make):
    client.put("/memory", json={"patterns_auto_add": False})
    out = make()  # the file to undo back to (its approval is off)
    client.put("/memory", json={"patterns_auto_add": True})
    make()
    assert client.post("/undo", json={"path": str(out)}).json()["success"]
    server.memory_hooks.before(str(out))
    assert not approved()


def test_memory_off_and_auto_add_off(client, make):
    client.put("/memory", json={"enabled": False})
    out = make()
    server.memory_hooks.before(str(out))
    assert not approved()
    client.put("/memory", json={"enabled": True, "patterns_auto_add": False})
    assert client.get("/memory").json()["patterns_auto_add"] is False
    out = make()
    server.memory_hooks.before(str(out))
    assert not approved()
    client.put("/memory", json={"patterns_auto_add": True})
    assert store.load_prefs()["patterns_auto_add"] is True


def test_saved_hand_edit_approves_and_levels_are_retrievable(client, make):
    out = make()
    doc = json.loads(out.read_text(encoding="utf-8"))
    layer = next(li for li in doc["levels"][0]["layerInstances"] if li["__type"] == "IntGrid")
    i = next(i for i, v in enumerate(layer["intGridCsv"]) if v == 0)
    layer["intGridCsv"][i] = 1
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    assert client.post("/memory/scan", json={"path": str(out)}).json()["edited"]
    assert len(levels()) == 1
    body = client.get("/patterns/levels", params={"query": "meadow then dungeon", "k": 2}).json()
    assert body["levels"], body
    hit = body["levels"][0]
    assert set(hit) == {"id", "title", "source", "origin_path", "level", "score"}
    assert hit["source"] == "approved"
    # whole levels never appear among fragments
    frags = client.get("/patterns", params={"query": "meadow then dungeon"}).json()["patterns"]
    assert all(p["id"] != hit["id"] for p in frags)
    assert client.get("/patterns/stats").json()["levels"] == 1


def test_generate_prompt_mentions_one_similar_level(client, make):
    from patterns.prompt import examples_block

    out = make()
    server.memory_hooks.before(str(out))
    text, used = examples_block(["meadow then dungeon"], 2)
    assert text.count("Similar approved level") == 1
    assert used


def test_chunks_job_progress_and_cancel(client, tmp_path, monkeypatch):
    out = tmp_path / "w.ldtk"
    body = client.post("/chunks", json={"path": str(out), "count": 2, "seed": 3, "job_id": "cj"}).json()
    assert body["success"], body.get("error")
    msgs = [e["message"] for e in jobs.registry.progress("cj")["events"]]
    assert any(m.startswith("Chunk 1/2") for m in msgs) and any(m.startswith("Chunk 2/2") for m in msgs)

    out2 = tmp_path / "c.ldtk"
    real, calls = chunks_mod.build_chunk, []

    def build(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            jobs.registry.cancel("cj2")
        return real(*a, **k)

    monkeypatch.setattr(chunks_mod, "build_chunk", build)
    body = client.post("/chunks", json={"path": str(out2), "count": 3, "seed": 3,
                                        "job_id": "cj2"}).json()
    assert body["cancelled"] and not body["success"]
    assert not out2.exists()
