#!/usr/bin/env python3
"""Render assets/preview.png: per theme its 14 roles + a sample level, then all sprites.

Run from the repo root:  python scripts/preview_assets.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aesthetic import THEMES, TileRole, autotile_level, load_library  # noqa: E402
from layout.ir import IRLevel, TileValue  # noqa: E402

Z = 3  # zoom
T = 16


def sample_level() -> IRLevel:
    ir = IRLevel.empty("preview", 20, 8)
    for c in range(0, 20):  # ground, 2 deep
        for r in (6, 7):
            ir.grid[r][c] = TileValue.SOLID
    for c in range(3, 8):  # thin platform
        ir.grid[3][c] = TileValue.SOLID
    for c in range(11, 16):  # 3-high block
        for r in (3, 4, 5):
            ir.grid[r][c] = TileValue.SOLID
    ir.grid[5][9] = TileValue.SOLID  # single
    ir.grid[5][1] = TileValue.HAZARD
    ir.grid[5][2] = TileValue.HAZARD
    ir.grid[5][18] = TileValue.HAZARD
    return ir


def cell(sheet: Image.Image, x: int, y: int) -> Image.Image:
    return sheet.crop((x, y, x + T, y + T)).resize((T * Z, T * Z), Image.NEAREST)


def main() -> None:
    lib = load_library()
    ir = sample_level()
    row_h = 8 * T * Z + 10
    role_w = 5 * T * Z
    lvl_w = 20 * T * Z
    sprites_h = 3 * (T * Z + 8) + 10
    W = role_w + lvl_w + 30
    H = len(lib.themes) * row_h + sprites_h + 10
    out = Image.new("RGBA", (W, H), (40, 40, 48, 255))
    d = ImageDraw.Draw(out)
    y = 5
    for t in lib.themes:
        theme = THEMES[t.id]
        sheet = Image.open(lib.theme_sheet_path(t)).convert("RGBA")
        # role grid (5x4 incl. variant row) on theme bg
        d.rectangle([5, y, 5 + role_w, y + 4 * T * Z], fill=theme.background_color)
        for i in range(5 * 4):
            c, r = i % 5, i // 5
            out.alpha_composite(cell(sheet, c * T, r * T), (5 + c * T * Z, y + r * T * Z))
        d.text((8, y + 4 * T * Z + 4), f"{t.id}  uid={theme.tileset_uid}", fill="white")
        # sample level
        lx = role_w + 20
        d.rectangle([lx, y, lx + lvl_w, y + 8 * T * Z], fill=theme.background_color)
        for e in autotile_level(ir, theme):
            out.alpha_composite(cell(sheet, e.src_x, e.src_y), (lx + e.col * T * Z, y + e.row * T * Z))
        y += row_h
    # sprites
    x = 5
    for i, s in enumerate(lib.sprites):
        im = Image.open(lib.sprite_path(s)).convert("RGBA")
        im = im.resize((im.width * Z, im.height * Z), Image.NEAREST)
        px = 5 + (i % 12) * (T * Z + 6)
        py = y + (i // 12) * (T * Z + 8)
        d.rectangle([px, py, px + T * Z, py + T * Z], fill=(25, 25, 30, 255))
        out.alpha_composite(im, (px, py))
    out.convert("RGB").save(ROOT / "assets" / "preview.png", optimize=True)
    print("wrote assets/preview.png", out.size)


if __name__ == "__main__":
    main()
