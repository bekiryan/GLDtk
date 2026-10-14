"""Regional editing + version history (v2 Part 8a).

The ``.ldtk`` file is the source of truth: every edit reads it
(``context``), regenerates a region, a section or the entities with the LLM
(``region``, ``entities``), re-verifies the whole level (``check``) and
splices the result into the file (``stitch``) after a history snapshot
(``history``).  ``locate`` resolves a text description to a rect without
writing anything.
"""

from .context import EditContext, EditError, Rect, load_context
from .history import list_snapshots, make_label, snapshot, undo
from .outcome import EditOutcome

__all__ = [
    "EditContext",
    "EditError",
    "EditOutcome",
    "Rect",
    "list_snapshots",
    "load_context",
    "make_label",
    "snapshot",
    "undo",
]
