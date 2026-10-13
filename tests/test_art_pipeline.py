"""AI art post-processing (Part 6): offline, synthetic images, fake provider."""

from __future__ import annotations

import io
import json
import random
import shutil
from pathlib import Path

import pytest
from PIL import Image

from aesthetic.library import DEFAULT_ROOT, Manifest, clear_cache, find_sprite, load_library
from art import derive, pipeline
from art.pipeline import pixels
from art.providers.fake import FakeImageProvider

FIX = Path(__file__).resolve().parent / "fixtures" / "art"
T = 16


def _load_fixture_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("art_fixtures", FIX / "make_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FIXMOD = _load_fixture_module()


@pytest.fixture
def lib_copy(tmp_path: Path):
    dest = tmp_path / "assets"
    shutil.copytree(DEFAULT_ROOT, dest, ignore=shutil.ignore_patterns("sources", "preview.png"))
    clear_cache()
    yield dest
    clear_cache()
    from aesthetic.themes import reload_themes

    reload_themes()  # restore the registry from the repo library


def _cell_center(sheet: Image.Image, c: int, r: int):
    return sheet.getpixel((c * T + T // 2, r * T + T // 2))


# ── fixtures are what make_fixtures.py builds ───────────────────────────────


def test_fixture_pngs_match_generator():
    for name, fn in (("grid_theme", FIXMOD.grid_theme), ("texture", FIXMOD.texture), ("sprite", FIXMOD.sprite)):
        on_disk = Image.open(FIX / f"{name}.png").convert("RGB")
        assert pixels(on_disk) == pixels(fn()), name


# ── pixel scale ─────────────────────────────────────────────────────────────


def test_estimate_pixel_scale_and_logical():
    img = Image.open(FIX / "texture.png")
    scale, ox, oy = pipeline.estimate_pixel_scale(img, max_scale=32)
    assert (scale, ox, oy) == (8, 0, 0)
    logical = pipeline.to_logical(img, scale, ox, oy)
    assert logical.size == (64, 64)


def test_estimate_pixel_scale_with_offset():
    img = Image.open(FIX / "grid_theme.png")
    scale, ox, oy = pipeline.estimate_pixel_scale(img, max_scale=12)
    assert scale == 12 and ox == 32 % 12 and oy == 224 % 12


# ── grid path ───────────────────────────────────────────────────────────────


def test_grid_image_becomes_exact_sheet_with_expected_colours():
    sheet, quality = pipeline.process_theme_image((FIX / "grid_theme.png").read_bytes())
    assert sheet.size == (80, 64)
    assert quality["path"] == "grid"
    assert quality["pixel_scale"] == 12
    for (c, r), col in FIXMOD.GRID_COLORS.items():
        tile = sheet.crop((c * T, r * T, (c + 1) * T, (r + 1) * T))
        assert set(pixels(tile)) == {col + (255,)}, (c, r)
    center = FIXMOD.GRID_COLORS[(1, 1)] + (255,)
    for c, r in derive.CENTER_VARIANT_CELLS:
        assert _cell_center(sheet, c, r) == center
    for c, r in ((4, 2), (3, 3), (4, 3)):
        assert _cell_center(sheet, c, r)[3] == 0


def test_grid_background_inside_open_role_becomes_transparent():
    logical = Image.new("RGB", (80, 48), (255, 0, 255))
    for (c, r), col in FIXMOD.GRID_COLORS.items():
        logical.paste(col, (c * T, r * T, (c + 1) * T, (r + 1) * T))
    # cap_left (3,0): leave its top-left quarter magenta; center (1,1): a magenta pixel inside
    logical.paste((255, 0, 255), (3 * T, 0, 3 * T + 6, 6))
    logical.putpixel((T + 8, T + 8), (255, 0, 255))
    big = logical.resize((800, 480), Image.NEAREST)
    canvas = Image.new("RGB", (1000, 700), (255, 0, 255))
    canvas.paste(big, (100, 110))
    sheet, q = pipeline.process_theme_image(canvas)
    assert q["path"] == "grid"
    assert sheet.getpixel((3 * T + 1, 1))[3] == 0  # open-side background removed
    assert sheet.getpixel((T + 8, T + 8))[3] == 255  # CENTER is never cleared


# ── fallback path ───────────────────────────────────────────────────────────


def test_non_grid_image_takes_fallback_path():
    sheet, quality = pipeline.process_theme_image(FIX / "texture.png")
    assert quality["path"] == "fallback"
    assert sheet.size == (80, 64)
    # derived roles: transparent outer corners on caps/single, opaque CENTER
    assert sheet.getpixel((4 * T, T))[3] == 0  # SINGLE top-left corner pixel
    assert all(p[3] == 255 for p in pixels(sheet.crop((T, T, 2 * T, 2 * T))))
    hazard = sheet.crop((3 * T, 2 * T, 4 * T, 3 * T))
    assert any(p[3] == 0 for p in pixels(hazard)) and any(p[3] == 255 for p in pixels(hazard))
    assert not pipeline.is_grid_layout(Image.open(FIX / "texture.png").convert("RGBA"), None)


def test_derive_slice_is_inverse_of_make_sheet():
    sheet, _ = pipeline.process_theme_image(FIX / "texture.png")
    roles = derive.slice_sheet(sheet)
    hazard = roles.pop("hazard_tile")
    rebuilt = derive.make_sheet(roles, hazard, derive.fill_variants(roles["center"]))
    assert pixels(rebuilt) == pixels(sheet)


# ── quantiser ───────────────────────────────────────────────────────────────


def _noise(size=(80, 64), seed=1):
    rnd = random.Random(seed)
    img = Image.new("RGBA", size)
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256), 255 if rnd.random() > 0.1 else 0)
                 for _ in range(size[0] * size[1])])
    return img


def test_quantize_limits_colours_and_is_deterministic():
    img = _noise()
    a = pipeline.quantize(img)
    b = pipeline.quantize(img.copy())
    assert len(pipeline.opaque_colors(a)) <= 16
    assert pipeline.png_bytes(a) == pipeline.png_bytes(b)
    # alpha is binary and transparent pixels are zeroed
    assert {p[3] for p in pixels(a)} <= {0, 255}
    assert all(p == (0, 0, 0, 0) for p in pixels(a) if p[3] == 0)


def test_process_theme_is_byte_identical_across_runs():
    raw = (FIX / "texture.png").read_bytes()
    s1, q1 = pipeline.process_theme_image(raw)
    s2, q2 = pipeline.process_theme_image(raw)
    assert pipeline.png_bytes(s1) == pipeline.png_bytes(s2) and q1 == q2
    assert q1["colors"] <= 16


# ── seams ───────────────────────────────────────────────────────────────────


def _gradient_tile():
    img = Image.new("RGBA", (T, T))
    img.putdata([(x * 15, 40, 255 - x * 15, 255) for y in range(T) for x in range(T)])
    return img


def test_seam_check_flags_and_repair_lowers_score():
    tile = _gradient_tile()
    before = pipeline.seam_score(tile, "both")
    assert before > pipeline.SEAM_THRESHOLD
    fixed, method = pipeline.repair_seams(tile, "both")
    assert method in ("blend", "mirror")
    assert pipeline.seam_score(fixed, "both") < before
    assert pipeline.seam_score(fixed, "both") <= pipeline.SEAM_THRESHOLD


def test_seamless_tile_is_left_alone():
    tile = Image.new("RGBA", (T, T), (90, 90, 90, 255))
    fixed, method = pipeline.repair_seams(tile, "both")
    assert method == "none" and fixed is tile


def test_seamed_center_in_theme_is_repaired_and_recorded():
    logical = Image.new("RGB", (80, 48), (255, 0, 255))
    for (c, r), col in FIXMOD.GRID_COLORS.items():
        logical.paste(col, (c * T, r * T, (c + 1) * T, (r + 1) * T))
    logical.paste(_gradient_tile().convert("RGB"), (T, T))  # CENTER with a hard wrap seam
    big = logical.resize((800, 480), Image.NEAREST)
    canvas = Image.new("RGB", (1000, 700), (255, 0, 255))
    canvas.paste(big, (100, 110))
    sheet, q = pipeline.process_theme_image(canvas)
    assert q["path"] == "grid"
    assert q["seam_center_raw"] > pipeline.SEAM_THRESHOLD
    assert q["seam_repair"]["center"] in ("blend", "mirror")
    assert q["seam_center"] < q["seam_center_raw"]
    assert q["seam_repair"]["fill"] == "none"


# ── background removal ──────────────────────────────────────────────────────


def test_clear_background_only_from_open_sides():
    tile = Image.new("RGBA", (T, T), (255, 0, 255, 255))
    tile.paste((10, 120, 10, 255), (0, 8, T, T))  # solid bottom half
    north_only = pipeline.clear_background(tile, (255, 0, 255), (1, 0, 0, 0))
    assert north_only.getpixel((5, 2))[3] == 0 and north_only.getpixel((5, 12))[3] == 255
    closed = pipeline.clear_background(tile, (255, 0, 255), (0, 0, 0, 0))
    assert closed.getpixel((5, 2))[3] == 255


def test_sprite_background_removal():
    sprite, quality = pipeline.process_sprite_image((FIX / "sprite.png").read_bytes())
    assert sprite.size == (16, 16)
    assert quality["pixel_scale"] == 24
    for xy in ((0, 0), (15, 0), (0, 15), (15, 15)):
        assert sprite.getpixel(xy)[3] == 0
    assert sprite.getpixel((8, 7)) == (80, 200, 80, 255)
    assert (255, 0, 255) not in pipeline.opaque_colors(sprite)
    assert len(pipeline.opaque_colors(sprite)) <= 16


def test_large_sprite_is_downscaled():
    big = Image.new("RGB", (400, 400), (255, 0, 255))
    big.paste((200, 30, 30), (50, 50, 350, 350))
    sprite, _ = pipeline.process_sprite_image(big, (16, 16))
    assert sprite.size == (16, 16)
    assert sprite.getpixel((8, 8)) == (200, 30, 30, 255)


# ── prompts / helpers ───────────────────────────────────────────────────────


def test_prompt_and_layout_guide():
    p = pipeline.build_theme_prompt("crystal cavern with glowing purple moss")
    assert "crystal cavern" in p and "5 columns by 3 rows" in p and "tile sheet" in p
    for words in ("top-left outer corner", "seamless interior fill", "hazard"):
        assert words in p
    guide = pipeline.layout_guide(scale=4)
    assert guide.size == (320, 192)
    assert guide.getpixel((4 * T * 4 + 8, 2 * T * 4 + 8))[:3] == pipeline.CHROMA  # empty cell


def test_keywords_and_slug():
    kws = pipeline.keywords_from("A crystal cavern with glowing purple moss", ["Cave"])
    assert kws == ["crystal", "cavern", "glowing", "purple", "moss", "cave"]
    assert pipeline.slugify("crystal cavern with glowing moss", ["crystal_cavern_glowing"]) == "crystal_cavern_glowing_2"


# ── library integration ─────────────────────────────────────────────────────


def test_generate_theme_saves_into_library_and_detect_theme_finds_it(lib_copy):
    from aesthetic.themes import THEMES, detect_theme

    before = (DEFAULT_ROOT / "manifest.json").read_bytes()
    provider = FakeImageProvider([FIX / "grid_theme.png"])
    desc = "crystal cavern with glowing purple moss"
    entry = pipeline.generate_theme(desc, None, provider, root=lib_copy)

    assert entry.id == "crystal_cavern_glowing"
    assert entry.generated and entry.source == "fake-image"
    assert entry.quality["path"] == "grid"
    assert entry.variants == {"center": [(0, 3), (1, 3), (2, 3)]}
    assert provider.calls and provider.calls[0]["has_reference"]
    assert "tile sheet" in provider.calls[0]["prompt"]

    lib = load_library(lib_copy)
    assert Image.open(lib.theme_sheet_path(entry)).size == (80, 64)
    raw = json.loads((lib_copy / "manifest.json").read_text())
    assert Manifest.model_validate(raw).themes[-1].quality["path"] == "grid"
    assert any(s["id"] == "fake-image" for s in raw["sources"])

    assert detect_theme(desc).identifier == entry.id
    assert entry.id in THEMES
    assert (DEFAULT_ROOT / "manifest.json").read_bytes() == before  # repo library untouched


def test_generate_sprite_saves_and_find_sprite_finds_it(lib_copy):
    provider = FakeImageProvider([FIX / "sprite.png"])
    entry = pipeline.generate_sprite("green slime blob", "Enemy", None, provider, root=lib_copy)
    assert entry.id == "enemy_green_slime" and entry.generated
    assert entry.size == (16, 16) and entry.quality["colors"] <= 16
    lib = load_library(lib_copy)
    assert find_sprite("Enemy", ["a slime"], library=lib).id == entry.id
    assert "kind" not in provider.calls[0]  # only prompt/size/reference recorded
    assert "green slime blob" in provider.calls[0]["prompt"]


def test_manifest_without_quality_round_trips_unchanged():
    raw_text = (DEFAULT_ROOT / "manifest.json").read_text()
    m = Manifest.model_validate(json.loads(raw_text))
    assert all(t.quality is None for t in m.themes)
    assert m.model_dump_json(indent=2) + "\n" == raw_text
    assert "quality" not in raw_text
