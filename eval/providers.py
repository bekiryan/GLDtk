"""Provider wrappers for the harness: call counting + budget, record, replay.

Recording layout (``--record DIR`` / ``--replay DIR``)::

    DIR/meta.json                     {"provider": "gemini", "model": "..."}
    DIR/<prompt_id>/00.txt, 01.txt    raw LLM responses, by call index
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from llm.providers.base import ProviderError


class BudgetExceeded(RuntimeError):
    """The ``--max-calls`` budget would be exceeded by the next LLM call."""


class ReplayMissing(RuntimeError):
    """A replayed prompt asked for a response that was never recorded."""


class CallBudget:
    """Shared across the whole run: total calls, an optional cap and a pause."""

    def __init__(self, max_calls: Optional[int] = None, sleep: float = 0.0) -> None:
        self.max_calls = max_calls
        self.sleep = sleep
        self.total = 0
        self.blocked = False  # a call was refused (even if the pipeline swallowed it)
        self.slept = 0.0  # seconds slept so far (subtracted from wall time)

    @property
    def exhausted(self) -> bool:
        return self.max_calls is not None and self.total >= self.max_calls


class CountingProvider:
    """Counts calls (per prompt and in the shared budget) and enforces the cap."""

    def __init__(self, inner: Any, budget: CallBudget) -> None:
        self._inner = inner
        self._budget = budget
        self.calls = 0  # calls made through this wrapper (one prompt)
        self.models_used: List[str] = []  # models that answered (Gemini may fall back)
        self.name = getattr(inner, "name", "unknown")

    def complete_json(self, system: str, messages: List[Dict[str, Any]], schema: Dict[str, Any]) -> str:
        b = self._budget
        if b.exhausted:
            b.blocked = True
            raise BudgetExceeded(f"--max-calls {b.max_calls} reached")
        if b.sleep > 0 and b.total > 0:
            time.sleep(b.sleep)
            b.slept += b.sleep
        b.total += 1  # a failed call (429) still spends quota
        self.calls += 1
        raw = self._inner.complete_json(system, messages, schema)
        used = getattr(self._inner, "model_used", None)
        if used and used not in self.models_used:
            self.models_used.append(used)
        return raw


class RecordingProvider:
    """Saves every raw response under ``DIR/<prompt_id>/<index>.txt``."""

    def __init__(self, inner: Any, directory: Path, prompt_id: str) -> None:
        self._inner = inner
        self._dir = Path(directory) / prompt_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index = 0
        self.name = getattr(inner, "name", "unknown")

    @property
    def model_used(self) -> Optional[str]:
        return getattr(self._inner, "model_used", None)

    def complete_json(self, system: str, messages: List[Dict[str, Any]], schema: Dict[str, Any]) -> str:
        raw = self._inner.complete_json(system, messages, schema)
        (self._dir / f"{self._index:02d}.txt").write_text(raw, encoding="utf-8")
        self._index += 1
        return raw


class ReplayProvider:
    """Returns the recorded responses of one prompt, in call order."""

    def __init__(self, directory: Path, prompt_id: str, name: str = "replay") -> None:
        self._dir = Path(directory) / prompt_id
        self._index = 0
        self.name = name

    def complete_json(self, system: str, messages: List[Dict[str, Any]], schema: Dict[str, Any]) -> str:
        path = self._dir / f"{self._index:02d}.txt"
        if not path.is_file():
            raise ReplayMissing(f"no recorded response {path}")
        self._index += 1
        return path.read_text(encoding="utf-8")


def write_meta(directory: Path, provider: str, model: Optional[str]) -> None:
    Path(directory).mkdir(parents=True, exist_ok=True)
    (Path(directory) / "meta.json").write_text(
        json.dumps({"provider": provider, "model": model}), encoding="utf-8"
    )


def read_meta(directory: Path) -> Dict[str, Any]:
    p = Path(directory) / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def is_quota_error(exc: BaseException) -> bool:
    """HTTP 429 / resource-exhausted: the free-tier quota, not a harness crash."""
    if isinstance(exc, ProviderError) and exc.status == 429:
        return True
    text = str(exc)
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower()
