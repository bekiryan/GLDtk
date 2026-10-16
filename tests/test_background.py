"""v4 Part 29c: the decorative Background layer (offline)."""

from __future__ import annotations

import json

from layout import TileValue
from llm import FakeProvider

from test_densify import _sparse
from test_edit import assert_schema_valid, client, doc_of  # noqa: F401


def _generate(client, monkeypatch, tmp_path, name, **extra):  # noqa: F811
    import server

    raw = json.dumps(_sparse().model_dump(by_alias=True))
    monkeypatch.setattr(server, "provider_factory", lambda req: FakeProvider([raw]))
    out = tmp_path / name / "level.ldtk"
    body = client.post("/generate", json={"description": "sparse", "output_path": str(out),
                                          "seed": 7, **extra}).json()
    assert body["success"], body.get("error")
    return body, doc_of(out)


def _coll(doc):
    return next(li for li in doc["levels"][0]["layerInstances"]
                if li["__identifier"] == "Collisions")["intGridCsv"]


def _ents(doc):
    return [(e["__identifier"], e["px"])
            for e in doc["levels"][0]["layerInstances"][0]["entityInstances"]]


def test_background_layer_is_schema_valid_ordered_and_decorative(client, monkeypatch, tmp_path):  # noqa: F811
    boff, off = _generate(client, monkeypatch, tmp_path, "off")
    bon, on = _generate(client, monkeypatch, tmp_path, "on", background=True)
    assert "Background" not in [d["identifier"] for d in off["defs"]["layers"]]
    defs = [d["identifier"] for d in on["defs"]["layers"]]
    insts = [li["__identifier"] for li in on["levels"][0]["layerInstances"]]
    assert defs == insts and defs[-2:] == ["Collisions", "Background"]
    assert_schema_valid(on)
    bg = on["levels"][0]["layerInstances"][-1]
    assert bg["__type"] == "IntGrid" and any(bg["intGridCsv"])
    bdef = on["defs"]["layers"][-1]
    assert bdef["intGridValues"] and all(v["tile"] for v in bdef["intGridValues"])
    uids = [d["uid"] for d in on["defs"]["layers"]]
    assert len(set(uids)) == len(uids)
    tilesets = {t["uid"] for t in on["defs"]["tilesets"]}
    assert all(v["tile"]["tilesetUid"] in tilesets for v in bdef["intGridValues"])
    # gameplay is identical: collisions, entities and the verification result
    assert _coll(on) == _coll(off) and _ents(on) == _ents(off)
    assert bon["stats"] == boff["stats"] and bon["warnings"] == boff["warnings"]
    for v, c in zip(bg["intGridCsv"], _coll(on)):  # nothing on solid rock
        assert not (v and c == int(TileValue.SOLID))


def test_background_noise_is_deterministic_and_read_only():
    from aesthetic.background import background_values
    from aesthetic.themes import THEMES
    from layout import IRLevel

    th = next(iter(THEMES.values()))
    ir = IRLevel.empty("L", 40, 20)
    for c in range(40):
        ir.grid[19][c] = TileValue.SOLID
        ir.grid[0][c] = TileValue.SOLID
    before = [list(r) for r in ir.grid]
    a = background_values(ir, lambda c, r: th, [], 3)
    assert a == background_values(ir, lambda c, r: th, [], 3)
    assert a != background_values(ir, lambda c, r: th, [], 4)
    assert ir.grid == before
    assert any(any(r) for r in a)
    whole = background_values(IRLevel.empty("W", 80, 20), lambda c, r: th, [], 3)
    right = background_values(IRLevel.empty("R", 40, 20), lambda c, r: th, [], 3, (40, 0))
    assert [r[40:] for r in whole] == right  # chunks share one field at their seam


def test_chunks_background_is_schema_valid_and_gameplay_identical(client, tmp_path):  # noqa: F811
    outs = {}
    for name, extra in (("off", {}), ("on", {"background": True})):
        out = tmp_path / name / "world.ldtk"
        body = client.post("/chunks", json={"path": str(out), "count": 2, "seed": 11,
                                            **extra}).json()
        assert body["success"], body.get("error")
        assert all(c["verified"] for c in body["chunks"])
        outs[name] = doc_of(out)
    on, off = outs["on"], outs["off"]
    assert_schema_valid(on)
    for lv in on["levels"]:
        assert [li["__identifier"] for li in lv["layerInstances"]] == \
            [d["identifier"] for d in on["defs"]["layers"]]
        assert any(next(li for li in lv["layerInstances"]
                        if li["__identifier"] == "Background")["intGridCsv"])

    def coll(d):
        return [next(li for li in lv["layerInstances"] if li["__identifier"] == "Collisions")
                ["intGridCsv"] for lv in d["levels"]]

    assert coll(on) == coll(off)
