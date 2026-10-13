"""Role-tile derivation shared by ``scripts/build_assets.py`` and ``art.pipeline``.

A theme sheet is 5 x 4 tiles of 16 px (80 x 64)::

    row 0: CORNER_TL  EDGE_T  CORNER_TR  CAP_LEFT   CAP_RIGHT
    row 1: EDGE_L     CENTER  EDGE_R     FILL       SINGLE
    row 2: CORNER_BL  EDGE_B  CORNER_BR  HAZARD     -
    row 3: three extra CENTER variants (cols 0-2)

When a source has no bordered block, the 13 solid roles are derived from one
seamless texture tile: the texture is the fill and a border (1 px outline,
optional top band such as grass, snow or lava) is drawn on the open sides of
each role (``roles_synth``).

The functions here must stay byte-for-byte compatible with what
``build_assets.py`` produced before they were factored out.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from . import require_pillow

Image = require_pillow()

T = 16  # tile size in px

Color = Tuple[int, int, int]

# role -> (north_open, south_open, east_open, west_open)
OPEN: Dict[str, Tuple[int, int, int, int]] = {
    "corner_tl": (1, 0, 0, 1), "edge_t": (1, 0, 0, 0), "corner_tr": (1, 0, 1, 0),
    "cap_left": (1, 1, 0, 1), "cap_right": (1, 1, 1, 0),
    "edge_l": (0, 0, 0, 1), "center": (0, 0, 0, 0), "edge_r": (0, 0, 1, 0),
    "fill": (1, 1, 0, 0), "single": (1, 1, 1, 1),
    "corner_bl": (0, 1, 0, 1), "edge_b": (0, 1, 0, 0), "corner_br": (0, 1, 1, 0),
}
# role -> (col, row) in the sheet
CELL: Dict[str, Tuple[int, int]] = {
    "corner_tl": (0, 0), "edge_t": (1, 0), "corner_tr": (2, 0), "cap_left": (3, 0), "cap_right": (4, 0),
    "edge_l": (0, 1), "center": (1, 1), "edge_r": (2, 1), "fill": (3, 1), "single": (4, 1),
    "corner_bl": (0, 2), "edge_b": (1, 2), "corner_br": (2, 2), "hazard_tile": (3, 2),
}
# Manifest ``variants`` entry for the row-3 CENTER variants.
CENTER_VARIANT_CELLS: List[Tuple[int, int]] = [(0, 3), (1, 3), (2, 3)]
SHEET_COLS, SHEET_ROWS = 5, 4


# ── colour helpers ───────────────────────────────────────────────────────────


def tint(img: "Image.Image", dark, light) -> "Image.Image":
    """Map luminance of *img* onto a dark->light colour ramp (alpha kept)."""
    out = img.copy()
    px = out.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            lum = (0.3 * r + 0.59 * g + 0.11 * b) / 255
            px[x, y] = tuple(int(dark[i] + (light[i] - dark[i]) * lum) for i in range(3)) + (a,)
    return out


def recolor(img: "Image.Image", color) -> "Image.Image":
    """Silhouette: every non-transparent pixel becomes *color* (shaded by luminance)."""
    out = img.copy()
    px = out.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            k = 0.75 + 0.25 * ((0.3 * r + 0.59 * g + 0.11 * b) / 255)
            px[x, y] = tuple(min(255, int(c * k)) for c in color) + (255,)
    return out


# ── block renderers ──────────────────────────────────────────────────────────


def render_synth(fill: "Image.Image", openness, outline, top=None, hi=None) -> "Image.Image":
    """Draw the border of one role (open sides from *openness*) over *fill*."""
    n, s, e, w = openness
    im = fill.copy()
    px = im.load()
    if n and top:
        for y, col in enumerate(top):  # top band rows 1..len(top)
            for x in range(T):
                px[x, y + 1] = col + (255,)
        drip = top[-1]
        for x in range(T):  # ragged lower edge
            if (x * 5 + 3) % 7 < 3:
                px[x, len(top) + 1] = drip + (255,)
    elif n and hi:
        for x in range(T):
            px[x, 1] = hi + (255,)
    for x in range(T):
        if n:
            px[x, 0] = outline + (255,)
        if s:
            px[x, T - 1] = outline + (255,)
    for y in range(T):
        if w:
            px[0, y] = outline + (255,)
        if e:
            px[T - 1, y] = outline + (255,)
    for (a, b, cx, cy) in ((n, w, 0, 0), (n, e, T - 1, 0), (s, w, 0, T - 1), (s, e, T - 1, T - 1)):
        if a and b:
            px[cx, cy] = (0, 0, 0, 0)
    return im


def half(top_img: "Image.Image", bottom_img: "Image.Image") -> "Image.Image":
    """Top half of one tile over the bottom half of another."""
    out = Image.new("RGBA", (T, T))
    out.paste(top_img.crop((0, 0, T, 8)), (0, 0))
    out.paste(bottom_img.crop((0, 8, T, T)), (0, 8))
    return out


def single_from_corners(tl, tr, bl, br) -> "Image.Image":
    out = Image.new("RGBA", (T, T))
    out.paste(tl.crop((0, 0, 8, 8)), (0, 0))
    out.paste(tr.crop((8, 0, T, 8)), (8, 0))
    out.paste(bl.crop((0, 8, 8, T)), (0, 8))
    out.paste(br.crop((8, 8, T, T)), (8, 8))
    return out


def fill_variants(fill: "Image.Image") -> list:
    """Three CENTER variants: mirrored / flipped / rotated copies of the fill."""
    return [
        fill.transpose(Image.FLIP_LEFT_RIGHT),
        fill.transpose(Image.FLIP_TOP_BOTTOM),
        fill.transpose(Image.ROTATE_180),
    ]


def roles_from_3x3(nine: Dict[str, "Image.Image"]) -> Dict[str, "Image.Image"]:
    """Complete a bordered 3x3 block (9 roles) with FILL / CAP_* / SINGLE."""
    roles = dict(nine)
    roles["fill"] = half(roles["edge_t"], roles["edge_b"])
    roles["cap_left"] = half(roles["corner_tl"], roles["corner_bl"])
    roles["cap_right"] = half(roles["corner_tr"], roles["corner_br"])
    roles["single"] = single_from_corners(roles["corner_tl"], roles["corner_tr"], roles["corner_bl"], roles["corner_br"])
    return roles


def roles_synth(fill, outline, top=None, hi=None) -> dict:
    """All 13 solid roles from one texture tile (see module docstring)."""
    return {role: render_synth(fill, o, outline, top, hi) for role, o in OPEN.items()}


def make_sheet(roles: dict, hazard: "Image.Image", variants: list) -> "Image.Image":
    """Assemble the 5 x 4 sheet from the roles, the hazard tile and CENTER variants."""
    sheet = Image.new("RGBA", (5 * T, 4 * T), (0, 0, 0, 0))
    for role, (c, r) in CELL.items():
        img = hazard if role == "hazard_tile" else roles[role]
        sheet.paste(img, (c * T, r * T))
    for i, v in enumerate(variants):
        sheet.paste(v, (i * T, 3 * T))
    return sheet


def slice_sheet(sheet: "Image.Image") -> Dict[str, "Image.Image"]:
    """Inverse of ``make_sheet`` for the 14 roles (variants not included)."""
    return {
        role: sheet.crop((c * T, r * T, (c + 1) * T, (r + 1) * T)) for role, (c, r) in CELL.items()
    }


# ── procedural pieces for generated themes ──────────────────────────────────


def spike_tile(color: Color, outline: Optional[Color] = None) -> "Image.Image":
    """A transparent 16 px tile with three spikes (hazard), drawn procedurally."""
    out = Image.new("RGBA", (T, T), (0, 0, 0, 0))
    px = out.load()
    dark = outline or tuple(max(0, int(c * 0.45)) for c in color)
    light = tuple(min(255, int(c + (255 - c) * 0.5)) for c in color)
    for base in (0, 5, 10):  # three 6 px wide spikes, 1 px overlap
        for y in range(4, T):
            half_w = (y - 4) * 3 // (T - 4)  # 0..2
            for x in range(base + 2 - half_w, base + 3 + half_w + 1):
                if 0 <= x < T:
                    px[x, y] = color + (255,)
            if 0 <= base + 2 - half_w < T:
                px[base + 2 - half_w, y] = dark + (255,)
        px[base + 2, 4] = light + (255,)
    for x in range(T):
        px[x, T - 1] = dark + (255,)
    return out


def shade(color: Sequence[int], k: float) -> Color:
    """Scale a colour towards black (k < 1) or white (k > 1)."""
    if k <= 1:
        return tuple(max(0, min(255, int(c * k))) for c in color[:3])  # type: ignore[return-value]
    t = min(1.0, k - 1)
    return tuple(int(c + (255 - c) * t) for c in color[:3])  # type: ignore[return-value]
