"""Packaged-app support in server.py: settings, fake-LLM switch, --check (v3 Part 18)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import server


NO_KEYS = {"gemini": False, "anthropic": False, "openai": False, "deepseek": False}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GLDTK_CONFIG_DIR", str(tmp_path))
    for env in server.PROVIDER_KEY_ENV.values():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.delenv("GLDTK_LOCAL_FALLBACK", raising=False)
    return TestClient(server.app)


def test_settings_roundtrip_hides_key(client, tmp_path):
    assert client.get("/settings").json() == {"has_key": False, "keys": NO_KEYS, "local_fallback": ""}
    r = client.post("/settings", json={"gemini_api_key": "secret-key", "local_fallback": "qwen"})
    assert r.status_code == 200
    assert "secret-key" not in r.text
    assert r.json() == {"has_key": True, "keys": {**NO_KEYS, "gemini": True}, "local_fallback": "qwen"}
    env = (tmp_path / ".env").read_text()
    assert "GEMINI_API_KEY=secret-key" in env and "GLDTK_LOCAL_FALLBACK=qwen" in env
    # updating one key keeps the other
    client.post("/settings", json={"gemini_api_key": "other"})
    env = (tmp_path / ".env").read_text()
    assert env.count("GEMINI_API_KEY=") == 1 and "GLDTK_LOCAL_FALLBACK=qwen" in env


def test_settings_rejects_multiline(client):
    assert client.post("/settings", json={"gemini_api_key": "a\nB=c"}).status_code == 400


def test_settings_key_per_provider(client, tmp_path):
    r = client.post("/settings", json={"provider": "anthropic", "api_key": "sk-ant"})
    assert r.status_code == 200 and "sk-ant" not in r.text
    assert r.json()["keys"] == {**NO_KEYS, "anthropic": True}
    assert r.json()["has_key"] is False
    client.post("/settings", json={"provider": "deepseek", "api_key": "sk-ds"})
    env = (tmp_path / ".env").read_text()
    assert "ANTHROPIC_API_KEY=sk-ant" in env and "DEEPSEEK_API_KEY=sk-ds" in env
    assert client.get("/settings").json()["keys"] == {**NO_KEYS, "anthropic": True, "deepseek": True}


def test_settings_key_rejects_keyless_provider(client):
    assert client.post("/settings", json={"provider": "ollama", "api_key": "x"}).status_code == 400
    assert client.post("/settings", json={"api_key": "x"}).status_code == 400


def test_fake_llm_switch(monkeypatch):
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "llm" / "valid_two_sections.json"
    monkeypatch.setenv("GLDTK_FAKE_LLM", str(fixture))

    class Req:
        provider, model = "gemini", None

    assert server.default_provider_factory(Req()).name == "fake"


def test_check_flag(capsys):
    assert server.check() == 0
    assert "check ok" in capsys.readouterr().out
