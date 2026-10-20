"""GLDtk never writes into LDtk's bundled samples (api/samples.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import server
from api.samples import is_bundled_sample


@pytest.fixture
def client():
    return TestClient(server.app)


@pytest.fixture
def sample(tmp_path):
    """A copy of a bundled sample in a dev-tree-like ``extraFiles/samples`` folder."""
    d = tmp_path / "app" / "extraFiles" / "samples"
    d.mkdir(parents=True)
    f = d / "AutoLayers_1_basic.ldtk"
    f.write_text("{}", encoding="utf-8")
    return f


def test_is_bundled_sample(tmp_path):
    assert is_bundled_sample("/Applications/GLDtk.app/Contents/Resources/extraFiles/samples/a.ldtk")
    assert is_bundled_sample("/Applications/LDtk.app/Contents/Resources/a.ldtk")
    assert is_bundled_sample(tmp_path / "extraFiles" / "samples" / "sub" / "a.ldtk")
    assert not is_bundled_sample(tmp_path / "samples" / "a.ldtk")
    assert not is_bundled_sample(tmp_path / "extraFiles" / "a.ldtk")
    assert not is_bundled_sample(None)


@pytest.mark.parametrize("route, body", [
    ("/generate", lambda p: {"description": "a level", "output_path": p}),
    ("/generate", lambda p: {"description": "", "output_path": p, "level": "Level_0"}),
    ("/edit/region", lambda p: {"path": p, "prompt": "x", "rect": {"col": 0, "row": 0, "w": 2, "h": 2}}),
    ("/edit/section", lambda p: {"path": p, "prompt": "x", "section_id": "s0"}),
    ("/edit/entities", lambda p: {"path": p, "prompt": "x"}),
    ("/undo", lambda p: {"path": p}),
    ("/chunks", lambda p: {"path": p, "count": 1}),
])
def test_write_routes_refuse_samples(client, sample, route, body):
    before = sorted(p.name for p in sample.parent.iterdir())
    resp = client.post(route, json=body(str(sample)))
    assert resp.status_code == 400, resp.text
    assert "bundled LDtk sample" in resp.json()["detail"]
    assert sorted(p.name for p in sample.parent.iterdir()) == before  # no history, assets, profile
    assert sample.read_text(encoding="utf-8") == "{}"


def test_profile_put_refuses_samples(client, sample):
    from game import GameProfile

    resp = client.put("/profile", params={"path": str(sample)}, json=GameProfile().model_dump())
    assert resp.status_code == 400
    assert [p.name for p in sample.parent.iterdir()] == [sample.name]
