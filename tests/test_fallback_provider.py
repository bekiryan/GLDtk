"""Gemini → local model fallback (FallbackProvider + registry + server warning)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import server
from llm import FakeProvider
from llm.providers import FallbackProvider, ProviderError, make_provider, make_provider_with_fallback
from llm.providers import gemini, ollama
from llm.providers.registry import parse_fallback


class _Failing:
    name = "gemini"
    model = "gemini-3.8-flash"

    def __init__(self, err: ProviderError) -> None:
        self.err = err
        self.calls = 0

    def complete_json(self, system, messages, schema):
        self.calls += 1
        raise self.err


def _local(responses):
    fake = FakeProvider(responses)
    fake.name, fake.model = "ollama", "qwen2.5:14b"
    return fake


@pytest.mark.parametrize(
    "err",
    [
        ProviderError("quota", status=429),
        ProviderError("busy", status=503),
        ProviderError("retired", status=404),
        ProviderError("slow", timed_out=True),
        ProviderError("no network"),
    ],
)
def test_falls_back_on_service_errors(err):
    primary, local = _Failing(err), _local(['{"a": 1}', '{"b": 2}'])
    p = FallbackProvider([primary, local])
    assert p.complete_json("s", [], {}) == '{"a": 1}'
    assert p.name == "ollama" and p.model_used == "ollama/qwen2.5:14b"
    assert len(p.notes) == 1 and "answered by ollama/qwen2.5:14b" in p.notes[0]
    # sticky: the next call of the same request skips the failing provider
    assert p.complete_json("s", [], {}) == '{"b": 2}'
    assert primary.calls == 1 and len(p.notes) == 1


def test_client_errors_do_not_fall_back():
    primary, local = _Failing(ProviderError("bad key", status=400)), _local(['{}'])
    with pytest.raises(ProviderError, match="bad key"):
        FallbackProvider([primary, local]).complete_json("s", [], {})
    assert local.calls == []


def test_all_failing_reports_every_error():
    a = _Failing(ProviderError("quota", status=429))
    b = _Failing(ProviderError("refused"))
    b.name, b.model = "ollama", "qwen2.5:14b"
    with pytest.raises(ProviderError) as exc:
        FallbackProvider([a, b]).complete_json("s", [], {})
    assert "gemini/gemini-3.8-flash: HTTP 429" in str(exc.value)
    assert "ollama/qwen2.5:14b: refused" in str(exc.value)


def test_parse_fallback():
    assert parse_fallback("ollama:qwen2.5:14b") == ("ollama", "qwen2.5:14b")
    assert parse_fallback("ollama") == ("ollama", None)
    assert parse_fallback("off") is None and parse_fallback("") is None
    assert parse_fallback(None) == ("ollama", "qwen2.5:14b")  # the default


def test_registry_wraps_default_gemini_only(monkeypatch):
    monkeypatch.delenv("GLDTK_LOCAL_FALLBACK", raising=False)
    p = make_provider_with_fallback("gemini")
    assert isinstance(p, FallbackProvider)
    primary, local = p._providers
    assert isinstance(primary, gemini.GeminiProvider)
    assert isinstance(local, ollama.OllamaProvider) and local.model == "qwen2.5:14b"
    # an explicit Gemini model still falls back locally
    p = make_provider_with_fallback("gemini", model="gemini-3.1-flash-lite")
    assert isinstance(p, FallbackProvider) and p._providers[0].model == "gemini-3.1-flash-lite"
    # another provider, or "off" → no wrapper
    assert isinstance(make_provider_with_fallback("ollama"), ollama.OllamaProvider)
    monkeypatch.setenv("GLDTK_LOCAL_FALLBACK", "off")
    assert isinstance(make_provider_with_fallback("gemini"), gemini.GeminiProvider)


def test_ollama_gets_a_long_timeout():
    assert make_provider("ollama")._timeout_seconds >= 600
    assert make_provider("gemini")._timeout_seconds == 120


def test_generate_reports_the_fallback(monkeypatch, tmp_path, load_fixture):
    local = _local([load_fixture("valid_minimal.json")])
    monkeypatch.setattr(
        server, "provider_factory",
        lambda req: FallbackProvider([_Failing(ProviderError("quota", status=429)), local]),
    )
    out = tmp_path / "level.ldtk"
    body = TestClient(server.app).post(
        "/generate", json={"description": "tiny", "output_path": str(out), "densify": False}
    ).json()
    assert body["success"], body.get("error")
    assert body["warnings"][0].startswith("gemini unavailable (HTTP 429); answered by ollama")
