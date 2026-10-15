"""The local pattern index: one JSON file with the patterns, their labels and their vectors.

A few thousand entries at most, so plain JSON plus a pure-Python cosine search is enough.
Default location ``~/Library/Application Support/GLDtk/patterns/``; ``GLDTK_PATTERNS_DIR``
overrides it (the tests point it at a temp dir).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

INDEX_VERSION = 1


def patterns_dir() -> Path:
    env = os.environ.get("GLDTK_PATTERNS_DIR")
    if env:
        return Path(env)
    return Path.home() / "Library" / "Application Support" / "GLDtk" / "patterns"


class PatternStore:
    """``patterns`` (id -> record), ``vectors`` (id -> {text_hash, vec}) and the embedder used."""

    def __init__(self, directory: Optional[Path] = None) -> None:
        self.dir = Path(directory) if directory else patterns_dir()
        self.path = self.dir / "index.json"
        self.patterns: Dict[str, Dict[str, Any]] = {}
        self.vectors: Dict[str, Dict[str, Any]] = {}
        self.embedder: Optional[Dict[str, Any]] = None  # {"name", "dim"}
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.patterns = dict(doc.get("patterns") or {})
        self.vectors = dict(doc.get("vectors") or {})
        self.embedder = doc.get("embedder")

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        doc = {"version": INDEX_VERSION, "embedder": self.embedder,
               "patterns": self.patterns, "vectors": self.vectors}
        fd, tmp = tempfile.mkstemp(prefix=".index-", suffix=".json", dir=self.dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, self.path)

    def __len__(self) -> int:
        return len(self.patterns)

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self.patterns.values())

    def get(self, pid: str) -> Optional[Dict[str, Any]]:
        return self.patterns.get(pid)

    def add(self, record: Dict[str, Any]) -> bool:
        """Add a pattern; False when its id is already present (the old record is kept)."""
        if record["id"] in self.patterns:
            return False
        self.patterns[record["id"]] = record
        return True

    def stats(self) -> Dict[str, Any]:
        recs = list(self.patterns.values())
        by_source: Dict[str, int] = {}
        for r in recs:
            by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        return {
            "patterns": len(recs),
            "accepted": sum(1 for r in recs if r.get("accepted")),
            "labelled": sum(1 for r in recs if r.get("label")),
            "embedded": len(self.vectors),
            "embedder": self.embedder,
            "by_source": by_source,
            "dir": str(self.dir),
        }


__all__ = ["INDEX_VERSION", "PatternStore", "patterns_dir"]
