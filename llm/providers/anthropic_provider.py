"""Anthropic provider — structured output via a single forced tool.

The schema becomes the ``input_schema`` of one tool (``emit_level_spec``) and
``tool_choice`` forces the model to call it; the tool input *is* the spec.
``SYSTEM_PROMPT`` is sent as a cached system block (the tool definition sits
before it in the cache prefix, so both are cached together).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import anthropic

from . import base

DEFAULT_MODEL = "claude-sonnet-5-5"
TOOL_NAME = "emit_level_spec"


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: float = 120.0,
        max_tokens: int = 8192,
        client: Any = None,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._timeout_seconds = timeout_seconds
        self._max_tokens = max_tokens
        self._client = client  # injectable for tests

    def build_request(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return the ``messages.create`` keyword arguments."""
        return {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": system,
                    # Cache the large, immutable system prompt (and the tool
                    # schema before it) across retries and requests.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "tools": [
                {
                    "name": TOOL_NAME,
                    "description": "Emit the complete level specification.",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
            "messages": messages,
        }

    def complete_json(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
    ) -> str:
        if self._client is None:
            if not self._api_key:
                raise base.ProviderError(
                    "Anthropic provider requires an API key. "
                    "Pass api_key=... or set ANTHROPIC_API_KEY."
                )
            self._client = anthropic.Anthropic(
                api_key=self._api_key, timeout=self._timeout_seconds
            )

        response = self._client.messages.create(
            **self.build_request(system, messages, schema)
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
                return json.dumps(block.input)
        raise base.ProviderError(
            f"Anthropic response contained no '{TOOL_NAME}' tool call "
            f"(stop_reason={getattr(response, 'stop_reason', None)})."
        )
