"""Completeness loop: meet every requirement before layout, spending as few LLM calls as possible.

    0. ``from_prompt``   — deterministic: requirements the prompt states outright
       (structure types it names, a key and lock) that the LLM left out
    1. ``fill_missing``  — deterministic: add missing entities, start items, size
    2. ``check_spec``    — anything still ``missing`` (a missing section, a
       Start / Exit count, gravity) → **one** LLM re-prompt (``reprompt``),
       then fill + check again.  Never more than one re-prompt; whatever is
       still missing stays ``missing`` in the report.

The Gemini free tier allows about 20 requests/day, so the re-prompt is the
last resort and a failed re-prompt never fails the build: the filled spec
from step 1 is kept and a warning is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from fidelity import FidelityReport, check_spec, fill_missing
from fidelity.prompt import from_prompt
from game.profile import GameProfile
from spec.level_spec import LevelSpec

Reprompt = Callable[[LevelSpec, List[str]], LevelSpec]


@dataclass
class CompletenessResult:
    spec: LevelSpec
    report: FidelityReport  # check_spec on the returned spec
    filled: List[str] = field(default_factory=list)  # requirement ids auto-filled
    entities_added: int = 0
    reprompts: int = 0
    notes: List[str] = field(default_factory=list)  # warnings for the response


def missing_lines(report: FidelityReport) -> List[str]:
    """One line per missing requirement, for the COMPLETENESS CHECK FAILED message."""
    return [f'{i.id} "{i.text}": {i.reason}' for i in report.missing]


def ensure_complete(
    spec: LevelSpec,
    profile: Optional[GameProfile] = None,
    reprompt: Optional[Reprompt] = None,
    prompt: Optional[str] = None,
) -> CompletenessResult:
    """Fill, check, and (at most once) re-prompt; see the module docstring.

    *reprompt* ``(spec, missing_lines) -> LevelSpec`` is the LLM call (None =
    deterministic fill only).  *prompt* is the user's description (None = no
    prompt requirements are added).
    """
    profile = profile or GameProfile()
    asked = from_prompt(spec, prompt)
    fill = fill_missing(asked.spec)
    out = CompletenessResult(
        spec=fill.spec,
        report=check_spec(fill.spec, profile, fill.filled),
        filled=sorted(set(asked.added) | set(fill.filled)),
        entities_added=fill.entities_added,
        notes=asked.notes + list(fill.notes),
    )
    if not out.report.missing or reprompt is None:
        return out

    lines = missing_lines(out.report)
    out.reprompts = 1
    try:
        answer = reprompt(out.spec, lines)
    except Exception as exc:  # keep the valid, filled spec
        out.notes.append(f"completeness re-prompt failed ({exc}); kept the filled spec")
        return out

    asked = from_prompt(answer, prompt)
    fill = fill_missing(asked.spec)
    out.spec = fill.spec
    out.filled = sorted(set(out.filled) | set(asked.added) | set(fill.filled))
    out.entities_added += fill.entities_added
    out.notes += asked.notes + fill.notes
    out.report = check_spec(out.spec, profile, out.filled)
    still = [i.id for i in out.report.missing]
    if still:
        out.notes.append(f"completeness: still missing after one re-prompt: {still}")
    return out
