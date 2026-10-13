"""OpenAI-compatible provider (openai, deepseek) — not first-class.

* ``openai``   → ``response_format: {type: json_schema, strict: true}``
* ``deepseek`` → ``response_format: {type: json_object}`` (no schema support);
  the schema is still described by the system prompt.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import base

DEFAULT_MODELS: Dict[str, str] = {
    "openai": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
}

DEFAULT_BASE_URLS: Dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
}

_API_KEY_ENV: Dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def _extract_message_text(content: Any) -> str:
    """Normalize OpenAI-compatible content payloads to a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif isinstance(part.get("content"), str):
                    parts.append(part["content"])
        return "\n".join(parts).strip()
    return str(content).strip()


class OpenAICompatProvider:
    def __init__(
        self,
        flavor: str = "openai",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        if flavor not in DEFAULT_MODELS:
            raise ValueError(f"Unknown OpenAI-compatible flavor: {flavor!r}")
        self.name = flavor
        self.model = model or DEFAULT_MODELS[flavor]
        self._api_key = api_key or os.getenv(_API_KEY_ENV[flavor])
        self._base_url = (base_url or DEFAULT_BASE_URLS[flavor]).rstrip("/")
        self._timeout_seconds = timeout_seconds

    def build_request(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
    ) -> tuple[str, Dict[str, str], Dict[str, Any]]:
        if self.name == "openai":
            response_format: Dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {"name": "level_spec", "strict": True, "schema": schema},
            }
        else:
            response_format = {"type": "json_object"}
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "response_format": response_format,
            "max_tokens": 8192,
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key or ''}",
            "Content-Type": "application/json",
        }
        return f"{self._base_url}/chat/completions", headers, payload

    def complete_json(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
    ) -> str:
        if not self._api_key:
            raise base.ProviderError(
                f"{self.name} provider requires an API key. "
                f"Pass api_key=... or set {_API_KEY_ENV[self.name]}."
            )
        url, headers, payload = self.build_request(system, messages, schema)
        response = base.http_json_post(url, headers, payload, self._timeout_seconds)
        try:
            raw_content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise base.ProviderError(
                "OpenAI-compatible response missing choices[0].message.content"
            ) from exc
        text = _extract_message_text(raw_content)
        if not text:
            raise base.ProviderError("OpenAI-compatible response contained empty text.")
        return text
