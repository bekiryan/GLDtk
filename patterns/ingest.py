"""Ingest levels into patterns: read, convert to the GLDtk grid, cut windows, compute features.

Sources:
  * LDtk's sample projects (MIT, ``ldtk-src/LICENSE``): layout structure only (IntGrid
    collision and entity positions/kinds), never their art.  Foreign IntGrid values are
    mapped per layer by name (``SOLID_NAMES`` / ``HAZARD_NAMES``); a layer whose values do not
    map is skipped, and so are the files in ``SKIP_FILES``.
  * GLDtk's own levels (``add_path``: the demo folders and any ``.ldtk`` the user approves).
  * Open corpora: ``OPEN_CORPORA_HOOK`` documents where to add one; none is downloaded, and
    a corpus must have its licence checked first.
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
    "Open corpora (for example VGLC, Mario AI levels): not ingested. Check the corpus licence "
    "first, convert it to a raw grid and call ingest_grid(..., source=<name>, license=<spdx>)."
)

TILE = 16  # GLDtk's tile size; foreign layers of another size are skipped
WIN_W = 32
WIN_STRIDE = 24
WIN_H = 20
MIN_WIN_W = 16
MAX_ASCII_W, MAX_ASCII_H = 40, 24

ENTITY_CHARS = {"Start": "S", "Exit": "X", "Enemy": "E", "Pickup": "P"}
_START_WORDS = ("player", "start", "spawn", "hero")
_ENEMY_WORDS = ("mob", "enemy", "monster")
_PICKUP_WORDS = ("chest", "item", "coin", "gem", "pickup")


@dataclass
class IngestReport:
    added: int = 0
    duplicates: int = 0
    rejected: int = 0  # stored but not accepted (no Start->Exit route in the window)
    skipped_files: Dict[str, str] = field(default_factory=dict)
    mapped_files: Dict[str, int] = field(default_factory=dict)  # file -> patterns added
    notes: List[str] = field(default_factory=list)

    def merge(self, other: "IngestReport") -> None:
        self.added += other.added
        self.duplicates += other.duplicates
        self.rejected += other.rejected
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


def _snap_cuts(grid: Sequence[Sequence[Any]], width: int) -> List[Tuple[int, int]]:
    """Column windows ``(c0, c1)`` (about WIN_W wide, overlapping), cut at vertical walls."""
    if width <= WIN_W + 8:
        return [(0, width)]
    h = len(grid)
    solid = [sum(1 for r in range(h) if int(grid[r][c]) == TileValue.SOLID) for c in range(width)]

    def snap(c: int) -> int:
        lo, hi = max(1, c - 4), min(width - 1, c + 4)
        return max(range(lo, hi + 1), key=lambda x: (solid[x], -abs(x - c)))

    cuts, start = [], 0
    while start < width - MIN_WIN_W:
        end = width if start + WIN_W + 8 >= width else snap(start + WIN_W)
        cuts.append((start, end))
        if end >= width:
            break
        start = max(start + 1, snap(start + WIN_STRIDE))
    return cuts


def _row_cuts(height: int) -> List[Tuple[int, int]]:
    if height <= WIN_H + 6:
        return [(0, height)]
    out, r = [], 0
    while r < height:
        end = min(height, r + WIN_H)
        out.append((max(0, end - WIN_H), end))
        if end >= height:
            break
        r += WIN_H - 6
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
    route = level_route(grid, ents, profile)
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
    return bool(f.get("route", {}).get("ok"))


def _ascii(grid, ents) -> str:
    ir = IRLevel(identifier="p", width_tiles=len(grid[0]), height_tiles=len(grid), tile_size=TILE,
                 grid=[list(r) for r in grid], entities=list(ents))
    return ir.ascii_render(ENTITY_CHARS)


def make_id(source: str, origin: str, level: str, ascii_text: str) -> str:
    return hashlib.sha1(f"{source}|{origin}|{level}|{ascii_text}".encode()).hexdigest()[:12]


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
) -> IngestReport:
    """Cut one level into patterns and add them to *store* (not saved)."""
    profile = profile or GameProfile()
    rep = IngestReport()
    if not grid or not grid[0]:
        return rep
    width = len(grid[0])
    for r0, r1 in _row_cuts(len(grid)):
        for c0, c1 in _snap_cuts(grid[r0:r1], width):
            if c1 - c0 < MIN_WIN_W:
                continue
            win = [[TileValue(int(v)) if int(v) in (0, 1, 2) else TileValue.SOLID for v in row[c0:c1]]
                   for row in grid[r0:r1]]
            if sum(1 for row in win for v in row if int(v)) < 0.08 * (c1 - c0) * (r1 - r0):
                continue
            if not _surface_runs(_standable_cells(win)):
                continue
            wents = [IREntity(e.entity_type, e.col - c0, e.row - r0, {})
                     for e in entities if c0 <= e.col < c1 and r0 <= e.row < r1]
            wents = guess_start_exit(win, wents)
            f = window_features(win, wents, profile)
            text = _ascii(win, wents)
            if len(text.splitlines()) > MAX_ASCII_H or max(len(x) for x in text.splitlines()) > MAX_ASCII_W:
                continue
            ok = accept_features(f)
            rec = {
                "id": make_id(source, origin_path, level, text),
                "source": source, "license": license, "origin_path": origin_path,
                "level": level, "window": {"col": c0, "row": r0, "w": c1 - c0, "h": r1 - r0},
                "ascii": text, "features": f, "accepted": ok, "label": None,
            }
            if store.add(rec):
                rep.added += 1
                rep.rejected += 0 if ok else 1
            else:
                rep.duplicates += 1
    return rep


def add_path(store: PatternStore, path: Any, level: Optional[str] = None, *,
             source: Optional[str] = None, license: Optional[str] = None,
             profile: Optional[GameProfile] = None) -> IngestReport:
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
    else:
        subset = dict(doc)
        subset["levels"] = [levels[i] for i in sel]
        for ident, grid, ents in _raw_levels(subset, p.name, rep):
            rep.merge(ingest_grid(store, grid, ents, source=source or "ldtk-samples",
                                  license=license or SAMPLE_LICENSE, origin_path=str(p),
                                  level=ident, profile=profile))
    n = rep.added - n0
    if n or rep.duplicates:
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


__all__ = ["IngestReport", "OPEN_CORPORA_HOOK", "SKIP_FILES", "accept_features", "add_path",
           "guess_start_exit", "ingest_grid", "ingest_samples", "map_intgrid_values", "window_features"]
