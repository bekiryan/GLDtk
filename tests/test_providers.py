"""Request payloads of the real providers — HTTP / SDK monkeypatched, no network."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from llm.providers import ProviderError, make_provider, _DEFAULT_PROVIDER_MODELS
from llm.providers import anthropic_provider, base, gemini, ollama, openai_compat
from spec import LevelSpec

SCHEMA = LevelSpec.json_schema()
MESSAGES = [
    {"role": "user", "content": "make a level"},
    {"role": "assistant", "content": "{bad}"},
    {"role": "user", "content": "fix it"},
]


class _Recorder:
    def __init__(self, response: Dict[str, Any]) -> None:
        self.response = response
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, url, headers, payload, timeout_seconds):
        self.calls.append(
            {"url": url, "headers": headers, "payload": payload, "timeout": timeout_seconds}
        )
        return self.response


# ─────────────────────────────────────────────────────────────────────────────
# Gemini
# ─────────────────────────────────────────────────────────────────────────────


def test_gemini_payload(monkeypatch):
    rec = _Recorder(
        {
            "candidates": [
                {"content": {"parts": [{"text": "thinking", "thought": True}, {"text": '{"a": 1}'}]}}
            ],
            "usageMetadata": {"totalTokenCount": 42},
        }
    )
    monkeypatch.setattr(base, "http_json_post", rec)
    p = gemini.GeminiProvider(model="gemini-test", api_key="KEY")
    out = p.complete_json("SYS", MESSAGES, SCHEMA)

    assert out == '{"a": 1}'  # thought parts are skipped
    call = rec.calls[0]
    assert call["url"].endswith("/v1beta/models/gemini-test:generateContent")
    assert "KEY" not in call["url"]  # key only in the header
    assert call["headers"]["x-goog-api-key"] == "KEY"
    payload = call["payload"]
    assert payload["system_instruction"] == {"parts": [{"text": "SYS"}]}
    assert [c["role"] for c in payload["contents"]] == ["user", "model", "user"]
    assert payload["contents"][1]["parts"] == [{"text": "{bad}"}]
    cfg = payload["generationConfig"]
    assert cfg["responseMimeType"] == "application/json"
    js = cfg["responseJsonSchema"]
    assert "additionalProperties" not in json.dumps(js)
    assert set(js["properties"]) == set(SCHEMA["properties"])
    assert p.last_usage == {"totalTokenCount": 42}


def test_gemini_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        gemini.GeminiProvider(api_key=None).complete_json("s", MESSAGES, SCHEMA)


def test_gemini_retries_transient_503(monkeypatch):
    calls = {"n": 0}

    def flaky(url, headers, payload, timeout_seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError("overloaded", status=503)
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}

    monkeypatch.setattr(base, "http_json_post", flaky)
    p = gemini.GeminiProvider(api_key="K", backoff_seconds=0)
    assert p.complete_json("s", MESSAGES, SCHEMA) == "{}"
    assert calls["n"] == 2


def test_gemini_falls_back_on_persistent_503(monkeypatch):
    urls = []

    def overloaded_primary(url, headers, payload, timeout_seconds):
        urls.append(url)
        if "/models/primary:" in url:
            raise ProviderError("overloaded", status=503)
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}

    monkeypatch.setattr(base, "http_json_post", overloaded_primary)
    p = gemini.GeminiProvider(
        model="primary", api_key="K", backoff_seconds=0, fallback_models=("backup",)
    )
    assert p.complete_json("s", MESSAGES, SCHEMA) == "{}"
    assert p.model_used == "backup" and p.model == "primary"
    assert sum("/models/primary:" in u for u in urls) == 3  # 1 call + 2 retries
    assert urls[-1].endswith("/models/backup:generateContent")


def test_gemini_falls_back_on_quota_429_without_retrying(monkeypatch):
    urls = []

    def quota_primary(url, headers, payload, timeout_seconds):
        urls.append(url)
        if "/models/primary:" in url:
            raise ProviderError("quota", status=429)
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}

    monkeypatch.setattr(base, "http_json_post", quota_primary)
    p = gemini.GeminiProvider(
        model="primary", api_key="K", backoff_seconds=0, fallback_models=("backup",)
    )
    assert p.complete_json("s", MESSAGES, SCHEMA) == "{}"
    assert len(urls) == 2 and p.model_used == "backup"


def test_gemini_explicit_model_has_no_fallback():
    assert make_provider("gemini", model="x", api_key="K")._fallback_models == ()
    assert make_provider("gemini", api_key="K")._fallback_models == gemini.FALLBACK_MODELS


def test_gemini_does_not_retry_client_errors(monkeypatch):
    def bad(url, headers, payload, timeout_seconds):
        raise ProviderError("bad request", status=400)

    monkeypatch.setattr(base, "http_json_post", bad)
    with pytest.raises(ProviderError):
        gemini.GeminiProvider(api_key="K", backoff_seconds=0).complete_json("s", [], SCHEMA)


# ─────────────────────────────────────────────────────────────────────────────
# Anthropic
# ─────────────────────────────────────────────────────────────────────────────


class _FakeMessages:
    def __init__(self) -> None:
        self.kwargs: Dict[str, Any] = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[
                SimpleNamespace(
                    type="tool_use", name=anthropic_provider.TOOL_NAME, input={"title": "T"}
                )
            ],
        )


def test_anthropic_forced_tool_payload():
    messages = _FakeMessages()
    client = SimpleNamespace(messages=messages)
    p = anthropic_provider.AnthropicProvider(client=client)
    out = p.complete_json("SYS", MESSAGES, SCHEMA)

    assert json.loads(out) == {"title": "T"}
    kw = messages.kwargs
    assert kw["model"] == "claude-sonnet-5-5"
    assert kw["system"] == [
        {"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}
    ]
    assert kw["tools"][0]["name"] == "emit_level_spec"
    assert kw["tools"][0]["input_schema"] == SCHEMA
    assert kw["tool_choice"] == {"type": "tool", "name": "emit_level_spec"}
    assert kw["messages"] == MESSAGES


def test_anthropic_without_tool_call_raises():
    class NoTool(_FakeMessages):
        def create(self, **kwargs):
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text")])

    p = anthropic_provider.AnthropicProvider(client=SimpleNamespace(messages=NoTool()))
    with pytest.raises(ProviderError):
        p.complete_json("SYS", MESSAGES, SCHEMA)


# ─────────────────────────────────────────────────────────────────────────────
# Ollama
# ─────────────────────────────────────────────────────────────────────────────


def test_ollama_payload(monkeypatch):
    rec = _Recorder({"message": {"content": '{"ok": true}'}})
    monkeypatch.setattr(base, "http_json_post", rec)
    out = ollama.OllamaProvider().complete_json("SYS", MESSAGES, SCHEMA)
    assert out == '{"ok": true}'
    call = rec.calls[0]
    assert call["url"] == "http://localhost:11434/api/chat"
    payload = call["payload"]
    assert payload["model"] == "llama3.1"
    assert payload["format"] == SCHEMA
    assert payload["stream"] is False
    assert payload["messages"][0] == {"role": "system", "content": "SYS"}
    assert payload["messages"][1:] == MESSAGES


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI-compatible
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("flavor", ["openai", "deepseek"])
def test_openai_compat_payload(monkeypatch, flavor):
    rec = _Recorder({"choices": [{"message": {"content": "{}"}}]})
    monkeypatch.setattr(base, "http_json_post", rec)
    p = openai_compat.OpenAICompatProvider(flavor=flavor, api_key="K")
    assert p.complete_json("SYS", MESSAGES, SCHEMA) == "{}"
    call = rec.calls[0]
    assert call["url"].endswith("/chat/completions")
    assert call["headers"]["Authorization"] == "Bearer K"
    fmt = call["payload"]["response_format"]
    if flavor == "openai":
        assert fmt["type"] == "json_schema"
        assert fmt["json_schema"]["strict"] is True
        assert fmt["json_schema"]["schema"] == SCHEMA
    else:
        assert fmt == {"type": "json_object"}


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────


def test_registry_defaults():
    assert set(_DEFAULT_PROVIDER_MODELS) == {"gemini", "anthropic", "openai", "deepseek", "ollama"}
    assert make_provider("gemini", api_key="K").model == gemini.DEFAULT_MODEL
    assert make_provider("claude").name == "anthropic"
    assert make_provider("ollama", model="qwen").model == "qwen"
    assert make_provider("deepseek").name == "deepseek"
    with pytest.raises(ValueError):
        make_provider("nope")


def test_validator_reexports_default_models():
    from llm.validator import _DEFAULT_PROVIDER_MODELS as from_validator

    assert from_validator is _DEFAULT_PROVIDER_MODELS


def test_gemini_falls_back_on_timeout_without_retrying(monkeypatch):
    urls = []

    def slow_primary(url, headers, payload, timeout_seconds):
        urls.append(url)
        if "/models/primary:" in url:
            raise ProviderError("transport error: timed out", timed_out=True)
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}

    monkeypatch.setattr(base, "http_json_post", slow_primary)
    p = gemini.GeminiProvider(
        model="primary", api_key="K", backoff_seconds=0, fallback_models=("backup",)
    )
    assert p.complete_json("s", MESSAGES, SCHEMA) == "{}"
    assert len(urls) == 2 and p.model_used == "backup"


def test_transport_timeout_is_flagged(monkeypatch):
    import socket

    def timeout(*a, **kw):
        raise socket.timeout("The read operation timed out")

    monkeypatch.setattr(base, "urlopen", timeout)
    with pytest.raises(ProviderError) as exc:
        base.http_json_post("http://x", {}, {}, 1.0)
    assert exc.value.timed_out and exc.value.status is None
