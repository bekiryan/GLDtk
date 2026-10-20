"""Provider protocol + shared HTTP helper for structured-output LLM calls.

Every provider takes a static system prompt, a chat-style message list
(``[{"role": "user"|"assistant", "content": str}, ...]``) and a JSON schema,
and returns the model's output as a JSON *string*.  Enforcing the schema is the
provider's job (Gemini ``responseJsonSchema``, Anthropic forced tool, Ollama
``format``, OpenAI ``response_format``); validating the content is the
controller's job.
"""

from __future__ import annotations

import json
from http.client import HTTPException
from typing import Any, Dict, List, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ─────────────────────────────────────────────────────────────────────────────
# Protocol
# ─────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def complete_json(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
    ) -> str: ...


class ProviderError(RuntimeError):
    """Raised when a provider call fails (HTTP, connection, empty output)."""

    def __init__(
        self,
        message: str,
        status: int | None = None,
        timed_out: bool = False,
        missing_key: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.timed_out = timed_out  # the request timed out (no HTTP status)
        self.missing_key = missing_key  # no API key set; nothing was sent


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────


def http_json_post(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout_seconds: float,
) -> Dict[str, Any]:
    """POST *payload* as JSON and return the decoded JSON response.

    Secrets must travel in *headers*, never in *url*: the URL appears in error
    messages.
    """
    body = json.dumps(payload).encode("utf-8")
    request = Request(url=url, data=body, headers=headers, method="POST")

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)
    except HTTPError as err:
        try:
            err_body = err.read().decode("utf-8")
        except Exception:
            err_body = "<unreadable error body>"
        raise ProviderError(
            f"LLM API HTTP error {err.code} when calling {url}: {err_body}",
            status=err.code,
        ) from err
    except URLError as err:
        raise ProviderError(f"LLM API connection error when calling {url}: {err}") from err
    except (OSError, HTTPException) as err:  # timeouts, dropped connections
        raise ProviderError(
            f"LLM API transport error when calling {url}: {type(err).__name__}: {err}",
            timed_out=isinstance(err, TimeoutError),
        ) from err
