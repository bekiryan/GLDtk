"""FidelityReport — how faithfully a level matches the prompt's requirements.

Every requirement gets one :class:`FidelityItem` with a status:

* ``satisfied``    — the structured check passes;
* ``approximated`` — the concept was mapped to the nearest kind (a "ghost"
  subkind on an Enemy), or the requirement was only partly met (2 of 3 coins);
* ``impossible``   — cannot be met in this game: an ability the Game Profile
  lacks, a size or gravity scale outside the limits;
* ``missing``      — could be met but is not (fixable);
* ``unchecked``    — no structured check (style, other, ``check`` null).

Score (the Part 10 evaluation metric)
-------------------------------------
    checkable = items whose status is not ``unchecked``
    score     = (satisfied + 0.5 · approximated) / len(checkable)

``impossible`` and ``missing`` count 0.  With no checkable item the score is
``None`` (nothing to measure), not 1.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

FidelityStatus = Literal["satisfied", "approximated", "impossible", "missing", "unchecked"]

STATUS_WEIGHTS: Dict[str, float] = {
    "satisfied": 1.0,
    "approximated": 0.5,
    "impossible": 0.0,
    "missing": 0.0,
}


@dataclass
class FidelityItem:
    id: str  # the requirement id ("R1")
    text: str
    category: str
    status: FidelityStatus
    reason: str
    auto_filled: bool = False  # the completeness fill added entities for it


@dataclass
class FidelityReport:
    items: List[FidelityItem] = field(default_factory=list)
    verified: Optional[bool] = None  # final grid proven beatable (check_level only)

    @property
    def score(self) -> Optional[float]:
        """See the module docstring; None when nothing is checkable."""
        checkable = [i for i in self.items if i.status != "unchecked"]
        if not checkable:
            return None
        return sum(STATUS_WEIGHTS[i.status] for i in checkable) / len(checkable)

    def by_status(self, status: str) -> List[FidelityItem]:
        return [i for i in self.items if i.status == status]

    @property
    def missing(self) -> List[FidelityItem]:
        return self.by_status("missing")

    def counts(self) -> Dict[str, int]:
        out = {s: 0 for s in ("satisfied", "approximated", "impossible", "missing", "unchecked")}
        for i in self.items:
            out[i.status] += 1
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "verified": self.verified,
            "counts": self.counts(),
            "items": [
                {
                    "id": i.id,
                    "text": i.text,
                    "category": i.category,
                    "status": i.status,
                    "reason": i.reason,
                    "auto_filled": i.auto_filled,
                }
                for i in self.items
            ],
        }
