"""v4 Part 26b: stamp retrieved patterns into terrain sections (offline, fake embedder)."""

from __future__ import annotations

import random

from api.models import GenerateRequest
from layout.ir import TileValue
from layout.terrain.base import Canvas, Port, TerrainContext
from layout.terrain.stamp import _air_connected, fit_pattern, pattern_grid
from pipeline import build_level
from pipeline.chunks import _FixedController
from pipeline.level import _layout_and_verify
from pipeline.stamps import StampFinder, stamped_patterns
from patterns.store import PatternStore
from tests.test_terrain import PROFILE, terrain_spec

import server


class ConstEmbedder:
    """Every text gets the same vector: every pattern scores 1.0 (+ the structure bonus)."""

    name = "const"

    def embed(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


def corridor_ascii(w: int = 30, h: int = 24, pit=(13, 14), floor: int = 21) -> str:
    rows = []
    for r in range(h):
        line = []
        for c in range(w):
            if floor - 6 <= r < floor:
                line.append(".")
            elif r == floor and pit[0] <= c <= pit[1]:
                line.append("^")
            else:
                line.append("#")
        rows.append("".join(line))
    return "\n".join(rows)


def record(pid: str, ascii_text: str, structure: str = "corridor", kind: str = "pattern"):
    return {"id": pid, "kind": kind, "source": "test", "license": "CC0", "origin_path": "/x.ldtk",
            "level": "L", "ascii": ascii_text, "accepted": True, "label": {"title": pid, "structure_type": structure},
            "features": {"structure": structure, "route": {"ok": True}}}


def make_store(*recs) -> PatternStore:
    store = PatternStore()
    store.embedder = {"name": "const", "dim": 3}
    for rec in recs:
        store.add(rec)
        store.vectors[rec["id"]] = {"text_hash": "x", "vec": [1.0, 0.0, 0.0]}
    store.save()
    return store


def ctx_for(width: int = 60, entry_row: int = 20, exit_row: int = 22) -> TerrainContext:
    return TerrainContext(section_id="s0", structure="corridor", rect=(0, 0, width - 1, 29),
                          level_size=(120, 30), orientation="horizontal",
                          entry=Port("end", 1, entry_row), exit=Port("door", width, exit_row),
                          rng=random.Random(0))


def apply(ctx: TerrainContext, cv: Canvas):
    grid = [[TileValue.SOLID] * 120 for _ in range(30)]
    cv.apply(grid)
    return grid


# ── fit_pattern ─────────────────────────────────────────────────────────────


def test_fit_pattern_tiles_pads_and_connects_ports():
    ctx = ctx_for()
    cv = Canvas(ctx)
    res = fit_pattern(pattern_grid(record("p", corridor_ascii())), ctx, cv)
    assert res is not None
    grid = apply(ctx, cv)
    c_lo, c_hi = ctx.inner_cols
    # the pit is kept 2 wide (only flat floor columns are repeated)
    spikes = {c for r in range(30) for c in range(120) if grid[r][c] == TileValue.HAZARD}
    assert len(spikes) == 2
    # 4 rock rows were padded on top (24 pattern rows into 28 inner rows)
    assert all(grid[r][c] == TileValue.SOLID for r in range(1, 5) for c in range(c_lo, c_hi + 1))
    # ports: floor under, air above, joined through the pattern
    assert grid[20][0] == TileValue.SOLID and grid[19][1] == TileValue.EMPTY
    assert grid[22][59] == TileValue.SOLID and grid[21][59] == TileValue.EMPTY
    assert _air_connected(cv, (1, 19), (59, 21))
    assert res.path[0] == (0, 20) and res.path[-1] == (59, 22)


def test_fit_pattern_crops_wide_and_rejects_far_off_sizes():
    ctx = ctx_for()
    assert fit_pattern(pattern_grid(record("w", corridor_ascii(w=70))), ctx) is not None  # 16 % crop
    assert fit_pattern(pattern_grid(record("x", corridor_ascii(w=100))), ctx) is None  # 41 % crop
    assert fit_pattern(pattern_grid(record("y", corridor_ascii(h=12, floor=10))), ctx) is None  # short
    assert fit_pattern(pattern_grid(record("z", corridor_ascii(h=40, floor=37))), ctx) is None  # tall
    vert = ctx_for()
    vert.orientation = "vertical"
    assert fit_pattern(pattern_grid(record("p", corridor_ascii())), vert) is None


def test_note_records_and_records_without_grid_are_never_stamped():
    assert pattern_grid(record("n", corridor_ascii(), kind="note")) is None
    assert pattern_grid({"id": "e", "ascii": ""}) is None
    store = make_store(record("n", corridor_ascii(), kind="note"))
    assert StampFinder("a corridor", store=store, embedder=ConstEmbedder())(terrain_spec(["corridor"])) == {}


# ── pipeline ────────────────────────────────────────────────────────────────


def test_level_stamps_matching_pattern_verifies_and_reports_it():
    store = make_store(record("good", corridor_ascii()))
    spec = terrain_spec(["corridor", "corridor"])
    finder = StampFinder("a corridor", store=store, embedder=ConstEmbedder())
    built = build_level("t", _FixedController(spec), PROFILE, seed=0, densify=False, stamp_finder=finder)
    assert built.report.ok
    stamped = [b.id for b in built.spec_layout.sections if b.pattern == "good"]
    assert stamped
    used = built.patterns_stamped
    assert used and used[0]["id"] == "good" and used[0]["use"] == "stamped"
    assert used[0]["source"] == "test" and used[0]["license"] == "CC0"
    meta = {s["id"]: s for s in built.spec_layout.ir.metadata["sections"]}
    assert meta[stamped[0]]["pattern"] == "good"


def test_stamp_that_breaks_verification_falls_back_to_procedural():
    # a spike floor 14 columns wide cannot be crossed: the stamp fails, the generator runs
    store = make_store(record("bad", corridor_ascii(pit=(8, 21))))
    spec = terrain_spec(["corridor", "corridor"])
    finder = StampFinder("a corridor", store=store, embedder=ConstEmbedder())
    sl, rep, applied = _layout_and_verify(spec, PROFILE, 0, finder)
    assert rep.ok
    assert not any(b.pattern for b in sl.sections)
    assert any(a.startswith("terrain:") for a in applied)


def test_off_or_empty_index_means_procedural_only():
    spec = terrain_spec(["corridor", "corridor"])
    plain, _r, _a = _layout_and_verify(spec, PROFILE, 0)
    empty = StampFinder("a corridor", store=PatternStore(), embedder=ConstEmbedder())
    assert empty(spec) == {}
    sl, _r, _a = _layout_and_verify(spec, PROFILE, 0, empty)
    assert sl.ir.grid == plain.ir.grid
    other = make_store(record("lab", corridor_ascii(), structure="labyrinth"))
    assert StampFinder("x", store=other, embedder=ConstEmbedder())(spec) == {}
    req = GenerateRequest(description="x", output_path="/tmp/x.ldtk", stamp_patterns=False)
    assert server.stamp_finder_for(req) is None
    assert server.stamp_finder_for(GenerateRequest(description="x", output_path="/tmp/x.ldtk")) is not None


def test_same_seed_and_index_give_the_same_level():
    store = make_store(record("good", corridor_ascii()))
    spec = terrain_spec(["corridor", "corridor"])
    grids = []
    for _ in range(2):
        finder = StampFinder("a corridor", store=store, embedder=ConstEmbedder())
        sl, rep, _a = _layout_and_verify(spec, PROFILE, 3, finder)
        assert rep.ok
        grids.append(sl.ir.grid)
    assert grids[0] == grids[1]


def parkour_ascii(w: int = 40, h: int = 20, pit=(12, 15)) -> str:
    """Open sky (no rock on top), floating platforms, ground with bottomless pits."""
    rows = []
    for r in range(h):
        line = []
        for c in range(w):
            if r >= h - 3:
                line.append("." if pit[0] <= c <= pit[1] or 26 <= c <= 29 else "#")
            elif (r == 7 and 10 <= c <= 14) or (r == 12 and 20 <= c <= 24):
                line.append("#")
            else:
                line.append(".")
        rows.append("".join(line))
    return "\n".join(rows)


def test_open_parkour_windows_stack_in_tall_sections():
    """Part 26f: the top window's open pits already reach the bottom window's sky, so the ramp
    need not merge two air regions; such stacks used to be rejected (no parkour stamp at 34+)."""
    store = make_store(record("p0", parkour_ascii(), structure="parkour"),
                       record("p1", parkour_ascii(pit=(13, 16)), structure="parkour"))
    for h in (34, 40):
        finder = StampFinder("a parkour level", store=store, embedder=ConstEmbedder())
        sl, rep, _a = _layout_and_verify(terrain_spec(["parkour", "parkour"], size=(120, h)),
                                         PROFILE, 0, finder)
        assert rep.ok, rep.error_messages()
        assert all(b.pattern and b.stacked for b in sl.sections), h
