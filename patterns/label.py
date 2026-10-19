"""Labelling: one structured-output LLM call per pattern, cached in the index.

A pattern that already has a label is never sent again; ``max_calls`` caps the provider
calls of one run like the eval harness (``eval.providers.CallBudget``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Collection, List, Literal, Optional

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
Describe only what is in the fragment. Style of a good title (never reuse these examples):
"Spike pit stairs", "Twin ledge climb".\
"""

# Titles the prompt shows as examples; a label that repeats one is rejected and asked again once.
EXAMPLE_TITLES = ("spike pit stairs", "twin ledge climb")


def is_example_title(title: str) -> bool:
    return title.strip().strip('."\'').lower() in EXAMPLE_TITLES


def richness(rec: dict) -> float:
    """How much structure a window has (more is a better labelling candidate)."""
    f = rec.get("features") or {}
    r = f.get("route") or {}
    return (f.get("platforms", 0) + 2 * r.get("jumps", 0) + 2 * f.get("hazards", 0) ** 0.5
            + f.get("enemies", 0) + len(set(f.get("platform_widths") or [])) + (f.get("max_gap", 0) > 0))


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
    geo = f.get("structure_geo") or {}
    if geo.get("type") and float(geo.get("confidence") or 0) >= 0.6:  # Part 29g: classifier hint
        feats["geometry_type"] = geo["type"]
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


def rich_order(recs: List[dict]) -> List[dict]:
    """Richest first within each source, then round-robin over the sources."""
    by_src: dict = {}
    for r in recs:
        by_src.setdefault(r.get("source", ""), []).append(r)
    queues = [sorted(q, key=lambda r: (-richness(r), r["id"])) for q in by_src.values()]
    out = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


def label_pending(
    store: PatternStore,
    provider: Any,
    *,
    max_calls: Optional[int] = None,
    dry_run: bool = False,
    max_retries: int = 2,
    budget: Optional[int] = None,
    only: Optional[Collection[str]] = None,
) -> LabelReport:
    """Label accepted, unlabelled patterns, structurally richest first and rotating across
    sources (saves the store after each label).  *budget* caps the number of labels written,
    *max_calls* the provider calls; *only* (Part 29g) limits the run to those pattern ids."""
    from edit.llm import EditLLMFailed, run_structured
    from eval.providers import BudgetExceeded, CallBudget, CountingProvider

    todo = rich_order([r for r in store if r.get("accepted") and not r.get("label")
                       and r.get("kind", "pattern") == "pattern"
                       and (only is None or r["id"] in only)])
    rep = LabelReport(cached=sum(1 for r in store if r.get("label")), dry_run=dry_run)
    if dry_run:
        rep.pending = len(todo)
        return rep
    budget_labels = budget
    budget = CallBudget(max_calls)
    counted = CountingProvider(provider, budget)
    for rec in todo:
        if budget_labels is not None and rep.labelled >= budget_labels:
            rep.stopped = True
            break
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
        if is_example_title(run.value.title):  # copied the prompt's example: ask once more
            msgs = label_messages(rec) + [{"role": "user", "content": (
                f'"{run.value.title}" is only an example. Give a title that names what is distinctive '
                "in THIS fragment.")}]
            try:
                again = run_structured(counted, LABEL_SYSTEM_PROMPT, msgs, PatternLabel,
                                       lambda obj: (obj, [], []), max_retries=1,
                                       what=f"the label of {rec['id']} (retitle)")
            except BudgetExceeded:
                rep.stopped = True
                break
            except EditLLMFailed as exc:
                rep.errors.append(f"{rec['id']}: {exc}")
                continue
            if is_example_title(again.value.title):
                rep.errors.append(f"{rec['id']}: title still an example, skipped")
                continue
            run = again
        rec["label"] = run.value.model_dump()
        rep.labelled += 1
        store.save()
    rep.calls = budget.total
    rep.pending = sum(1 for r in store if r.get("accepted") and not r.get("label") and r.get("kind", "pattern") == "pattern")
    return rep


__all__ = ["LABEL_SYSTEM_PROMPT", "EXAMPLE_TITLES", "LabelReport", "PatternLabel", "rich_order", "richness", "is_example_title", "label_pending", "label_messages"]
