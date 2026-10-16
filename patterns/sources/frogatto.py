"""Frogatto & Friends: licence check only.

Frogatto's ``LICENSE`` puts most files under CC-BY 3.0 but exempts ``data/levels`` (all level
files), which are CC-BY-NC-SA 4.0: non-commercial, so they are never converted here.  The
checkout is sparse (top-level files only) so the level data is not even downloaded.  If a future
``LICENSE`` frees ``data/levels``, the gate below lets it through and a converter for the FSON
``.cfg`` tile layers can be added.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, Optional

from patterns.sources import ConvertedLevel, SourceReport

GAME = "frogatto"
URL = "https://github.com/frogatto/frogatto"
SPARSE = ["/*", "!/*/"]  # top-level files (LICENSE, README) only
LEVEL_DIR = "data/levels"


def level_folder_license(license_text: str, folder: str = LEVEL_DIR) -> Optional[str]:
    """The licence *license_text* gives *folder*: ``None`` when the folder is listed under a
    non-commercial / unclear exemption (or the text is empty)."""
    if not license_text.strip():
        return None
    for para in re.split(r"\n\s*\n", license_text):
        if folder in para or "level files" in para.lower():
            return None
    # the folder is not exempt; the blanket licence is in the first paragraph
    first = license_text.strip().split("\n", 1)[0]
    m = re.search(r"CC-BY(?:-SA)?\s*\d\.\d", first)
    return m.group(0).replace(" ", "-") if m else None


def _exempt_reason(license_text: str) -> str:
    for para in re.split(r"\n\s*\n", license_text):
        m = re.search(r"CC-BY-NC[-\w. ]*", para)
        if m and LEVEL_DIR in license_text:
            return f"{LEVEL_DIR} is {m.group(0).strip()} (non-commercial) per LICENSE"
    return f"{LEVEL_DIR} licence unclear"


def iter_levels(root: Path, report: SourceReport, max_levels: Optional[int] = None
                ) -> Iterator[ConvertedLevel]:
    lic = root / "LICENSE"
    text = lic.read_text(encoding="utf-8", errors="replace") if lic.is_file() else ""
    if level_folder_license(text) is None:
        report.skipped[LEVEL_DIR + "/"] = _exempt_reason(text) if text else "no LICENSE file"
        return
    report.skipped[LEVEL_DIR + "/"] = "licence looks reusable but no FSON converter yet"
    return
    yield  # pragma: no cover


__all__ = ["GAME", "URL", "iter_levels", "level_folder_license"]
