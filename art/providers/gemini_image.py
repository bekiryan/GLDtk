"""Gemini image provider (default) — REST ``generateContent`` with image output.

The model list was checked with the ListModels endpoint on 2026-10-13: the
newest Flash image model is ``gemini-3.1-flash-image``; ``gemini-3.1-flash-lite-image``
and the historical ``gemini-2.5-flash-image`` are the fallbacks.

* ``responseModalities: ["IMAGE"]``; if the model rejects IMAGE-only output
  (HTTP 400 mentioning modalities), the call is repeated once with
  ``["TEXT", "IMAGE"]``.
* The API key travels in the ``x-goog-api-key`` header only, never in the URL,
  so it cannot leak into error messages.
* Transient 5xx errors are retried with backoff (as in ``llm/providers/gemini.py``).
  When no model was chosen explicitly, a 429 (quota) or persistent 5xx moves
  on to the next fallback model. A final 429 raises ``ImageProviderError``
  with a quota message.
* ``max_calls`` caps the number of HTTP requests this instance may make (used
  by the e2e test to respect the tiny free-tier quota).
"""

from __future__ import annotations

import base64
import io
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from llm.providers.base import ProviderError, http_json_post

from .base import ImageProviderError

DEFAULT_MODEL = "gemini-3.1-flash-image"
FALLBACK_MODELS = ("gemini-3.1-flash-lite-image", "gemini-2.5-flash-image")
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Aspect ratios accepted by imageConfig.aspectRatio.
_ASPECTS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]
_TRANSIENT = {500, 502, 503, 504}


def nearest_aspect(size: Tuple[int, int]) -> str:
    w, h = size
    target = w / h
    return min(_ASPECTS, key=lambda a: abs(int(a.split(":")[0]) / int(a.split(":")[1]) - target))


class GeminiImageProvider:
    name = "gemini"

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: float = 180.0,
        transient_retries: int = 1,
        backoff_seconds: float = 5.0,
        fallback_models: Optional[Sequence[str]] = None,
        max_calls: Optional[int] = None,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        if fallback_models is None:
            fallback_models = () if model else FALLBACK_MODELS
        self._fallback_models = tuple(fallback_models)
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._timeout = timeout_seconds
        self._transient_retries = transient_retries
        self._backoff = backoff_seconds
        self.max_calls = max_calls
        self.calls = 0  # HTTP requests made
        self.model_used: Optional[str] = None
        self.last_text: str = ""  # any text parts returned with the image
        self.last_usage: Dict[str, Any] = {}

    # ------------------------------------------------------------------

    def build_request(
        self,
        prompt: str,
        *,
        size_hint: Optional[Tuple[int, int]] = None,
        reference_png: Optional[bytes] = None,
        modalities: Sequence[str] = ("IMAGE",),
        model: Optional[str] = None,
    ) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        """Return ``(url, headers, payload)`` — separated out for tests."""
        parts: List[Dict[str, Any]] = [{"text": prompt}]
        if reference_png:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": base64.b64encode(reference_png).decode("ascii"),
                    }
                }
            )
        gen: Dict[str, Any] = {"responseModalities": list(modalities)}
        if size_hint:
            gen["imageConfig"] = {"aspectRatio": nearest_aspect(size_hint)}
        payload = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen}
        url = f"{self._base_url}/models/{model or self.model}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._api_key or ""}
        return url, headers, payload

    def generate(
        self,
        prompt: str,
        *,
        size_hint: Optional[Tuple[int, int]] = None,
        reference_png: Optional[bytes] = None,
    ) -> bytes:
        if not self._api_key:
            raise ImageProviderError(
                "Gemini image provider requires an API key. "
                "Pass api_key=... or set GEMINI_API_KEY (e.g. in .env)."
            )
        models = [self.model, *self._fallback_models]
        last: Optional[ProviderError] = None
        for i, model in enumerate(models):
            try:
                response = self._call_model(model, prompt, size_hint, reference_png)
            except ProviderError as err:
                last = err
                if err.status in (_TRANSIENT | {429}) and i < len(models) - 1:
                    continue
                raise self._wrap(err, model) from err
            self.model_used = model
            return self._extract_png(response)
        raise self._wrap(last, models[-1])  # pragma: no cover

    # ------------------------------------------------------------------

    def _call_model(self, model, prompt, size_hint, reference_png) -> Dict[str, Any]:
        modalities: Sequence[str] = ("IMAGE",)
        while True:
            url, headers, payload = self.build_request(
                prompt, size_hint=size_hint, reference_png=reference_png,
                modalities=modalities, model=model,
            )
            try:
                return self._post_with_retry(url, headers, payload)
            except ProviderError as err:
                msg = str(err).lower()
                if err.status == 400 and "modalit" in msg and tuple(modalities) == ("IMAGE",):
                    modalities = ("TEXT", "IMAGE")
                    continue
                raise

    def _post(self, url, headers, payload) -> Dict[str, Any]:
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise ImageProviderError(f"image call budget exhausted ({self.max_calls} calls)")
        self.calls += 1
        return http_json_post(url, headers, payload, self._timeout)

    def _post_with_retry(self, url, headers, payload) -> Dict[str, Any]:
        for attempt in range(self._transient_retries + 1):
            try:
                return self._post(url, headers, payload)
            except ProviderError as err:
                if err.status not in _TRANSIENT or attempt == self._transient_retries:
                    raise
                time.sleep(self._backoff * (2**attempt))
        raise AssertionError("unreachable")

    @staticmethod
    def _wrap(err: Optional[ProviderError], model: str) -> ImageProviderError:
        if isinstance(err, ImageProviderError):
            return err
        status = getattr(err, "status", None)
        if status == 429:
            return ImageProviderError(
                f"Gemini image quota exhausted for {model} (HTTP 429). The free tier may not "
                f"allow image generation at all ('limit: 0'). Details: {err}",
                status=429,
            )
        return ImageProviderError(f"Gemini image call failed ({model}): {err}", status=status)

    def _extract_png(self, response: Dict[str, Any]) -> bytes:
        self.last_usage = dict(response.get("usageMetadata") or {})
        try:
            candidate = response["candidates"][0]
            parts = candidate.get("content", {}).get("parts", []) or []
        except (KeyError, IndexError, TypeError) as exc:
            raise ImageProviderError(
                f"Gemini response has no candidates (promptFeedback={response.get('promptFeedback')})."
            ) from exc
        self.last_text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        for p in parts:
            inline = p.get("inlineData") or p.get("inline_data") if isinstance(p, dict) else None
            if inline and inline.get("data"):
                data = base64.b64decode(inline["data"])
                mime = inline.get("mimeType") or inline.get("mime_type") or ""
                return data if mime == "image/png" or data[:8] == b"\x89PNG\r\n\x1a\n" else _to_png(data)
        raise ImageProviderError(
            f"Gemini returned no image (finishReason={candidate.get('finishReason')}, "
            f"text={self.last_text[:200]!r})."
        )


def _to_png(data: bytes) -> bytes:
    """Convert JPEG/WebP bytes to PNG (needs Pillow)."""
    from art import require_pillow

    Image = require_pillow()
    buf = io.BytesIO()
    Image.open(io.BytesIO(data)).save(buf, format="PNG")
    return buf.getvalue()
