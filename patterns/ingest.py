"""Ingest levels into patterns: read, convert to the GLDtk grid, cut windows, compute features.

Sources:
  * LDtk's sample projects (MIT, ``ldtk-src/LICENSE``): layout structure only (IntGrid
    collision and entity positions/kinds), never their art.  Foreign IntGrid values are
    mapped per layer by name (``SOLID_NAMES`` / ``HAZARD_NAMES``); a layer whose values do not
    map is skipped, and so are the files in ``SKIP_FILES``.
  * GLDtk's own levels (``add_path``: the demo folders and any ``.ldtk`` the user approves).
  * Open-licensed game levels (Part 26d, ``patterns.sources``): SuperTux and Open Surge,
    converted per level after a licence check; each record carries ``attribution``.
    ``OPEN_CORPORA_HOOK`` documents how to add another corpus.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from game.profile import GameProfile
from layout.ir import IREntity, IRLevel, TileValue
from layout.route import level_route

from patterns.store import PatternStore

SOLID_NAMES = {"wall", "walls", "ground", "solid", "dirt", "stone", "floor", "block", "blocks", "rock"}
HAZARD_NAMES = {"lava", "spike", "spikes", "water", "acid", "hazard", "thorns"}
IGNORED_NAMES = {"ladder", "ladders", "air", "empty"}  # mapped to EMPTY on purpose

# Whole files that are not side-view platformer structure (reason is reported by the CLI).
SKIP_FILES = {
    "Typical_TopDown_example.ldtk": "top-down, not a platformer",
    "Test_file_for_API_showing_all_features.ldtk": "an API feature test, not level design",
}
SAMPLE_LICENSE = "MIT (LDtk, ldtk-src/LICENSE)"
GLDTK_LICENSE = "project-owned (GLDtk)"
OPEN_CORPORA_HOOK = (
    "Other open corpora: check the licence first, convert to a raw grid and call "
    "ingest_grid(..., source=<name>, license=<spdx>, attribution=<authors, URL>); open-licensed "
    "game levels come from patterns.sources (--source)."
)

TILE = 16  # GLDtk's tile size; foreign layers of another size are skipped
WIN_W = 32
WIN_STRIDE = 24
WIN_H = 20
MIN_WIN_W = 16
# Window sizes (rows, columns, stride) cut from every level (Part 26e): the tall ones let
# layout/terrain/stamp.py (which accepts +-25% of the section height) fill sections of 26-40 rows.
WIN_SIZES = ((20, 32, 24), (32, 48, 36))
MAX_ASCII_W, MAX_ASCII_H = 52, 36
# Window quality (Part 26e): standable spans, solid fraction, padding rows around the content.
MIN_SPANS = 2
MIN_SOLID, MAX_SOLID = 0.12, 0.7
CROP_PAD = 2

ENTITY_CHARS = {"Start": "S", "Exit": "X", "Enemy": "E", "Pickup": "P"}
_START_WORDS = ("player", "start", "spawn", "hero")
_ENEMY_WORDS = ("mob", "enemy", "monster")
_PICKUP_WORDS = ("chest", "item", "coin", "gem", "pickup")


@dataclass
class IngestReport:
    added: int = 0
    duplicates: int = 0
    rejected: int = 0  # stored but not accepted (no Start->Exit route in the window)
    levels_added: int = 0  # whole-level entries (kind "level", Part 26c)
    dropped: Dict[str, int] = field(default_factory=dict)  # windows not kept, by reason (Part 26e)
    skipped_files: Dict[str, str] = field(default_factory=dict)
    mapped_files: Dict[str, int] = field(default_factory=dict)  # file -> patterns added
    notes: List[str] = field(default_factory=list)

    def merge(self, other: "IngestReport") -> None:
        self.added += other.added
        self.duplicates += other.duplicates
        self.rejected += other.rejected
        self.levels_added += other.levels_added
        for k, n in other.dropped.items():
            self.dropped[k] = self.dropped.get(k, 0) + n
        self.skipped_files.update(other.skipped_files)
        self.mapped_files.update(other.mapped_files)
        self.notes += other.notes


# ── mapping foreign IntGrid values ──────────────────────────────────────────


def map_intgrid_values(layer_def: Dict[str, Any]) -> Optional[Dict[int, TileValue]]:
    """value -> TileValue for one IntGrid layer definition, or None when it has no solid value."""
    out: Dict[int, TileValue] = {}
    for v in layer_def.get("intGridValues") or []:
        name = str(v.get("identifier") or "").strip().lower()
        if name in SOLID_NAMES:
            out[int(v["value"])] = TileValue.SOLID
        elif name in HAZARD_NAMES:
            out[int(v["value"])] = TileValue.HAZARD
        elif name in IGNORED_NAMES:
            out[int(v["value"])] = TileValue.EMPTY
        else:
            return None  # an unnamed or unknown value: the layer's meaning is unclear
    return out if TileValue.SOLID in out.values() else None


def _entity_kind(identifier: str) -> Optional[str]:
    n = identifier.lower()
    if "exit" in n:
        return "Exit"
    if any(w in n for w in _START_WORDS):
        return "Start"
    if any(w in n for w in _ENEMY_WORDS):
        return "Enemy"
    if any(w in n for w in _PICKUP_WORDS):
        return "Pickup"
    return None


def _raw_levels(doc: Dict[str, Any], source_name: str, report: IngestReport):
    """Yield ``(identifier, grid, entities)`` for each mappable level of a foreign project."""
    maps = {ld["identifier"]: map_intgrid_values(ld) for ld in doc["defs"]["layers"]
            if ld["type"] == "IntGrid"}
    for lvl in doc.get("levels") or []:
        layers = lvl.get("layerInstances")
        if not layers:
            report.notes.append(f"{source_name}/{lvl.get('identifier')}: external level file, skipped")
            continue
        layer = next((li for li in layers if li["__type"] == "IntGrid"
                      and maps.get(li["__identifier"]) and int(li["__gridSize"]) == TILE), None)
        if layer is None:
            report.notes.append(f"{source_name}/{lvl.get('identifier')}: no IntGrid layer maps to "
                                f"solid/hazard on a {TILE}px grid, skipped")
            continue
        vmap = maps[layer["__identifier"]]
        w, h = int(layer["__cWid"]), int(layer["__cHei"])
        csv = layer.get("intGridCsv") or []
        grid = [[vmap.get(int(csv[r * w + c]), TileValue.EMPTY) if r * w + c < len(csv) else TileValue.EMPTY
                 for c in range(w)] for r in range(h)]
        ents: List[IREntity] = []
        for li in layers:
            if li["__type"] != "Entities":
                continue
            g = int(li.get("__gridSize") or TILE)
            for ei in li.get("entityInstances") or []:
                kind = _entity_kind(str(ei.get("__identifier", "")))
                if not kind:
                    continue
                px, py = ei["px"]
                col = int((px + ei.get("width", g) / 2) // g)
                row = int((py + ei.get("height", g) - 1) // g)
                if 0 <= col < w and 0 <= row < h:
                    ents.append(IREntity(kind, col, row))
        yield str(lvl.get("identifier")), grid, ents


# ── windows ─────────────────────────────────────────────────────────────────


def _standable_cells(grid: Sequence[Sequence[Any]], head: int = 2) -> List[Tuple[int, int]]:
    """Cells ``(col, row)`` a player can stand in: empty, SOLID below, ``head`` empty above."""
    h, w = len(grid), len(grid[0]) if grid else 0
    out = []
    # (row r is the surface row: the solid cell; the player stands in r-1)
    for r in range(head, h):
        for c in range(w):
            if int(grid[r][c]) == TileValue.SOLID and all(
                    int(grid[r - k][c]) == TileValue.EMPTY for k in range(1, head + 1)):
                out.append((c, r))
    return out


def _surface_runs(cells: List[Tuple[int, int]]) -> List[Tuple[int, int, int]]:
    """``(row, col0, col1)`` horizontal runs of standable surface cells."""
    by_row: Dict[int, List[int]] = {}
    for c, r in cells:
        by_row.setdefault(r, []).append(c)
    runs = []
    for r, cols in by_row.items():
        cols.sort()
        s = p = cols[0]
        for c in cols[1:]:
            if c != p + 1:
                runs.append((r, s, p))
                s = c
            p = c
        runs.append((r, s, p))
    return sorted(runs, key=lambda x: (x[1], x[0]))


def _snap_cuts(grid: Sequence[Sequence[Any]], width: int, win_w: int = WIN_W,
               stride: int = WIN_STRIDE) -> List[Tuple[int, int]]:
    """Column windows ``(c0, c1)`` (about *win_w* wide, overlapping), cut at vertical walls."""
    if width <= win_w + 8:
        return [(0, width)]
    h = len(grid)
    solid = [sum(1 for r in range(h) if int(grid[r][c]) == TileValue.SOLID) for c in range(width)]

    def snap(c: int) -> int:
        lo, hi = max(1, c - 4), min(width - 1, c + 4)
        return max(range(lo, hi + 1), key=lambda x: (solid[x], -abs(x - c)))

    cuts, start = [], 0
    while start < width - MIN_WIN_W:
        end = width if start + win_w + 8 >= width else snap(start + win_w)
        cuts.append((start, end))
        if end >= width:
            break
        start = max(start + 1, snap(start + stride))
    return cuts


def _row_cuts(height: int, win_h: int = WIN_H) -> List[Tuple[int, int]]:
    if height <= win_h + 6:
        return [(0, height)]
    out, r = [], 0
    while r < height:
        end = min(height, r + win_h)
        out.append((max(0, end - win_h), end))
        if end >= height:
            break
        r += win_h - 6
    return out


# ── features ────────────────────────────────────────────────────────────────


def structure_of(grid: Sequence[Sequence[Any]], runs: List[Tuple[int, int, int]], hazards: int) -> str:
    """Heuristic structure type (used until a label gives one)."""
    h, w = len(grid), len(grid[0]) if grid else 0
    rows = {r for r, _, _ in runs}
    if h > w * 1.2 and len(rows) >= 3:
        return "shaft"
    if w >= 3 * h and len(rows) <= 2:
        return "corridor"
    if len(rows) >= 6 and len(runs) >= 8:
        return "parkour" if sum(b - a + 1 for _, a, b in runs) / len(runs) < 6 else "open"
    if hazards >= 4 and len(rows) >= 3:
        return "parkour"
    return "open" if len(runs) >= 4 else "corridor"


def guess_start_exit(grid: Sequence[Sequence[Any]], ents: List[IREntity]) -> List[IREntity]:
    """The window's entities with a Start and an Exit (guessed from its walkable structure when
    missing: the leftmost and the rightmost standable surface)."""
    out = [e for e in ents]
    cells = _standable_cells(grid)
    if not cells:
        return out
    if not any(e.entity_type == "Start" for e in out):
        c, r = min(cells, key=lambda x: (x[0], -x[1]))
        out.append(IREntity("Start", c, r - 1, {"guessed": True}))
    if not any(e.entity_type == "Exit" for e in out):
        c, r = max(cells, key=lambda x: (x[0], x[1]))
        out.append(IREntity("Exit", c, r - 1, {"guessed": True}))
    return out


def window_features(grid: List[List[Any]], ents: List[IREntity], profile: GameProfile) -> Dict[str, Any]:
    h, w = len(grid), len(grid[0])
    runs = _surface_runs(_standable_cells(grid))
    hazards = sum(1 for row in grid for v in row if int(v) == TileValue.HAZARD)
    enemies = sum(1 for e in ents if e.entity_type == "Enemy")
    pickups = sum(1 for e in ents if e.entity_type == "Pickup")
    widths = sorted(b - a + 1 for _, a, b in runs)
    ordered = sorted(runs, key=lambda x: x[1])
    gaps = [max(0, nxt[1] - cur[2] - 1) for cur, nxt in zip(ordered, ordered[1:])]
    from patterns.classify import classify
    from symbolic.extractor import extract_graph

    graph = extract_graph(grid, profile=profile, collision=True)
    route = level_route(grid, ents, profile, graph=graph)
    jumps = sum(1 for s in route.steps if s.kind != "walk")
    f: Dict[str, Any] = {
        "width": w, "height": h,
        "platforms": len(runs),
        "platform_widths": widths,
        "max_gap": max(gaps) if gaps else 0,
        "gaps": gaps,
        "hazards": hazards,
        "enemies": enemies,
        "pickups": pickups,
        "structure": structure_of(grid, runs, hazards),
        "route": {"ok": route.ok, "error": route.error, "steps": len(route.steps), "jumps": jumps,
                  "length_tiles": round(route.length_px / profile.tile_size, 1),
                  # spans where the player gets stuck (Part 22); such patterns are never offered
                  "softlocks": len(getattr(route, "softlock_spans", None) or [])},
    }
    f["difficulty"] = _difficulty(grid, ents, profile, route)
    try:  # Part 26f: geometry structure type (labyrinth / arena / hub / shaft)
        f["structure_geo"], f["geometry"] = classify(grid, ents, profile, graph, route,
                                                     fallback=f["structure"])
    except Exception:  # noqa: BLE001 - a classifier failure must not drop the pattern
        pass
    return f


def _difficulty(grid, ents, profile, route) -> Optional[float]:
    if not route.ok:
        return None
    from fidelity.difficulty import measure_difficulty

    try:
        rep = measure_difficulty(grid, ents, [], profile, route)
    except Exception:  # a measure failure must not drop the pattern
        return None
    return None if rep.overall is None else round(float(rep.overall), 3)


def accept_features(f: Dict[str, Any]) -> bool:
    r = f.get("route", {})
    return bool(r.get("ok")) and not r.get("softlocks")


def _ascii(grid, ents) -> str:
    ir = IRLevel(identifier="p", width_tiles=len(grid[0]), height_tiles=len(grid), tile_size=TILE,
                 grid=[list(r) for r in grid], entities=list(ents))
    return ir.ascii_render(ENTITY_CHARS)


def grid_hash(ascii_text: str) -> str:
    """Hash of a window's geometry only (entity letters count as empty): near-duplicate windows
    (the same level cut twice, repeated rooms) share it."""
    norm = "\n".join("".join(ch if ch in "#^" else "." for ch in ln) for ln in ascii_text.splitlines())
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def _known_hashes(store: PatternStore) -> set:
    cache = getattr(store, "_grid_hashes", None)
    if cache is None:
        cache = {grid_hash(r["ascii"]) for r in store
                 if r.get("kind", "pattern") == "pattern" and r.get("ascii")}
        store._grid_hashes = cache
    return cache


def crop_window(win: List[List[Any]], wents: List[IREntity], pad: int = CROP_PAD
                ) -> Tuple[List[List[Any]], List[IREntity], int, int]:
    """Crop *win* to its content: rows from *pad* above the first non-empty row to *pad* below the
    last, and the empty edge columns.  Returns ``(grid, entities, drow, dcol)``: how many rows /
    columns were cut off the top / left."""
    h, w = len(win), len(win[0])
    rows = [r for r in range(h) if any(int(v) for v in win[r])]
    cols = [c for c in range(w) if any(int(win[r][c]) for r in range(h))]
    if not rows:
        return win, wents, 0, 0
    full = [r for r in range(h) if all(int(v) == TileValue.SOLID for v in win[r])]
    # rows of solid rock far from any surface are not structure: stop *pad* rows into the rock
    top = next((r for r in rows if r not in full), rows[0] + 1)
    bot = next((r for r in reversed(rows) if r not in full), rows[-1] - 1)
    r0, r1 = max(0, top - 1 - pad), min(h, bot + 2 + pad)  # +-1: the surface row of the rock
    c0, c1 = cols[0], cols[-1] + 1
    out = [list(row[c0:c1]) for row in win[r0:r1]]
    ents = [IREntity(e.entity_type, e.col - c0, e.row - r0, {})
            for e in wents if c0 <= e.col < c1 and r0 <= e.row < r1]
    return out, ents, r0, c0


def make_id(source: str, origin: str, level: str, ascii_text: str) -> str:
    return hashlib.sha1(f"{source}|{origin}|{level}|{ascii_text}".encode()).hexdigest()[:12]


# ── whole-level entries (Part 26c) ──────────────────────────────────────────

THUMB_W, THUMB_H = 60, 20


def thumbnail(grid: Sequence[Sequence[Any]], ents: Sequence[IREntity],
              max_w: int = THUMB_W, max_h: int = THUMB_H) -> str:
    """The whole level as ASCII of at most *max_w* x *max_h*: each character covers a block of
    tiles (hazard beats solid beats empty; Start / Exit / Enemy / Pickup are overlaid)."""
    h, w = len(grid), len(grid[0]) if grid else 0
    if not h or not w:
        return ""
    sx, sy = -(-w // max_w), -(-h // max_h)  # block size, ceil
    cols, rows = -(-w // sx), -(-h // sy)
    cells = [["." for _ in range(cols)] for _ in range(rows)]
    for r in range(rows):
        for c in range(cols):
            vals = [int(grid[y][x]) for y in range(r * sy, min(h, (r + 1) * sy))
                    for x in range(c * sx, min(w, (c + 1) * sx))]
            n_solid = vals.count(int(TileValue.SOLID))
            if int(TileValue.HAZARD) in vals:
                cells[r][c] = "^"
            elif n_solid * 2 >= len(vals) or (n_solid and sx * sy == 1):
                cells[r][c] = "#"
    for e in ents:  # rarer kinds last so Start / Exit win
        ch = ENTITY_CHARS.get(e.entity_type)
        if ch and 0 <= e.row < h and 0 <= e.col < w:
            cells[e.row // sy][e.col // sx] = ch
    return "\n".join("".join(r) for r in cells)


def ingest_level(store: PatternStore, grid: List[List[Any]], entities: List[IREntity], *,
                 source: str, license: str, origin_path: str, level: str,
                 prompt: str = "", sections: Optional[List[Dict[str, Any]]] = None,
                 profile: Optional[GameProfile] = None, attribution: Optional[str] = None,
                 title: Optional[str] = None) -> bool:
    """Add one whole-level entry (``kind`` "level"); False when it is already there (same file,
    level and content) or the level is empty.  The label stays None: it is filled lazily."""
    if not grid or not grid[0]:
        return False
    profile = profile or GameProfile()
    content = hashlib.sha1(json.dumps(
        [[int(v) for v in row] for row in grid] + [[e.entity_type, e.col, e.row] for e in entities]
    ).encode()).hexdigest()
    pid = make_id("level:" + source, origin_path, level, content)
    if pid in store.patterns:
        return False
    ents = guess_start_exit(grid, list(entities))
    feats = window_features(grid, ents, profile)
    secs = [{k: s[k] for k in ("name", "theme", "difficulty", "style") if s.get(k)} for s in sections or []]
    title = title or (prompt or "").strip().split("\n")[0][:60] or f"{feats['structure']} level"
    extra = {"attribution": attribution} if attribution else {}
    return store.add({**extra,
        "id": pid, "kind": "level", "source": source, "license": license, "origin_path": origin_path,
        "level": level, "title": title, "prompt": (prompt or "")[:300], "sections": secs,
        "window": {"col": 0, "row": 0, "w": len(grid[0]), "h": len(grid)},
        "ascii": thumbnail(grid, ents), "features": feats, "accepted": accept_features(feats),
        "label": None,
    })


# ── public entry points ─────────────────────────────────────────────────────


def ingest_grid(
    store: PatternStore,
    grid: List[List[Any]],
    entities: List[IREntity],
    *,
    source: str,
    license: str,
    origin_path: str,
    level: str,
    profile: Optional[GameProfile] = None,
    attribution: Optional[str] = None,
    max_windows: Optional[int] = None,
    max_fill: Optional[float] = None,
) -> IngestReport:
    """Cut one level into patterns and add them to *store* (not saved).

    *max_windows* keeps at most that many windows, evenly spread over the level (big game
    levels, Part 26d); *max_fill* drops windows whose share of non-empty cells is higher
    (solid rock around a few corridors)."""
    profile = profile or GameProfile()
    rep = IngestReport()
    if not grid or not grid[0]:
        return rep
    width = len(grid[0])
    drops = rep.dropped

    def drop(why: str) -> None:
        drops[why] = drops.get(why, 0) + 1

    cands = []
    seen_win = set()
    for win_h, win_w, stride in WIN_SIZES:
        for r0, r1 in _row_cuts(len(grid), win_h):
            for c0, c1 in _snap_cuts(grid[r0:r1], width, win_w, stride):
                if c1 - c0 < MIN_WIN_W or (r0, r1, c0, c1) in seen_win:
                    continue
                seen_win.add((r0, r1, c0, c1))
                win = [[TileValue(int(v)) if int(v) in (0, 1, 2) else TileValue.SOLID for v in row[c0:c1]]
                       for row in grid[r0:r1]]
                wents = [IREntity(e.entity_type, e.col - c0, e.row - r0, {})
                         for e in entities if c0 <= e.col < c1 and r0 <= e.row < r1]
                win, wents, dr, dc = crop_window(win, wents)
                h, w = len(win), len(win[0])
                if w < MIN_WIN_W:
                    drop("too narrow")
                    continue
                n = h * w
                solid = sum(1 for row in win for v in row if int(v) == TileValue.SOLID) / n
                if solid < MIN_SOLID:
                    drop("mostly sky")
                    continue
                if solid > (max_fill if max_fill is not None else MAX_SOLID):
                    drop("mostly rock")
                    continue
                if len(_surface_runs(_standable_cells(win))) < MIN_SPANS:
                    drop("fewer than 2 standable spans")
                    continue
                cands.append((r0 + dr, c0 + dc, win, wents))
    if max_windows is not None and len(cands) > max_windows:
        step = len(cands) / float(max_windows)
        cands = [cands[int(i * step)] for i in range(max_windows)]
    known = _known_hashes(store)
    for r0, c0, win, wents in cands:
        wents = guess_start_exit(win, wents)
        text = _ascii(win, wents)
        lines = text.splitlines()
        if len(lines) > MAX_ASCII_H or max(len(x) for x in lines) > MAX_ASCII_W:
            drop("too large")
            continue
        rid = make_id(source, origin_path, level, text)
        if rid in store.patterns:
            rep.duplicates += 1
            continue
        gh = grid_hash(text)
        if gh in known:
            drop("near-duplicate")
            continue
        f = window_features(win, wents, profile)
        if not accept_features(f):
            drop("no route or soft-lock")
            continue
        rec = {
            "id": rid,
            "source": source, "license": license, "origin_path": origin_path,
            "level": level, "window": {"col": c0, "row": r0, "w": len(win[0]), "h": len(win)},
            "ascii": text, "features": f, "accepted": True, "label": None,
        }
        if attribution:  # open-licensed game levels (Part 26d): authors, URL, licence
            rec["attribution"] = attribution
        if store.add(rec):
            known.add(gh)
            rep.added += 1
        else:
            rep.duplicates += 1
    return rep


def ascii_to_grid(ascii_text: Any) -> Optional[Tuple[List[List[TileValue]], List[IREntity]]]:
    """``(grid, entities)`` of a window's ASCII (short lines are padded with rock); None if empty."""
    lines = [ln for ln in str(ascii_text or "").splitlines() if ln]
    if not lines:
        return None
    w = max(len(ln) for ln in lines)
    grid, ents = [], []
    kinds = {v: k for k, v in ENTITY_CHARS.items()}
    for r, ln in enumerate(lines):
        row = []
        for c, ch in enumerate(ln.ljust(w, "#")):
            row.append(TileValue.SOLID if ch == "#" else TileValue.HAZARD if ch == "^" else TileValue.EMPTY)
            if ch in kinds:
                ents.append(IREntity(kinds[ch], c, r, {}))
        grid.append(row)
    return grid, ents


def recheck(store: PatternStore, profile: Optional[GameProfile] = None,
            sources: Optional[Sequence[str]] = None) -> Dict[str, int]:
    """Recompute route features, difficulty and the soft-lock status of every window (and its
    ``accepted`` flag) with *profile* (default: the current ``GameProfile``), from its ASCII.
    Whole-level records are thumbnails and are rebuilt by re-ingesting.  Returns counts."""
    profile = profile or GameProfile()
    n = {"checked": 0, "accepted": 0, "dropped": 0}
    for rec in list(store):
        if rec.get("kind", "pattern") != "pattern" or (sources and rec.get("source") not in sources):
            continue
        parsed = ascii_to_grid(rec.get("ascii"))
        if parsed is None:
            continue
        grid, ents = parsed
        feats = window_features(grid, guess_start_exit(grid, ents), profile)
        rec["features"] = feats
        rec["accepted"] = accept_features(feats)
        n["checked"] += 1
        n["accepted" if rec["accepted"] else "dropped"] += 1
    return n


def add_path(store: PatternStore, path: Any, level: Optional[str] = None, *,
             source: Optional[str] = None, license: Optional[str] = None,
             profile: Optional[GameProfile] = None, with_levels: bool = False) -> IngestReport:
    """Ingest an ``.ldtk`` file: a GLDtk export (``gldtk_version`` set) through the GLDtk reader,
    anything else as a foreign project through the name mapping.  *level* picks one level."""
    p = Path(path)
    rep = IngestReport()
    if p.name in SKIP_FILES:
        rep.skipped_files[p.name] = SKIP_FILES[p.name]
        return rep
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        rep.skipped_files[p.name] = f"unreadable: {exc}"
        return rep
    from layout.ldtk_reader import all_levels, read_level

    levels = all_levels(doc)
    sel = [i for i, lv in enumerate(levels)
           if level is None or level in (lv.get("identifier"), lv.get("iid"), str(i))]
    if not sel:
        rep.skipped_files[p.name] = f"no level {level!r}"
        return rep

    own = any((f.get("__identifier") == "gldtk_version") for lv in levels
              for f in lv.get("fieldInstances") or [])
    n0 = rep.added
    if own:
        for i in sel:
            rl = read_level(doc, i)
            rep.merge(ingest_grid(store, rl.grid, rl.entities, source=source or "gldtk",
                                  license=license or GLDTK_LICENSE, origin_path=str(p),
                                  level=rl.identifier, profile=profile))
            if with_levels and ingest_level(
                    store, rl.grid, rl.entities, source=source or "gldtk",
                    license=license or GLDTK_LICENSE, origin_path=str(p), level=rl.identifier,
                    prompt=rl.prompt or "", sections=rl.sections, profile=profile):
                rep.levels_added += 1
    else:
        subset = dict(doc)
        subset["levels"] = [levels[i] for i in sel]
        for ident, grid, ents in _raw_levels(subset, p.name, rep):
            rep.merge(ingest_grid(store, grid, ents, source=source or "ldtk-samples",
                                  license=license or SAMPLE_LICENSE, origin_path=str(p),
                                  level=ident, profile=profile))
    n = rep.added - n0
    if n or rep.duplicates or rep.levels_added:
        rep.mapped_files[p.name] = n
    else:
        rep.skipped_files[p.name] = "no level mapped"
    return rep


def ingest_samples(store: PatternStore, directory: Any, profile: Optional[GameProfile] = None) -> IngestReport:
    """Ingest every ``*.ldtk`` directly in *directory* (LDtk's samples)."""
    rep = IngestReport()
    for p in sorted(Path(directory).glob("*.ldtk")):
        rep.merge(add_path(store, p, profile=profile))
    return rep


__all__ = ["IngestReport", "crop_window", "grid_hash", "recheck", "OPEN_CORPORA_HOOK", "SKIP_FILES", "accept_features", "add_path",
           "guess_start_exit", "ingest_grid", "ingest_level", "thumbnail", "ingest_samples", "map_intgrid_values", "window_features"]
