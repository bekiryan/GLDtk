"""Build the synthetic images used by the art tests and the fake image provider.

Run from the repo root:  python tests/fixtures/art/make_fixtures.py

* grid_theme.png   1024 x 1024: a "grid-respecting" 5 x 3 role sheet (one flat
                   colour per cell) drawn at 12 px per art pixel on magenta.
* texture.png      512 x 512: a non-grid noise texture at 8 px per art pixel.
* sprite.png       512 x 512: a 16 x 16 sprite at 24 px per art pixel on magenta.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
MAGENTA = (255, 0, 255)

# (col, row) -> colour; cell (4, 2) is left as background.
GRID_COLORS = {
    (0, 0): (200, 40, 40), (1, 0): (40, 200, 40), (2, 0): (40, 40, 200),
    (3, 0): (200, 200, 40), (4, 0): (40, 200, 200),
    (0, 1): (120, 60, 20), (1, 1): (90, 90, 90), (2, 1): (20, 60, 120),
    (3, 1): (160, 110, 60), (4, 1): (230, 230, 230),
    (0, 2): (60, 20, 120), (1, 2): (20, 120, 60), (2, 2): (120, 20, 60),
    (3, 2): (250, 140, 0),
}


def grid_theme() -> Image.Image:
    logical = Image.new("RGB", (80, 48), MAGENTA)
    for (c, r), col in GRID_COLORS.items():
        logical.paste(col, (c * 16, r * 16, (c + 1) * 16, (r + 1) * 16))
    big = logical.resize((960, 576), Image.NEAREST)
    out = Image.new("RGB", (1024, 1024), MAGENTA)
    out.paste(big, (32, 224))
    return out


def texture() -> Image.Image:
    rnd = random.Random(7)
    palette = [(70, 50, 90), (95, 70, 120), (120, 95, 150), (55, 40, 70)]
    logical = Image.new("RGB", (64, 64))
    px = logical.load()
    for y in range(64):
        for x in range(64):
            px[x, y] = palette[rnd.randrange(len(palette))]
    return logical.resize((512, 512), Image.NEAREST)


SPRITE_ROWS = [
    "................",
    ".....######.....",
    "....#oooooo#....",
    "...#oooooooo#...",
    "..#oo##oo##oo#..",
    "..#oo##oo##oo#..",
    "..#oooooooooo#..",
    "..#oooooooooo#..",
    "..#oo#oooo#oo#..",
    "..#ooo####ooo#..",
    "...#oooooooo#...",
    "....#oooooo#....",
    ".....######.....",
    "......#..#......",
    ".....##..##.....",
    "................",
]


def sprite() -> Image.Image:
    colors = {".": MAGENTA, "#": (20, 60, 20), "o": (80, 200, 80)}
    logical = Image.new("RGB", (16, 16))
    px = logical.load()
    for y, row in enumerate(SPRITE_ROWS):
        for x, ch in enumerate(row):
            px[x, y] = colors[ch]
    out = Image.new("RGB", (512, 512), MAGENTA)
    out.paste(logical.resize((384, 384), Image.NEAREST), (64, 64))
    return out


def main() -> None:
    grid_theme().save(HERE / "grid_theme.png", optimize=True)
    texture().save(HERE / "texture.png", optimize=True)
    sprite().save(HERE / "sprite.png", optimize=True)


if __name__ == "__main__":
    main()
