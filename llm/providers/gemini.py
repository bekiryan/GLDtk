"""Gemini provider (default) — REST ``generateContent`` with structured output.

Structured output uses ``generationConfig.responseJsonSchema`` (full JSON
Schema, accepts ``"type": [T, "null"]``).  The canonical schema from
``LevelSpec.json_schema()`` is sent unchanged apart from dropping
``additionalProperties`` (not part of Gemini's supported subset).

The API key travels in the ``x-goog-api-key`` header, never in the URL, so it
cannot leak into error messages.

When the model was not chosen explicitly, persistent 5xx overload, a 429
(the free tier allows ~20 requests/day per Flash model; quota is per model) or
a read timeout on the default Flash model falls back to ``FALLBACK_MODELS`` in
order.  A timeout is not retried on the same model: it already cost the full
timeout once.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

from api import jobs

from . import base

DEFAULT_MODEL = "gemini-3.8-flash"
FALLBACK_MODELS = ("gemini-3.1-flash-lite",)
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def adapt_schema(schema: Any) -> Any:
    """Gemini-specific schema adaptation (strip unsupported keywords)."""
    if isinstance(schema, list):
        return [adapt_schema(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    out: Dict[str, Any] = {}
    for k, v in schema.items():
        if k == "properties":
            out[k] = {name: adapt_schema(sub) for name, sub in v.items()}
        elif k != "additionalProperties":
            out[k] = adapt_schema(v)
    return out


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: float = 120.0,
        temperature: Optional[float] = None,
        transient_retries: int = 2,
        backoff_seconds: float = 5.0,
        fallback_models: Sequence[str] = (),
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self._fallback_models = tuple(fallback_models)
        self.model_used: Optional[str] = None  # model that answered the last call
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature
        self._transient_retries = transient_retries
        self._backoff_seconds = backoff_seconds
        self.last_usage: Dict[str, Any] = {}  # usageMetadata of the last call

    # ------------------------------------------------------------------

    def build_request(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
    ) -> tuple[str, Dict[str, str], Dict[str, Any]]:
        """Return ``(url, headers, payload)`` — separated out for tests."""
        contents = [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": str(m["content"])}],
            }
            for m in messages
        ]
        generation_config: Dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseJsonSchema": adapt_schema(schema),
        }
        if self._temperature is not None:
            generation_config["temperature"] = self._temperature
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": generation_config,
        }
        url = f"{self._base_url}/models/{self.model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key or "",
        }
        return url, headers, payload

    def complete_json(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
    ) -> str:
        if not self._api_key:
            raise base.ProviderError(
                "Gemini provider requires an API key. "
                "Pass api_key=... or set GEMINI_API_KEY (e.g. in .env).",
                missing_key=True,
            )
        response = self._post_with_fallback(system, messages, schema)
        self.last_usage = dict(response.get("usageMetadata") or {})

        try:
            candidate = response["candidates"][0]
            parts = candidate.get("content", {}).get("parts", [])
        except (KeyError, IndexError, TypeError) as exc:
            feedback = response.get("promptFeedback")
            raise base.ProviderError(
                f"Gemini response has no candidates (promptFeedback={feedback})."
            ) from exc

        text = "".join(
            p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")
        ).strip()
        if not text:
            raise base.ProviderError(
                f"Gemini returned empty text (finishReason={candidate.get('finishReason')})."
            )
        return text

    def _post_with_fallback(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Try ``self.model``, then each fallback model on overload, quota or timeout errors."""
        primary = self.model
        candidates = [primary, *self._fallback_models]
        try:
            for i, model in enumerate(candidates):
                self.model = model
                url, headers, payload = self.build_request(system, messages, schema)
                try:
                    response = self._post_with_retry(url, headers, payload)
                except base.ProviderError as err:
                    retryable = err.status in _FALLBACK_STATUSES or err.timed_out
                    if retryable and i < len(candidates) - 1:
                        jobs.report("llm", f"{model} {_failure(err)}, trying {candidates[i + 1]}")
                        continue
                    raise
                self.model_used = model
                return response
        finally:
            self.model = primary
        raise AssertionError("unreachable")

    def _post_with_retry(
        self, url: str, headers: Dict[str, str], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """POST, retrying transient 5xx overload errors ("high demand" 503s).

        A cancelled job stops before each POST, right after it returns (an
        in-flight call cannot be interrupted) and during the backoff sleep.
        """
        for attempt in range(self._transient_retries + 1):
            jobs.checkpoint()
            try:
                response = base.http_json_post(url, headers, payload, self._timeout_seconds)
            except base.ProviderError as err:
                jobs.checkpoint()
                transient = err.status in _TRANSIENT_STATUSES
                if not transient or attempt == self._transient_retries:
                    raise
                delay = self._backoff_seconds * (2**attempt)
                jobs.report("llm", f"Gemini busy ({err.status}), retrying in {delay:g} s "
                                   f"({attempt + 1}/{self._transient_retries})")
                jobs.sleep(delay)
                continue
            jobs.checkpoint()
            return response
        raise AssertionError("unreachable")


def _failure(err: base.ProviderError) -> str:
    """Why a model was given up on, for the progress line."""
    if err.status == 429:
        return "out of quota"
    if err.status == 404:
        return "is not available"
    if err.timed_out:
        return "timed out"
    return f"still busy ({err.status})"


_TRANSIENT_STATUSES = {500, 502, 503, 504}
# 404: the model was retired (gemini-2.5-flash is gone for new users).
_FALLBACK_STATUSES = _TRANSIENT_STATUSES | {404, 429}
