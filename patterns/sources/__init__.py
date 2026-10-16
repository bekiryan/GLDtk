"""Open-licensed game levels for the pattern library (v4 Part 26d).

Each converter (``patterns.sources.<game>``) reads one game's level format from a downloaded
checkout and yields :class:`ConvertedLevel` grids (GLDtk tiles: EMPTY / SOLID / HAZARD at 16 px)
with Start / Exit / Enemy / Pickup positions.  Downloads are *data*: they are parsed here and
never executed.  The licence comes first: a level (or a whole folder) whose licence is unknown,
non-commercial or no-derivatives is skipped and reported.

Downloads live in ``~/Library/Application Support/GLDtk/sources/<game>/`` (``GLDTK_SOURCES_DIR``
overrides it); the converted patterns go to the pattern index, never to git.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from layout.ir import IREntity, TileValue

TILE = 16
WINDOWS_PER_LEVEL = 40  # evenly spread over a level; a converter module may override it
MAX_FILL = 0.7  # windows that are mostly rock are dropped
MANIFEST_PATH = Path(__file__).with_name("manifest.json")

# Licences whose level data may be reused (also commercially) with attribution / share-alike.
_ALLOWED_SPDX = {
    "CC0-1.0", "CC-BY-3.0", "CC-BY-4.0", "CC-BY-SA-3.0", "CC-BY-SA-4.0",
    "GPL-2.0-or-later", "GPL-2.0-only", "GPL-3.0-or-later", "GPL-3.0-only", "MIT", "Zlib",
}


def sources_dir() -> Path:
    env = os.environ.get("GLDTK_SOURCES_DIR")
    if env:
        return Path(env)
    return Path.home() / "Library" / "Application Support" / "GLDtk" / "sources"


def _one_spdx(text: str) -> Optional[str]:
    t = text.strip().lower().replace("_", "-")
    t = re.sub(r"\s+international$", "", t)
    if not t:
        return None
    if re.search(r"(\bnc\b|non-?commercial|\bnd\b|no-?deriv)", t):
        return None  # non-commercial or no-derivatives: not reusable here
    ver = re.search(r"(\d)(?:\.(\d))?", t)
    v = f"{ver.group(1)}.{ver.group(2) or '0'}" if ver else None
    if re.fullmatch(r"cc[\s-]?0(?:[\s-]?1\.0)?|public[\s-]domain|cc[\s-]?zero", t):
        return "CC0-1.0"
    m = re.fullmatch(r"cc[\s-]?by([\s-]?sa)?[\s-]*(\d\.\d)?", t)
    if m and (v in ("3.0", "4.0")):
        return f"CC-BY{'-SA' if m.group(1) else ''}-{v}"
    m = re.fullmatch(r"(?:gnu\s+)?gpl\s*v?(\d)(?:\.0)?\s*(\+|or later|or-later|only)?", t)
    if m and m.group(1) in ("2", "3"):
        return f"GPL-{m.group(1)}.0-{'only' if m.group(2) == 'only' else 'or-later'}"
    if t in ("mit", "zlib"):
        return {"mit": "MIT", "zlib": "Zlib"}[t]
    return None


def spdx_of(text: Optional[str]) -> Optional[str]:
    """The SPDX expression of a licence string from a level file, or None when it is unknown,
    non-commercial or no-derivatives.  Dual licences ("GPL 2+ / CC-by-sa 3.0") become
    ``A OR B`` when every part is reusable."""
    if not text:
        return None
    parts = [p for p in re.split(r"\s*(?:/|\bor\b|,)\s*", text.strip(), flags=re.I) if p]
    out = [_one_spdx(p) for p in parts]
    if not out or any(s is None or s not in _ALLOWED_SPDX for s in out):
        return None
    return " OR ".join(dict.fromkeys(out))  # type: ignore[arg-type]


@dataclass
class ConvertedLevel:
    level: str  # identifier (file stem / sector)
    grid: List[List[TileValue]]
    entities: List[IREntity]
    license: str  # SPDX
    attribution: str
    origin_path: str
    title: str = ""


@dataclass
class SourceReport:
    """What one converter found: included levels, skipped files/folders and why."""
    game: str
    included: List[str] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)
    licenses: Dict[str, int] = field(default_factory=dict)  # SPDX -> included levels

    def include(self, name: str, spdx: str) -> None:
        self.included.append(name)
        self.licenses[spdx] = self.licenses.get(spdx, 0) + 1


def empty_grid(w: int, h: int) -> List[List[TileValue]]:
    return [[TileValue.EMPTY] * w for _ in range(h)]


def fill_box(grid: List[List[TileValue]], x0: float, y0: float, w: float, h: float,
             value: TileValue, *, overwrite_solid: bool = True) -> None:
    """Fill the 16 px cells a pixel box covers (at least one cell)."""
    gh, gw = len(grid), len(grid[0]) if grid else 0
    c0, r0 = int(x0 // TILE), int(y0 // TILE)
    c1 = max(c0, int((x0 + w - 1) // TILE))
    r1 = max(r0, int((y0 + h - 1) // TILE))
    for r in range(max(0, r0), min(gh, r1 + 1)):
        for c in range(max(0, c0), min(gw, c1 + 1)):
            if overwrite_solid or grid[r][c] == TileValue.EMPTY:
                grid[r][c] = value


def entity_at(kind: str, x: float, y: float, w: int, h: int, *, feet: bool = True) -> Optional[IREntity]:
    """An entity for a pixel position (top-left of a ~32 px object): the cell of its feet."""
    col = int((x + TILE / 2) // TILE)
    row = int((y + (TILE * 2 - 1 if feet else 0)) // TILE)
    if 0 <= col < w and 0 <= row < h:
        return IREntity(kind, col, row)
    return None


def settle(grid: List[List[TileValue]], ents: List[IREntity], max_drop: int = 12) -> List[IREntity]:
    """Entities moved out of solid cells (upwards) and down onto the floor below them (at most
    *max_drop* cells), so a Start / Exit stands on something."""
    h = len(grid)
    out = []
    for e in ents:
        r, c = e.row, e.col
        while 0 < r and grid[r][c] != TileValue.EMPTY:
            r -= 1
        d = 0
        while r + 1 < h and grid[r + 1][c] == TileValue.EMPTY and d < max_drop:
            r += 1
            d += 1
        if r + 1 < h and grid[r + 1][c] == TileValue.EMPTY:
            r = e.row  # nothing below within reach: keep the original row
        out.append(IREntity(e.entity_type, c, r, dict(e.metadata or {})))
    return out


def trim(grid: List[List[TileValue]], ents: List[IREntity], pad: int = 2
         ) -> Tuple[List[List[TileValue]], List[IREntity]]:
    """Crop a grid to the bounding box of its non-empty cells (plus *pad*)."""
    rows = [r for r, row in enumerate(grid) if any(int(v) for v in row)]
    cols = [c for c in range(len(grid[0])) if any(int(row[c]) for row in grid)] if grid else []
    if not rows or not cols:
        return grid, ents
    r0, r1 = max(0, rows[0] - pad), min(len(grid), rows[-1] + 1 + pad)
    c0, c1 = max(0, cols[0] - pad), min(len(grid[0]), cols[-1] + 1 + pad)
    out = [row[c0:c1] for row in grid[r0:r1]]
    oents = [IREntity(e.entity_type, e.col - c0, e.row - r0, dict(e.metadata or {}))
             for e in ents if c0 <= e.col < c1 and r0 <= e.row < r1]
    return out, oents


def git_clone(url: str, dest: Path, sparse: Optional[List[str]] = None) -> None:
    """Shallow clone *url* into the new folder *dest* (``git`` only reads data; nothing from the
    download is executed).  *sparse* limits the checkout to these patterns."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if sparse:
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", url, str(dest)],
                       check=True)
        subprocess.run(["git", "-C", str(dest), "sparse-checkout", "set", "--no-cone", *sparse], check=True)
    else:
        subprocess.run(["git", "clone", "--depth", "1", url, str(dest)], check=True)


def checkout_commit(dest: Path) -> Optional[str]:
    try:
        return subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def load_manifest() -> Dict[str, Any]:
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"games": []}


def converter(game: str) -> Any:
    """The converter module of *game* (``supertux`` | ``opensurge`` | ``frogatto``)."""
    import importlib

    if game not in GAMES:
        raise ValueError(f"unknown source {game!r} ({' | '.join(GAMES)})")
    return importlib.import_module(f"patterns.sources.{game}")


GAMES = ("supertux", "opensurge", "frogatto")


def ensure_download(game: str, root: Optional[Path] = None) -> Path:
    """The checkout folder of *game*, cloned (shallow) into a new folder when missing."""
    mod = converter(game)
    dest = (root or sources_dir()) / game
    if not dest.exists():
        git_clone(mod.URL, dest, getattr(mod, "SPARSE", None))
    return dest


def iter_source(game: str, root: Path, report: SourceReport,
                max_levels: Optional[int] = None) -> Iterator[ConvertedLevel]:
    yield from converter(game).iter_levels(root, report, max_levels=max_levels)


def ingest_source(store: Any, game: str, root: Optional[Path] = None, *,
                  max_levels: Optional[int] = None, download: bool = True,
                  with_levels: bool = True, profile: Any = None) -> Tuple[SourceReport, Any]:
    """Convert *game*'s levels (from *root*, or the downloaded checkout) and ingest them into
    *store* (windows + whole-level entries; not saved).  Returns ``(SourceReport, IngestReport)``;
    ``IngestReport.mapped_files`` maps each level to its windows added."""
    from patterns.ingest import IngestReport, ingest_grid, ingest_level

    rep = SourceReport(game)
    irep = IngestReport()
    if root is None:
        root = ensure_download(game) if download else sources_dir() / game
    if not Path(root).is_dir():
        rep.skipped[game] = f"not downloaded ({root})"
        return rep, irep
    for lv in iter_source(game, Path(root), rep, max_levels):
        r = ingest_grid(store, lv.grid, lv.entities, source=game, license=lv.license,
                        origin_path=lv.origin_path, level=lv.level, profile=profile,
                        attribution=lv.attribution, max_fill=MAX_FILL,
                        max_windows=getattr(converter(game), "WINDOWS_PER_LEVEL", WINDOWS_PER_LEVEL))
        irep.merge(r)
        irep.mapped_files[lv.level] = r.added
        if with_levels and ingest_level(store, lv.grid, lv.entities, source=game, license=lv.license,
                                        origin_path=lv.origin_path, level=lv.level, profile=profile,
                                        attribution=lv.attribution, title=lv.title):
            irep.levels_added += 1
    return rep, irep


def attribution_text(store: Any) -> Dict[str, Any]:
    """Attribution for every open-licensed level in *store*: one line per source level, grouped
    by game, plus the manifest's per-game notes."""
    games: Dict[str, Dict[str, Any]] = {}
    man = {g["game"]: g for g in load_manifest().get("games", [])}
    for rec in store:
        att = rec.get("attribution")
        if not att:
            continue
        g = games.setdefault(rec["source"], {"game": rec["source"], "levels": {}, "patterns": 0})
        g["levels"][rec["origin_path"]] = {"attribution": att, "license": rec["license"]}
        g["patterns"] += 1
    lines = ["GLDtk pattern library: level structure (collision layout only, no art) derived from "
             "these open-licensed games. Patterns built from them carry the same licence."]
    out = []
    for name in sorted(games):
        g = games[name]
        m = man.get(name, {})
        head = f"{m.get('title', name)} ({m.get('url', '')})"
        if m.get("commit"):
            head += f", commit {m['commit'][:10]}"
        lines.append("")
        lines.append(head)
        for path, v in sorted(g["levels"].items()):
            lines.append(f"  - {v['attribution']} [{path}]")
        out.append({"game": name, "title": m.get("title", name), "url": m.get("url"),
                    "commit": m.get("commit"), "patterns": g["patterns"],
                    "levels": [{"origin_path": p, **v} for p, v in sorted(g["levels"].items())]})
    return {"text": "\n".join(lines), "sources": out}


__all__ = ["ConvertedLevel", "GAMES", "SourceReport", "TILE", "attribution_text", "checkout_commit",
           "converter", "empty_grid", "ensure_download", "entity_at", "fill_box", "git_clone",
           "ingest_source", "iter_source", "load_manifest", "settle", "sources_dir", "spdx_of",
           "trim"]
