"""Retrieve patterns to stamp into terrain sections (v4 Part 26b).

The retrieval lives here, not in ``layout/``: :func:`find_stamps` asks the pattern
index (Part 26) for each terrain section's best accepted ``kind="pattern"`` entries of
its structure type and hands their grids to ``layout_spec`` (``stamps``).  An empty
index, no embedder or any retrieval error means no stamps (the generators run).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from layout.terrain import is_terrain
from layout.terrain.stamp import pattern_grid
from spec.level_spec import LevelSpec

STAMP_MIN_SCORE = 0.6  # retrieval score a pattern needs to be stamped
MAX_STAMPS = 2  # patterns tried per section before the generator


class StampFinder:
    """Per-section stamp candidates for a prompt, cached by (structure, description)."""

    def __init__(self, description: str, store: Any = None, embedder: Any = None) -> None:
        self.description = description
        self.store = store
        self.embedder = embedder
        self._cache: Dict[tuple, List[Dict[str, Any]]] = {}
        self._disabled = False

    def _hits(self, structure: str, text: str) -> List[Dict[str, Any]]:
        key = (structure, text)
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
                    hits = retrieve(f"{self.description}\n{text}".strip(), MAX_STAMPS * 2,
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
                                    "score": round(h.score, 3)})
                        if len(out) == MAX_STAMPS:
                            break
            except Exception:  # noqa: BLE001 - no embedder / unreadable index: procedural only
                self._disabled = True
                out = []
        self._cache[key] = out
        return out

    def __call__(self, spec: LevelSpec) -> Dict[str, List[Dict[str, Any]]]:
        return {sec.id: got for sec in spec.sections if is_terrain(sec.structure)
                for got in [self._hits(sec.structure or "", sec.description)] if got}


def stamped_patterns(spec_layout: Any, stamps: Optional[Dict[str, List[Dict[str, Any]]]]
                     ) -> List[Dict[str, str]]:
    """``patterns_used`` entries (``"use": "stamped"``) for the patterns stamped in *spec_layout*."""
    out: List[Dict[str, str]] = []
    seen = set()
    for band in spec_layout.sections:
        pid = getattr(band, "pattern", None)
        if not pid or pid in seen:
            continue
        seen.add(pid)
        rec = next((r for r in (stamps or {}).get(band.id, []) if r["id"] == pid), {})
        out.append({"id": pid, "title": str(rec.get("title", pid)), "source": rec.get("source", ""),
                    "license": rec.get("license", ""), "section": band.id, "use": "stamped"})
    return out


__all__ = ["MAX_STAMPS", "STAMP_MIN_SCORE", "StampFinder", "stamped_patterns"]
