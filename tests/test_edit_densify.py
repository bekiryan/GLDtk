"""Part 12: densify inside region and section edits (offline, FakeProvider)."""

from __future__ import annotations

import server
from edit.check import verify_level
from edit.context import find_seams, load_context
from edit.region import edit_section, section_rect
from layout import TileValue
from layout.ldtk_reader import read_level
from llm import FakeProvider

from test_edit import client, doc_of, level, platform, base_level, region_answer  # noqa: F401

SPARSE = region_answer([platform("a", 4, 12), platform("b", 14, 10)], [])


def _platform_count(grid, rect=None) -> int:
    n = 0
    for r, row in enumerate(grid):
        c = 0
        while c < len(row):
            if row[c] == TileValue.SOLID and (rect is None or rect.contains(c, r)):
                while c < len(row) and row[c] == TileValue.SOLID:
                    c += 1
                n += 1
            else:
                c += 1
    return n


def test_section_edit_densifies_inside_the_band_only(level):
    before = load_context(level)
    band = section_rect(before, "dungeon")
    out = edit_section(str(level), "dungeon", "sparse", FakeProvider([SPARSE]), densify=False)
    assert out.success, out.error
    sparse = load_context(level)
    level.write_bytes(before.raw)

    out = edit_section(str(level), "dungeon", "sparse", FakeProvider([SPARSE]))
    assert out.success and out.verified, out.error
    added = out.stats["densify_added"]
    assert added > 0
    assert any(w.startswith(f"densify: added {added} fill platform") for w in out.warnings)
    dense = load_context(level)
    assert _platform_count(dense.grid, band) >= _platform_count(sparse.grid, band) + added
    for r in range(dense.height):  # nothing outside the band changes
        for c in range(dense.width):
            if not band.contains(c, r):
                assert dense.grid[r][c] == before.grid[r][c]
    assert verify_level(dense, dense.grid, dense.entities).ok

    nodes = [n.id for n in read_level(doc_of(level)).spec.nodes]
    assert sum(1 for n in nodes if n.startswith("fill_")) == added


def test_densify_false_adds_nothing(level):
    out = edit_section(str(level), "dungeon", "sparse", FakeProvider([SPARSE]), densify=False)
    assert out.success and out.stats["densify_added"] == 0
    assert not any("densify" in w for w in out.warnings)
    assert not any(n.id.startswith("fill_") for n in read_level(doc_of(level)).spec.nodes)


def test_densify_is_seeded(level):
    base = level.read_bytes()
    grids = {}
    for key, seed in (("a", 3), ("b", 3), ("c", 4)):
        level.write_bytes(base)
        assert edit_section(str(level), "dungeon", "sparse", FakeProvider([SPARSE]), seed=seed).success
        grids[key] = load_context(level).grid
    assert grids["a"] == grids["b"]
    assert grids["a"] != grids["c"]


def test_densify_keeps_seams_and_headroom_empty(level):
    before = load_context(level)
    band = section_rect(before, "dungeon")
    seams = find_seams(before, band, before.extract(before.grid))
    out = edit_section(str(level), "dungeon", "sparse", FakeProvider([SPARSE]))
    assert out.success and out.stats["densify_added"] > 0
    ctx = load_context(level)
    ph = ctx.player_h
    for s in seams:
        for c, r in s.cells:
            assert ctx.grid[r][c] == TileValue.SOLID
            assert all(ctx.grid[r - d][c] == TileValue.EMPTY for d in range(1, ph + 1))
    for e in ctx.entities:
        assert ctx.grid[e.row][e.col] == TileValue.EMPTY
        assert all(ctx.grid[e.row - d][e.col] == TileValue.EMPTY
                   for d in range(1, ph + 1) if e.row - d >= 0)


def test_region_route_reports_densify_added(client, level, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([SPARSE]))
    body = client.post("/edit/region", json={
        "path": str(level), "rect": {"col": 30, "row": 0, "w": 30, "h": 20},
        "prompt": "sparse"}).json()
    assert body["success"], body.get("error")
    assert body["stats"]["densify_added"] > 0
