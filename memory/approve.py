"""Approved levels feed the pattern library (v4 Part 26c).

Every successful GLDtk write marks ``(file, level)`` as *pending*.  The mark turns into an
approval when the level is *kept*: the next GLDtk request on the file (or a memory scan that
sees a saved hand edit) finds it was not undone.  An undo drops the mark.  Approving adds the
file's level as source ``approved`` (licence ``user``) to the pattern index: windows plus one
whole-level entry, deduplicated by file + level + content, unlabelled until the next
``python -m patterns.build`` run (no LLM call here; a hash embedder is run locally so the
entries are retrievable at once).  Nothing happens while memory is off or the
``patterns_auto_add`` preference is false.  Every step is guarded.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from memory import store

PENDING_FILE = "pending_approval.json"
SOURCE = "approved"
LICENSE = "user"


def auto_add() -> bool:
    prefs = store.load_prefs()
    return bool(prefs.get("enabled", True)) and bool(prefs.get("patterns_auto_add", True))


def _read() -> Dict[str, Any]:
    try:
        d = json.loads((store.memory_dir() / PENDING_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d: Dict[str, Any]) -> None:
    f = store.memory_dir() / PENDING_FILE
    if not d:
        f.unlink(missing_ok=True)
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d), encoding="utf-8")


def _key(path: Any) -> str:
    return str(Path(path).resolve())


def mark(path: Any, level: Optional[str] = None) -> None:
    """GLDtk just wrote *path* (*level* None = every level): wait for it to be kept."""
    if not auto_add():
        return
    with store._lock:
        d = _read()
        k = _key(path)
        cur = d.get(k)
        if level is None or (cur is not None and cur.get("levels") is None):
            d[k] = {"levels": None}
        else:
            lv = list((cur or {}).get("levels") or [])
            if level not in lv:
                lv.append(level)
            d[k] = {"levels": lv}
        _write(d)


def discard(path: Any) -> None:
    """The write was undone: it is not kept."""
    with store._lock:
        d = _read()
        if d.pop(_key(path), None) is not None:
            _write(d)


def approve(path: Any) -> Dict[str, Any]:
    """Ingest the pending levels of *path* (if any) and clear the mark.  Never raises."""
    res: Dict[str, Any] = {"added": 0, "levels": 0}
    try:
        with store._lock:
            d = _read()
            ent = d.pop(_key(path), None)
            if ent is None:
                return res
            _write(d)
        if not auto_add() or not Path(path).is_file():
            return res
        from patterns.embed import HashEmbedder, sync_vectors
        from patterns.ingest import add_path
        from patterns.store import PatternStore

        ps = PatternStore()
        levels = ent.get("levels")
        for lv in (levels if levels else [None]):
            rep = add_path(ps, path, lv, source=SOURCE, license=LICENSE, with_levels=True)
            res["added"] += rep.added
            res["levels"] += rep.levels_added
        if res["added"] or res["levels"]:
            if ps.embedder is None or ps.embedder.get("name") == "hash":
                sync_vectors(ps, HashEmbedder())
            ps.save()
    except Exception as exc:
        res["error"] = str(exc)
    return res
