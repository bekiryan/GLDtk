"""The per-user GLDtk folder (settings, ``.env``, memory, patterns, sources).

macOS ``~/Library/Application Support/GLDtk``, Windows ``%APPDATA%\\GLDtk``, elsewhere
``$XDG_DATA_HOME/GLDtk`` (default ``~/.local/share/GLDtk``). The packaged app's Electron
``userData`` folder is the same one.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_support_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "GLDtk"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "GLDtk"
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "GLDtk"
