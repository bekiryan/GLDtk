"""Cross-provider fallback — e.g. Gemini first, a local Ollama model when it fails.

The Gemini free tier runs out (429), gets overloaded (503) or retires models
(404).  ``FallbackProvider`` tries each provider in order and moves on only for
those "the service cannot answer" errors (or a missing API key, which never
reaches the network); anything else (a bad key, a malformed
response) is raised as is.  Once a fallback has answered, later calls of the
same request go straight to it, so a multi-call generate does not pay for the
failing provider again.  ``notes`` records each switch for the response
warnings.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from api import jobs

from . import base

FALLBACK_STATUSES = {404, 429, 500, 502, 503, 504}


def should_fall_back(err: base.ProviderError) -> bool:
    """Quota, overload, retired model, timeout, no connection or no API key."""
    return err.status in FALLBACK_STATUSES or err.timed_out or err.status is None


def provider_label(provider: Any) -> str:
    model = getattr(provider, "model_used", None) or getattr(provider, "model", None)
    return f"{provider.name}/{model}" if model else provider.name


def describe(provider: Any) -> str:
    """``provider/model`` of any provider, for progress lines."""
    if isinstance(provider, FallbackProvider):
        return provider.model_used
    return provider_label(provider)


class FallbackProvider:
    def __init__(self, providers: Sequence[base.LLMProvider]) -> None:
        if not providers:
            raise ValueError("FallbackProvider needs at least one provider")
        self._providers: Tuple[base.LLMProvider, ...] = tuple(providers)
        self._current = 0
        self.notes: List[str] = []

    @property
    def providers(self) -> Tuple[base.LLMProvider, ...]:
        """The chain, in the order tried."""
        return self._providers

    @property
    def name(self) -> str:
        return self._providers[self._current].name

    @property
    def model_used(self) -> str:
        return provider_label(self._providers[self._current])

    def complete_json(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
    ) -> str:
        errors: List[str] = []
        missing_key = False
        for i in range(self._current, len(self._providers)):
            provider = self._providers[i]
            try:
                text = provider.complete_json(system, messages, schema)
            except base.ProviderError as err:
                if not should_fall_back(err) or i == len(self._providers) - 1:
                    if errors:
                        raise base.ProviderError(
                            "; ".join(errors + [f"{provider_label(provider)}: {err}"]),
                            status=err.status,
                            timed_out=err.timed_out,
                        ) from err
                    raise
                if i == self._current and getattr(err, "missing_key", False):
                    missing_key = True
                errors.append(f"{provider_label(provider)}: {_short(err)}")
                jobs.report("llm", switch_message(self._providers[i + 1]))
                continue
            if i != self._current:
                self.notes.append(
                    _no_key_note(self._providers[self._current], provider)
                    if missing_key
                    else f"{self._providers[self._current].name} unavailable "
                    f"({_short(_last_error(errors))}); answered by {provider_label(provider)}"
                )
                self._current = i
            return text
        raise AssertionError("unreachable")


def switch_message(provider: Any) -> str:
    """The progress line for moving on to *provider*."""
    if provider.name == "ollama":
        model = getattr(provider, "model", None) or "?"
        return f"Switched to local model {model} (this can take a few minutes)"
    return f"Switched to {provider_label(provider)}"


def _no_key_note(primary: Any, used: Any) -> str:
    """The warning when *primary* was skipped because no API key is set."""
    model = getattr(used, "model", None) or provider_label(used)
    where = f"the local model {model}" if used.name == "ollama" else provider_label(used)
    return f"No {primary.name.capitalize()} API key set — used {where}. Add a key in Settings."


def _short(err: Any) -> str:
    if isinstance(err, base.ProviderError):
        if err.missing_key:
            return "no API key"
        if err.status:
            return f"HTTP {err.status}"
        return "timeout" if err.timed_out else "connection error"
    return str(err)


def _last_error(errors: List[str]) -> str:
    return errors[-1].split(": ", 1)[-1] if errors else ""
