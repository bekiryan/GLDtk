"""Open-licensed game levels and design notes (v4 Part 26d): offline, hand-written fixtures."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from layout.ir import TileValue
from llm import FakeProvider
from patterns.embed import HashEmbedder, sync_vectors
from patterns.notes import (MAX_WORDS, TOPICS, excluded_notes, generate_notes, review_note,
                            topic_id)
from patterns.prompt import NOTES_HEADER, examples_block
from patterns.retrieve import retrieve
from patterns.sources import (SourceReport, attribution_text, ingest_source, load_manifest,
                              spdx_of)
from patterns.sources import frogatto, opensurge, supertux
from patterns.store import PatternStore

FIX = Path(__file__).parent / "fixtures" / "sources"


@pytest.fixture(autouse=True)
def _sources_dir(tmp_path, monkeypatch):
    """Nothing is downloaded or read from the user's sources folder in tests."""
    monkeypatch.setenv("GLDTK_SOURCES_DIR", str(tmp_path / "sources"))


@pytest.fixture
def store():
    return PatternStore()


def cells(grid, value):
    return [(r, c) for r, row in enumerate(grid) for c, v in enumerate(row) if v == value]


# ── licences ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text,spdx", [
    ("CC-BY-SA 4.0 International", "CC-BY-SA-4.0"),
    ("CC-BY 3.0", "CC-BY-3.0"),
    ("CC BY 4.0", "CC-BY-4.0"),
    ("CC0", "CC0-1.0"),
    ("CC-0", "CC0-1.0"),
    ("GPL 2+ / CC-by-sa 3.0", "GPL-2.0-or-later OR CC-BY-SA-3.0"),
    ("CC-BY-NC-SA 4.0", None),
    ("CC BY-NC 4.0", None),
    ("CC-BY-ND 4.0", None),
    ("All rights reserved", None),
    ("", None),
    (None, None),
])
def test_spdx_of(text, spdx):
    assert spdx_of(text) == spdx


# ── SuperTux ────────────────────────────────────────────────────────────────


def test_supertux_sexpr_and_run_length():
    forms = supertux.parse_sexpr('(a (b 1 -2 "x;y") ; comment\n (c #t))')
    assert forms == [["a", ["b", 1, -2, "x;y"], ["c", True]]]
    assert supertux.decode_tiles([-3, 7, 1, -2, 0]) == [7, 7, 7, 1, 0, 0]


def test_supertux_tileset_flags_map_tiles():
    ts = supertux.load_tileset(FIX / "supertux" / "data" / "images" / "tiles.strf")
    assert ts == {1: 1, 2: 1024, 3: 8, 4: 3}
    assert supertux.classify(ts[1]) == TileValue.SOLID
    assert supertux.classify(ts[2]) == TileValue.HAZARD
    assert supertux.classify(ts[4]) == TileValue.SOLID  # unisolid ledge: standable
    assert supertux.classify(ts[3]) == TileValue.EMPTY  # goal marker


def test_supertux_converts_a_level():
    rep = SourceReport("supertux")
    levels = list(supertux.iter_levels(FIX / "supertux", rep))
    assert [lv.level for lv in levels] == ["tiny"]
    lv = levels[0]
    assert lv.license == "CC-BY-SA-4.0" and "Test Author" in lv.attribution and "SuperTux" in lv.attribution
    assert lv.origin_path == "supertux/data/levels/world1/tiny.stl"
    # 32 px tiles become 2x2 GLDtk tiles: 12 source columns -> 24 (+ the trim padding)
    assert len(lv.grid[0]) == 24
    hz = cells(lv.grid, TileValue.HAZARD)
    assert len(hz) == 2 * 2 * 2 and {c for _, c in hz} == {10, 11, 12, 13}
    kinds = {e.entity_type: e for e in lv.entities}
    assert set(kinds) == {"Start", "Exit", "Enemy", "Pickup"}
    floor = max(r for r, _ in cells(lv.grid, TileValue.SOLID))
    # Start and Exit stand on the floor (the cell below them is solid)
    for k in ("Start", "Exit"):
        e = kinds[k]
        assert lv.grid[e.row][e.col] == TileValue.EMPTY and lv.grid[e.row + 1][e.col] != TileValue.EMPTY
        assert e.row < floor
    assert kinds["Exit"].col == 22


def test_supertux_licence_gate_skips_nc_level():
    rep = SourceReport("supertux")
    list(supertux.iter_levels(FIX / "supertux", rep))
    assert "world1/nc_only.stl" in rep.skipped and "licence" in rep.skipped["world1/nc_only.stl"]
    assert rep.included == ["world1/tiny.stl"] and rep.licenses == {"CC-BY-SA-4.0": 1}


# ── Open Surge ──────────────────────────────────────────────────────────────


def test_opensurge_brickset_and_level():
    bricks = opensurge.parse_brickset((FIX / "opensurge" / "themes" / "tiny.brk").read_text())
    assert bricks[0]["type"] == "SOLID" and bricks[0]["rect"] == (0, 0, 128, 32)
    assert bricks[1]["type"] == "PASSABLE" and bricks[2]["type"] == "CLOUD"
    rep = SourceReport("opensurge")
    levels = list(opensurge.iter_levels(FIX / "opensurge", rep))
    assert [lv.level for lv in levels] == ["tiny-1"]
    lv = levels[0]
    assert lv.license == "CC-BY-4.0" and lv.title == "Tiny Zone act 1" and "Test Author" in lv.attribution
    solid = cells(lv.grid, TileValue.SOLID)
    assert solid and cells(lv.grid, TileValue.HAZARD)
    # the passable brick adds nothing; the cloud (one-way) brick is standable
    cloud_row = min(r for r, _ in solid)
    assert cloud_row < max(r for r, _ in solid)
    kinds = sorted(e.entity_type for e in lv.entities)
    assert kinds == ["Enemy", "Exit", "Pickup", "Start"]


def test_opensurge_licence_gate_and_placeholder_author():
    rep = SourceReport("opensurge")
    list(opensurge.iter_levels(FIX / "opensurge", rep))
    assert "licence" in rep.skipped["nc-1.lev"]
    assert "author" in rep.skipped["template-1.lev"]
    assert rep.included == ["tiny-1.lev"]


def test_frogatto_nc_folder_is_skipped():
    rep = SourceReport("frogatto")
    assert list(frogatto.iter_levels(FIX / "frogatto", rep)) == []
    assert "non-commercial" in rep.skipped["data/levels/"]
    assert frogatto.level_folder_license("") is None
    assert frogatto.level_folder_license("Everything is licensed under CC-BY 3.0.\n") == "CC-BY-3.0"


# ── ingest + attribution ────────────────────────────────────────────────────


def test_ingest_source_records_licence_and_attribution(store, tmp_path):
    srep, irep = ingest_source(store, "supertux", FIX / "supertux", download=False)
    assert irep.added >= 1 and irep.levels_added == 1 and srep.included == ["world1/tiny.stl"]
    recs = list(store)
    assert all(r["source"] == "supertux" and r["license"] == "CC-BY-SA-4.0" for r in recs)
    assert all("Test Author" in r["attribution"] for r in recs)
    assert {r.get("kind", "pattern") for r in recs} == {"pattern", "level"}
    again = ingest_source(store, "supertux", FIX / "supertux", download=False)[1]
    assert again.added == 0 and again.duplicates == irep.added
    # nothing downloaded, nothing written outside the temp dirs
    assert not (tmp_path / "sources").exists()


def test_missing_download_is_reported_not_fetched(store):
    srep, irep = ingest_source(store, "opensurge", download=False)
    assert irep.added == 0 and "not downloaded" in srep.skipped["opensurge"]


def test_attribution_text_and_endpoint(store):
    ingest_source(store, "supertux", FIX / "supertux", download=False)
    ingest_source(store, "opensurge", FIX / "opensurge", download=False)
    store.save()
    att = attribution_text(store)
    assert "SuperTux" in att["text"] and "Open Surge" in att["text"] and "Test Author" in att["text"]
    assert {s["game"] for s in att["sources"]} == {"supertux", "opensurge"}
    body = TestClient(server.app).get("/patterns/attribution").json()
    assert body["text"] == att["text"]
    man = load_manifest()
    assert {g["game"] for g in man["games"]} == {"supertux", "opensurge", "frogatto"}
    assert all(g["url"].startswith("https://") for g in man["games"])


# ── design notes ────────────────────────────────────────────────────────────


def note_json(title, game, structure="corridor", words=40, tags=("teaching",), desc=None):
    return json.dumps({
        "title": title, "game": game, "level_or_section": "x", "technique": f"{title} technique",
        "description": desc or " ".join(["design"] * words), "structure_type": structure,
        "difficulty": 3, "tags": list(tags)})


TOPICS3 = TOPICS[:3]


def test_notes_are_generated_cached_and_budgeted(store):
    assert len(TOPICS) >= 40
    fake = FakeProvider([note_json("Teach by layout", "Super Mario Bros."),
                         note_json("Warp zone reward", "Super Mario Bros.")])
    rep = generate_notes(store, fake, topics=TOPICS3, max_calls=2)
    assert rep.generated == 2 and rep.stopped and rep.pending == 1
    rec = store.get(topic_id(TOPICS3[0]))
    assert rec["kind"] == "note" and rec["note"]["title"] == "Teach by layout"
    assert rec["reviewed"] is False and rec["accepted"] and not rec["ascii"]
    # cached: a second run only asks for the missing topic
    fake2 = FakeProvider([note_json("Remix one idea", "Super Mario Bros. 3")])
    rep = generate_notes(store, fake2, topics=TOPICS3)
    assert rep.generated == 1 and rep.cached == 2 and len(fake2.calls) == 1
    rep = generate_notes(store, FakeProvider([]), topics=TOPICS3)
    assert rep.calls == 0 and rep.pending == 0
    assert generate_notes(store, None, topics=TOPICS, dry_run=True).pending == len(TOPICS) - 3


def test_notes_reject_long_text_and_coordinates(store):
    fake = FakeProvider([note_json("Too long", "Celeste", words=MAX_WORDS + 40),
                         note_json("Coords", "Celeste", desc="Put a spike at (12, 4) then jump."),
                         note_json("Fine", "Celeste")])
    rep = generate_notes(store, fake, topics=TOPICS[:1], max_retries=3)
    assert rep.generated == 1 and len(fake.calls) == 3
    assert store.get(topic_id(TOPICS[0]))["note"]["title"] == "Fine"
    assert "VALIDATION FAILED" in fake.calls[1].messages[-1]["content"]


def _notes_store(store):
    fake = FakeProvider([
        note_json("Teach by layout", "Super Mario Bros.", "corridor", tags=("teaching", "first", "level")),
        note_json("Loops and speed", "Sonic the Hedgehog", "open", tags=("speed", "loop", "slopes")),
        note_json("Readable boss arena", "Hollow Knight", "arena", tags=("boss", "arena", "fight")),
    ])
    topics = [TOPICS[0], next(t for t in TOPICS if t["game"] == "Sonic the Hedgehog"),
              next(t for t in TOPICS if t["level_or_section"] == "boss arenas")]
    generate_notes(store, fake, topics=topics)
    sync_vectors(store, HashEmbedder())
    return store


def test_notes_are_retrieved_by_kind_and_go_in_the_prompt(store):
    _notes_store(store)
    hits = retrieve("boss arena fight", 1, store=store, kind="note")
    assert [h.title for h in hits] == ["Readable boss arena"]
    assert retrieve("boss arena", 3, store=store) == []  # notes are not patterns
    text, used = examples_block(["A Sonic speed loop level"], 2, store=store)
    assert NOTES_HEADER in text and "Loops and speed" in text
    assert any(u["title"] == "Loops and speed" for u in used) and len(used) <= 2
    assert "REFERENCE PATTERNS" not in text  # no pattern windows in this store


def test_excluded_notes_are_never_offered(store):
    _notes_store(store)
    nid = retrieve("boss arena fight", 1, store=store, kind="note")[0].id
    assert review_note(store, nid, reviewed=True, exclude=True)["reviewed"] is True
    assert nid in excluded_notes()
    _, used = examples_block(["boss arena fight"], 2, store=store)
    assert nid not in {u["id"] for u in used}
    review_note(store, nid, exclude=False)
    assert nid not in excluded_notes()


def test_notes_endpoints(store):
    _notes_store(store)
    store.save()
    client = TestClient(server.app)
    notes = client.get("/patterns/notes").json()["notes"]
    assert len(notes) == 3 and not any(n["reviewed"] for n in notes)
    nid = notes[0]["id"]
    r = client.post("/patterns/notes/review", json={"id": nid, "reviewed": True, "exclude": True}).json()
    assert r == {"id": nid, "reviewed": True, "excluded": True}
    assert client.post("/patterns/notes/review", json={"id": "nope"}).status_code == 404
    s = client.get("/patterns/stats").json()
    assert s["notes"] == 3 and s["patterns"] == 0


def test_generate_prompt_gets_design_guidance(store, monkeypatch, tmp_path, load_fixture):
    _notes_store(store)
    store.save()
    fake = FakeProvider([load_fixture("valid_two_sections.json")])
    monkeypatch.setattr(server, "provider_factory", lambda req: fake)
    out = tmp_path / "gen" / "level.ldtk"
    body = TestClient(server.app).post("/generate", json={
        "description": "A first level that teaches the jump.", "output_path": str(out),
        "densify": False, "patterns": 2}).json()
    assert body["success"] and any(u["id"].startswith("note-") for u in body["patterns_used"])
    assert NOTES_HEADER in fake.calls[0].messages[0]["content"]


def test_nothing_written_outside_temp_dirs(store, tmp_path):
    _notes_store(store)
    store.save()
    assert Path(os.environ["GLDTK_PATTERNS_DIR"]).parent == tmp_path
    assert (tmp_path / "patterns" / "index.json").is_file()
