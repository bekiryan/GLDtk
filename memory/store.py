"""Design memory storage: ``events.jsonl`` (append-only, rotated) + ``preferences.json``.

The folder is ``GLDTK_MEMORY_DIR`` or ``~/Library/Application Support/GLDtk/memory``.
Nothing is read or written while ``preferences.json`` has ``enabled: false``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from app_dirs import app_support_dir

ROTATE_BYTES = 5 * 1024 * 1024
EVENTS_FILE = "events.jsonl"
OLD_EVENTS_FILE = "events.1.jsonl"
PREFS_FILE = "preferences.json"
MAX_READ_EVENTS = 3000  # statistics look at the newest events only
SUMMARY_EVERY = 20  # new events between two LLM summaries
_lock = threading.RLock()


def memory_dir() -> Path:
    env = os.environ.get("GLDTK_MEMORY_DIR")
    return Path(env) if env else app_support_dir() / "memory"


def default_prefs() -> Dict[str, Any]:
    return {"enabled": True, "patterns_auto_add": True, "rules": [], "dismissed": [], "stats": {}, "event_count": 0,
            "events_since_summary": 0}


def load_prefs() -> Dict[str, Any]:
    try:
        data = json.loads((memory_dir() / PREFS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    out = default_prefs()
    if isinstance(data, dict):
        out.update(data)
    return out


def save_prefs(prefs: Dict[str, Any]) -> None:
    d = memory_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / PREFS_FILE).write_text(json.dumps(prefs, indent=2, ensure_ascii=False), encoding="utf-8")


def enabled() -> bool:
    return bool(load_prefs().get("enabled", True))


def read_events(limit: int = MAX_READ_EVENTS) -> List[Dict[str, Any]]:
    """The newest *limit* events, oldest first."""
    lines: List[str] = []
    for name in (OLD_EVENTS_FILE, EVENTS_FILE):
        try:
            lines += (memory_dir() / name).read_text(encoding="utf-8").splitlines()
        except OSError:
            pass
    events = []
    for ln in lines[-limit:]:
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


def record(event_type: str, **fields: Any) -> bool:
    """Append one compact event; False (and nothing written) when memory is off."""
    with _lock:
        prefs = load_prefs()
        if not prefs.get("enabled", True):
            return False
        d = memory_dir()
        d.mkdir(parents=True, exist_ok=True)
        ev = {"type": event_type, "ts": round(time.time(), 1)}
        ev.update({k: v for k, v in fields.items() if v not in (None, "", [], {})})
        f = d / EVENTS_FILE
        if f.is_file() and f.stat().st_size > ROTATE_BYTES:
            f.replace(d / OLD_EVENTS_FILE)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n")
        prefs["event_count"] = int(prefs.get("event_count", 0)) + 1
        prefs["events_since_summary"] = int(prefs.get("events_since_summary", 0)) + 1
        save_prefs(prefs)
    from memory import analyze  # late: analyze imports this module

    analyze.refresh()
    return True


def reset() -> None:
    """Delete events and preferences (``enabled`` is kept as it was)."""
    with _lock:
        was = enabled()
        for name in (EVENTS_FILE, OLD_EVENTS_FILE, PREFS_FILE):
            (memory_dir() / name).unlink(missing_ok=True)
        if not was:
            p = default_prefs()
            p["enabled"] = False
            save_prefs(p)
