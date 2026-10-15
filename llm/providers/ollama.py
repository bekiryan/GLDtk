"""Ollama provider — local ``/api/chat`` with ``format: <json schema>``."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import base

DEFAULT_MODEL = "llama3.1"
DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: float = 300.0,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._timeout_seconds = timeout_seconds

    def build_request(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
    ) -> tuple[str, Dict[str, str], Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "format": schema,
            "stream": False,
            "options": {"temperature": 0},
        }
        return f"{self._base_url}/api/chat", {"Content-Type": "application/json"}, payload

    def complete_json(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
    ) -> str:
        url, headers, payload = self.build_request(system, messages, schema)
        response = base.http_json_post(url, headers, payload, self._timeout_seconds)
        try:
            text = response["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise base.ProviderError("Ollama response missing message.content") from exc
        if not isinstance(text, str) or not text.strip():
            raise base.ProviderError("Ollama response contained empty text.")
        return text
