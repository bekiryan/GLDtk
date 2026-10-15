"""Asset library: manifest validity, sprite lookup, add_theme/add_sprite."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from aesthetic.library import (
    DEFAULT_ROOT,
    Manifest,
    add_sprite,
    add_theme,
    clear_cache,
    find_sprite,
    load_library,
)


@pytest.fixture
def lib_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "assets"
    shutil.copytree(DEFAULT_ROOT, dest, ignore=shutil.ignore_patterns("sources", "preview.png"))
    clear_cache()
    yield dest
    clear_cache()


def test_manifest_validates_and_counts():
    raw = json.loads((DEFAULT_ROOT / "manifest.json").read_text())
    m = Manifest.model_validate(raw)
    assert m.version == 1 and m.tile_size == 16
    assert len(m.themes) >= 5
    assert 15 <= len(m.sprites) <= 25
    assert all(s.license == "CC0-1.0" for s in m.sources)


def test_manifest_rejects_unknown_source():
    with pytest.raises(ValueError):
        Manifest.model_validate(
            {"themes": [{"id": "x", "display_name": "X", "sheet": "themes/x.png", "source": "nope"}]}
        )


def test_referenced_files_exist_with_declared_size():
    lib = load_library()
    for t in lib.themes:
        p = lib.theme_sheet_path(t)
        assert p.exists(), p
        w, h = Image.open(p).size
        assert w % 16 == 0 and h % 16 == 0
        assert w >= 5 * 16 and h >= 3 * 16
    for s in lib.sprites:
        p = lib.sprite_path(s)
        assert p.exists(), p
        assert Image.open(p).size == tuple(s.size)


def test_sprite_coverage():
    lib = load_library()
    kinds = {s.kind for s in lib.sprites}
    assert {"Player", "Enemy", "Pickup", "Weapon", "Interactive", "NPC", "Checkpoint", "Key", "Start", "Exit"} <= kinds
    tags = {t for s in lib.sprites for t in s.tags}
    for needed in ("walker", "flyer", "jumper", "turret", "boss", "coin", "gem", "health", "ammo", "powerup",
                   "sword", "bow", "gun", "staff", "chest", "door", "lever", "switch", "sign"):
        assert needed in tags, needed


def test_find_sprite_exact_tag_wins():
    assert find_sprite("Enemy", ["a", "bat", "swooping"]).id == "enemy_bat"
    assert find_sprite("Enemy", ["jumper"]).id == "enemy_spider"
    assert find_sprite("Pickup", ["gem"]).id == "pickup_gem"
    assert find_sprite("Weapon", ["Bow"]).id == "weapon_bow"


def test_find_sprite_kind_fallback():
    assert find_sprite("Enemy", ["zzzz"]).kind == "Enemy"
    assert find_sprite("Enemy", []).default is True
    # a tag of another kind must not leak across kinds
    assert find_sprite("Pickup", ["skeleton"]).kind == "Pickup"
    assert find_sprite("NoSuchKind", ["x"]) is None


def test_add_sprite_updates_manifest_and_reloads(lib_copy: Path):
    img = Image.new("RGBA", (16, 32), (255, 0, 0, 255))
    entry = add_sprite("enemy_dragon", "Enemy", img, tags=["Dragon", "boss"], root=lib_copy)
    assert entry.generated and entry.size == (16, 32)
    assert (lib_copy / "sprites" / "enemy_dragon.png").exists()
    lib = load_library(lib_copy)  # cache was cleared
    assert find_sprite("Enemy", ["dragon"], library=lib).id == "enemy_dragon"
    assert any(s.id == "generated" for s in lib.sources)
    with pytest.raises(ValueError):
        add_sprite("enemy_dragon", "Enemy", img, root=lib_copy)
    with pytest.raises(ValueError):
        add_sprite("../evil", "Enemy", img, root=lib_copy)


def test_add_theme_updates_manifest_and_reloads(lib_copy: Path):
    sheet = Image.new("RGBA", (80, 64), (10, 200, 10, 255))
    n_before = len(load_library(lib_copy).themes)
    add_theme("jungle2", "Jungle 2", sheet, keywords=["Liana"], root=lib_copy)
    lib = load_library(lib_copy)
    assert len(lib.themes) == n_before + 1
    t = lib.themes[-1]
    assert t.id == "jungle2" and t.generated and t.keywords == ["liana"]
    assert (lib_copy / "themes" / "jungle2.png").exists()
    with pytest.raises(ValueError):
        add_theme("tiny", "Tiny", Image.new("RGBA", (16, 16)), root=lib_copy)
    with pytest.raises(ValueError):
        add_theme("jungle2", "Dup", sheet, root=lib_copy)
    # the original library is untouched
    assert all(t.id != "jungle2" for t in load_library().themes)
