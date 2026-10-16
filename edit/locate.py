"""Resolve a text location ("the gap before the boss") to a tile rect.

Nothing is written: the sidebar highlights the rect, the user confirms, and
then calls ``/edit/region`` with it.  The LLM sees the level map (downsampled
for large levels), the sections, the platforms and the entities.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import Field

from llm.providers import LLMProvider
from spec.level_spec import _SpecModel

from .context import EditContext, EditError, Rect, level_view, load_context
from .llm import EditLLMFailed, run_structured
from .prompts import LOCATE_SYSTEM_PROMPT

MAX_LISTED_PLATFORMS = 120
MIN_SIDE = 3


class LocateResult(_SpecModel):
    col: int = Field(..., description="Left column (level tiles).")
    row: int = Field(..., description="Top row (level tiles, 0 = top).")
    w: int = Field(..., description="Width in tiles.")
    h: int = Field(..., description="Height in tiles.")
    explanation: str = Field(..., description="One sentence: what the rectangle covers.")


@dataclass
class LocateOutcome:
    success: bool
    rect: Optional[Rect] = None
    explanation: str = ""
    error: Optional[str] = None
    notes: Optional[List[str]] = None


def build_locate_messages(ctx: EditContext, text: str) -> List[Dict[str, Any]]:
    view, factor = level_view(ctx)
    lines = [f"LOCATION: {text.strip()}", "",
             f"LEVEL: {ctx.width} × {ctx.height} tiles (col 0 = left, row 0 = top)."]
    if ctx.level.prompt:
        lines.append(f"Generated from: \"{ctx.level.prompt.strip()}\"")
    lines.append("SECTIONS:")
    for b in ctx.bands:
        lines.append(f"  - {b.id} \"{b.name}\" ({b.theme or 'default'}, {b.difficulty}): columns "
                     f"{b.col_start}..{b.col_end}, rows {b.row_start}..{b.row_end}")
    lines.append("ENTITIES:")
    for e in ctx.entities:
        m = e.metadata
        sub = m.get("archetype") or m.get("pickup_kind") or m.get("interactive_kind") or \
            m.get("weapon_kind") or m.get("name") or ""
        desc = m.get("description") or ""
        lines.append(f"  - {e.entity_type}{' ' + str(sub) if sub else ''} at column {e.col}, row "
                     f"{e.row}{': ' + json.dumps(desc, ensure_ascii=False) if desc else ''}")
    spans = ctx.extract(ctx.grid)
    plats = sorted(spans.rects.values())
    lines.append("PLATFORMS (row, columns):")
    for row, c0, c1 in plats[:MAX_LISTED_PLATFORMS]:
        lines.append(f"  - row {row}: columns {c0}..{c1}")
    if len(plats) > MAX_LISTED_PLATFORMS:
        lines.append(f"  … {len(plats) - MAX_LISTED_PLATFORMS} more")
    scale = "" if factor == 1 else f" (downsampled: each character is {factor}×{factor} tiles; " \
                                   "the row labels are level rows)"
    lines += ["", f"MAP{scale}: # solid, ^ hazard, letters = entities (S start, X exit, E enemy, "
              "P pickup, W weapon, I interactive, N npc, C checkpoint, K key)", view]
    return [{"role": "user", "content": "\n".join(lines)}]


def locate(path: str, text: str, provider: LLMProvider, max_retries: int = 3,
           level: Optional[str] = None) -> LocateOutcome:
    try:
        ctx = load_context(path, level)
    except EditError as exc:
        return LocateOutcome(False, error=str(exc))
    if not text or not text.strip():
        return LocateOutcome(False, error="Describe the place to select.")

    def parse(res: LocateResult):
        notes: List[str] = []
        w = min(max(res.w, MIN_SIDE), ctx.width)
        h = min(max(res.h, MIN_SIDE), ctx.height)
        col = min(max(res.col, 0), ctx.width - w)
        row = min(max(res.row, 0), ctx.height - h)
        if (col, row, w, h) != (res.col, res.row, res.w, res.h):
            notes.append("clamped the rectangle into the level")
        if res.col >= ctx.width or res.row >= ctx.height or res.col + res.w <= 0 or res.row + res.h <= 0:
            return None, [f"The rectangle {res.col},{res.row} {res.w}×{res.h} lies outside the "
                          f"{ctx.width} × {ctx.height} level."], notes
        return (Rect(col, row, w, h), res.explanation.strip()), [], notes

    try:
        run = run_structured(provider, LOCATE_SYSTEM_PROMPT, build_locate_messages(ctx, text),
                             LocateResult, parse, max_retries, what="the location")
    except EditLLMFailed as exc:
        return LocateOutcome(False, error=str(exc))
    except Exception as exc:  # provider errors
        return LocateOutcome(False, error=f"LLM error: {exc}")
    rect, explanation = run.value
    return LocateOutcome(True, rect, explanation, notes=run.notes)


__all__ = ["LocateOutcome", "LocateResult", "build_locate_messages", "locate"]
