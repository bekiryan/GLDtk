"""Multi-level worlds: detection, the outline call and the world fidelity report
(v3 Part 17).  The levels themselves are built by ``build_level`` and exported by
``layout.world``; ``server.py`` runs the steps.

When is there an outline call?
------------------------------
``GenerateRequest.levels``:

* ``1``    — never: the single-level pipeline, exactly as before;
* ``2..8`` — always, and the outline must have exactly that many levels;
* ``None`` — only when :func:`mentions_levels` finds a cheap sign in the prompt
  that it asks for several levels ("3 levels", "three stages", "campaign",
  "multi-level", "levels" …).  Then the outline call decides the count; an
  outline of one level falls back to the single-level pipeline with the
  original prompt.  A prompt without such a sign is one level and costs no
  extra LLM call (so "a forest, then a cave" without the word "levels" stays a
  single level with two sections, as before Part 17).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fidelity.checker import SUBKIND_FIELDS, resolve_subkind
from fidelity.report import FidelityItem, FidelityReport
from game.profile import GameProfile
from layout.ir import IRLevel
from llm.prompt import WORLD_SYSTEM_PROMPT, build_outline_messages
from spec.world import MAX_LEVELS, WorldOutline

_NUMBER = r"(?:\d+|two|three|four|five|six|seven|eight|several|multiple|many)"
_WORLD_HINT = re.compile(
    rf"\b{_NUMBER}[\s-]+(?:levels?|stages?|worlds?|maps?|chapters?|acts?)\b"
    r"|\b(?:campaign|multi[\s-]?level|levels|stages|chapters)\b"
    r"|\blevel\s+(?:1|one)\b.*\blevel\s+(?:2|two)\b",
    re.IGNORECASE | re.DOTALL,
)


def mentions_levels(description: str) -> bool:
    """A cheap sign that *description* asks for several levels (see the module doc)."""
    return bool(_WORLD_HINT.search(description or ""))


def wants_outline(description: str, levels: Optional[int]) -> bool:
    """Whether generate makes the outline call (see the module docstring)."""
    if levels is not None:
        return levels > 1
    return mentions_levels(description)


# ─────────────────────────────────────────────────────────────────────────────
# The outline call
# ─────────────────────────────────────────────────────────────────────────────


def _parser(levels: Optional[int]):
    def parse(outline: WorldOutline) -> Tuple[Optional[WorldOutline], List[str], List[str]]:
        notes: List[str] = []
        errors: List[str] = []
        lv = list(outline.levels)
        want = levels
        if want is not None and len(lv) > want:
            notes.append(f"The outline had {len(lv)} levels; kept the first {want}.")
            lv = lv[:want]
        if want is not None and len(lv) < want:
            errors.append(f"The world must have exactly {want} levels; you gave {len(lv)}. "
                          "Add levels.")
        if len(lv) > MAX_LEVELS:
            notes.append(f"The outline had {len(lv)} levels; kept the first {MAX_LEVELS}.")
            lv = lv[:MAX_LEVELS]
        # unique, non-empty ids (deterministic patch)
        seen: Dict[str, int] = {}
        fixed = []
        for i, level in enumerate(lv):
            base = re.sub(r"\W+", "_", level.id.strip().lower()).strip("_") or f"level{i + 1}"
            lid = base
            while lid in seen:
                seen[base] = seen.get(base, 1) + 1
                lid = f"{base}{seen[base]}"
            seen[lid] = 1
            if not level.prompt.strip():
                errors.append(f"Level {level.id!r} has an empty prompt.")
            fixed.append(level if lid == level.id else level.model_copy(update={"id": lid}))
        ids = {lvl.id for lvl in fixed}
        reqs = []
        for r in outline.requirements:
            if r.check is not None and r.check.type == "entity_in_level" and r.check.level not in ids:
                # an index ("2") or a title is mapped to the level id when it can be
                ref = str(r.check.level or "")
                match = None
                if ref.isdigit() and 1 <= int(ref) <= len(fixed):
                    match = fixed[int(ref) - 1].id
                else:
                    match = next((x.id for x in fixed if x.title.lower() == ref.lower()), None)
                if match:
                    r = r.model_copy(update={"check": r.check.model_copy(update={"level": match})})
                else:
                    errors.append(f"Requirement {r.id}: check.level {r.check.level!r} is not one of "
                                  f"the level ids {sorted(ids)}.")
            reqs.append(r)
        out = outline.model_copy(update={"levels": fixed, "requirements": reqs})
        return (None if errors else out), errors, notes

    return parse


def outline_world(
    description: str,
    provider: Any,
    profile: Optional[GameProfile] = None,
    levels: Optional[int] = None,
    max_retries: int = 3,
) -> WorldOutline:
    """One structured-output call: the :class:`WorldOutline` of *description*.

    Raises ``edit.llm.EditLLMFailed`` when no valid outline came back.
    """
    from edit.llm import run_structured  # the generic structured-output loop

    run = run_structured(
        provider,
        WORLD_SYSTEM_PROMPT,
        build_outline_messages(description, levels, profile),
        WorldOutline,
        _parser(levels),
        max_retries=max_retries,
        schema=WorldOutline.json_schema(),
        what="the world outline",
    )
    return run.value


# ─────────────────────────────────────────────────────────────────────────────
# World fidelity
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class WorldLevelInfo:
    """One built level, as the world report sees it."""

    id: str  # outline level id
    identifier: str
    iid: Optional[str]
    title: str
    ir: IRLevel
    fidelity: Optional[FidelityReport]
    verified: bool


@dataclass
class WorldFidelity:
    """The world requirements plus a roll-up of the levels.

    ``score`` is the mean of the world requirements' score (when any is checkable)
    and every level's score; ``verified`` is True when every level verified.
    """

    report: FidelityReport  # the world requirements (W1, W2 …)
    levels: List[WorldLevelInfo] = field(default_factory=list)

    @property
    def score(self) -> Optional[float]:
        parts = [self.report.score] + [lv.fidelity.score if lv.fidelity else None
                                       for lv in self.levels]
        parts = [p for p in parts if p is not None]
        return sum(parts) / len(parts) if parts else None

    @property
    def verified(self) -> bool:
        return all(lv.verified for lv in self.levels)

    def to_dict(self) -> Dict[str, Any]:
        base = self.report.to_dict()
        base["score"] = self.score
        base["verified"] = self.verified
        base["requirements_score"] = self.report.score
        base["levels"] = [
            {"id": lv.id, "identifier": lv.identifier, "iid": lv.iid, "title": lv.title,
             "score": lv.fidelity.score if lv.fidelity else None, "verified": lv.verified}
            for lv in self.levels
        ]
        return base


def _matches(ent: Any, kind: str, subkind: Optional[str], valid: bool) -> bool:
    if ent.entity_type != kind:
        return False
    if not subkind:
        return True
    if valid:
        return str(ent.metadata.get(SUBKIND_FIELDS[kind]) or "").lower() == subkind
    text = f"{ent.metadata.get('description') or ''} {ent.metadata.get('sprite') or ''}".lower()
    return subkind in text


def check_world(outline: WorldOutline, levels: Sequence[WorldLevelInfo]) -> WorldFidelity:
    """Check the outline's world requirements on the built levels."""
    by_id = {lv.id: lv for lv in levels}
    items: List[FidelityItem] = []
    for r in outline.requirements:
        c = r.check
        category = "world"
        if c is None or c.type == "none":
            items.append(FidelityItem(r.id, r.text, category, "unchecked", "no structured check"))
            continue
        need = 1 if c.min_count is None else c.min_count
        if c.type == "level_count":
            n = len(levels)
            status = "satisfied" if n >= need else "missing"
            items.append(FidelityItem(r.id, r.text, category, status,
                                      f"the world has {n} level(s), {need} asked"))
            continue
        level = by_id.get(c.level or "")
        if level is None or not c.entity_kind:
            items.append(FidelityItem(r.id, r.text, category, "unchecked",
                                      f"unknown level {c.level!r}" if level is None
                                      else "no entity kind given"))
            continue
        sub, valid = resolve_subkind(c.entity_kind, c.subkind)
        n = sum(1 for e in level.ir.entities if _matches(e, c.entity_kind, sub, valid))
        what = f"{c.entity_kind}{f' ({sub})' if sub else ''}"
        reason = f"{n} {what} in {level.identifier}, {need} asked"
        if n >= need and (valid or not sub):
            status = "satisfied"
        elif n >= need or n > 0:
            status = "approximated"
        else:
            status = "missing"
        items.append(FidelityItem(r.id, r.text, category, status, reason))
    return WorldFidelity(FidelityReport(items=items, verified=all(lv.verified for lv in levels)),
                         list(levels))


__all__ = [
    "WorldFidelity",
    "WorldLevelInfo",
    "check_world",
    "mentions_levels",
    "outline_world",
    "wants_outline",
]
