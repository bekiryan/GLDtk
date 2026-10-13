"""/generate end to end with a FakeProvider, plus the .env loader."""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

import server
from llm import FakeProvider


@pytest.fixture
def client():
    return TestClient(server.app)


def test_generate_with_fake_provider_writes_ldtk(client, monkeypatch, tmp_path, load_fixture):
    fake = FakeProvider([load_fixture("valid_two_sections.json")])
    seen = {}

    def factory(req):
        seen["provider"], seen["model"] = req.provider, req.model
        return fake

    monkeypatch.setattr(server, "provider_factory", factory)
    out = tmp_path / "gen" / "level.ldtk"
    resp = client.post(
        "/generate",
        json={"description": "A meadow then a dungeon.", "output_path": str(out)},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["success"], body.get("error")
    assert seen == {"provider": "gemini", "model": None}  # request defaults
    assert body["sections"] == ["Grassy Meadow", "Dungeon"]
    assert body["requirements"][0] == "There is a grassy meadow section."
    assert len(body["requirements"]) == 6
    assert body["stats"]["nodes"] == 6

    doc = json.loads(out.read_text(encoding="utf-8"))
    level = doc["levels"][0]
    # spec.size (60×20) drives the level size when the request leaves it unset
    assert level["pxWid"] == 60 * 16
    assert level["pxHei"] == 20 * 16
    assert level["layerInstances"]


def test_generate_request_size_overrides_spec(client, monkeypatch, tmp_path, load_fixture):
    fake = FakeProvider([load_fixture("valid_minimal.json")])
    monkeypatch.setattr(server, "provider_factory", lambda req: fake)
    out = tmp_path / "level.ldtk"
    resp = client.post(
        "/generate",
        json={
            "description": "tiny",
            "output_path": str(out),
            "width_tiles": 80,
            "height_tiles": 30,
        },
    )
    assert resp.json()["success"]
    level = json.loads(out.read_text(encoding="utf-8"))["levels"][0]
    assert (level["pxWid"], level["pxHei"]) == (80 * 16, 30 * 16)
    assert "width_tiles=80" in fake.calls[0].messages[0]["content"]


def test_generate_reports_llm_failure(client, monkeypatch, tmp_path, load_fixture):
    bad = load_fixture("invalid_unknown_section.json")
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([bad]))
    resp = client.post(
        "/generate",
        json={"description": "x", "output_path": str(tmp_path / "l.ldtk"), "max_retries": 1},
    )
    body = resp.json()
    assert not body["success"]
    assert "UNKNOWN_SECTION" in body["error"]


def test_load_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n\nGLDTK_TEST_A=one\nGLDTK_TEST_B = \"two\"\n"
        "export GLDTK_TEST_C='three'\nGLDTK_TEST_KEEP=new\nnot a pair\n",
        encoding="utf-8",
    )
    for k in ("GLDTK_TEST_A", "GLDTK_TEST_B", "GLDTK_TEST_C"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GLDTK_TEST_KEEP", "old")
    server.load_env_file(env)
    assert os.environ["GLDTK_TEST_A"] == "one"
    assert os.environ["GLDTK_TEST_B"] == "two"
    assert os.environ["GLDTK_TEST_C"] == "three"
    assert os.environ["GLDTK_TEST_KEEP"] == "old"  # never overridden
    for k in ("GLDTK_TEST_A", "GLDTK_TEST_B", "GLDTK_TEST_C"):
        monkeypatch.delenv(k)
