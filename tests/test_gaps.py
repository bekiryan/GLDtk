"""v4 Part 29g: gap windows (labyrinth / arena / hub, tall parkour) and the coverage table."""

from __future__ import annotations

from patterns.gaps import GapKeeper, TALL_ROWS, gap_type
from patterns.coverage import stampable, table
from patterns.label import label_messages, label_pending
from patterns.store import PatternStore
from layout.terrain.stamp import pattern_grid
from tests.test_stamp import corridor_ascii, record


def feats(structure="open", height=20, geo=None, conf=0.8):
    f = {"structure": structure, "height": height}
    if geo:
        f["structure_geo"] = {"type": geo, "confidence": conf}
    return f


def test_gap_type_keeps_rare_geometry_and_tall_parkour_only():
    assert gap_type(feats(geo="labyrinth")) == "labyrinth"
    assert gap_type(feats(geo="hub")) == "hub"
    assert gap_type(feats(geo="arena", conf=0.5)) is None  # below the retrieval cut
    assert gap_type(feats("parkour", TALL_ROWS)) == "parkour"
    assert gap_type(feats("parkour", TALL_ROWS - 1)) is None
    assert gap_type(feats("corridor", 34)) is None
    assert gap_type(feats(geo="shaft")) is None


def test_gap_keeper_caps_and_drops_overlapping_windows():
    keep = GapKeeper(per_level=2)
    win = [[0] * 40 for _ in range(20)]
    f1, f2, f3 = feats(geo="hub"), feats(geo="hub"), feats(geo="hub")
    assert keep(f1, 0, 0, win) is None and f1["gap"] == "hub"
    assert "overlaps" in keep(f2, 0, 8, win)  # 80 % of its area is the first window
    assert keep(f3, 0, 30, win) is None
    assert "cap" in keep(feats(geo="hub"), 0, 80, win)
    assert keep(feats(), 0, 200, win) == "fills no gap"


def test_ingest_grid_asks_the_keep_hook():
    from patterns.ingest import ingest_grid
    from patterns.ingest import ascii_to_grid

    grid, _e = ascii_to_grid(corridor_ascii(w=40, h=20, floor=17))
    store = PatternStore()
    rep = ingest_grid(store, grid, [], source="t", license="CC0", origin_path="/x", level="L",
                      keep=lambda f, r, c, w: "nope")
    assert rep.added == 0 and rep.dropped.get("nope")


def test_label_only_limits_the_run_and_geometry_hint_is_sent():
    store = PatternStore()
    for pid in ("a", "b"):
        rec = record(pid, corridor_ascii())
        rec["label"] = None
        rec["features"]["structure_geo"] = {"type": "hub", "confidence": 0.7}
        store.add(rec)
    assert '"geometry_type": "hub"' in label_messages(store.get("a"))[0]["content"]
    rep = label_pending(store, None, dry_run=True, only={"a"})
    assert rep.pending == 1


def test_coverage_counts_a_fitting_window_and_prints_a_table():
    win = {"id": "c", "grid": pattern_grid(record("c", corridor_ascii(w=60, h=24)))}
    assert stampable(win, [], "corridor", 26)
    assert not stampable(win, [], "parkour", 40)  # too short, not padded, nothing to stack on
    out = table({"corridor": {20: 1, 26: 2}}, (20, 26))
    assert out.splitlines()[2] == "| corridor | 1 | 2 |"


def test_finder_puts_windows_that_fit_the_section_height_first():
    from pipeline.stamps import StampFinder
    from tests.test_stamp import ConstEmbedder, make_store
    from tests.test_terrain import terrain_spec

    from pipeline.stamps import MAX_STAMPS

    short = [record(f"a{i}", corridor_ascii(h=20, floor=17), "parkour") for i in range(MAX_STAMPS)]
    store = make_store(*short, record("z32", corridor_ascii(h=32, floor=29), "parkour"))
    got = StampFinder("p", store=store, embedder=ConstEmbedder())(terrain_spec(["parkour"], size=(120, 40)))
    assert [c["id"] for c in got["s0"]][0] == "z32"
    got = StampFinder("p", store=store, embedder=ConstEmbedder())(terrain_spec(["parkour"], size=(120, 22)))
    assert {c["id"] for c in got["s0"]} == {r["id"] for r in short}


def test_gap_window_keeps_its_type_whatever_the_label_says():
    from patterns.classify import structure_matches
    from patterns.embed import embed_text

    rec = record("g", corridor_ascii(), "parkour")
    rec["features"]["structure_geo"] = {"type": "labyrinth", "confidence": 0.7}
    assert not structure_matches(rec, "labyrinth")
    assert "maze" not in embed_text(rec)
    rec["features"]["gap"] = "labyrinth"
    assert structure_matches(rec, "labyrinth") and structure_matches(rec, "parkour")
    assert "maze" in embed_text(rec)
