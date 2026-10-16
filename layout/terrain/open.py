"""Open — platforms in the open air (the behaviour before Part 21).

Nothing is carved: the section keeps the LLM's platforms, laid out by
``layout/spec_layout.py``, then the comfort pass and densify fill it in.  The
generator only exists so every structure type has a module.
"""

from __future__ import annotations

from .base import Canvas, TerrainContext, TerrainResult


def generate(ctx: TerrainContext, cv: Canvas) -> TerrainResult:  # pragma: no cover - trivial
    return TerrainResult()
