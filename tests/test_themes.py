"""Themes built from the asset library: roles, detection, asset copying, variants."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from aesthetic import THEMES, TileRole, autotile_level, copy_theme_assets, detect_theme
from layout.ir import IRLevel, TileValue

REAL = [t for t in THEMES if t != "default"]


def test_at_least_five_themes_and_default():
    assert len(REAL) >= 5
    assert "default" in THEMES
    assert len({THEMES[t].tileset_uid for t in REAL}) == len(REAL)  # stable, unique uids


@pytest.mark.parametrize("tid", REAL)
def test_all_roles_inside_sheet_and_opaque(tid: str):
    theme = THEMES[tid]
    sheet = Image.open(theme.sheet_path).convert("RGBA")
    assert (theme.tileset_px_width, theme.tileset_px_height) == sheet.size
    addrs = {r: theme.tile(r) for r in TileRole}
    assert len(addrs) == 14
    for role, a in addrs.items():
        assert a.src_x % 16 == 0 and a.src_y % 16 == 0
        assert 0 <= a.src_x <= sheet.width - 16 and 0 <= a.src_y <= sheet.height - 16, role
        tile = sheet.crop((a.src_x, a.src_y, a.src_x + 16, a.src_y + 16))
        assert tile.getchannel("A").getbbox() is not None, f"{tid}:{role} is fully transparent"
    for role, extra in theme.variants.items():
        for a in extra:
            tile = sheet.crop((a.src_x, a.src_y, a.src_x + 16, a.src_y + 16))
            assert tile.getchannel("A").getbbox() is not None


@pytest.mark.parametrize("tid", REAL)
def test_each_theme_detected_by_its_own_keywords(tid: str):
    theme = THEMES[tid]
    assert theme.keywords
    other = {kw for t in REAL if t != tid for kw in THEMES[t].keywords}
    unique = [kw for kw in theme.keywords if kw not in other]
    assert unique, "theme needs at least one distinguishing keyword"
    assert detect_theme(f"a level with {unique[0]}").identifier == tid


def test_detect_theme_override_and_default():
    assert detect_theme("a snowy glacier", override="lava").identifier == "lava"
    assert detect_theme("qwerty asdf").identifier == "default"
    assert detect_theme("").identifier == "default"
    assert detect_theme("lava volcano").identifier == "lava"


def test_copy_theme_assets_returns_working_relative_path(tmp_path: Path):
    theme = THEMES["forest"]
    rel = copy_theme_assets(theme, tmp_path)
    assert rel == theme.tileset_rel_path and not Path(rel).is_absolute()
    copied = tmp_path / rel
    assert copied.exists() and copied.read_bytes() == theme.sheet_path.read_bytes()
    copy_theme_assets(theme, tmp_path)  # idempotent


def test_copy_theme_assets_leaves_unchanged_sheet_alone(tmp_path: Path):
    # LDtk hot-reloads a rewritten tileset image and marks the level modified.
    theme = THEMES["forest"]
    copied = tmp_path / copy_theme_assets(theme, tmp_path)
    os.utime(copied, ns=(1, 1))
    copy_theme_assets(theme, tmp_path)
    assert copied.stat().st_mtime_ns == 1

    copied.write_bytes(b"stale")
    copy_theme_assets(theme, tmp_path)
    assert copied.read_bytes() == theme.sheet_path.read_bytes()


def test_first_generate_copies_every_theme_sheet_and_later_ones_write_nothing(tmp_path: Path):
    # A sheet appearing after LDtk loaded the level makes its file watcher reload
    # the tileset (M2 bug 2): the first copy writes every library sheet.
    from aesthetic import build_aesthetic_layer, copy_aesthetic_assets
    from symbolic.schema import AbstractLevelGraph

    ir = IRLevel.empty("Level_0", 30, 15)
    ir.fill_rect(0, 14, 30, 1, TileValue.SOLID)
    result = build_aesthetic_layer("a forest", AbstractLevelGraph(), {}, ir,
                                   theme_override="forest", place_random_entities=False)
    paths = copy_aesthetic_assets(result, tmp_path)
    assert THEMES["forest"].tileset_rel_path in paths
    with_sheets = [t for t in THEMES.values() if t.sheet_path and t.sheet_path.exists()]
    assert len(with_sheets) >= 8
    for t in with_sheets:
        assert (tmp_path / t.tileset_rel_path).read_bytes() == t.sheet_path.read_bytes()

    files = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    for p in files:
        os.utime(p, ns=(1, 1))
    other = build_aesthetic_layer("a dungeon", AbstractLevelGraph(), {}, ir,
                                  theme_override="dungeon", place_random_entities=False)
    copy_aesthetic_assets(other, tmp_path)
    assert sorted(p for p in tmp_path.rglob("*") if p.is_file()) == files
    assert all(p.stat().st_mtime_ns == 1 for p in files)


def test_write_sprite_atlas_skips_identical_bytes(tmp_path: Path):
    from aesthetic.sprites import SpriteAtlas, write_sprite_atlas

    atlas = SpriteAtlas(png=b"png-bytes", width=16, height=16, tile_size=16)
    dest = tmp_path / write_sprite_atlas(atlas, tmp_path)
    os.utime(dest, ns=(1, 1))
    write_sprite_atlas(atlas, tmp_path)
    assert dest.stat().st_mtime_ns == 1

    atlas.png = b"new-bytes"
    write_sprite_atlas(atlas, tmp_path)
    assert dest.read_bytes() == b"new-bytes"


def test_autotile_uses_variants_deterministically():
    ir = IRLevel.empty("t", 12, 8)
    for r in range(8):
        for c in range(12):
            ir.grid[r][c] = TileValue.SOLID
    theme = THEMES["dungeon"]
    a = autotile_level(ir, theme)
    b = autotile_level(ir, theme)
    assert [(e.src_x, e.src_y) for e in a] == [(e.src_x, e.src_y) for e in b]
    centers = {(e.src_x, e.src_y) for e in a if 0 < e.col < 11 and 0 < e.row < 7}
    assert len(centers) > 1  # variant selection actually varies
    allowed = {(theme.tile(TileRole.CENTER).src_x, theme.tile(TileRole.CENTER).src_y)} | {
        (v.src_x, v.src_y) for v in theme.variants[TileRole.CENTER]
    }
    assert centers <= allowed
