"""v4 Part 25a: endless chunks (offline, no LLM)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import pipeline.chunks as chunks_mod
from game import GameProfile
from layout import TileValue
from pipeline.chunks import (
    CHUNK_H,
    CHUNK_W,
    SEAM_W,
    build_chunk,
    export_chunk,
    seam_of,
    seam_cells,
)

from test_world import assert_linked, assert_schema_valid, client, doc_of, instances  # noqa: F401

PROFILE = GameProfile()


@pytest.fixture(scope="module")
def run3():
    return [build_chunk(11, k) for k in range(3)]


def _platform_at(chunk, nid):
    return chunk.built.spec_layout.node_layouts[nid]


def test_seams_come_from_the_border_hash():
    for k in range(-3, 6):
        for o in ("horizontal", "vertical"):
            assert seam_of(11, k, "exit", o) == seam_of(11, k + 1, "entry", o)
    rows = {seam_of(s, 0, "exit").row for s in range(20)}
    assert len(rows) > 3 and all(CHUNK_H // 3 + 2 <= r <= CHUNK_H - 3 for r in rows)


def test_chunk_seams_agree_with_the_neighbour(run3):
    ph = PROFILE.player_tiles()[1]
    for a, b in zip(run3, run3[1:]):
        assert a.ok and b.ok
        assert a.exit == b.entry
        ex, st = _platform_at(a, "exit"), _platform_at(b, "start")
        assert ex.row == st.row == a.exit.row
        assert ex.col + ex.width == CHUNK_W and st.col == 0
        # the opening above the shared floor is empty on both sides of the border
        ga, gb = a.built.spec_layout.ir.grid, b.built.spec_layout.ir.grid
        for r in range(a.exit.row - ph - 1, a.exit.row):
            assert ga[r][CHUNK_W - 1] == TileValue.EMPTY and gb[r][0] == TileValue.EMPTY
        assert ga[a.exit.row][CHUNK_W - 1] == gb[b.entry.row][0] == TileValue.SOLID
        for c, r in seam_cells(a.exit, "exit", ph):
            assert ga[r][c] == TileValue.EMPTY


def test_chunks_are_noise_terrain_with_a_verified_path(run3):
    for c in run3:
        b = c.built
        assert b.report.ok and b.stats["fill_added"] > 200  # mostly rock
        kinds = [e.entity_type for e in b.spec_layout.ir.entities]
        assert kinds.count("Start") == 1 and "Exit" in kinds and "Pickup" in kinds
    far = build_chunk(11, 12)
    assert far.ok
    near_enemies = sum(e.kind == "Enemy" for e in run3[0].built.spec.entities)
    assert sum(e.kind == "Enemy" for e in far.built.spec.entities) > near_enemies


def test_vertical_chunk_verifies():
    c = build_chunk(5, 1, orientation="vertical", theme="sky islands")
    assert c.ok and c.built.spec_layout.width == CHUNK_H and c.built.spec_layout.height == CHUNK_W
    assert _platform_at(c, "start").col == c.entry.col
    ex = _platform_at(c, "exit")
    assert ex.col == c.exit.col and ex.row == CHUNK_W - 1 and ex.width == SEAM_W


def test_chunk_k_is_the_same_alone_or_in_a_run(run3, tmp_path):
    alone = build_chunk(11, 2)
    assert alone.built.spec_layout.ir.grid == run3[2].built.spec_layout.ir.grid
    out = tmp_path / "w.ldtk"
    texts = []
    for c in (alone, run3[2], build_chunk(11, 2)):
        fresh, _, _ = export_chunk(c, "cave", 11, out, tmp_path / "gldtk.game.json", PROFILE)
        fresh.pop("appBuildId")
        texts.append(json.dumps(fresh, indent=2))
    assert texts[0] == texts[1] == texts[2]


def _level_content(lvl):
    """What a chunk level holds, without its place in the file (uid, links)."""
    grid = next(li["intGridCsv"] for li in lvl["layerInstances"] if li["__type"] == "IntGrid")
    ents = sorted((li["__identifier"], ei["__identifier"], tuple(ei["px"]))
                  for li in lvl["layerInstances"] for ei in li["entityInstances"])
    return grid, ents, lvl["pxWid"], lvl["pxHei"]


def test_chunks_route_appends_linked_schema_valid_levels(client, tmp_path):  # noqa: F811
    out = tmp_path / "endless" / "world.ldtk"
    body = client.post("/chunks", json={"path": str(out), "count": 2, "seed": 11}).json()
    assert body["success"], body.get("error")
    assert [c["k"] for c in body["chunks"]] == [0, 1] and all(c["verified"] for c in body["chunks"])
    first = doc_of(out)
    assert len(first["levels"]) == 2

    body = client.post("/chunks", json={"path": str(out), "count": 2, "seed": 11}).json()
    assert body["success"], body.get("error")
    assert [c["k"] for c in body["chunks"]] == [2, 3]  # continues after the last chunk
    doc = doc_of(out)
    assert [lv["identifier"] for lv in doc["levels"]] == [f"Chunk_{k}" for k in range(4)]
    assert doc["worldLayout"] == "LinearHorizontal"
    assert_schema_valid(doc)
    assert_linked(doc)
    # the first two levels are unchanged apart from chunk 1's new link to chunk 2
    assert json.dumps(doc["levels"][0]) == json.dumps(first["levels"][0])
    assert (Path(out.parent) / "gldtk_assets").is_dir()

    # chunk 2 generated alone in another file holds the same level
    other = tmp_path / "alone" / "world.ldtk"
    body = client.post("/chunks", json={"path": str(other), "count": 1, "seed": 11,
                                        "start_k": 2}).json()
    assert body["success"], body.get("error")
    assert _level_content(doc_of(other)["levels"][0]) == _level_content(doc["levels"][2])


def test_a_failing_chunk_check_writes_nothing(client, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(chunks_mod, "CHUNK_CHECKS", [lambda built, profile: ["soft-lock at 3,4"]])
    out = tmp_path / "w.ldtk"
    body = client.post("/chunks", json={"path": str(out), "count": 1}).json()
    assert not body["success"] and "soft-lock" in body["error"]
    assert not out.exists()
