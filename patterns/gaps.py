"""Fill the stamping gaps of the pattern library (v4 Part 29g): ``python -m patterns.build --gaps``.

The normal ingest (Part 26e) keeps at most ``WINDOWS_PER_LEVEL`` windows per game level, cut at
20 and 32 rows, so the rare structure types (labyrinth, arena, hub) and tall parkour windows are
mostly cut away.  This pass re-reads the same licensed game levels (SuperTux CC-BY-SA, Open
Surge CC-BY; ``patterns.sources``) with denser cuts and more sizes (``GAP_SIZES``, up to the
36-row ASCII limit) and keeps **only** windows that fill a gap:

* the geometry classifier (``patterns/classify.py``) types it labyrinth, arena or hub with
  confidence >= ``MIN_CONFIDENCE``; or
* it is parkour (heuristic or geometry type) and at least ``TALL_ROWS`` rows high, so it can
  be stamped into 26-40-row sections alone or stacked.

Every other rule of :func:`patterns.ingest.ingest_grid` still applies (quality, a Start -> Exit
route, no soft-lock, near-duplicate hash).  Per level and type at most ``PER_LEVEL`` windows are
kept, and a window overlapping a kept one of the same type by more than ``MAX_OVERLAP`` of its
area is dropped (the dense cuts see the same room many times).  No synthetic windows.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from patterns.classify import MIN_CONFIDENCE

GAP_GEO = ("labyrinth", "arena", "hub")
TALL_ROWS = 26
GAP_SIZES = ((20, 32, 8), (26, 40, 10), (32, 48, 12), (36, 46, 14))
PER_LEVEL = 6
MAX_OVERLAP = 0.5
GAME_SOURCES = ("supertux", "opensurge")  # Frogatto is NC: skipped (Part 26d)


def gap_type(f: Dict[str, Any]) -> Optional[str]:
    """The gap a window with features *f* fills, or None."""
    geo = f.get("structure_geo") or {}
    t, conf = geo.get("type"), float(geo.get("confidence") or 0)
    if t in GAP_GEO and conf >= MIN_CONFIDENCE:
        return str(t)
    if int(f.get("height", 0)) >= TALL_ROWS and "parkour" in (f.get("structure"), t):
        return "parkour"
    return None


class GapKeeper:
    """The ``keep`` hook of ``ingest_grid`` for one level: gap windows only, spread out."""

    def __init__(self, per_level: int = PER_LEVEL) -> None:
        self.per_level = per_level
        self.kept: Dict[str, List[Tuple[int, int, int, int]]] = {}

    def __call__(self, f: Dict[str, Any], r0: int, c0: int, win: List[List[Any]]) -> Optional[str]:
        t = gap_type(f)
        if t is None:
            return "fills no gap"
        boxes = self.kept.setdefault(t, [])
        if len(boxes) >= self.per_level:
            return f"gap: {t} cap per level"
        box = (r0, c0, r0 + len(win), c0 + len(win[0]))
        area = len(win) * len(win[0])
        for b in boxes:
            dr = min(box[2], b[2]) - max(box[0], b[0])
            dc = min(box[3], b[3]) - max(box[1], b[1])
            if dr > 0 and dc > 0 and dr * dc > MAX_OVERLAP * area:
                return f"gap: overlaps a kept {t} window"
        boxes.append(box)
        f["gap"] = t
        return None


def ingest_gaps(store: Any, games: Tuple[str, ...] = GAME_SOURCES, *,
                max_levels: Optional[int] = None, profile: Any = None,
                root: Any = None) -> Tuple[Any, Dict[str, int]]:
    """Add gap windows of *games* to *store* (not saved).  ``(IngestReport, added per gap type)``."""
    from pathlib import Path

    from patterns.ingest import IngestReport, ingest_grid
    from patterns.sources import MAX_FILL, SourceReport, ensure_download, iter_source

    irep = IngestReport()
    by_type: Dict[str, int] = {}
    for game in games:
        groot = Path(root) / game if root else ensure_download(game)
        if not groot.is_dir():
            continue
        srep = SourceReport(game)
        for lv in iter_source(game, Path(groot), srep, max_levels):
            before = set(store.patterns)
            r = ingest_grid(store, lv.grid, lv.entities, source=game, license=lv.license,
                            origin_path=lv.origin_path, level=lv.level, profile=profile,
                            attribution=lv.attribution, max_fill=MAX_FILL,
                            win_sizes=GAP_SIZES, keep=GapKeeper())
            irep.merge(r)
            for pid in set(store.patterns) - before:
                t = (store.patterns[pid].get("features") or {}).get("gap")
                if t:
                    by_type[t] = by_type.get(t, 0) + 1
    return irep, by_type


__all__ = ["GAP_GEO", "GAP_SIZES", "GapKeeper", "PER_LEVEL", "TALL_ROWS", "gap_type", "ingest_gaps"]
