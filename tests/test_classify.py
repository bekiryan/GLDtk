"""v4 Part 26f: geometry structure classifier, its use in retrieval, and taller stamps (offline)."""

from __future__ import annotations

from layout.terrain.base import Canvas
from layout.terrain.stamp import PAD_MAX, _air_connected, fit_pattern, pattern_grid, stack_grids, stamp_section
from patterns.classify import MIN_CONFIDENCE, classify, decide, fill_structure_geo, structure_matches
from patterns.ingest import ascii_to_grid, guess_start_exit, recheck, window_features
from patterns.retrieve import Filters
from patterns.store import PatternStore
from game.profile import GameProfile
from tests.test_stamp import corridor_ascii, ctx_for, record

import random

# a low tunnel with floating ledges (each a dead end) and a spike pit splitting the floor
LABYRINTH = """\
##############################################
##############################################
##############################################
##############################################
##############################################
##############################################
..............................................
..............................................
..###..###....###..###...###..###..###........
..............................................
..............................................
###################^^#########################
##############################################"""

# a roofed, walled room with a flat floor and two doors (left and right)
ARENA = """\
##############################
##############################
##..........................##
##..........................##
##..........................##
##..........................##
##..........................##
..............................
..............................
..............................
##############################
##############################"""

# a central room with doors left, right and up (into a closed chamber)
HUB = "\n".join(["#" * 30] + ["#" * 10 + "." * 10 + "#" * 10] * 3 + ["#" * 14 + ".." + "#" * 14]
                + ["#" * 8 + "." * 14 + "#" * 8] * 5 + ["." * 30] * 3 + ["#" * 30] * 3)

# a narrow climb, ledges alternating sides every 3 rows
SHAFT = """\
#########
#.......#
#....X..#
#...#####
#.......#
#.......#
###.....#
#.......#
#.......#
#...#####
#.......#
#.......#
###.....#
#.......#
#.......#
#...#####
#.......#
#S......#
#########"""

# a plain flat tunnel: no rule fires, the heuristic type is kept
TUNNEL = "\n".join(["#" * 40] * 3 + ["." * 40] * 3 + ["#" * 40] * 3)


def geo_of(text: str):
    grid, ents = ascii_to_grid(text)
    return classify(grid, guess_start_exit(grid, ents))


def test_classifier_types_on_hand_made_windows():
    for text, want in ((LABYRINTH, "labyrinth"), (ARENA, "arena"), (HUB, "hub"), (SHAFT, "shaft")):
        sg, geo = geo_of(text)
        assert sg["type"] == want, (want, sg, geo)
        assert sg["confidence"] >= MIN_CONFIDENCE
    sg, geo = geo_of(LABYRINTH)
    assert geo["branching"] >= 2 and geo["dead_ends"] >= 2 and geo["corridor"] >= 0.5
    sg, geo = geo_of(ARENA)
    assert geo["room"]["w"] >= 12 and geo["room"]["h"] >= 6 and geo["room"]["openings"] == 2
    sg, geo = geo_of(HUB)
    assert geo["room"]["openings"] >= 3 and geo["room"]["sides"] >= 2
    sg, geo = geo_of(SHAFT)
    assert geo["rise"] >= 2 * geo["run"]


def test_plain_tunnel_keeps_heuristic_type_with_low_confidence():
    sg, _ = geo_of(TUNNEL)
    assert sg == {"type": "corridor", "confidence": 0.5}
    assert decide({"branching": 0, "dead_ends": 0, "cycles": 0, "corridor": 0, "room": None,
                   "rise": 0, "run": 10, "route_ok": True}, "parkour")["type"] == "parkour"


def test_open_room_is_no_arena_and_open_sky_is_no_room():
    # the same room without its roof: open sky above, so no room / arena
    no_roof = "\n".join(["." * 30] * 2 + ARENA.splitlines()[2:])
    sg, geo = geo_of(no_roof)
    assert sg["type"] != "arena"
    # labyrinth needs enclosure: the ledges under open sky are parkour, not a maze
    open_air = "\n".join(["." * 46] * 6 + LABYRINTH.splitlines()[6:])
    assert geo_of(open_air)[0]["type"] != "labyrinth"


def test_window_features_and_recheck_fill_structure_geo():
    grid, ents = ascii_to_grid(ARENA)
    f = window_features(grid, guess_start_exit(grid, ents), GameProfile())
    assert f["structure_geo"]["type"] == "arena" and "room" in f["geometry"]
    store = PatternStore()
    store.add({"id": "a", "kind": "pattern", "source": "t", "ascii": ARENA, "features": {}})
    recheck(store)
    assert store.patterns["a"]["features"]["structure_geo"]["type"] == "arena"
    store.patterns["a"]["features"].pop("structure_geo")
    assert fill_structure_geo(store, only_missing=True) == 1
    assert fill_structure_geo(store, only_missing=True) == 0
    assert store.patterns["a"]["features"]["structure_geo"]["type"] == "arena"


def _rec(label=None, heuristic="open", geo_type=None, conf=0.8):
    feats = {"structure": heuristic, "route": {"ok": True}}
    if geo_type:
        feats["structure_geo"] = {"type": geo_type, "confidence": conf}
    return {"id": "x", "label": {"structure_type": label} if label else None, "features": feats}


def test_structure_matches_label_heuristic_and_geometry():
    assert structure_matches(_rec(label="arena"), "arena")
    assert structure_matches(_rec(heuristic="parkour"), "parkour")  # no label: heuristic
    assert structure_matches(_rec(geo_type="arena"), "arena")  # no label: geometry
    assert structure_matches(_rec(label="corridor", geo_type="hub"), "hub")  # generic label
    assert structure_matches(_rec(label="open", geo_type="labyrinth"), "labyrinth")
    assert not structure_matches(_rec(label="parkour", geo_type="arena"), "arena")  # specific label
    assert not structure_matches(_rec(geo_type="arena", conf=0.55), "arena")  # low confidence
    assert Filters(structure_type="hub").matches(_rec(label="corridor", geo_type="hub"))
    assert not Filters(structure_type="hub").matches(_rec(label="corridor", geo_type="hub", conf=0.5))
    assert Filters(structure_type="hub").bonus(_rec(geo_type="hub")) > 0


# ── taller stamps ───────────────────────────────────────────────────────────


def short(h: int, floor: int, pid: str):
    return record(pid, corridor_ascii(w=30, h=h, floor=floor))


def test_short_window_is_padded_only_for_floor_types():
    ctx = ctx_for()  # 28 inner rows; 18 rows is 36 % short
    g = pattern_grid(short(18, 15, "p"))
    assert fit_pattern(g, ctx) is not None  # corridor: padded with rock on top
    ctx.structure = "parkour"
    assert fit_pattern(g, ctx) is None  # parkour keeps the 25 % limit
    ctx.structure = "corridor"
    too_short = pattern_grid(short(14, 11, "q"))  # 50 % short: more than PAD_MAX
    assert 28 - 14 > PAD_MAX * 28 and fit_pattern(too_short, ctx) is None


def test_two_short_windows_stack_with_a_stair():
    g = pattern_grid(short(14, 11, "a"))
    stacked = stack_grids(g, g, 58, 28, 2, random.Random(0))
    assert stacked is not None and len(stacked) == 28 and len(stacked[0]) == 58
    ctx = ctx_for()
    ctx.structure = "parkour"
    cv = Canvas(ctx)
    res, fits = stamp_section(ctx, cv, [short(14, 11, "a"), short(14, 11, "b")], 0)
    assert res is not None and fits == 2
    assert res.stats["pattern"] == "a" and res.stats["stacked"] == "b"
    a = next((c, r - 1) for c, r in res.path if c == 1)
    b = next((c, r - 1) for c, r in reversed(res.path) if c == 59)
    assert _air_connected(cv, a, b)
    # alone, a parkour window that short does not stamp
    res, fits = stamp_section(ctx, Canvas(ctx), [short(14, 11, "a")], 0)
    assert res is None and fits == 0


def test_tall_level_stamps_a_verified_stack():
    from pipeline import build_level
    from pipeline.chunks import _FixedController
    from pipeline.stamps import StampFinder
    from tests.test_stamp import ConstEmbedder, make_store
    from tests.test_terrain import PROFILE, terrain_spec

    store = make_store(record("a", corridor_ascii(w=40, h=20, floor=17), structure="parkour"),
                       record("b", corridor_ascii(w=40, h=20, floor=17, pit=(20, 21)), structure="parkour"))
    spec = terrain_spec(["parkour", "parkour"], size=(120, 40))
    built = build_level("t", _FixedController(spec), PROFILE, seed=0, densify=False,
                        stamp_finder=StampFinder("x", store=store, embedder=ConstEmbedder()))
    assert built.report.ok
    bands = [b for b in built.spec_layout.sections if b.pattern]
    assert bands and bands[0].stacked
    assert {p["id"] for p in built.patterns_stamped} == {"a", "b"}  # both credited


def test_embed_text_names_the_measured_structure():
    from patterns.embed import embed_text

    rec = {"kind": "pattern", "label": {"title": "Room", "structure_type": "open"},
           "features": {"structure": "open", "structure_geo": {"type": "hub", "confidence": 0.9}}}
    assert "hub" in embed_text(rec)
    rec["label"]["structure_type"] = "parkour"  # a specific label wins
    assert "hub" not in embed_text(rec)
    rec["label"]["structure_type"] = "open"
    rec["features"]["structure_geo"]["confidence"] = 0.4
    assert "hub" not in embed_text(rec)


def test_stamp_typed_by_geometry_is_named_by_its_measured_structure():
    from types import SimpleNamespace

    from pipeline.stamps import StampFinder, stamped_patterns
    from tests.test_stamp import ConstEmbedder, make_store
    from tests.test_terrain import terrain_spec

    geo = record("g", corridor_ascii(w=30, h=24), structure="corridor")
    geo["features"]["structure_geo"] = {"type": "arena", "confidence": 0.9}
    own = record("o", corridor_ascii(w=30, h=24), structure="corridor")
    store = make_store(geo, own)
    finder = StampFinder("x", store=store, embedder=ConstEmbedder())
    stamps = finder(terrain_spec(["arena", "corridor"]))
    assert [r["id"] for r in stamps["s0"]] == ["g"]
    layout = SimpleNamespace(sections=[SimpleNamespace(id="s0", pattern="g", stacked=None),
                                       SimpleNamespace(id="s1", pattern="o", stacked=None)])
    used = {p["id"]: p for p in stamped_patterns(layout, stamps)}
    assert used["g"]["title"] == "test arena room (30×24)"  # not its corridor title
    assert used["o"]["title"] == next(r["title"] for r in stamps["s1"] if r["id"] == "o")
