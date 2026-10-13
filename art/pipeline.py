"""Post-processing of generated images into library themes and sprites.

``generate_theme``: description -> image prompt -> provider -> 16 px theme sheet
(80 x 64, the library's 5 x 4 layout) -> ``add_theme(generated=True)``.

``generate_sprite``: description -> image prompt -> provider -> background
removed, 16 x 16 sprite -> ``add_sprite(generated=True)``.

Every step is a deterministic function on ``PIL.Image`` (no randomness, no
dithering) so tests can drive them one at a time:

    estimate_pixel_scale -> to_logical -> detect_background / content_bbox
    -> is_grid_layout ? grid_sheet : texture_sheet (fallback, via art.derive)
    -> seam_score / repair_seams (CENTER both ways, FILL horizontally)
    -> assemble_sheet -> quantize (<= 16 colours) -> clear_background
"""

from __future__ import annotations

import io
import re
from collections import Counter, deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from . import require_pillow
from . import derive
from .derive import CELL, CENTER_VARIANT_CELLS, OPEN, T

Image = require_pillow()

Color = Tuple[int, int, int]
PathLike = Union[str, Path]

SHEET_W, SHEET_H = 5 * T, 3 * T  # role grid the model is asked for (80 x 48)
MAX_COLORS = 16
SEAM_THRESHOLD = 2.0  # seam_score above this gets repaired
CHANGE_THRESHOLD = 60  # summed RGB difference that counts as a colour change
BG_TOLERANCE = 60  # summed RGB difference to the background colour
CHROMA = (255, 0, 255)

ROLE_DESCRIPTIONS = [
    ["top-left outer corner", "top edge (walkable surface)", "top-right outer corner",
     "left end of a one-tile-thick floating platform", "right end of that floating platform"],
    ["left edge", "seamless interior fill (tiles seamlessly in every direction)", "right edge",
     "middle of the one-tile-thick platform (tiles seamlessly left-right)",
     "a single isolated one-tile block"],
    ["bottom-left outer corner", "bottom edge", "bottom-right outer corner",
     "a hazard tile such as spikes, on the magenta background", "empty: plain magenta"],
]

_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "with", "in", "on", "at", "to", "for", "from", "by",
    "into", "is", "are", "be", "this", "that", "its", "it", "as", "some", "very", "lots", "full",
    "level", "theme", "style", "tiles", "tile", "tileset", "pixel", "art", "like", "made",
}


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────


def load_image(png: Union[bytes, PathLike, "Image.Image"]) -> "Image.Image":
    if isinstance(png, Image.Image):
        return png.convert("RGBA")
    if isinstance(png, (bytes, bytearray)):
        return Image.open(io.BytesIO(png)).convert("RGBA")
    return Image.open(png).convert("RGBA")


def png_bytes(img: "Image.Image") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def pixels(img: "Image.Image") -> List[Any]:
    """Pixel values in row order (``get_flattened_data`` on Pillow >= 12)."""
    getter = getattr(img, "get_flattened_data", None) or img.getdata
    return list(getter())


def _dist(a: Sequence[int], b: Sequence[int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def opaque_colors(img: "Image.Image") -> set:
    return {p[:3] for p in pixels(img.convert("RGBA")) if p[3] > 0}


def keywords_from(description: str, extra: Optional[Iterable[str]] = None) -> List[str]:
    """Lower-case content words of *description* plus *extra*, order kept, unique."""
    out: List[str] = []
    for w in re.findall(r"[a-z]+", " ".join([description, *(extra or [])]).lower()):
        if len(w) >= 3 and w not in _STOPWORDS and w not in out:
            out.append(w)
    return out


def slugify(description: str, existing: Iterable[str] = (), max_words: int = 3) -> str:
    words = keywords_from(description)[:max_words] or ["generated"]
    base = "_".join(words)
    taken = set(existing)
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1
    return slug


def _hex(c: Sequence[int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*c[:3])


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────


def build_theme_prompt(description: str, seed: int = 0) -> str:
    rows = "\n".join(
        f"Row {i + 1}: " + " | ".join(cells) for i, cells in enumerate(ROLE_DESCRIPTIONS)
    )
    variation = f"\nVariation #{seed}." if seed else ""
    return (
        "Create a pixel-art tile sheet for a 2D side-scrolling platformer game.\n"
        f"Theme: {description}.\n"
        "Layout: exactly 5 columns by 3 rows of square tiles. Each tile is 16x16 art pixels, "
        "drawn as big crisp square pixels (no anti-aliasing, no blur, no gradients). Tiles touch "
        "edge to edge with no gaps, no grid lines, no labels and no text. The tile sheet is the "
        "only thing in the picture, centred on a flat, solid, pure magenta (#FF00FF) background. "
        "Use a limited palette of at most 16 colours and never use magenta inside the tiles.\n"
        "All cells are pieces of one solid ground/platform material. The open sides of edge and "
        "corner tiles show the outer surface (for example grass, snow, crust or a highlight); the "
        "interior fill must tile seamlessly.\n"
        f"{rows}\n"
        "The attached image is a layout guide: grey blocks with dark outlines on their open sides. "
        "Follow its arrangement exactly but redraw every tile in the theme."
        f"{variation}"
    )


def build_sprite_prompt(description: str, kind: str) -> str:
    return (
        f"A single pixel-art game sprite for a 2D side-scrolling platformer: {description} "
        f"(entity type: {kind}). 16x16 art pixels drawn as big crisp square pixels, no "
        "anti-aliasing, facing right, centred and filling most of the frame, on a flat, solid, "
        "pure magenta (#FF00FF) background. No shadow, no text, no border, limited palette, never "
        "magenta inside the sprite."
    )


def layout_guide(scale: int = 12) -> "Image.Image":
    """Reference image for the model: the 5 x 3 role layout as grey blocks."""
    grey = Image.new("RGBA", (T, T), (150, 150, 150, 255))
    roles = derive.roles_synth(grey, (40, 40, 40), top=[(220, 220, 220), (190, 190, 190)])
    hazard = derive.spike_tile((200, 200, 200))
    sheet = Image.new("RGBA", (SHEET_W, SHEET_H), CHROMA + (255,))
    for role, (c, r) in CELL.items():
        img = hazard if role == "hazard_tile" else roles[role]
        sheet.alpha_composite(img, (c * T, r * T))
    return sheet.resize((SHEET_W * scale, SHEET_H * scale), Image.NEAREST)


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: pixel scale, logical image, background
# ─────────────────────────────────────────────────────────────────────────────


def _change_positions(img: "Image.Image", samples: int = 64) -> Tuple[List[int], List[int]]:
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    xs: List[int] = []
    ys: List[int] = []
    for y in sorted({min(h - 1, (i * h) // samples + h // (2 * samples)) for i in range(samples)}):
        prev = px[0, y]
        for x in range(1, w):
            cur = px[x, y]
            if _dist(prev, cur) > CHANGE_THRESHOLD:
                xs.append(x)
            prev = cur
    for x in sorted({min(w - 1, (i * w) // samples + w // (2 * samples)) for i in range(samples)}):
        prev = px[x, 0]
        for y in range(1, h):
            cur = px[x, y]
            if _dist(prev, cur) > CHANGE_THRESHOLD:
                ys.append(y)
            prev = cur
    return xs, ys


def _best_offset(positions: List[int], s: int) -> Tuple[int, int]:
    """(hits, offset) maximising positions on the grid ``offset + k*s`` (±1 for s >= 8)."""
    counts = Counter(p % s for p in positions)
    best = (0, 0, 0)  # (hits, exact hits, offset)
    for o in range(s):
        hits = counts[o]
        if s >= 8:
            hits += counts[(o + 1) % s] + counts[(o - 1) % s]
        if (hits, counts[o]) > best[:2]:
            best = (hits, counts[o], o)
    return best[0], best[2]


def estimate_pixel_scale(
    img: "Image.Image", max_scale: Optional[int] = None, min_fraction: float = 0.8
) -> Tuple[int, int, int]:
    """Estimate the size of one art pixel in image pixels: ``(scale, off_x, off_y)``.

    Colour-change positions along sampled rows and columns are collected; the
    scale is the largest ``s`` for which at least *min_fraction* of them fall on
    a grid of step ``s`` (each axis with its own offset). Returns ``(1, 0, 0)``
    when no consistent grid is found.
    """
    w, h = img.size
    max_scale = max(1, min(max_scale or 64, w, h))
    xs, ys = _change_positions(img)
    n = len(xs) + len(ys)
    if n < 8:
        return 1, 0, 0
    for s in range(max_scale, 1, -1):
        hx, ox = _best_offset(xs, s) if xs else (0, 0)
        hy, oy = _best_offset(ys, s) if ys else (0, 0)
        if (hx + hy) / n >= min_fraction:
            return s, ox, oy
    return 1, 0, 0


def to_logical(img: "Image.Image", scale: int, off_x: int = 0, off_y: int = 0) -> "Image.Image":
    """Sample one pixel per ``scale`` block (nearest neighbour at block centres)."""
    if scale <= 1:
        return img.convert("RGBA")
    w, h = img.size
    nx, ny = (w - off_x) // scale, (h - off_y) // scale
    box = (off_x, off_y, off_x + nx * scale, off_y + ny * scale)
    return img.convert("RGBA").resize((nx, ny), Image.NEAREST, box=box)


def detect_background(img: "Image.Image", min_share: float = 0.6) -> Optional[Color]:
    """The flat background colour, if most border pixels share one colour."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    border = [px[x, y] for x in range(w) for y in (0, h - 1)] + [
        px[x, y] for y in range(1, h - 1) for x in (0, w - 1)
    ]
    buckets = Counter((r // 16, g // 16, b // 16) for r, g, b in border)
    (bucket, _), = buckets.most_common(1)
    members = [p for p in border if (p[0] // 16, p[1] // 16, p[2] // 16) == bucket]
    mean = tuple(sum(p[i] for p in members) // len(members) for i in range(3))
    share = sum(1 for p in border if _dist(p, mean) <= BG_TOLERANCE) / len(border)
    return mean if share >= min_share else None  # type: ignore[return-value]


def content_bbox(img: "Image.Image", bg: Optional[Color]) -> Tuple[int, int, int, int]:
    """Bounding box of the pixels that are neither transparent nor background."""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    px = rgba.load()
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            p = px[x, y]
            if p[3] < 128 or (bg is not None and _dist(p, bg) <= BG_TOLERANCE):
                continue
            xs.append(x)
            ys.append(y)
    if not xs:
        return 0, 0, w, h
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


# ─────────────────────────────────────────────────────────────────────────────
# Step 3a: grid path / 3b: single-texture fallback
# ─────────────────────────────────────────────────────────────────────────────


def _resize_to(img: "Image.Image", size: Tuple[int, int]) -> "Image.Image":
    if img.size == size:
        return img.copy()
    close = abs(img.width - size[0]) <= 2 and abs(img.height - size[1]) <= 2
    return img.resize(size, Image.NEAREST if close else Image.BOX)


def _coverage(img: "Image.Image", bg: Optional[Color]) -> float:
    px = pixels(img.convert("RGBA"))
    solid = sum(1 for p in px if p[3] >= 128 and (bg is None or _dist(p, bg) > BG_TOLERANCE))
    return solid / len(px)


def is_grid_layout(logical: "Image.Image", bg: Optional[Color]) -> bool:
    """Did the model draw the 5 x 3 role grid? (aspect ~5:3, role cells filled)."""
    x0, y0, x1, y1 = content_bbox(logical, bg)
    aspect = (x1 - x0) / max(1, y1 - y0)
    if not (5 / 3 * 0.8 <= aspect <= 5 / 3 * 1.25) or (x1 - x0) < SHEET_W // 2:
        return False
    grid = _resize_to(logical.crop((x0, y0, x1, y1)), (SHEET_W, SHEET_H))
    for role, (c, r) in CELL.items():
        cell = grid.crop((c * T, r * T, (c + 1) * T, (r + 1) * T))
        need = 0.1 if role == "hazard_tile" else 0.5
        if _coverage(cell, bg) < need:
            return False
    return True


def grid_sheet(logical: "Image.Image", bg: Optional[Color]) -> "Image.Image":
    """Crop the drawn grid and resample it to exactly 80 x 48."""
    x0, y0, x1, y1 = content_bbox(logical, bg)
    return _resize_to(logical.crop((x0, y0, x1, y1)), (SHEET_W, SHEET_H)).convert("RGBA")


def most_tile_like(logical: "Image.Image", bg: Optional[Color], stride: int = 4) -> "Image.Image":
    """The 16 x 16 window that tiles best with itself (lowest wrap seam score).

    Large images are first box-downscaled so the short side is 64 px. Windows
    containing background are skipped (unless every window does); flat windows
    are penalised. Ties keep the first window in scan order.
    """
    img = logical.convert("RGBA")
    short = min(img.size)
    if short > 64:
        k = 64 / short
        img = img.resize((max(T, round(img.width * k)), max(T, round(img.height * k))), Image.BOX)
    if min(img.size) < T:
        img = img.resize((max(T, img.width), max(T, img.height)), Image.NEAREST)
    best = None
    for allow_bg in (False, True):
        for y in range(0, img.height - T + 1, stride):
            for x in range(0, img.width - T + 1, stride):
                win = img.crop((x, y, x + T, y + T))
                if not allow_bg and bg is not None and _coverage(win, bg) < 1.0:
                    continue
                score = seam_score(win) + (10.0 if _contrast(win) < 6 else 0.0)
                if best is None or score < best[0]:
                    best = (score, win)
        if best is not None:
            break
    win = best[1]  # type: ignore[index]
    flat = Image.new("RGBA", (T, T))
    flat.paste(win.convert("RGB"), (0, 0))  # fully opaque
    return flat


def _contrast(tile: "Image.Image") -> float:
    px = [p[:3] for p in pixels(tile.convert("RGBA"))]
    mean = [sum(p[i] for p in px) / len(px) for i in range(3)]
    return sum(_dist(p, mean) for p in px) / (3 * len(px))


def texture_sheet(texture: "Image.Image") -> Tuple[Dict[str, "Image.Image"], "Image.Image"]:
    """Fallback: the 13 solid roles + hazard from one texture (Part 5's synth approach)."""
    px = [p[:3] for p in pixels(texture.convert("RGBA"))]
    mean = tuple(sum(p[i] for p in px) // len(px) for i in range(3))
    outline = derive.shade(mean, 0.35)
    hi = derive.shade(mean, 1.45)
    roles = derive.roles_synth(texture.convert("RGBA"), outline, hi=hi)
    hazard = derive.spike_tile(derive.shade(mean, 1.6), outline)
    return roles, hazard


# ─────────────────────────────────────────────────────────────────────────────
# Step 4: quantise / transparency
# ─────────────────────────────────────────────────────────────────────────────


def quantize(img: "Image.Image", colors: int = MAX_COLORS) -> "Image.Image":
    """At most *colors* opaque colours (median cut, no dithering); binary alpha.

    Transparent pixels do not take palette slots and come out as (0,0,0,0).
    """
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
    opaque = [p[:3] for p, a in zip(pixels(rgba), pixels(alpha)) if a]
    if not opaque:
        return Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    common = Counter(opaque).most_common(1)[0][0]
    rgb = Image.composite(rgba.convert("RGB"), Image.new("RGB", rgba.size, common), alpha)
    q = rgb.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    out = Image.composite(q.convert("RGB").convert("RGBA"), Image.new("RGBA", rgba.size, (0, 0, 0, 0)), alpha)
    return out


def clear_background(
    tile: "Image.Image",
    bg: Sequence[int],
    sides: Tuple[int, int, int, int] = (1, 1, 1, 1),
    tolerance: int = BG_TOLERANCE,
) -> "Image.Image":
    """Flood-fill background-coloured pixels to transparent from the open sides.

    *sides* is ``(north, south, east, west)`` like ``derive.OPEN``.
    """
    out = tile.convert("RGBA").copy()
    w, h = out.size
    px = out.load()
    n, s, e, wst = sides
    seeds = []
    if n:
        seeds += [(x, 0) for x in range(w)]
    if s:
        seeds += [(x, h - 1) for x in range(w)]
    if e:
        seeds += [(w - 1, y) for y in range(h)]
    if wst:
        seeds += [(0, y) for y in range(h)]
    seen = set()
    queue = deque(p for p in seeds if px[p][3] > 0 and _dist(px[p], bg) <= tolerance)
    while queue:
        x, y = queue.popleft()
        if (x, y) in seen:
            continue
        seen.add((x, y))
        px[x, y] = (0, 0, 0, 0)
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen:
                p = px[nx, ny]
                if p[3] > 0 and _dist(p, bg) <= tolerance:
                    queue.append((nx, ny))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Step 6: seams
# ─────────────────────────────────────────────────────────────────────────────


def _pdiff(a, b) -> float:
    return _dist(a, b) / 3.0


def seam_score(tile: "Image.Image", axes: str = "both") -> float:
    """Seam colour jump relative to the tile's own pixel-to-pixel variation.

    ``axes="h"`` checks the left/right wrap (FILL next to FILL), ``"both"``
    also top/bottom (CENTER tiled 2 x 2). About 1 for a seamless texture; a
    hard seam scores much higher.
    """
    rgb = tile.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    seam = [_pdiff(px[w - 1, y], px[0, y]) for y in range(h)]
    inner = [_pdiff(px[x, y], px[x + 1, y]) for y in range(h) for x in range(w - 1)]
    if axes == "both":
        seam += [_pdiff(px[x, h - 1], px[x, 0]) for x in range(w)]
        inner += [_pdiff(px[x, y], px[x, y + 1]) for x in range(w) for y in range(h - 1)]
    mean_seam = sum(seam) / len(seam)
    mean_inner = sum(inner) / len(inner)
    return round(mean_seam / max(mean_inner, 4.0), 4)


def _blend_edges(tile: "Image.Image", axes: str) -> "Image.Image":
    out = tile.convert("RGBA").copy()
    w, h = out.size
    px = out.load()

    def mix(a, b, t):
        return tuple(int(round(a[i] * (1 - t) + b[i] * t)) for i in range(3)) + (a[3],)

    for y in range(h):  # left/right wrap
        a, b = px[0, y], px[w - 1, y]
        avg = mix(a, b, 0.5)
        a1, b1 = px[1, y], px[w - 2, y]
        px[0, y] = avg
        px[w - 1, y] = avg
        px[1, y] = mix(a1, avg, 0.5)
        px[w - 2, y] = mix(b1, avg, 0.5)
    if axes == "both":
        for x in range(w):
            a, b = px[x, 0], px[x, h - 1]
            avg = mix(a, b, 0.5)
            a1, b1 = px[x, 1], px[x, h - 2]
            px[x, 0] = avg
            px[x, h - 1] = avg
            px[x, 1] = mix(a1, avg, 0.5)
            px[x, h - 2] = mix(b1, avg, 0.5)
    return out


def _mirror_wrap(tile: "Image.Image", axes: str) -> "Image.Image":
    out = tile.convert("RGBA").copy()
    w, h = out.size
    left = out.crop((0, 0, w // 2, h))
    out.paste(left.transpose(Image.FLIP_LEFT_RIGHT), (w - w // 2, 0))
    if axes == "both":
        top = out.crop((0, 0, w, h // 2))
        out.paste(top.transpose(Image.FLIP_TOP_BOTTOM), (0, h - h // 2))
    return out


def repair_seams(
    tile: "Image.Image", axes: str = "both", threshold: float = SEAM_THRESHOLD
) -> Tuple["Image.Image", str]:
    """Lower a tile's seam score: blend the edge columns/rows, else mirror-wrap.

    Returns ``(tile, method)`` with method ``"none"``, ``"blend"`` or ``"mirror"``.
    """
    if seam_score(tile, axes) <= threshold:
        return tile, "none"
    blended = _blend_edges(tile, axes)
    if seam_score(blended, axes) <= threshold:
        return blended, "blend"
    return _mirror_wrap(tile, axes), "mirror"


# ─────────────────────────────────────────────────────────────────────────────
# Steps 3-6 together
# ─────────────────────────────────────────────────────────────────────────────


def slice_roles(sheet80x48: "Image.Image") -> Tuple[Dict[str, "Image.Image"], "Image.Image"]:
    tiles = derive.slice_sheet(sheet80x48.convert("RGBA"))
    hazard = tiles.pop("hazard_tile")
    return tiles, hazard


def assemble_sheet(roles: Dict[str, "Image.Image"], hazard: "Image.Image") -> "Image.Image":
    """80 x 64 sheet: 14 roles + three CENTER variants (Part 5 layout)."""
    return derive.make_sheet(roles, hazard, derive.fill_variants(roles["center"]))


def process_theme_image(png: Union[bytes, PathLike, "Image.Image"]) -> Tuple["Image.Image", Dict[str, Any]]:
    """Generated image -> final 80 x 64 theme sheet plus a quality dict."""
    src = load_image(png)
    max_scale = max(1, min(src.width // SHEET_W, src.height // SHEET_H))
    scale, ox, oy = estimate_pixel_scale(src, max_scale=max_scale)
    logical = to_logical(src, scale, ox, oy)
    bg = detect_background(logical)

    if is_grid_layout(logical, bg):
        path = "grid"
        roles, hazard = slice_roles(grid_sheet(logical, bg))
    else:
        path = "fallback"
        roles, hazard = texture_sheet(most_tile_like(logical, bg))

    raw_center = seam_score(roles["center"], "both")
    raw_fill = seam_score(roles["fill"], "h")
    roles["center"], center_fix = repair_seams(roles["center"], "both")
    roles["fill"], fill_fix = repair_seams(roles["fill"], "h")

    sheet = quantize(assemble_sheet(roles, hazard))

    if path == "grid" and bg is not None:
        for role, (c, r) in CELL.items():
            sides = (1, 1, 1, 1) if role == "hazard_tile" else OPEN[role]
            if not any(sides):
                continue
            box = (c * T, r * T, (c + 1) * T, (r + 1) * T)
            sheet.paste(clear_background(sheet.crop(box), bg, sides), box[:2])

    final = derive.slice_sheet(sheet)
    quality = {
        "path": path,
        "pixel_scale": scale,
        "colors": len(opaque_colors(sheet)),
        "seam_center": seam_score(final["center"], "both"),
        "seam_fill": seam_score(final["fill"], "h"),
        "seam_center_raw": raw_center,
        "seam_fill_raw": raw_fill,
        "seam_repair": {"center": center_fix, "fill": fill_fix},
        "seam_threshold": SEAM_THRESHOLD,
    }
    return sheet, quality


def remove_background(img: "Image.Image", bg: Optional[Color] = None) -> "Image.Image":
    """Flood-fill the corner/background colour to transparent from the image border."""
    rgba = img.convert("RGBA")
    if bg is None:
        bg = detect_background(rgba, min_share=0.3) or rgba.getpixel((0, 0))[:3]
    return clear_background(rgba, bg, (1, 1, 1, 1))


def process_sprite_image(
    png: Union[bytes, PathLike, "Image.Image"], size: Tuple[int, int] = (T, T)
) -> Tuple["Image.Image", Dict[str, Any]]:
    """Generated image -> transparent sprite of *size* plus a quality dict."""
    src = load_image(png)
    scale, ox, oy = estimate_pixel_scale(src, max_scale=max(1, min(src.size) // min(size)))
    logical = to_logical(src, scale, ox, oy)
    if max(logical.size) > 128:  # keep the flood fill cheap
        k = 128 / max(logical.size)
        logical = logical.resize((max(1, round(logical.width * k)), max(1, round(logical.height * k))), Image.NEAREST)
    cut = remove_background(logical)
    alpha_box = cut.getchannel("A").getbbox() or (0, 0, cut.width, cut.height)
    subject = cut.crop(alpha_box)
    side = max(subject.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(subject, ((side - subject.width) // 2, side - subject.height))  # bottom-aligned
    tw, th = size
    if side <= min(tw, th):
        out = Image.new("RGBA", size, (0, 0, 0, 0))
        out.paste(square, ((tw - side) // 2, th - side))
    else:
        out = square.resize(size, Image.BOX)
    out = quantize(out)
    return out, {
        "pixel_scale": scale,
        "colors": len(opaque_colors(out)),
        "coverage": round(sum(1 for a in pixels(out.getchannel("A")) if a) / (tw * th), 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────


def _source_id(provider) -> str:
    """Manifest source id for a provider: ``gemini`` -> ``gemini-image``."""
    return f"{getattr(provider, 'name', 'unknown')}-image"


def generate_theme(
    description: str,
    theme_id: Optional[str],
    provider,
    *,
    keywords: Optional[List[str]] = None,
    seed: int = 0,
    display_name: Optional[str] = None,
    root: Optional[PathLike] = None,
    overwrite: bool = False,
    reload: bool = True,
    source: Optional[str] = None,
):
    """Generate, post-process and save a theme; returns its ``ThemeEntry``.

    After saving, the theme registry is reloaded (``reload=True``) so
    ``detect_theme(description)`` can pick the new theme right away.
    """
    from aesthetic.library import add_theme, load_library

    lib = load_library(root)
    theme_id = theme_id or slugify(description, (t.id for t in lib.themes))
    prompt = build_theme_prompt(description, seed)
    png = provider.generate(
        prompt, size_hint=(SHEET_W, SHEET_H), reference_png=png_bytes(layout_guide())
    )
    sheet, quality = process_theme_image(png)
    quality["seed"] = seed
    center = derive.slice_sheet(sheet)["center"]
    px = [p[:3] for p in pixels(center)]
    mean = tuple(sum(p[i] for p in px) // len(px) for i in range(3))
    entry = add_theme(
        theme_id,
        display_name or description.strip()[:40].title() or theme_id,
        png_bytes(sheet),
        keywords=keywords_from(description, keywords),
        background_color=_hex(derive.shade(mean, 0.25)),
        variants={"center": list(CENTER_VARIANT_CELLS)},
        source=source or _source_id(provider),
        generated=True,
        overwrite=overwrite,
        root=root,
        quality=quality,
    )
    if reload:
        from aesthetic.themes import reload_themes

        reload_themes(load_library(root) if root is not None else None)
    return entry


def generate_sprite(
    description: str,
    kind: str,
    sprite_id: Optional[str],
    provider,
    size: Tuple[int, int] = (T, T),
    *,
    root: Optional[PathLike] = None,
    overwrite: bool = False,
    source: Optional[str] = None,
):
    """Generate, post-process and save a sprite; returns its ``SpriteEntry``."""
    from aesthetic.library import add_sprite, load_library

    lib = load_library(root)
    sprite_id = sprite_id or slugify(f"{kind} {description}", (s.id for s in lib.sprites))
    png = provider.generate(build_sprite_prompt(description, kind), size_hint=(1, 1))
    sprite, quality = process_sprite_image(png, size)
    return add_sprite(
        sprite_id,
        kind,
        png_bytes(sprite),
        tags=keywords_from(description, [kind]),
        source=source or _source_id(provider),
        generated=True,
        overwrite=overwrite,
        root=root,
        quality=quality,
    )
