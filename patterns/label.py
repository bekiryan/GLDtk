"""Labelling: one structured-output LLM call per pattern, cached in the index.

A pattern that already has a label is never sent again; ``max_calls`` caps the provider
calls of one run like the eval harness (``eval.providers.CallBudget``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from patterns.store import PatternStore

StructureType = Literal["corridor", "parkour", "labyrinth", "arena", "shaft", "hub", "open"]

LABEL_SYSTEM_PROMPT: str = """\
You label fragments of 2-D platformer levels for a pattern library. You get one fragment as
ASCII ("." empty, "#" solid, "^" hazard, S start, X exit, E enemy, P pickup; row 0 is the TOP)
and measured features. Output ONE JSON object and nothing else:
  "title": 2-5 words naming what is distinctive in THIS fragment (its shape, hazard or challenge),
  "purpose": one sentence on what it does for a level's pacing,
  "mood": 1-3 words ("tense", "calm"),
  "mechanics": the skills it asks of the player (jump, double jump, drop, climb, dodge, ...),
  "structure_type": corridor | parkour | labyrinth | arena | shaft | hub | open,
  "difficulty": integer 1 (trivial) .. 10 (very hard),
  "tags": 2-6 lowercase words for retrieval.
Describe only what is in the fragment.\
"""


class PatternLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    purpose: str
    mood: str
    mechanics: List[str] = Field(default_factory=list)
    structure_type: StructureType
    difficulty: int = Field(ge=1, le=10)
    tags: List[str] = Field(default_factory=list)


@dataclass
class LabelReport:
    labelled: int = 0
    cached: int = 0  # already labelled, not sent
    pending: int = 0  # accepted patterns still without a label after the run
    calls: int = 0
    stopped: bool = False  # the --max-calls budget ran out
    dry_run: bool = False
    errors: List[str] = field(default_factory=list)


def label_messages(rec: dict) -> List[dict]:
    f = rec["features"]
    feats = {k: f[k] for k in ("width", "height", "platform_widths", "max_gap", "hazards",
                               "enemies", "pickups", "structure") if k in f}
    feats["route"] = f.get("route")
    feats["difficulty_estimate"] = f.get("difficulty")
    return [{"role": "user", "content": f"FRAGMENT:\n{rec['ascii']}\n\nFEATURES: {json.dumps(feats)}"}]


def spread(recs: List[dict]) -> List[dict]:
    """Round-robin over sources, then over levels within a source, so a capped run labels a
    bit of every game and level instead of the first level's windows only."""
    by_src: dict = {}
    for r in recs:
        by_src.setdefault(r.get("source", ""), {}).setdefault(r.get("level", ""), []).append(r)
    per_src = []
    for levels in by_src.values():
        queues = list(levels.values())
        seq = []
        while any(queues):
            for q in queues:
                if q:
                    seq.append(q.pop(0))
        per_src.append(seq)
    out = []
    while any(per_src):
        for s in per_src:
            if s:
                out.append(s.pop(0))
    return out


def label_pending(
    store: PatternStore,
    provider: Any,
    *,
    max_calls: Optional[int] = None,
    dry_run: bool = False,
    max_retries: int = 2,
) -> LabelReport:
    """Label every accepted, unlabelled pattern (saves the store after each label)."""
    from edit.llm import EditLLMFailed, run_structured
    from eval.providers import BudgetExceeded, CallBudget, CountingProvider

    todo = spread([r for r in store if r.get("accepted") and not r.get("label")
                   and r.get("kind", "pattern") == "pattern"])
    rep = LabelReport(cached=sum(1 for r in store if r.get("label")), dry_run=dry_run)
    if dry_run:
        rep.pending = len(todo)
        return rep
    budget = CallBudget(max_calls)
    counted = CountingProvider(provider, budget)
    for rec in todo:
        if budget.exhausted:
            rep.stopped = True
            break
        try:
            run = run_structured(counted, LABEL_SYSTEM_PROMPT, label_messages(rec), PatternLabel,
                                 lambda obj: (obj, [], []), max_retries=max_retries,
                                 what=f"the label of {rec['id']}")
        except BudgetExceeded:
            rep.stopped = True
            break
        except EditLLMFailed as exc:
            rep.errors.append(f"{rec['id']}: {exc}")
            continue
        rec["label"] = run.value.model_dump()
        rep.labelled += 1
        store.save()
    rep.calls = budget.total
    rep.pending = sum(1 for r in store if r.get("accepted") and not r.get("label") and r.get("kind", "pattern") == "pattern")
    return rep


__all__ = ["LABEL_SYSTEM_PROMPT", "LabelReport", "PatternLabel", "label_pending", "label_messages"]
