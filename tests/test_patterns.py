"""Pattern library and retrieval (v4 Part 26a): offline, FakeProvider labelling, hash embedder."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from layout.ir import IREntity, TileValue
from llm import FakeProvider
from llm.prompt import build_generate_messages
from tests.test_edit import base_level, level  # noqa: F401 (fixtures)
from patterns.build import default_samples_dir
from patterns.embed import HashEmbedder, make_embedder, sync_vectors
from patterns.ingest import add_path, ingest_grid, ingest_samples
from patterns.label import label_pending, label_from_features
from patterns.prompt import crop, examples_block
from patterns.retrieve import EXTRA_CHECKS, Filters, accept, retrieve
from patterns.store import PatternStore, patterns_dir

SAMPLES = default_samples_dir()
needs_samples = pytest.mark.skipif(SAMPLES is None, reason="LDtk samples not available")


def label_json(title: str, structure: str = "parkour", difficulty: int = 4, tags=("jump",), mech=("jump",)):
    return json.dumps({"title": title, "purpose": f"{title} purpose", "mood": "tense",
                       "mechanics": list(mech), "structure_type": structure,
                       "difficulty": difficulty, "tags": list(tags)})


def synthetic_grid(kind: str):
    """A 30x12 level: 'pit' has a spike pit between two ledges, 'steps' has stairs."""
    h, w = 12, 30
    g = [[TileValue.EMPTY] * w for _ in range(h)]
    for c in range(w):
        g[h - 1][c] = TileValue.SOLID
    if kind == "pit":
        for c in range(10, 18):
            g[h - 1][c] = TileValue.HAZARD
        for c in range(8, 10):
            g[h - 2][c] = TileValue.SOLID
    else:
        for i, c0 in enumerate(range(4, 28, 6)):
            for c in range(c0, c0 + 4):
                g[h - 2 - i][c] = TileValue.SOLID
    ents = [IREntity("Start", 1, h - 2), IREntity("Exit", 28, h - 2)]
    return g, ents


@pytest.fixture
def store():
    return PatternStore()


@pytest.fixture
def small_store(store):
    for kind in ("pit", "steps"):
        g, e = synthetic_grid(kind)
        ingest_grid(store, g, e, source="test", license="CC0", origin_path=f"/x/{kind}.ldtk", level=kind)
    sync_vectors(store, HashEmbedder())
    return store


def test_patterns_dir_is_the_test_tmp_dir(tmp_path):
    assert patterns_dir() == tmp_path / "patterns"


@needs_samples
def test_ingest_platformer_sample(store):
    rep = add_path(store, SAMPLES / "WorldMap_GridVania_layout.ldtk")
    assert rep.added >= 3
    rec = next(iter(store))
    assert rec["source"] == "ldtk-samples" and "MIT" in rec["license"]
    assert rec["origin_path"].endswith("WorldMap_GridVania_layout.ldtk")
    f = rec["features"]
    for key in ("platform_widths", "max_gap", "hazards", "enemies", "route", "difficulty", "structure"):
        assert key in f
    assert set("".join(rec["ascii"].split())) <= set(".#^SXEP")
    assert any(r["accepted"] for r in store)


@needs_samples
def test_sample_mapping_skips_unmappable_files(store):
    rep = ingest_samples(store, SAMPLES)
    assert "WorldMap_GridVania_layout.ldtk" in rep.mapped_files
    assert "Typical_TopDown_example.ldtk" in rep.skipped_files
    assert rep.added == len(store) > 10


def test_ingest_is_idempotent(store):
    g, e = synthetic_grid("pit")
    r1 = ingest_grid(store, g, e, source="t", license="CC0", origin_path="a", level="l")
    r2 = ingest_grid(store, g, e, source="t", license="CC0", origin_path="a", level="l")
    assert r1.added == 1 and r2.added == 0 and r2.duplicates == 1


def test_window_without_route_is_dropped(store):
    g, e = synthetic_grid("pit")
    for c in range(30):  # a ceiling and a wall from it to the floor split the level
        g[0][c] = TileValue.SOLID
    for r in range(11):
        for c in range(20, 23):
            g[r][c] = TileValue.SOLID
    rep = ingest_grid(store, g, e, source="t", license="CC0", origin_path="a", level="walled")
    assert len(store) == 0 and rep.dropped.get("no route or soft-lock") == 1
    assert not accept({"features": {"route": {"ok": False, "softlocks": 0}}})


def test_labelling_caches_and_respects_budget(small_store):
    provider = FakeProvider([label_json("Spike pit"), label_json("Stairs", "corridor", 2, ("climb",))])
    rep = label_pending(small_store, provider, max_calls=1)
    assert rep.labelled == 1 and rep.stopped and rep.pending == 1 and len(provider.calls) == 1
    # a second run with a bigger budget labels only the remaining pattern
    rep = label_pending(small_store, provider, max_calls=5)
    assert rep.labelled == 1 and rep.cached == 1 and len(provider.calls) == 2
    # nothing left: no call at all
    rep = label_pending(small_store, FakeProvider([]), max_calls=5)
    assert rep.labelled == 0 and rep.calls == 0
    assert all(r["label"] for r in small_store)


def test_labelling_dry_run_makes_no_call(small_store):
    provider = FakeProvider([])
    rep = label_pending(small_store, provider, dry_run=True)
    assert rep.pending == 2 and not provider.calls


def test_index_round_trips(small_store):
    small_store.save()
    again = PatternStore()
    assert again.patterns == small_store.patterns
    assert again.vectors == small_store.vectors
    assert again.embedder == {"name": "hash", "dim": 256}
    assert Path(os.environ["GLDTK_PATTERNS_DIR"]) == again.dir


def test_hash_embedder_is_deterministic():
    a = HashEmbedder().embed(["spike pit jump"])[0]
    assert a == HashEmbedder().embed(["spike pit jump"])[0]
    assert abs(sum(x * x for x in a) - 1) < 1e-9
    with pytest.raises(ValueError):
        make_embedder("nope")


def labelled_store(small_store):
    ids = sorted(small_store.patterns)
    small_store.patterns[ids[0]]["label"] = json.loads(label_json("Spike pit crossing", "parkour", 6,
                                                                  ("spikes", "gap"), ("jump", "dodge")))
    small_store.patterns[ids[1]]["label"] = json.loads(label_json("Gentle staircase", "corridor", 2,
                                                                  ("stairs", "climb"), ("climb",)))
    sync_vectors(small_store, HashEmbedder())
    return ids


def test_retrieval_ranks_match_first_and_filters_work(small_store):
    ids = labelled_store(small_store)
    top = retrieve("a spike pit to jump across", 2, store=small_store)
    assert top[0].id == ids[0]
    top = retrieve("gentle stairs to climb", 2, store=small_store)
    assert top[0].id == ids[1]
    only = retrieve("anything", 5, Filters(structure_type="corridor"), store=small_store)
    assert [h.id for h in only] == [ids[1]]
    assert [h.id for h in retrieve("x", 5, Filters(difficulty_min=5), store=small_store)] == [ids[0]]
    assert [h.id for h in retrieve("x", 5, Filters(tags=["stairs"]), store=small_store)] == [ids[1]]
    soft = retrieve("spike pit", 5, Filters(structure_type="corridor", hard=False), store=small_store)
    assert len(soft) == 2


def test_accept_hook_filters_retrieval(small_store):
    ids = labelled_store(small_store)
    check = lambda p: p["id"] != ids[0]  # noqa: E731
    EXTRA_CHECKS.append(check)
    try:
        assert [h.id for h in retrieve("spike pit", 5, store=small_store)] == [ids[1]]
    finally:
        EXTRA_CHECKS.remove(check)  # keep the built-in soft-lock check


def test_retrieval_on_empty_index_is_empty(store):
    assert retrieve("anything", 3, store=store) == []
    assert examples_block(["anything"], 3, store=store) == ("", [])


def test_crop_limits_size():
    big = "\n".join("#" * 40 for _ in range(24))
    out = crop(big)
    assert len(out.splitlines()) == 16 and len(out.splitlines()[0]) == 30


def test_examples_in_generate_prompt_only_when_requested(small_store):
    labelled_store(small_store)
    text, used = examples_block(["spike pit"], 2, store=small_store)
    assert used and "do not copy" in text
    base = build_generate_messages("A level.", None)
    assert base == build_generate_messages("A level.", None, None, None)  # off = unchanged
    with_ex = build_generate_messages("A level.", None, None, text)
    assert with_ex[0]["content"].startswith(base[0]["content"]) and text in with_ex[0]["content"]
    assert "REFERENCE PATTERNS" not in base[0]["content"]


@pytest.fixture
def client():
    return TestClient(server.app)


def _generate(client, monkeypatch, tmp_path, load_fixture, **extra):
    fake = FakeProvider([load_fixture("valid_two_sections.json")])
    monkeypatch.setattr(server, "provider_factory", lambda req: fake)
    out = tmp_path / "gen" / "level.ldtk"
    resp = client.post("/generate", json={"description": "A spike pit level.", "output_path": str(out),
                                          "densify": False, **extra})
    return resp.json(), fake, out


def test_generate_patterns_off_is_unchanged(client, monkeypatch, tmp_path, load_fixture, small_store):
    labelled_store(small_store)
    small_store.save()
    body0, fake0, out0 = _generate(client, monkeypatch, tmp_path / "a", load_fixture)
    assert body0["success"] and body0["patterns_used"] == []
    assert "REFERENCE PATTERNS" not in fake0.calls[0].messages[0]["content"]
    body1, fake1, out1 = _generate(client, monkeypatch, tmp_path / "b", load_fixture, patterns=2)
    assert body1["success"] and body1["patterns_used"]
    msg = fake1.calls[0].messages[0]["content"]
    assert "REFERENCE PATTERNS" in msg
    assert fake1.calls[0].system == fake0.calls[0].system  # the cacheable prefix is untouched
    # the examples only change the prompt: the level for the same LLM answer is identical
    def grid_of(path):
        layers = json.loads(path.read_text())["levels"][0]["layerInstances"]
        return [li["intGridCsv"] for li in layers if li["__type"] == "IntGrid"]

    assert grid_of(out0) == grid_of(out1)


def test_patterns_endpoints(client, small_store):
    labelled_store(small_store)
    small_store.save()
    s = client.get("/patterns/stats").json()
    assert s["patterns"] == 2 and s["embedded"] == 2 and s["labelled"] == 2
    r = client.get("/patterns", params={"query": "spike pit", "k": 1}).json()
    assert len(r["patterns"]) == 1 and r["patterns"][0]["title"] == "Spike pit crossing"
    r = client.get("/patterns", params={"query": "x", "structure_type": "corridor"}).json()
    assert [p["title"] for p in r["patterns"]] == ["Gentle staircase"]


@needs_samples
def test_patterns_add_endpoint(client):
    r = client.post("/patterns/add", json={"path": str(SAMPLES / "WorldMap_GridVania_layout.ldtk")}).json()
    assert r["added"] >= 3 and r["embedded"] >= 1
    assert client.get("/patterns/stats").json()["patterns"] == r["added"]
    again = client.post("/patterns/add", json={"path": str(SAMPLES / "WorldMap_GridVania_layout.ldtk")}).json()
    assert again["added"] == 0 and again["duplicates"] == r["added"]


def test_no_writes_outside_patterns_dir(small_store, tmp_path):
    small_store.save()
    assert (tmp_path / "patterns" / "index.json").is_file()
    assert Path(os.environ["GLDTK_PATTERNS_DIR"]).parent == tmp_path


def test_region_edit_prompt(level, small_store):
    from edit.region import edit_region
    from tests.test_edit import GOOD_REGION, REGION

    labelled_store(small_store)
    small_store.save()
    off, on = FakeProvider([GOOD_REGION]), FakeProvider([GOOD_REGION])
    edit_region(str(level), REGION, "a spike pit", off)
    out = edit_region(str(level), REGION, "a spike pit", on, patterns=2)
    assert "REFERENCE PATTERNS" not in off.calls[0].messages[0]["content"]
    assert "REFERENCE PATTERNS" in on.calls[0].messages[0]["content"]
    assert on.calls[0].system == off.calls[0].system
    assert out.stats["patterns"]


def test_softlocked_patterns_are_not_accepted():
    from patterns.retrieve import accept
    ok = {"features": {"route": {"ok": True, "softlocks": 0}}}
    trapped = {"features": {"route": {"ok": True, "softlocks": 2}}}
    assert accept(ok) and not accept(trapped)


def test_label_from_features():
    from patterns.label import PatternLabel, is_example_title

    rec = {
        "features": {
            "width": 16,
            "height": 30,
            "platforms": 10,
            "max_gap": 5,
            "hazards": 2,
            "enemies": 1,
            "route": {"jumps": 4},
            "geometry": {"rise": 8},
            "structure_geo": {"type": "parkour", "confidence": 0.9}
        },
        "source": "supertux"
    }
    lbl = label_from_features(rec)
    assert isinstance(lbl, PatternLabel)
    assert not is_example_title(lbl.title)
    assert lbl.structure_type == "parkour"
    assert lbl.difficulty == 5
    assert "jump" in lbl.mechanics
    assert "hazard dodge" in lbl.mechanics
    assert lbl.mood == "tense hazardous"
    assert "Tall" in lbl.title
    assert "supertux" in lbl.tags
    assert 3 <= len(lbl.tags) <= 6
    assert all(t == t.lower() for t in lbl.tags)


def test_label_from_features_edge_cases():
    from patterns.label import PatternLabel, is_example_title

    # Empty record falls back cleanly to a valid PatternLabel
    empty_lbl = label_from_features({})
    assert isinstance(empty_lbl, PatternLabel)
    assert not is_example_title(empty_lbl.title)
    assert empty_lbl.structure_type == "parkour"
    assert empty_lbl.difficulty == 5
    assert "walk" in empty_lbl.mechanics and "jump" in empty_lbl.mechanics
    assert 3 <= len(empty_lbl.tags) <= 6

    # Difficulty 0 clamps to 1, >10 clamps to 10; gap overrides low-confidence geo
    lbl = label_from_features({
        "features": {
            "width": 20, "height": 20, "platforms": 4, "pickups": 2, "enemies": 3,
            "difficulty": 0,
            "gap": "arena",
            "structure": "corridor",
            "structure_geo": {"type": "labyrinth", "confidence": 0.5},
            "geometry": {"rise": 2, "cycles": 1},
        },
        "source": "OpenSurge",
    })
    assert lbl.structure_type == "arena"
    assert lbl.difficulty == 1
    assert lbl.mood == "combative"
    assert "combat" in lbl.mechanics and "collect" in lbl.mechanics and "exploration" in lbl.mechanics
    assert "opensurge" in lbl.tags

    hard_lbl = label_from_features({"features": {"difficulty": 14, "structure": "invalid"}})
    assert hard_lbl.difficulty == 10
    assert hard_lbl.structure_type == "parkour"


def test_label_pending_geometry_provider(small_store):
    for r in small_store:
        r["label"] = None
    ids = sorted(small_store.patterns)
    # Budget stops early when more todo items remain
    rep = label_pending(small_store, "geometry", budget=1)
    assert rep.labelled == 1
    assert rep.stopped
    assert rep.pending == 1
    assert rep.calls == 0

    # `only` restricts labelling to the requested pattern id
    for r in small_store:
        r["label"] = None
    rep_only = label_pending(small_store, "geometry", budget=5, only=[ids[0]])
    assert rep_only.labelled == 1
    assert not rep_only.stopped
    assert rep_only.pending == 1
    assert rep_only.calls == 0
    assert small_store.patterns[ids[0]]["label"] is not None
    assert small_store.patterns[ids[1]].get("label") is None

    rep2 = label_pending(small_store, "geometry", budget=5)
    assert rep2.labelled == 1
    assert not rep2.stopped
    assert rep2.pending == 0
    assert rep2.calls == 0

    rep3 = label_pending(small_store, "geometry", budget=5)
    assert rep3.labelled == 0
    assert rep3.calls == 0


def test_build_cli_geometry_provider(small_store, tmp_path, monkeypatch):
    import patterns.build as pbuild

    for r in small_store:
        r["label"] = None
    small_store.save()
    empty_samples = tmp_path / "no_samples"
    empty_samples.mkdir()
    monkeypatch.setattr(pbuild, "demo_files", lambda: [])
    rc = pbuild.main(["--samples", str(empty_samples), "--provider", "geometry", "--embedder", "hash",
                      "--label-budget", "10"])
    assert rc == 0
    reloaded = PatternStore()
    assert all(r.get("label") for r in reloaded)
    assert reloaded.stats()["labelled"] == 2


