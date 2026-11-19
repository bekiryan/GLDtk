"""Provider wrappers for the harness: call counting + budget, record, replay.

Recording layout (``--record DIR`` / ``--replay DIR``)::

    DIR/meta.json                     {"provider": "gemini", "model": "..."}
    DIR/<prompt_id>/00.txt, 01.txt    raw LLM responses, by call index
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

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
        self.answered_by: List[str] = []  # provider/model labels that answered
        self.fallback_reasons: List[str] = []
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
        try:
            raw = self._inner.complete_json(system, messages, schema)
        finally:
            reasons = getattr(self._inner, "fallback_reasons", None) or ()
            for r in reasons:
                if r not in self.fallback_reasons:
                    self.fallback_reasons.append(r)
        used = getattr(self._inner, "model_used", None)
        if used and used not in self.models_used:
            self.models_used.append(used)
        label = answered_label(self._inner)
        if label and label not in self.answered_by:
            self.answered_by.append(label)
        return raw


class RecordingProvider:
    """Saves every raw response under ``DIR/<prompt_id>/<index>.txt``."""

    def __init__(self, inner: Any, directory: Path, prompt_id: str) -> None:
        self._inner = inner
        self._dir = Path(directory) / prompt_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index = 0

    @property
    def name(self) -> str:  # live: a FallbackProvider's name changes when it switches
        return getattr(self._inner, "name", "unknown")

    @property
    def model_used(self) -> Optional[str]:
        return getattr(self._inner, "model_used", None)

    @property
    def fallback_reasons(self) -> List[str]:
        return list(getattr(self._inner, "fallback_reasons", None) or [])

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


# ── backoff on overload ─────────────────────────────────────────────────────

DEFAULT_BACKOFF = (20.0, 60.0, 120.0)  # seconds before retry 1, 2, 3


def is_unavailable_error(exc: BaseException) -> bool:
    """HTTP 503 / UNAVAILABLE ("model is experiencing high demand"): wait and retry."""
    if isinstance(exc, ProviderError) and exc.status == 503:
        return True
    text = str(exc)
    return "503" in text or "UNAVAILABLE" in text


class BackoffProvider:
    """Retries a call after 503/UNAVAILABLE, sleeping ``delays[i]`` before retry i+1.

    ``retries`` and ``slept`` are cumulative (the harness takes per-prompt deltas);
    any other error, or the last 503, is raised as is.
    """

    def __init__(self, inner: Any, delays: Sequence[float] = DEFAULT_BACKOFF,
                 sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[str], None] = lambda s: None) -> None:
        self._inner = inner
        self._delays = tuple(delays)
        self._sleep = sleep
        self._log = log
        self.retries = 0
        self.slept = 0.0

    def __getattr__(self, attr: str) -> Any:  # name, model, model_used, ...
        return getattr(self._inner, attr)

    def complete_json(self, system: str, messages: List[Dict[str, Any]], schema: Dict[str, Any]) -> str:
        for attempt in range(len(self._delays) + 1):
            try:
                return self._inner.complete_json(system, messages, schema)
            except Exception as exc:
                if attempt >= len(self._delays) or not is_unavailable_error(exc):
                    raise
                delay = self._delays[attempt]
                self._log(f"wait  {delay:g}s after 503/UNAVAILABLE (retry {attempt + 1}/{len(self._delays)})")
                self._sleep(delay)
                self.retries += 1
                self.slept += delay
        raise AssertionError("unreachable")


def answered_label(provider: Any) -> Optional[str]:
    """``provider/model`` of the provider that answered the last call (None if unknown)."""
    name = getattr(provider, "name", None) or "unknown"
    model = getattr(provider, "model_used", None) or getattr(provider, "model", None)
    if not model:
        return name
    return model if str(model).startswith(f"{name}/") else f"{name}/{model}"
