"""Version history: ``.ldtk`` snapshots taken before every GLDtk write.

Layout::

    <ldtk_dir>/.gldtk_history/<ldtk stem>/
        20261014T153012_123456Z.ldtk     one snapshot per write (UTC timestamp)
        index.json                       [{file, timestamp, label}], oldest first

A snapshot is a byte-for-byte copy of the file as it was *before* the write,
so :func:`undo` restores it exactly.  At most :data:`MAX_SNAPSHOTS` are kept
(the oldest are deleted).  Undo restores the newest snapshot and removes it,
so repeated undo walks back through the history.

The index is the source of the labels only: snapshot files without an index
entry still count (label ``""``), and index entries whose file is gone are
dropped.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Union

HISTORY_DIR = ".gldtk_history"
INDEX_FILE = "index.json"
MAX_SNAPSHOTS = 30
LABEL_PROMPT_CHARS = 60

PathLike = Union[str, Path]


@dataclass
class Snapshot:
    file: Path
    timestamp: str  # ISO 8601, UTC
    label: str

    def as_dict(self) -> Dict[str, str]:
        return {"file": str(self.file), "timestamp": self.timestamp, "label": self.label}


@dataclass
class UndoResult:
    success: bool
    restored_from: Optional[str] = None
    remaining: int = 0
    error: Optional[str] = None


def history_dir(ldtk_path: PathLike) -> Path:
    p = Path(ldtk_path)
    return p.parent / HISTORY_DIR / p.stem


def make_label(mode: str, prompt: Optional[str] = None) -> str:
    """``"region: make the gap wider…"`` — the edit mode and the start of the prompt."""
    text = " ".join((prompt or "").split())
    if len(text) > LABEL_PROMPT_CHARS:
        text = text[: LABEL_PROMPT_CHARS - 1].rstrip() + "…"
    return f"{mode}: {text}" if text else mode


def _read_index(folder: Path) -> List[Dict[str, str]]:
    try:
        data = json.loads((folder / INDEX_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [d for d in data if isinstance(d, dict) and d.get("file")] if isinstance(data, list) else []


def _write_index(folder: Path, entries: List[Dict[str, str]]) -> None:
    (folder / INDEX_FILE).write_text(json.dumps(entries, indent=2), encoding="utf-8")


def list_snapshots(ldtk_path: PathLike) -> List[Snapshot]:
    """Every snapshot of *ldtk_path*, oldest first."""
    folder = history_dir(ldtk_path)
    if not folder.is_dir():
        return []
    labels = {d["file"]: d for d in _read_index(folder)}
    snaps = []
    for f in sorted(folder.glob("*.ldtk")):  # timestamp names sort chronologically
        entry = labels.get(f.name, {})
        snaps.append(Snapshot(f, entry.get("timestamp") or _timestamp_of(f.name), entry.get("label", "")))
    return snaps


def _timestamp_of(name: str) -> str:
    try:
        dt = datetime.strptime(name.split(".")[0], "%Y%m%dT%H%M%S_%fZ")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return ""


def _sync_index(folder: Path, snaps: List[Snapshot]) -> None:
    _write_index(folder, [{"file": s.file.name, "timestamp": s.timestamp, "label": s.label} for s in snaps])


def snapshot(ldtk_path: PathLike, label: str = "", keep: int = MAX_SNAPSHOTS) -> Optional[Path]:
    """Copy *ldtk_path* into its history folder before it is overwritten.

    Returns the snapshot path, or None when the file does not exist yet (a
    first generate has nothing to snapshot).
    """
    src = Path(ldtk_path)
    if not src.is_file():
        return None
    folder = history_dir(src)
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    name = now.strftime("%Y%m%dT%H%M%S_%fZ") + ".ldtk"
    while (folder / name).exists():  # two writes in the same microsecond
        now = now.replace(microsecond=(now.microsecond + 1) % 1_000_000)
        name = now.strftime("%Y%m%dT%H%M%S_%fZ") + ".ldtk"
    dest = folder / name
    shutil.copyfile(src, dest)

    snaps = list_snapshots(src)
    for s in snaps:
        if s.file == dest:
            s.label, s.timestamp = label, now.isoformat()
    while len(snaps) > keep:
        old = snaps.pop(0)
        old.file.unlink(missing_ok=True)
    _sync_index(folder, snaps)
    return dest


def undo(ldtk_path: PathLike) -> UndoResult:
    """Restore the newest snapshot over *ldtk_path* and remove it."""
    target = Path(ldtk_path)
    snaps = list_snapshots(target)
    if not snaps:
        return UndoResult(False, error=f"No GLDtk history for {target.name}: nothing to undo.")
    newest = snaps.pop()
    shutil.copyfile(newest.file, target)
    newest.file.unlink(missing_ok=True)
    _sync_index(history_dir(target), snaps)
    return UndoResult(True, restored_from=str(newest.file), remaining=len(snaps))


__all__ = [
    "HISTORY_DIR",
    "MAX_SNAPSHOTS",
    "Snapshot",
    "UndoResult",
    "history_dir",
    "list_snapshots",
    "make_label",
    "snapshot",
    "undo",
]
