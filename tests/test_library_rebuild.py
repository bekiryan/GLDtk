"""Library rebuild (v4 Part 26e): window quality, sizes, dedupe, recheck, labelling order, caching."""

from __future__ import annotations

import json

import pytest

from layout.ir import IREntity, TileValue
from llm import FakeProvider
from patterns.build import rebuild_reset
from patterns.ingest import WIN_SIZES, crop_window, grid_hash, ingest_grid, recheck
from patterns.label import label_pending, is_example_title, rich_order
from patterns.store import PatternStore

S, E, H = TileValue.SOLID, TileValue.EMPTY, TileValue.HAZARD


def level_grid(h: int = 26, w: int = 90, ledges: bool = True):
    g = [[E] * w for _ in range(h)]
    for c in range(w):
        for r in range(h - 5, h):
            g[r][c] = S
    if ledges:
        for k, c0 in enumerate(range(8, w - 10, 9)):
            for c in range(c0, c0 + 6):
                g[h - 8 - (k % 3) * 2][c] = S
    return g, [IREntity("Start", 1, h - 6), IREntity("Exit", w - 3, h - 6)]


@pytest.fixture
def store():
    return PatternStore()


def label_json(title, structure="parkour"):
    return json.dumps({"title": title, "purpose": "p", "mood": "calm", "mechanics": ["jump"],
                       "structure_type": structure, "difficulty": 3, "tags": ["jump"]})


def test_two_window_sizes_are_cut(store):
    g, e = level_grid(h=36, w=120)
    rep = ingest_grid(store, g, e, source="t", license="CC0", origin_path="a", level="l")
    assert rep.added > 2
    assert max(r["window"]["w"] for r in store) > WIN_SIZES[0][1]  # the 48-column windows
    assert max(r["window"]["w"] for r in store) <= WIN_SIZES[1][1]
    assert all(r["accepted"] for r in store)


def test_sky_and_rock_windows_are_dropped_with_reasons(store):
    sky = [[E] * 40 for _ in range(20)]
    sky[19] = [S] * 40  # one row of floor: ~5% solid, one span
    rep = ingest_grid(store, sky, [IREntity("Start", 1, 18), IREntity("Exit", 38, 18)],
                      source="t", license="CC0", origin_path="a", level="sky")
    assert len(store) == 0 and sum(rep.dropped.values()) >= 1
    rock = [[S] * 40 for _ in range(20)]
    for c in range(40):
        rock[3][c] = E
    rep = ingest_grid(store, rock, [], source="t", license="CC0", origin_path="a", level="rock")
    assert len(store) == 0 and rep.dropped


def test_crop_pads_at_most_two_rows():
    g = [[E] * 20 for _ in range(15)]
    for c in range(20):
        g[10][c] = S
    g[11] = [S] * 20
    out, ents, dr, dc = crop_window(g, [IREntity("Start", 2, 9)])
    assert len(out) <= 6 and dr == 8 and ents[0].row == 1


def test_near_duplicate_windows_are_dropped(store):
    g, e = level_grid(h=22, w=34)
    r1 = ingest_grid(store, g, e, source="t", license="CC0", origin_path="a", level="one")
    r2 = ingest_grid(store, g, e, source="t", license="CC0", origin_path="b", level="two")
    assert r1.added >= 1 and r2.added == 0 and r2.dropped.get("near-duplicate", 0) >= 1
    assert grid_hash("#S.\n.E#") == grid_hash("#..\n..#")


def test_recheck_recomputes_acceptance(store):
    g, e = level_grid(h=22, w=34)
    ingest_grid(store, g, e, source="t", license="CC0", origin_path="a", level="l")
    rec = next(iter(store))
    rec["accepted"] = False
    rec["features"]["route"] = {"ok": False}
    n = recheck(store)
    assert n["checked"] == 1 and rec["accepted"] and rec["features"]["route"]["ok"]


def test_rebuild_reset_keeps_notes_and_clears_labels(store):
    store.add({"id": "n1", "kind": "note", "source": "design-notes", "accepted": True, "label": {"title": "x"},
               "ascii": "", "features": {}})
    g, e = level_grid(h=22, w=34)
    ingest_grid(store, g, e, source="supertux", license="CC0", origin_path="a", level="l")
    ingest_grid(store, g, e, source="gldtk", license="CC0", origin_path="b", level="l")
    for r in store:
        if r.get("kind") != "note":
            r["label"] = {"title": "old"}
    dropped = rebuild_reset(store)
    assert dropped >= 1
    assert store.get("n1")["label"] == {"title": "x"}
    assert all(r.get("label") is None for r in store if r.get("kind", "pattern") == "pattern")


def test_example_title_is_rejected_and_asked_again(store):
    g, e = level_grid(h=22, w=34)
    ingest_grid(store, g, e, source="t", license="CC0", origin_path="a", level="l")
    assert is_example_title("Spike pit stairs") and is_example_title("  spike pit stairs. ")
    provider = FakeProvider([label_json("Spike pit stairs"), label_json("Ledge hop rhythm")])
    rep = label_pending(store, provider, max_calls=5)
    assert rep.labelled == 1 and len(provider.calls) == 2
    assert next(iter(store))["label"]["title"] == "Ledge hop rhythm"
    store2 = PatternStore()
    store2.patterns = {k: dict(v, label=None) for k, v in store.patterns.items()}
    rep = label_pending(store2, FakeProvider([label_json("Spike pit stairs")] * 2), max_calls=5)
    assert rep.labelled == 0 and rep.errors


def test_label_budget_richest_first_and_rotates_sources():
    def rec(i, src, plats):
        return {"id": f"{src}{i}", "source": src, "features": {"platforms": plats, "route": {"jumps": 0}}}
    recs = [rec(0, "a", 1), rec(1, "a", 9), rec(0, "b", 5), rec(1, "b", 2)]
    order = [r["id"] for r in rich_order(recs)]
    assert order == ["a1", "b0", "a0", "b1"]


def test_label_budget_stops_the_run(store):
    g, e = level_grid(h=36, w=120)
    ingest_grid(store, g, e, source="t", license="CC0", origin_path="a", level="l")
    assert len(store) >= 2
    rep = label_pending(store, FakeProvider([label_json(f"Title {i}") for i in range(20)]), budget=1)
    assert rep.labelled == 1 and rep.stopped


def test_cosine_is_fast_and_correct():
    from patterns.embed import cosine

    assert cosine([0.6, 0.8], [0.6, 0.8]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
