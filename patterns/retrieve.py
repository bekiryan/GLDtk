"""Retrieval: cosine similarity + a small filter bonus, then max-marginal relevance.

``accept(pattern)`` is the step Part 22 (soft-locks) extends: a pattern is only retrieved
when it passes.  Today it rejects patterns without a Start -> Exit route in the window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from patterns.embed import cosine, make_embedder
from patterns.store import PatternStore

TAG_BONUS = 0.1  # added to the cosine, scaled by the share of the requested tags a pattern has
STRUCTURE_BONUS = 0.05
MMR_LAMBDA = 0.7

# Extra checks run by accept(); Part 22 appends its soft-lock check here.
def _no_softlocks(pattern: Dict[str, Any]) -> bool:
    """Part 22: a pattern where the player can get stuck is never an example."""
    return not (pattern.get("features", {}).get("route") or {}).get("softlocks")


EXTRA_CHECKS: List[Callable[[Dict[str, Any]], bool]] = [_no_softlocks]


def accept(pattern: Dict[str, Any]) -> bool:
    """True when the pattern may be offered as an example (Part 22 fills in the soft-lock check)."""
    if not (pattern.get("features", {}).get("route") or {}).get("ok"):
        return False
    return all(check(pattern) for check in EXTRA_CHECKS)


@dataclass
class Filters:
    structure_type: Optional[str] = None
    difficulty_min: Optional[int] = None
    difficulty_max: Optional[int] = None
    tags: List[str] = field(default_factory=list)
    hard: bool = True  # False: the filters only add a score bonus, nothing is excluded

    def matches(self, rec: Dict[str, Any]) -> bool:
        lab = rec.get("label") or {}
        st = lab.get("structure_type") or rec["features"].get("structure")
        if self.structure_type and st != self.structure_type:
            return False
        d = lab.get("difficulty")
        if d is not None:
            if self.difficulty_min is not None and d < self.difficulty_min:
                return False
            if self.difficulty_max is not None and d > self.difficulty_max:
                return False
        elif self.difficulty_min is not None or self.difficulty_max is not None:
            return False
        if self.tags and not set(t.lower() for t in self.tags) & set(lab.get("tags") or []):
            return False
        return True

    def bonus(self, rec: Dict[str, Any]) -> float:
        lab = rec.get("label") or {}
        b = 0.0
        if self.tags:
            have = set(lab.get("tags") or [])
            b += TAG_BONUS * len({t.lower() for t in self.tags} & have) / len(self.tags)
        if self.structure_type and (lab.get("structure_type") or rec["features"].get("structure")) == self.structure_type:
            b += STRUCTURE_BONUS
        return b


@dataclass
class Hit:
    pattern: Dict[str, Any]
    score: float

    @property
    def id(self) -> str:
        return self.pattern["id"]

    @property
    def title(self) -> str:
        return (self.pattern.get("label") or {}).get("title") or f"{self.pattern['features']['structure']} fragment"


def retrieve(
    query_text: str,
    k: int = 3,
    filters: Optional[Filters] = None,
    *,
    store: Optional[PatternStore] = None,
    embedder: Any = None,
    mmr_lambda: float = MMR_LAMBDA,
    exclude: Optional[set] = None,
) -> List[Hit]:
    """The *k* best patterns for *query_text*, diverse (max-marginal relevance)."""
    store = store or PatternStore()
    if k <= 0 or not store.vectors or not store.embedder:
        return []
    embedder = embedder or make_embedder(store.embedder["name"])
    if embedder.name != store.embedder["name"]:
        return []  # vectors of another embedder are not comparable
    qv = embedder.embed([query_text])[0]
    cands: List[Hit] = []
    for pid, vrec in store.vectors.items():
        rec = store.patterns.get(pid)
        if rec is None or (exclude and pid in exclude) or not accept(rec):
            continue
        if filters and filters.hard and not filters.matches(rec):
            continue
        score = cosine(qv, vrec["vec"]) + (filters.bonus(rec) if filters else 0.0)
        cands.append(Hit(rec, score))
    cands.sort(key=lambda h: (-h.score, h.id))

    chosen: List[Hit] = []
    while cands and len(chosen) < k:
        if not chosen:
            best = cands[0]
        else:
            def mmr(h: Hit) -> float:
                sim = max(cosine(store.vectors[h.id]["vec"], store.vectors[c.id]["vec"]) for c in chosen)
                return mmr_lambda * h.score - (1 - mmr_lambda) * sim
            best = max(cands, key=lambda h: (mmr(h), h.score, h.id))
        chosen.append(best)
        cands.remove(best)
    return chosen


__all__ = ["EXTRA_CHECKS", "Filters", "Hit", "accept", "retrieve"]
