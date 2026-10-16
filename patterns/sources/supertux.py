"""SuperTux levels (``data/levels/**/*.stl``) -> GLDtk grids.

``.stl`` files are S-expressions: a level has a ``(license ...)`` and ``(author ...)`` and one or
more sectors; each sector has tilemaps (``(solid #t)`` marks the collision ones) with
run-length-encoded tile ids (``-N v`` = N times v), plus objects with ``(x)``/``(y)`` in pixels.
Tile ids map to solid / hazard through the tileset's own attribute flags (``data/images/*.strf``,
SuperTux ``src/supertux/tile.hpp``).  SuperTux tiles are 32 px, so each one becomes 2x2 GLDtk
tiles (pixel scale is kept: Tux and the GLDtk player are about the same size).

The game is GPL-3 and its data mostly CC-BY-SA; every level file names its own licence, which is
what decides (a level without a recognised licence is skipped).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from layout.ir import IREntity, TileValue
from patterns.sources import (TILE, ConvertedLevel, SourceReport, empty_grid, entity_at,
                              fill_box, settle, spdx_of, trim)

GAME = "supertux"
URL = "https://github.com/SuperTux/supertux"
SPARSE = ["/*", "!/*/", "/data/levels/", "/data/images/*.strf"]
SRC_TILE = 32
# GLDtk tiles per SuperTux tile (per side).  1: Tux jumps about 3-4 of his tiles, which matches
# the Part 28 short hop; 2 is the old 16 px conversion (jumps twice as long, now unplayable).
SCALE = 1

# tile.hpp attribute flags
SOLID, UNISOLID, BRICK, GOAL, SLOPE = 0x1, 0x2, 0x4, 0x8, 0x10
WATER, HURTS, FIRE = 0x200, 0x400, 0x800

SKIP_DIRS = {"misc": "menus and credits, not levels"}
SKIP_WORDS = ("cutscene", "intro", "outro", "credits", "menu")

# Objects that are solid blocks in play.
SOLID_OBJECTS = {"bonusblock", "brick", "unstable_tile", "weak_block", "infoblock",
                 "invisible_block", "magicblock", "fallblock"}
HAZARD_OBJECTS = {"hurting_platform", "flame", "spikes", "livefire_dormant"}
PICKUP_OBJECTS = {"coin", "powerup", "key", "heavycoin"}
ENEMY_OBJECTS = {
    "snowball", "jumpy", "viciousivy", "walkingleaf", "mrbomb", "smartball", "mriceblock",
    "haywire", "flyingsnowball", "snail", "short_fuse", "sspiky", "spiky", "mrtree", "darttrap",
    "bouncingsnowball", "igel", "owl", "rcrystallo", "crystallo", "scrystallo", "captainsnowball",
    "zeekling", "mole", "goldbomb", "walking_candle", "root_sapling", "livefire",
    "livefire_asleep", "yeti", "tarantula", "stalactite", "dispenser", "kugelblitz", "toad",
    "skydive", "fish", "stumpy", "ghosttree", "plant", "poisonivy", "kamikazesnowball",
    "leafshot", "dart", "skullyhop", "corrupted_granito", "corrupted_granito_big", "willowisp",
    "treewillowisp", "angrystone", "bomb", "skullyhop", "snowman", "ghoul", "crusher",
}


# ── S-expressions ───────────────────────────────────────────────────────────


def _tokens(text: str) -> Iterator[Any]:
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
        elif ch == ";":
            while i < n and text[i] != "\n":
                i += 1
        elif ch in "()":
            yield ch
            i += 1
        elif ch == '"':
            j, buf = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(text[j])
                j += 1
            yield ("str", "".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()";':
                j += 1
            yield ("atom", text[i:j])
            i = j


def _atom(tok: str) -> Any:
    if tok == "#t":
        return True
    if tok == "#f":
        return False
    try:
        return int(tok)
    except ValueError:
        try:
            return float(tok)
        except ValueError:
            return tok  # a symbol


def parse_sexpr(text: str) -> List[Any]:
    """The top-level forms of *text*; a list is ``[head, *items]``, strings stay ``str``."""
    stack: List[List[Any]] = [[]]
    for tok in _tokens(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            if len(stack) == 1:
                raise ValueError("unbalanced ')'")
            done = stack.pop()
            stack[-1].append(done)
        else:
            kind, val = tok
            stack[-1].append(val if kind == "str" else _atom(val))
    if len(stack) != 1:
        raise ValueError("unbalanced '('")
    return stack[0]


def children(form: List[Any], name: str) -> List[List[Any]]:
    return [x for x in form[1:] if isinstance(x, list) and x and x[0] == name]


def child(form: List[Any], name: str) -> Optional[List[Any]]:
    c = children(form, name)
    return c[0] if c else None


def value(form: List[Any], name: str, default: Any = None) -> Any:
    c = child(form, name)
    if not c or len(c) < 2:
        return default
    v = c[1]
    if isinstance(v, list) and v and v[0] == "_" and len(v) > 1:  # (_ "translatable")
        return v[1]
    return v


# ── tileset ─────────────────────────────────────────────────────────────────


def load_tileset(path: Path) -> Dict[int, int]:
    """tile id -> attribute flags, from a ``.strf`` file."""
    attrs: Dict[int, int] = {}
    for top in parse_sexpr(path.read_text(encoding="utf-8", errors="replace")):
        if not isinstance(top, list):
            continue
        for f in top[1:]:
            if not isinstance(f, list) or not f:
                continue
            if f[0] == "tile":
                tid = value(f, "id")
                if isinstance(tid, int):
                    a = value(f, "attributes", 0)
                    attrs[tid] = int(a) if isinstance(a, int) else 0
            elif f[0] == "tiles":
                ids = child(f, "ids")
                at = child(f, "attributes")
                if not ids:
                    continue
                avals = list(at[1:]) if at else []
                for i, tid in enumerate(ids[1:]):
                    if isinstance(tid, int) and tid:
                        a = avals[i] if i < len(avals) else 0
                        attrs[tid] = int(a) if isinstance(a, int) else 0
    return attrs


def classify(flags: int) -> TileValue:
    if flags & (HURTS | FIRE):
        return TileValue.HAZARD
    if flags & (SOLID | UNISOLID):
        return TileValue.SOLID
    return TileValue.EMPTY


def decode_tiles(items: List[Any]) -> List[int]:
    """Run-length decode: ``-N v`` stands for N copies of v."""
    out: List[int] = []
    it = iter(items)
    for v in it:
        if isinstance(v, int) and v < 0:
            nxt = next(it, 0)
            out.extend([int(nxt)] * (-v))
        else:
            out.append(int(v))
    return out


# ── levels ──────────────────────────────────────────────────────────────────


def _xy(obj: List[Any]) -> Optional[Tuple[float, float]]:
    x, y = value(obj, "x"), value(obj, "y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return float(x), float(y)
    return None


def convert_sector(sector: List[Any], tileset: Dict[int, int], scale: int = SCALE
                   ) -> Optional[Tuple[List[List[TileValue]], List[IREntity]]]:
    """One sector as a grid (*scale* GLDtk tiles per SuperTux tile), or None when it has no solid tilemap."""
    maps = []
    for tm in children(sector, "tilemap"):
        if value(tm, "solid") is not True or child(tm, "path") is not None:
            continue  # decoration, or a moving tilemap
        w, h, tiles = value(tm, "width"), value(tm, "height"), child(tm, "tiles")
        if not isinstance(w, int) or not isinstance(h, int) or not tiles:
            continue
        maps.append((w, h, decode_tiles(tiles[1:]),
                     float(value(tm, "x-offset", 0) or 0), float(value(tm, "y-offset", 0) or 0)))
    if not maps:
        return None
    sw = max(w + int(xo // SRC_TILE) for w, _, _, xo, _ in maps)
    sh = max(h + int(yo // SRC_TILE) for _, h, _, _, yo in maps)
    k = max(1, int(scale))
    cell = SRC_TILE // k  # px per GLDtk tile
    gw, gh = sw * k, sh * k
    grid = empty_grid(gw, gh)
    ents: List[IREntity] = []
    for w, h, ids, xo, yo in maps:
        oc, orow = int(xo // SRC_TILE), int(yo // SRC_TILE)
        for i, tid in enumerate(ids[: w * h]):
            if not tid:
                continue
            flags = tileset.get(tid, 0)
            v = classify(flags)
            r, c = divmod(i, w)
            if v != TileValue.EMPTY:
                for dr in range(k):
                    for dc in range(k):
                        rr, cc = (orow + r) * k + dr, (oc + c) * k + dc
                        if 0 <= rr < gh and 0 <= cc < gw and grid[rr][cc] != TileValue.SOLID:
                            grid[rr][cc] = v
            if flags & GOAL and not any(e.entity_type == "Exit" for e in ents):
                ents.append(IREntity("Exit", (oc + c) * k, (orow + r) * k + 1))
    for obj in sector[1:]:
        if not isinstance(obj, list) or not obj or not isinstance(obj[0], str):
            continue
        name, pos = obj[0], _xy(obj)
        if pos is None:
            continue
        x, y = pos
        if name in SOLID_OBJECTS:
            fill_box(grid, x, y, SRC_TILE, SRC_TILE, TileValue.SOLID, tile=cell)
        elif name in HAZARD_OBJECTS:
            fill_box(grid, x, y, SRC_TILE, SRC_TILE, TileValue.HAZARD, overwrite_solid=False, tile=cell)
        elif name == "spawnpoint" and value(obj, "name", "main") == "main":
            e = entity_at("Start", x, y, gw, gh, tile=cell)
            if e:
                ents.append(e)
        elif name == "sequencetrigger" and str(value(obj, "sequence", "")) == "endsequence":
            w = float(value(obj, "width", SRC_TILE) or SRC_TILE)
            e = entity_at("Exit", x + w / 2 - SRC_TILE / 2, y, gw, gh, tile=cell)
            if e:
                ents = [x for x in ents if x.entity_type != "Exit"] + [e]
        elif name in ENEMY_OBJECTS:
            e = entity_at("Enemy", x, y, gw, gh, tile=cell)
            if e:
                ents.append(e)
        elif name in PICKUP_OBJECTS:
            e = entity_at("Pickup", x, y, gw, gh, tile=cell)
            if e:
                ents.append(e)
    return trim(grid, settle(grid, ents))


def _tileset_for(level: List[Any], data_dir: Path, cache: Dict[str, Dict[int, int]]) -> Dict[int, int]:
    rel = str(value(level, "tileset", "/images/tiles.strf") or "/images/tiles.strf").lstrip("/")
    path = data_dir / rel
    if rel not in cache:
        cache[rel] = load_tileset(path) if path.is_file() else {}
    return cache[rel]


def convert_file(path: Path, data_dir: Path, report: SourceReport,
                 cache: Optional[Dict[str, Dict[int, int]]] = None, rel: str = "",
                 scale: int = SCALE) -> List[ConvertedLevel]:
    """The levels (one per sector with collision) of one ``.stl`` file; [] when skipped."""
    cache = {} if cache is None else cache
    rel = rel or path.name
    try:
        forms = parse_sexpr(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        report.skipped[rel] = f"unreadable: {exc}"
        return []
    level = next((f for f in forms if isinstance(f, list) and f and f[0] == "supertux-level"), None)
    if level is None:
        report.skipped[rel] = "not a supertux-level"
        return []
    lic_text = value(level, "license")
    spdx = spdx_of(lic_text)
    if not spdx:
        report.skipped[rel] = f"licence not reusable or unknown: {lic_text!r}"
        return []
    name = str(value(level, "name", path.stem))
    author = str(value(level, "author", "SuperTux contributors"))
    tileset = _tileset_for(level, data_dir, cache)
    out: List[ConvertedLevel] = []
    sectors = children(level, "sector")
    for sec in sectors:
        conv = convert_sector(sec, tileset, scale)
        if conv is None:
            continue
        grid, ents = conv
        sname = str(value(sec, "name", "main"))
        ident = path.stem if sname == "main" or len(sectors) == 1 else f"{path.stem}:{sname}"
        out.append(ConvertedLevel(
            level=ident, grid=grid, entities=ents, license=spdx,
            attribution=f"\"{name}\" by {author}, SuperTux ({URL}), {lic_text}",
            origin_path=f"supertux/data/levels/{rel}", title=name))
    if not out:
        report.skipped[rel] = "no solid tilemap"
    else:
        report.include(rel, spdx)
    return out


def iter_levels(root: Path, report: SourceReport, max_levels: Optional[int] = None,
                scale: int = SCALE) -> Iterator[ConvertedLevel]:
    """Levels (one per sector) of a SuperTux checkout at *root*, at most *max_levels*."""
    data = root / "data"
    levels_dir = data / "levels"
    cache: Dict[str, Dict[int, int]] = {}
    n = 0
    # the main story worlds first, then the bonus / contrib worlds
    files = sorted(levels_dir.rglob("*.stl"),
                   key=lambda q: (not str(q.relative_to(levels_dir)).startswith("world"), str(q)))
    for p in files:
        rel = str(p.relative_to(levels_dir))
        top = rel.split("/")[0]
        if top in SKIP_DIRS:
            report.skipped[rel] = SKIP_DIRS[top]
            continue
        if any(w in p.stem for w in SKIP_WORDS):
            report.skipped[rel] = "cutscene / menu, not a level"
            continue
        if max_levels is not None and n >= max_levels:
            break
        conv = convert_file(p, data, report, cache, rel, scale)
        for lv in conv:
            if max_levels is not None and n >= max_levels:
                return
            n += 1
            yield lv


__all__ = ["GAME", "URL", "classify", "convert_file", "convert_sector", "decode_tiles",
           "iter_levels", "load_tileset", "parse_sexpr"]
