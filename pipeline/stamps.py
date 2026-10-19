"""Retrieve patterns to stamp into terrain sections (v4 Part 26b).

The retrieval lives here, not in ``layout/``: :func:`find_stamps` asks the pattern
index (Part 26) for each terrain section's best accepted ``kind="pattern"`` entries of
its structure type and hands their grids to ``layout_spec`` (``stamps``).  An empty
index, no embedder or any retrieval error means no stamps (the generators run).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from layout.terrain import is_terrain
from layout.terrain.stamp import MAX_RESIZE, PAD_MAX, PAD_TYPES, pattern_grid
from spec.level_spec import LevelSpec

STAMP_MIN_SCORE = 0.6  # retrieval score a pattern needs to be stamped
MAX_STAMPS = 3  # patterns tried per section before the generator (Part 29g: was 2)
POOL = 8  # Part 29g: hits retrieved per stamp, so ones that fit the section's height go first


class StampFinder:
    """Per-section stamp candidates for a prompt, cached by (structure, description)."""

    def __init__(self, description: str, store: Any = None, embedder: Any = None) -> None:
        self.description = description
        self.store = store
        self.embedder = embedder
        self._cache: Dict[tuple, List[Dict[str, Any]]] = {}
        self._disabled = False

    def _hits(self, structure: str, text: str, rows: Optional[int] = None) -> List[Dict[str, Any]]:
        """The best stamp candidates; with *rows* (the section's inner height) the ones whose
        height fits it without stacking come first (Part 29g; score order otherwise)."""
        key = (structure, text, rows)
        if key in self._cache:
            return self._cache[key]
        out: List[Dict[str, Any]] = []
        if not self._disabled:
            try:
                from patterns.retrieve import Filters, retrieve
                from patterns.store import PatternStore

                if self.store is None:
                    self.store = PatternStore()
                if not self.store.vectors:
                    self._disabled = True
                else:
                    hits = retrieve(f"{self.description}\n{text}".strip(), MAX_STAMPS * POOL,
                                    Filters(structure_type=structure), store=self.store,
                                    embedder=self.embedder)
                    for h in hits:
                        rec = h.pattern
                        if h.score < STAMP_MIN_SCORE or not rec.get("accepted") \
                                or pattern_grid(rec) is None:
                            continue
                        out.append({"id": h.id, "title": h.title, "grid": pattern_grid(rec),
                                    "source": str(rec.get("source", "")),
                                    "license": str(rec.get("license", "")),
                                    "score": round(h.score, 3),
                                    "geo_structure": structure if _geo_match(rec, structure)
                                    else ""})
                    if rows:
                        out.sort(key=lambda c: not _fits_height(len(c["grid"]), rows, structure))
                    out = out[:MAX_STAMPS]
            except Exception:  # noqa: BLE001 - no embedder / unreadable index: procedural only
                self._disabled = True
                out = []
        self._cache[key] = out
        return out

    def __call__(self, spec: LevelSpec) -> Dict[str, List[Dict[str, Any]]]:
        rows = None if spec.layout_orientation == "vertical" else spec.size.height_tiles - 2
        return {sec.id: got for sec in spec.sections if is_terrain(sec.structure)
                for got in [self._hits(sec.structure or "", sec.description, rows)] if got}


def _fits_height(h: int, rows: int, structure: str) -> bool:
    """Does a window *h* rows high fit *rows* alone (``layout/terrain/stamp.py`` rules)?"""
    pad = PAD_MAX if structure in PAD_TYPES else MAX_RESIZE
    return h - rows <= MAX_RESIZE * rows and rows - h <= max(pad, MAX_RESIZE) * rows


def _geo_match(rec: Dict[str, Any], structure: str) -> bool:
    """Did *rec* match *structure* only through the geometry classifier (Part 26f), i.e.
    its label (or heuristic) names another, generic type?"""
    lab = (rec.get("label") or {}).get("structure_type")
    feats = rec.get("features") or {}
    return (lab or feats.get("structure")) != structure


def stamp_title(rec: Dict[str, Any], pid: str = "") -> str:
    """Sidebar title of a stamped pattern: its own title, or — when it was typed by the
    geometry classifier, so its LLM title may name another structure — ``"<source>
    <structure> room (<w>×<h>)"``."""
    structure = rec.get("geo_structure") or ""
    if not structure:
        return str(rec.get("title", pid))
    grid = rec.get("grid") or []
    size = f" ({len(grid[0])}×{len(grid)})" if grid and grid[0] else ""
    return f"{rec.get('source') or 'Pattern'} {structure} room{size}"


def stamped_patterns(spec_layout: Any, stamps: Optional[Dict[str, List[Dict[str, Any]]]]
                     ) -> List[Dict[str, str]]:
    """``patterns_used`` entries (``"use": "stamped"``) for the patterns stamped in *spec_layout*."""
    out: List[Dict[str, str]] = []
    seen = set()
    for band in spec_layout.sections:
        for pid in (getattr(band, "pattern", None), getattr(band, "stacked", None)):  # Part 26f
            if not pid or pid in seen:
                continue
            seen.add(pid)
            rec = next((r for r in (stamps or {}).get(band.id, []) if r["id"] == pid), {})
            out.append({"id": pid, "title": stamp_title(rec, pid), "source": rec.get("source", ""),
                        "license": rec.get("license", ""), "section": band.id, "use": "stamped"})
    return out


__all__ = ["MAX_STAMPS", "STAMP_MIN_SCORE", "StampFinder", "stamp_title", "stamped_patterns"]
