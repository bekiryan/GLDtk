"""Open Surge levels (``levels/*.lev``) -> GLDtk grids.

A ``.lev`` file is line based: a header (``name``, ``author``, ``license``, ``theme``,
``spawn_point x y``), then ``brick <id> <x> <y> [layer] [flip]`` and ``entity "<name>" <x> <y> <uid>``
lines in pixels.  The brickset (``themes/*.brk``) gives each brick its ``type`` (SOLID, CLOUD =
one-way, PASSABLE = decoration), ``behavior`` and its collision mask (the alpha of the ``mask``
image, or of the sprite when there is none, at ``source_rect``).  The mask is sampled per 16 px
cell, so slopes and loops keep their shape.  Pixel scale is kept (16 px cells).

The engine is GPL-3; every level file names its own licence (CC-BY 3.0 / 4.0, CC0), which is
what decides.  Template levels with a placeholder author are skipped.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from layout.ir import IREntity, TileValue
from patterns.sources import (TILE, ConvertedLevel, SourceReport, empty_grid, fill_box,
                              settle, spdx_of, trim)

GAME = "opensurge"
URL = "https://github.com/alemart/opensurge"
SPARSE = None  # a plain shallow clone (~85 MB)

SKIP_FILES = {"empty.lev": "empty template", "sandbox.lev": "test sandbox",
              "surgescript.lev": "scripting demo, not a level"}
PLACEHOLDER_AUTHORS = {"", "your name here"}
SOLID_TYPES = {"SOLID", "CLOUD"}
SKIP_BEHAVIORS = {"PENDULAR", "MARKER", "CIRCULAR"}  # moving or invisible bricks
WINDOWS_PER_LEVEL = 80  # Sonic-style levels are several screens tall and very long
COVER = 0.35  # share of a cell's pixels that must be opaque for it to be solid

ENEMIES = {"Crococopter", "SwoopHarrier", "Fish", "Jumping Fish", "Lady Bugsy", "RedMarmot",
           "GreenMarmot", "Mosquito", "RulerSalamander", "Wolfey", "Springfling", "Skaterbug",
           "Giant Wolf", "Hydra", "Salamander Boss"}
HAZARDS = {"Spikes", "Spikes Down", "Spikes Left", "Spikes Right"}
GOALS = {"Goal", "Goal Capsule", "Animal Prison"}


# ── brickset ────────────────────────────────────────────────────────────────


def _lines(text: str) -> Iterator[List[str]]:
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        line = line.replace("{", " { ").replace("}", " } ")
        try:
            yield shlex.split(line)
        except ValueError:
            yield line.split()


def parse_brickset(text: str) -> Dict[int, Dict[str, Any]]:
    """brick id -> {type, behavior, mask, source_file, rect (x, y, w, h)}."""
    bricks: Dict[int, Dict[str, Any]] = {}
    cur: Optional[Dict[str, Any]] = None
    depth = 0
    for toks in _lines(text):
        for i, t in enumerate(toks):
            if t == "{":
                depth += 1
            elif t == "}":
                depth -= 1
                if depth == 0:
                    cur = None
        words = [t for t in toks if t not in "{}"]
        if not words:
            continue
        key = words[0]
        if depth <= 1 and key == "brick" and len(words) > 1 and words[1].isdigit():
            cur = bricks.setdefault(int(words[1]), {"type": "PASSABLE", "behavior": "DEFAULT"})
            continue
        if cur is None:
            continue
        if key in ("type", "behavior") and len(words) > 1:
            cur[key] = words[1].upper()
        elif key == "mask" and len(words) > 1:
            cur["mask"] = words[1]
        elif key == "source_file" and len(words) > 1:
            cur["source_file"] = words[1]
        elif key == "source_rect" and len(words) >= 5:
            cur["rect"] = tuple(int(float(x)) for x in words[1:5])
        elif key == "frame_size" and len(words) >= 3 and "rect" in cur:
            x, y, _, _ = cur["rect"]
            cur["rect"] = (x, y, int(float(words[1])), int(float(words[2])))
    return bricks


class MaskCache:
    """Opaque-pixel counts per 16 px cell for each (brick, flip, x % 16, y % 16)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.images: Dict[str, Any] = {}
        self.cells: Dict[Tuple, List[Tuple[int, int, float]]] = {}

    def _alpha(self, rel: str) -> Any:
        if rel not in self.images:
            img = None
            try:
                from PIL import Image

                p = (self.root / rel).resolve()
                if self.root.resolve() in p.parents and p.is_file():
                    with Image.open(p) as im:
                        img = im.convert("RGBA").getchannel("A")
            except Exception:  # an unreadable image: fall back to the brick box
                img = None
            self.images[rel] = img
        return self.images[rel]

    def cover(self, brick: Dict[str, Any], flip: str, ox: int, oy: int) -> List[Tuple[int, int, float]]:
        """``(dc, dr, share)`` for the cells the brick covers when its top-left is at (ox, oy)
        inside the cell grid (0 <= ox, oy < 16)."""
        key = (id(brick), flip, ox, oy)
        if key in self.cells:
            return self.cells[key]
        rect = brick.get("rect") or (0, 0, TILE, TILE)
        x, y, w, h = rect
        alpha = self._alpha(brick.get("mask") or brick.get("source_file") or "")
        crop = None
        if alpha is not None:
            from PIL import Image

            crop = alpha.crop((x, y, x + w, y + h)).point(lambda a: 255 if a > 0 else 0)
            if "h" in flip:
                crop = crop.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            if "v" in flip:
                crop = crop.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        out = []
        for dr in range((oy + h + TILE - 1) // TILE):
            for dc in range((ox + w + TILE - 1) // TILE):
                # the brick's pixels inside this cell
                px0, py0 = max(0, dc * TILE - ox), max(0, dr * TILE - oy)
                px1, py1 = min(w, (dc + 1) * TILE - ox), min(h, (dr + 1) * TILE - oy)
                if px1 <= px0 or py1 <= py0:
                    continue
                if crop is None:
                    n = (px1 - px0) * (py1 - py0)
                else:
                    n = crop.crop((px0, py0, px1, py1)).histogram()[255]
                if n:
                    out.append((dc, dr, n / float(TILE * TILE)))
        self.cells[key] = out
        return out


# ── levels ──────────────────────────────────────────────────────────────────


def parse_level(text: str) -> Dict[str, Any]:
    head: Dict[str, Any] = {"bricks": [], "entities": []}
    for toks in _lines(text):
        k = toks[0]
        if k == "brick" and len(toks) >= 4:
            try:
                head["bricks"].append((int(toks[1]), float(toks[2]), float(toks[3]),
                                       toks[4] if len(toks) > 4 else "default",
                                       toks[5] if len(toks) > 5 else ""))
            except ValueError:
                continue
        elif k == "entity" and len(toks) >= 4:
            try:
                head["entities"].append((toks[1], float(toks[2]), float(toks[3])))
            except ValueError:
                continue
        elif k == "spawn_point" and len(toks) >= 3:
            head["spawn"] = (float(toks[1]), float(toks[2]))
        elif k in ("name", "author", "license", "theme") and len(toks) > 1:
            head[k] = toks[1]
    return head


def convert_level(lev: Dict[str, Any], bricks: Dict[int, Dict[str, Any]], masks: MaskCache
                  ) -> Optional[Tuple[List[List[TileValue]], List[IREntity]]]:
    placed = [(bricks[b], x, y, flip) for b, x, y, _layer, flip in lev["bricks"]
              if b in bricks and bricks[b]["type"] in SOLID_TYPES
              and bricks[b]["behavior"] not in SKIP_BEHAVIORS]
    if not placed:
        return None
    points = [(x, y) for _, x, y in lev["entities"]] + ([lev["spawn"]] if lev.get("spawn") else [])
    x0 = min([x for _, x, _, _ in placed] + [max(0.0, x - TILE) for x, _ in points])
    y0 = min([y for _, _, y, _ in placed] + [max(0.0, y - TILE) for _, y in points])
    x0, y0 = (int(x0) // TILE) * TILE, (int(y0) // TILE) * TILE
    x1 = max(x + (b.get("rect") or (0, 0, TILE, TILE))[2] for b, x, _, _ in placed)
    y1 = max(y + (b.get("rect") or (0, 0, TILE, TILE))[3] for b, _, y, _ in placed)
    gw, gh = int((x1 - x0) // TILE) + 2, int((y1 - y0) // TILE) + 2
    share: Dict[Tuple[int, int], float] = {}
    for b, x, y, flip in placed:
        lx, ly = int(x - x0), int(y - y0)
        c0, r0 = lx // TILE, ly // TILE
        for dc, dr, s in masks.cover(b, flip, lx % TILE, ly % TILE):
            k = (c0 + dc, r0 + dr)
            share[k] = share.get(k, 0.0) + s
    grid = empty_grid(gw, gh)
    for (c, r), s in share.items():
        if s >= COVER and 0 <= r < gh and 0 <= c < gw:
            grid[r][c] = TileValue.SOLID
    ents: List[IREntity] = []
    for name, x, y in lev["entities"]:
        lx, ly = x - x0, y - y0
        col, row = int(lx // TILE), int(ly // TILE)
        if not (0 <= col < gw and 0 <= row < gh):
            continue
        if name in HAZARDS:
            fill_box(grid, lx - TILE, ly - TILE, 2 * TILE, 2 * TILE, TileValue.HAZARD, overwrite_solid=False)
        elif name in ENEMIES:
            ents.append(IREntity("Enemy", col, row))
        elif name in GOALS:
            ents = [e for e in ents if e.entity_type != "Exit"] + [IREntity("Exit", col, row)]
        elif name == "Collectible" or name.startswith("Powerup"):
            ents.append(IREntity("Pickup", col, row))
    if lev.get("spawn"):
        sx, sy = lev["spawn"]
        col, row = int((sx - x0) // TILE), int((sy - y0) // TILE)
        if 0 <= col < gw and 0 <= row < gh:
            ents.append(IREntity("Start", col, row))
    return trim(grid, settle(grid, ents))


def convert_file(path: Path, root: Path, report: SourceReport, bricksets: Dict[str, Any],
                 masks: MaskCache) -> Optional[ConvertedLevel]:
    rel = path.name
    if rel in SKIP_FILES:
        report.skipped[rel] = SKIP_FILES[rel]
        return None
    try:
        lev = parse_level(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        report.skipped[rel] = f"unreadable: {exc}"
        return None
    spdx = spdx_of(lev.get("license"))
    if not spdx:
        report.skipped[rel] = f"licence not reusable or unknown: {lev.get('license')!r}"
        return None
    author = str(lev.get("author", "")).strip()
    if author.lower() in PLACEHOLDER_AUTHORS:
        report.skipped[rel] = "no author named (attribution unclear)"
        return None
    theme = str(lev.get("theme") or "")
    if theme not in bricksets:
        tp = (root / theme).resolve()
        bricksets[theme] = (parse_brickset(tp.read_text(encoding="utf-8", errors="replace"))
                            if theme and root.resolve() in tp.parents and tp.is_file() else {})
    conv = convert_level(lev, bricksets[theme], masks)
    if conv is None:
        report.skipped[rel] = "no solid bricks"
        return None
    grid, ents = conv
    name = str(lev.get("name") or path.stem)
    act = re.search(r"-(\d+)$", path.stem)
    title = f"{name} act {act.group(1)}" if act else name
    report.include(rel, spdx)
    return ConvertedLevel(level=path.stem, grid=grid, entities=ents, license=spdx,
                          attribution=f"\"{title}\" by {author}, Open Surge ({URL}), {lev.get('license')}",
                          origin_path=f"opensurge/levels/{rel}", title=title)


def iter_levels(root: Path, report: SourceReport, max_levels: Optional[int] = None
                ) -> Iterator[ConvertedLevel]:
    """Levels of an Open Surge checkout at *root* (``levels/*.lev``; not scenes or legacy)."""
    bricksets: Dict[str, Any] = {}
    masks = MaskCache(root)
    n = 0
    for p in sorted((root / "levels").glob("*.lev")):
        if max_levels is not None and n >= max_levels:
            break
        lv = convert_file(p, root, report, bricksets, masks)
        if lv:
            n += 1
            yield lv
    for sub in ("scenes", "legacy"):
        if (root / "levels" / sub).is_dir():
            report.skipped[f"levels/{sub}/"] = "menus, cutscenes and legacy-format levels"


__all__ = ["GAME", "URL", "MaskCache", "convert_level", "iter_levels", "parse_brickset", "parse_level"]
