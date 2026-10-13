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


# ── Game Profile ─────────────────────────────────────────────────────────────


def test_profile_get_put_round_trip(client, tmp_path):
    from game import PROFILE_FILENAME, GameProfile

    ldtk = tmp_path / "proj" / "level.ldtk"
    resp = client.get("/profile", params={"path": str(ldtk)})
    assert resp.status_code == 200
    assert resp.json() == GameProfile().model_dump()  # defaults, nothing written
    assert not (ldtk.parent / PROFILE_FILENAME).exists()

    body = GameProfile(name="Moon").model_dump()
    body["abilities"]["dash"]["enabled"] = True
    body["physics"]["gravity"] = 500
    resp = client.put("/profile", params={"path": str(ldtk)}, json=body)
    assert resp.status_code == 200
    written = ldtk.parent / PROFILE_FILENAME
    assert written.is_file()

    # GET by the profile file path itself returns what was PUT
    assert client.get("/profile", params={"path": str(written)}).json() == body


def test_profile_put_rejects_invalid(client, tmp_path):
    resp = client.put(
        "/profile",
        params={"path": str(tmp_path / "level.ldtk")},
        json={"physics": {"gravity": -5}},
    )
    assert resp.status_code == 422


def test_profile_get_reports_broken_file(client, tmp_path):
    from game import PROFILE_FILENAME

    (tmp_path / PROFILE_FILENAME).write_text("{oops", encoding="utf-8")
    resp = client.get("/profile", params={"path": str(tmp_path / "level.ldtk")})
    assert resp.status_code == 400
    assert "not valid JSON" in resp.json()["detail"]


def test_generate_uses_profile_file(client, monkeypatch, tmp_path, minimal_spec_dict):
    from game import PROFILE_FILENAME, GameProfile, save_profile

    # Exit 200 px up: impossible as a plain jump, fine with double_jump.
    d = json.loads(json.dumps(minimal_spec_dict))
    d["nodes"][1]["y"] = 232
    d["edges"] = [{"from": "start", "to": "exit", "type": "Jump", "vx": 50, "vy": 600,
                   "ability": "double_jump"}]
    fake = FakeProvider([json.dumps(d)])
    monkeypatch.setattr(server, "provider_factory", lambda req: fake)

    profile = GameProfile()
    profile.abilities.double_jump.enabled = True
    profile_path = save_profile(profile, tmp_path / PROFILE_FILENAME)

    out = tmp_path / "level.ldtk"
    body = client.post(
        "/generate",
        json={"description": "a tall jump", "output_path": str(out), "max_retries": 1},
    ).json()
    assert body["success"], body.get("error")
    assert body["profile_path"] == str(profile_path.resolve())
    assert "double_jump" in fake.calls[0].messages[0]["content"]


def test_generate_explicit_profile_path_missing_uses_defaults(
    client, monkeypatch, tmp_path, load_fixture
):
    fake = FakeProvider([load_fixture("valid_minimal.json")])
    monkeypatch.setattr(server, "provider_factory", lambda req: fake)
    missing = tmp_path / "nowhere" / "game.json"
    body = client.post(
        "/generate",
        json={
            "description": "tiny",
            "output_path": str(tmp_path / "level.ldtk"),
            "game_profile_path": str(missing),
        },
    ).json()
    assert body["success"], body.get("error")
    assert body["profile_path"] == str(missing.resolve())
    assert not missing.exists()  # generate never writes a profile


# ── Layout v2 (Part 3) ───────────────────────────────────────────────────────


def test_generate_is_verified_and_counts_spec_entities(client, monkeypatch, tmp_path, load_fixture):
    from collections import Counter

    raw = load_fixture("valid_two_sections.json")
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([raw]))
    out = tmp_path / "level.ldtk"
    body = client.post(
        "/generate", json={"description": "A meadow then a dungeon.", "output_path": str(out)}
    ).json()
    assert body["success"], body.get("error")
    assert body["verified"] is True
    assert isinstance(body["warnings"], list)
    assert body["stats"]["layout_retries"] == 0
    assert body["stats"]["grid_repairs"] == 0
    expected = Counter(e["kind"] for e in json.loads(raw)["entities"])
    assert body["stats"]["entities"] == dict(expected)  # no random coins / enemies

    doc = json.loads(out.read_text(encoding="utf-8"))  # loads as JSON
    layer = next(l for l in doc["levels"][0]["layerInstances"] if l["__type"] == "Entities")
    assert len(layer["entityInstances"]) == sum(expected.values())


def test_generate_reports_unverifiable_level(client, monkeypatch, tmp_path, load_fixture):
    bad = load_fixture("grid_unrepairable_tunnel.json")
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([bad] * 3))
    out = tmp_path / "level.ldtk"
    body = client.post(
        "/generate", json={"description": "tunnel", "output_path": str(out)}
    ).json()
    assert not body["success"] and body["verified"] is False
    assert "NO_GRID_PATH" in body["error"]
    assert not out.exists()  # never write an unverified level
