"""v4 Part 25a: seeded noise and the noise terrain fill (offline)."""

from __future__ import annotations

import json

import pytest

import layout.fill as fill_mod
from aesthetic.autotile import _BITMASK_TO_ROLE, _neighbour_mask
from aesthetic.themes import TileRole
from edit.check import verify_level
from edit.context import load_context
from edit.region import edit_section, section_rect
from layout import TileValue, verify_grid
from layout.fill import FillParams, fill_layout, level_protected, noise_fill
from layout.noise import fbm, sample_field, smooth_ca, value_noise
from llm import FakeProvider, LLMController
from pipeline import build_level

from test_densify import PROFILE, _laid_out, _sparse
from test_edit import client, doc_of, level, platform, base_level, region_answer  # noqa: F401


# ── noise ────────────────────────────────────────────────────────────────────


def test_noise_is_deterministic_and_seeded():
    a = sample_field(5, 20, 10, (100, 7), warp_strength=3.0)
    assert a == sample_field(5, 20, 10, (100, 7), warp_strength=3.0)
    assert a != sample_field(6, 20, 10, (100, 7), warp_strength=3.0)
    assert all(0.0 <= v <= 1.0 for line in a for v in line)


def test_noise_is_continuous_across_chunk_borders():
    w = 64
    for k in range(-2, 3):
        left = sample_field(9, w, 12, (k * w, 0))
        right = sample_field(9, w, 12, ((k + 1) * w, 0))
        whole = sample_field(9, 2 * w, 12, (k * w, 0))
        for r in range(12):  # both chunks sample one world field
            assert left[r] + right[r] == whole[r]
        border = (k + 1) * w
        for y in (0.0, 3.3, 7.9):  # no jump at the border itself
            for eps in (1e-3, 1e-5):
                assert abs(fbm(9, border - eps, y) - fbm(9, border + eps, y)) < 50 * eps
                assert abs(value_noise(9, border - eps, y) - value_noise(9, border + eps, y)) < 5 * eps


def test_ca_smoothing_follows_the_4_5_rule_and_pins_cells():
    cells = [[False] * 5 for _ in range(5)]
    for r, c in ((1, 1), (1, 2), (2, 1), (2, 2), (3, 3)):
        cells[r][c] = True
    out = smooth_ca(cells, 1, outside=False)
    assert out[1][1] is False  # 4 of 9 solid: below 5
    assert out[2][2] is True  # 5 of 9 (itself, 3 of the block, (3,3))
    pinned = smooth_ca(cells, 2, mutable=lambda c, r: (c, r) != (1, 1), outside=False)
    assert pinned[1][1] is True


# ── fill ─────────────────────────────────────────────────────────────────────


def _filled(style="cave", density=0.6, seed=7):
    spec = _sparse()
    lay, report = _laid_out(spec)
    before = [list(r) for r in lay.ir.grid]
    protected = level_protected(lay, report, PROFILE)
    res = fill_layout(lay, report, PROFILE, spec, FillParams(density=density, style=style), seed=seed)
    return spec, lay, report, before, protected, res


@pytest.mark.parametrize("style", ["cave", "ruins", "sky"])
def test_fill_never_touches_protected_cells_and_the_level_verifies(style):
    spec, lay, report, before, protected, res = _filled(style)
    assert res.added > 0 and res.report is not None and res.report.ok
    assert verify_grid(lay, PROFILE, spec).ok  # from scratch
    grid = lay.ir.grid
    changed = {(c, r) for r, line in enumerate(grid) for c, v in enumerate(line)
               if v != before[r][c]}
    assert len(changed) == res.added
    assert all(before[r][c] == TileValue.EMPTY and grid[r][c] == TileValue.SOLID
               for c, r in changed)  # only rock is added, nothing is removed
    margin = FillParams().min_distance_from_path
    for c, r in changed:  # never within the margin of a protected cell
        assert not any((c + dc, r + dr) in protected for dc in range(-margin, margin + 1)
                       for dr in range(-margin, margin + 1))
    # rasterise keeps the rock (rock_N layout nodes), which are not spec nodes
    lay.rasterise()
    assert all(lay.ir.grid[r][c] == TileValue.SOLID for c, r in changed)
    assert any(n.startswith("rock_") for n in lay.node_layouts)


def test_fill_is_seeded_and_density_scales_it():
    a = _filled(seed=3)[1].ir.grid
    assert a == _filled(seed=3)[1].ir.grid
    assert a != _filled(seed=4)[1].ir.grid
    sparse = _filled(density=0.2)[-1].added
    dense = _filled(density=0.9)[-1].added
    assert dense > sparse
    sky = _filled("sky", 0.6)[-1].added
    assert sky < _filled("cave", 0.6)[-1].added  # sky = sparse islands


def test_fill_drops_blobs_that_break_verification():
    grid = [[TileValue.EMPTY] * 40 for _ in range(20)]
    calls = []

    def verify(g):  # pretend every blob touching column 30+ breaks the level
        calls.append(1)
        return not any(g[r][c] == TileValue.SOLID for r in range(20) for c in range(30, 40))

    wall = [(20, r) for r in range(20)]  # a protected column splits the rock in two
    res = noise_fill(grid, wall, 1, FillParams(density=0.9), verify=verify)
    assert res.blobs and res.dropped > 0
    assert verify(res.grid)
    every = noise_fill(grid, wall, 1, FillParams(density=0.9))
    assert len(res.blobs) + res.dropped == len(every.blobs)


def test_large_masses_use_the_center_tiles():
    _, lay, *_ = _filled(density=0.9)
    roles = [_BITMASK_TO_ROLE[_neighbour_mask(lay.ir, c, r, TileValue.SOLID)]
             for r in range(lay.height) for c in range(lay.width)
             if lay.ir.grid[r][c] == TileValue.SOLID]
    assert roles.count(TileRole.CENTER) > 50


def test_build_level_fill_flag(monkeypatch):
    raw = json.dumps(_sparse().model_dump(by_alias=True))

    def build(**kw):
        return build_level("sparse", LLMController(provider=FakeProvider([raw])), PROFILE, seed=7,
                           densify=False, **kw)

    on = build(fill=0.7)
    assert on.report.ok and on.stats["fill_added"] > 0
    assert any(w.startswith(f"fill: added {on.stats['fill_added']} rock tile") for w in on.warnings)
    assert not any(n.id.startswith("rock_") for n in on.spec.nodes)

    def boom(*a, **k):
        raise AssertionError("fill ran with fill=None")

    monkeypatch.setattr(fill_mod, "fill_layout", boom)
    off, default = build(fill=None), build()
    assert off.stats["fill_added"] == 0
    assert off.spec_layout.ir.grid == default.spec_layout.ir.grid
    assert off.spec == default.spec and off.warnings == default.warnings


def test_fill_null_gives_byte_identical_files(client, monkeypatch, tmp_path):  # noqa: F811
    """/generate with fill null (the default) writes the same bytes as before the fill existed."""
    import itertools
    import uuid

    import server
    from layout import ldtk_adapter

    raw = json.dumps(_sparse().model_dump(by_alias=True))
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([raw]))
    outs = []
    for i, extra in enumerate(({}, {"fill": None})):
        out = tmp_path / f"{i}" / "level.ldtk"
        counter = itertools.count()  # the server thread's iids, the same in both runs
        monkeypatch.setattr(ldtk_adapter, "_new_iid", lambda: str(uuid.UUID(int=next(counter))))
        body = client.post("/generate", json={"description": "sparse", "output_path": str(out),
                                              "seed": 7, **extra}).json()
        assert body["success"], body.get("error")
        assert body["stats"]["fill_added"] == 0
        doc = doc_of(out)
        doc.pop("appBuildId", None)
        outs.append(json.dumps(doc, indent=2))
    assert outs[0] == outs[1]


# ── region / section edits ───────────────────────────────────────────────────


SPARSE = region_answer([platform("a", 4, 12), platform("b", 14, 10)], [])


def test_section_edit_fills_inside_the_band_only(level):  # noqa: F811
    before = load_context(level)
    band = section_rect(before, "dungeon")
    out = edit_section(str(level), "dungeon", "sparse", FakeProvider([SPARSE]), densify=False,
                       fill=0.9)
    assert out.success and out.verified, out.error
    after = load_context(level)
    assert out.stats["fill_added"] > 0
    assert any(w.startswith("fill: added") for w in out.warnings)
    for r in range(after.height):
        for c in range(after.width):
            if not band.contains(c, r):
                assert after.grid[r][c] == before.grid[r][c]
    assert verify_level(after, after.grid, after.entities).ok
