"""/art endpoints (Part 6) with the fake image provider and a temp library."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aesthetic.library import DEFAULT_ROOT, clear_cache
from api import art_routes
from art.providers import ImageProviderError
from art.providers.fake import FakeImageProvider

FIX = Path(__file__).resolve().parent / "fixtures" / "art"


@pytest.fixture
def client(tmp_path, monkeypatch):
    lib = tmp_path / "assets"
    shutil.copytree(DEFAULT_ROOT, lib, ignore=shutil.ignore_patterns("sources", "preview.png"))
    clear_cache()
    providers = []

    def factory(name, model=None):
        if name == "broken":
            raise ValueError("unknown image provider 'broken'")
        p = FakeImageProvider()
        providers.append(p)
        return p

    monkeypatch.setattr(art_routes, "image_provider_factory", factory)
    monkeypatch.setattr(art_routes, "library_root", lib)
    app = FastAPI()
    app.include_router(art_routes.router)
    c = TestClient(app)
    c.lib = lib
    c.providers = providers
    yield c
    clear_cache()
    from aesthetic.themes import reload_themes

    reload_themes()


def test_library_lists_themes_and_sprites(client):
    r = client.get("/art/library")
    assert r.status_code == 200
    body = r.json()
    assert any(t["id"] == "dungeon" and t["generated"] is False for t in body["themes"])
    assert len(body["sprites"]) >= 15
    assert all(Path(t["sheet_path"]).exists() for t in body["themes"])


def test_theme_endpoint_generates_and_lists(client):
    from aesthetic.themes import detect_theme

    desc = "crystal cavern with glowing purple moss"
    r = client.post("/art/theme", json={"description": desc})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["theme_id"] == "crystal_cavern_glowing"
    assert Path(body["sheet_path"]).is_file()
    assert Path(body["sheet_path"]).is_relative_to(client.lib)
    assert body["quality"]["path"] == "grid"
    assert detect_theme(desc).identifier == "crystal_cavern_glowing"

    listed = client.get("/art/library").json()["themes"]
    gen = [t for t in listed if t["generated"]]
    assert [t["id"] for t in gen] == ["crystal_cavern_glowing"]
    assert gen[0]["quality"]["colors"] <= 16

    dup = client.post("/art/theme", json={"description": desc, "theme_id": "crystal_cavern_glowing"})
    assert dup.status_code == 409


def test_sprite_endpoint(client):
    r = client.post("/art/sprite", json={"description": "green slime", "kind": "Enemy", "sprite_id": "slime_green"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sprite_id"] == "slime_green" and Path(body["file"]).is_file()
    sprites = client.get("/art/library").json()["sprites"]
    assert any(s["id"] == "slime_green" and s["generated"] for s in sprites)
    bad = client.post("/art/sprite", json={"description": "x", "kind": "Dragon"})
    assert bad.status_code == 400


def test_unknown_provider_is_400(client):
    r = client.post("/art/theme", json={"description": "x", "provider": "broken"})
    assert r.status_code == 400


def test_provider_quota_error_maps_to_429(client, monkeypatch):
    class Quota:
        name = "gemini"

        def generate(self, prompt, **kw):
            raise ImageProviderError("quota exhausted", status=429)

    monkeypatch.setattr(art_routes, "image_provider_factory", lambda name, model=None: Quota())
    r = client.post("/art/theme", json={"description": "lava caves"})
    assert r.status_code == 429 and "quota" in r.json()["detail"]
