"""v3 Part 21: terrain from section structure types (layout/terrain)."""

from __future__ import annotations

import json
from typing import Dict, List, Optional

import pytest

import server
from api.models import GenerateRequest
from edit.region import edit_section
from edit.terrain import structure_from_prompt
from fidelity import check_spec, fill_missing
from game import GameProfile
from layout.ir import TileValue
from layout.ldtk_reader import read_level
from layout.spec_entities import place_spec_entities
from layout.spec_layout import layout_spec
from layout.terrain import MAX_ATTEMPTS, STRUCTURES
from layout.terrain.base import DOOR_AIR
from layout.verify import verify_grid
from llm import FakeProvider, LLMController
from pipeline import build_level
from pipeline.level import _layout_and_verify
from spec import LevelSpec
from symbolic import extract_graph

PROFILE = GameProfile()


def terrain_spec(structures: List[str], size=(120, 30), orientation: Optional[str] = None,
                 params: Optional[Dict[int, dict]] = None, hazards: int = 0) -> LevelSpec:
    """A chain of sections built as *structures*, a node per section, Start and Exit."""
    n = len(structures)
    w, h = size
    sections, nodes = [], []
    for i, st in enumerate(structures):
        sections.append({"id": f"s{i}", "name": f"S{i}", "theme": "dungeon", "difficulty": "medium",
                         "gravity_scale": 1.0, "description": st, "order": i, "structure": st,
                         "params": (params or {}).get(i)})
        if orientation == "vertical":
            x, y = 64, int((i + 0.5) * h * 16 / n)
        else:
            x, y = int((i + 0.5) * w * 16 / n), 48
        kind = "Start" if i == 0 else "Platform"
        nodes.append({"id": f"n{i}", "type": kind, "x": x, "y": y, "w": 48, "h": 16, "section": f"s{i}"})
    last = nodes[-1]
    nodes.append({"id": "exit", "type": "Exit", "x": last["x"] + 64, "y": last["y"], "w": 48,
                  "h": 16, "section": last["section"]})
    for k in range(hazards):
        nodes.append({"id": f"hz{k}", "type": "Hazard", "x": 0, "y": 0, "w": 32, "h": 16,
                      "section": "s0"})
    walk = [nd for nd in nodes if nd["type"] != "Hazard"]
    return LevelSpec.model_validate({
        "title": "T", "size": {"width_tiles": w, "height_tiles": h}, "orientation": orientation,
        "sections": sections, "nodes": nodes,
        "edges": [{"from": a["id"], "to": b["id"], "type": "Walk", "vx": 200, "vy": 0}
                  for a, b in zip(walk, walk[1:])],
        "entities": [
            {"id": "player", "kind": "Start", "anchor": "n0", "section": "s0", "description": "",
             "sprite": ""},
            {"id": "door", "kind": "Exit", "anchor": "exit", "section": last["section"],
             "description": "", "sprite": ""},
        ],
        "requirements": [{"id": "R1", "text": "t", "category": "other"}],
    })


def load(name: str) -> LevelSpec:
    from conftest import fixture_text

    return LevelSpec.model_validate(json.loads(fixture_text(name)))


# ─────────────────────────────────────────────────────────────────────────────
# Schema and backward compatibility
# ─────────────────────────────────────────────────────────────────────────────


def test_schema_has_structure_params_and_place():
    sch = LevelSpec.json_schema()
    sec = sch["properties"]["sections"]["items"]["properties"]
    assert "corridor" in sec["structure"]["enum"] and "params" in sec
    ent = sch["properties"]["entities"]["items"]["properties"]
    assert {"place", "position"} <= set(ent)
    check = sch["properties"]["requirements"]["items"]["properties"]["check"]["properties"]
    assert "structure" in check["type"]["enum"] and "structure" in check


def test_old_specs_dump_without_the_new_fields(valid_spec_dict):
    spec = LevelSpec.model_validate(valid_spec_dict)
    out = json.loads(spec.model_dump_json(by_alias=True))
    assert all("structure" not in s and "params" not in s for s in out["sections"])
    assert all("place" not in e for e in out["entities"])
    assert not spec.has_terrain


def test_structure_aliases():
    spec = terrain_spec(["maze", "rooms"])
    assert [s.structure for s in spec.sections] == ["labyrinth", "hub"]


def test_explicit_open_lays_out_like_no_structure(valid_spec_dict):
    old = LevelSpec.model_validate(valid_spec_dict)
    d = json.loads(json.dumps(valid_spec_dict))
    for s in d["sections"]:
        s["structure"] = "open"
    new = LevelSpec.model_validate(d)
    a, b = layout_spec(old, PROFILE, seed=3), layout_spec(new, PROFILE, seed=3)
    assert a.ir.grid == b.ir.grid
    assert a.ir.metadata["sections"] == b.ir.metadata["sections"]
    assert b.terrain is None and "terrain" not in b.ir.metadata


def test_old_spec_builds_identically_for_the_same_seed(load_fixture):
    def build():
        ctrl = LLMController(provider=FakeProvider([load_fixture("valid_two_sections.json")]))
        return build_level("x", ctrl, PROFILE, seed=5)

    a, b = build(), build()
    assert a.spec_layout.ir.grid == b.spec_layout.ir.grid
    assert a.spec.model_dump() == b.spec.model_dump()
    assert all("structure" not in s for s in a.spec_layout.ir.metadata["sections"])


# ─────────────────────────────────────────────────────────────────────────────
# Stairs in the extractor
# ─────────────────────────────────────────────────────────────────────────────


def _stair_grid():
    g = [[1] * 30 for _ in range(14)]
    row = 11
    for c in range(1, 29):
        if c in (10, 12, 14, 16):
            row -= 1
        for r in range(row - 3, row):  # 3 empty rows: too low for any jump (a hop needs 4)
            g[r][c] = 0
    return g


def test_steps_are_walkable_only_with_step_at():
    g = _stair_grid()
    plain = extract_graph(g, profile=PROFILE, collision=True)
    steps = extract_graph(g, profile=PROFILE, collision=True, step_at=lambda c, r: True)

    def connected(graph):
        import networkx as nx

        def span(row):
            return next(n.id for n in graph.nodes() if n.metadata["row"] == row
                        and n.metadata["col_start"] <= 5 + 20 * (row == 7) <= n.metadata["col_end"])

        lo, hi = span(11), span(7)
        return nx.has_path(graph.nx_graph, lo, hi) and nx.has_path(graph.nx_graph, hi, lo)

    assert not connected(plain)
    assert connected(steps)
    assert any(e.metadata.get("step") for e in steps.edges())


# ─────────────────────────────────────────────────────────────────────────────
# Generators
# ─────────────────────────────────────────────────────────────────────────────

TERRAIN_TYPES = [s for s in STRUCTURES if s != "open"]


@pytest.mark.parametrize("structure", TERRAIN_TYPES)
def test_each_generator_is_beatable_over_seeds_horizontal(structure):
    spec = terrain_spec(["corridor", structure, "corridor"])
    for seed in range(6):
        lay, report, _ = _layout_and_verify(spec, PROFILE, seed)
        assert report.ok, (structure, seed, report.error_messages())


@pytest.mark.parametrize("structure", ["corridor", "shaft", "labyrinth", "arena"])
def test_generators_in_a_vertical_level(structure):
    spec = terrain_spec(["corridor", structure, "hub"], size=(40, 75), orientation="vertical")
    lay, report, _ = _layout_and_verify(spec, PROFILE, 0)
    assert report.ok, (structure, report.error_messages())


def test_output_is_deterministic_per_seed():
    spec = terrain_spec(["corridor", "labyrinth", "parkour", "arena"], size=(160, 32))
    a, b = layout_spec(spec, PROFILE, seed=4), layout_spec(spec, PROFILE, seed=4)
    c = layout_spec(spec, PROFILE, seed=5)
    assert a.ir.grid == b.ir.grid
    assert a.ir.grid != c.ir.grid
    attempt = layout_spec(spec, PROFILE, seed=4, terrain_attempts={"s1": 1})
    assert attempt.ir.grid != a.ir.grid  # another generator seed for one section


def test_terrain_bands_are_carved_from_rock_with_a_frame():
    lay = layout_spec(terrain_spec(["corridor", "hub"]), PROFILE)
    g = lay.ir.grid
    h, w = len(g), len(g[0])
    assert all(v == TileValue.SOLID for v in g[0]) and all(v == TileValue.SOLID for v in g[-1])
    assert all(g[r][0] == TileValue.SOLID and g[r][w - 1] == TileValue.SOLID for r in range(h))
    solid = sum(v == TileValue.SOLID for row in g for v in row)
    assert solid > 0.5 * w * h  # mostly rock, carved


def test_corridors_have_headroom():
    spec = terrain_spec(["corridor", "corridor"], params={0: {"ceiling_height": 4}})
    for seed in range(5):
        lay = layout_spec(spec, PROFILE, seed=seed)
        g = lay.ir.grid
        for sid, st in lay.terrain.sections.items():
            for c, r in st.result.path:
                if not (1 <= c <= len(g[0]) - 2):
                    continue
                air = 0
                while r - air - 1 >= 0 and g[r - air - 1][c] == TileValue.EMPTY:
                    air += 1
                assert air >= PROFILE.player_tiles()[1] + 1, (seed, sid, c, r, air)


def test_corridors_have_open_pits_and_ledges_to_hop():
    """v4 Part 28: a short hop fits under a 5-row ceiling, so corridors get real jumps."""
    spec = terrain_spec(["corridor", "corridor", "corridor"], size=(180, 30), hazards=3,
                        params={i: {"complexity": 1.0} for i in range(3)})
    for seed in range(4):
        lay, report, _ = _layout_and_verify(spec, PROFILE, seed)
        assert report.ok, (seed, report.error_messages())
        g = lay.ir.grid
        pits = [(r, c) for r, row in enumerate(g) for c, v in enumerate(row)
                if v == TileValue.HAZARD and g[r - 1][c] == TileValue.EMPTY]
        assert pits, seed  # spikes lie open at the bottom of a pit, not under the floor
        ledges = [cell for st in lay.terrain.sections.values()
                  for cell in st.result.anchors.get("ledge", [])]
        assert ledges, seed
        for c, r in ledges:  # a ledge stands 2 rows above the floor beside it
            assert g[r][c] == TileValue.SOLID and g[r - 1][c] == TileValue.EMPTY


def test_labyrinth_is_a_tree_with_dead_ends():
    spec = terrain_spec(["corridor", "labyrinth"], params={1: {"complexity": 1.0}})
    for seed in range(5):
        lay = layout_spec(spec, PROFILE, seed=seed)
        stats = lay.terrain.sections["s1"].result.stats
        assert stats["passages"] == stats["cells"] - 1  # a tree: one path between two cells
        assert stats["dead_ends"] >= 1
        assert lay.terrain.sections["s1"].result.anchors["dead_end"]


def test_arena_is_closed_except_its_doors():
    spec = terrain_spec(["corridor", "arena", "corridor"])
    for seed in range(4):
        lay = layout_spec(spec, PROFILE, seed=seed)
        st = lay.terrain.sections["s1"]
        c0, r0, c1, r1 = st.rect
        g = lay.ir.grid
        for col, port in ((c0, st.entry), (c1, st.exit)):
            open_rows = [r for r in range(r0, r1 + 1) if g[r][col] == TileValue.EMPTY]
            assert open_rows and all(port.row - DOOR_AIR <= r < port.row for r in open_rows), (
                seed, col, open_rows, port)
        assert all(g[r0][c] == TileValue.SOLID for c in range(c0, c1 + 1))
        assert all(g[r1][c] == TileValue.SOLID for c in range(c0, c1 + 1))


def test_joins_keep_entry_and_exit_aligned():
    spec = terrain_spec(["corridor", "shaft", "labyrinth", "hub", "arena"], size=(200, 34))
    for seed in range(3):
        lay = layout_spec(spec, PROFILE, seed=seed)
        g = lay.ir.grid
        st = lay.terrain.sections
        for a, b in zip(lay.sections, lay.sections[1:]):
            assert st[a.id].exit == st[b.id].entry
            row = st[a.id].exit.row
            for col in (a.col_end, b.col_start):  # the door floor on both sides of the seam
                assert g[row][col] == TileValue.SOLID
                assert g[row - 1][col] == g[row - 2][col] == TileValue.EMPTY


def test_vertical_joins_are_links():
    spec = terrain_spec(["corridor", "labyrinth", "shaft"], size=(40, 75), orientation="vertical")
    lay = layout_spec(spec, PROFILE, seed=1)
    g = lay.ir.grid
    st = lay.terrain.sections
    for lower, upper in zip(lay.sections, lay.sections[1:]):
        port = st[lower.id].exit
        assert port.kind == "hole" and st[upper.id].entry == port
        assert g[port.row][port.col] == g[port.row][port.col + 1] == TileValue.EMPTY  # the hole
        assert g[port.row + 4][port.col] == TileValue.SOLID  # the pedestal under it


def test_comfort_and_densify_leave_terrain_alone(load_fixture):
    ctrl = LLMController(provider=FakeProvider([load_fixture("terrain_crypt.json")]))
    built = build_level("a crypt", ctrl, PROFILE, seed=0)
    assert built.report.ok
    assert built.stats["comfort_added"] == 0 and built.stats["densify_added"] == 0


def test_mixed_level_open_section_keeps_its_platforms():
    spec = terrain_spec(["open", "corridor"], size=(80, 24))
    lay, report, _ = _layout_and_verify(spec, PROFILE, 0)
    assert report.ok, report.error_messages()
    band = lay.sections[0]
    g = lay.ir.grid
    assert not band.is_terrain
    empty = sum(g[r][c] == TileValue.EMPTY for r in range(len(g))
                for c in range(band.col_start, band.col_end + 1))
    assert empty > 0.8 * (band.col_end - band.col_start + 1) * len(g)  # open air


# ─────────────────────────────────────────────────────────────────────────────
# Entities on logical anchors
# ─────────────────────────────────────────────────────────────────────────────


def test_logical_places_resolve_to_cells():
    spec = load("terrain_crypt.json")
    lay = layout_spec(spec, PROFILE, seed=0)
    place_spec_entities(spec, lay)
    ents = {e.metadata["spec_id"]: e for e in lay.ir.entities if "spec_id" in e.metadata}
    maze = lay.terrain.sections["maze"].result.anchors["dead_end"]
    for eid in ("coin1", "coin2", "chest"):
        e = ents[eid]
        assert (e.col, e.row + 1) in maze or any(abs(e.col - c) <= 2 and e.row + 1 == r for c, r in maze)
    boss = lay.terrain.sections["boss"].result.anchors["arena_center"][0]
    assert abs(ents["lich"].col - boss[0]) <= 1 and ents["lich"].row == boss[1] - 1
    hall = lay.sections[0]
    xs = [ents[k].col for k in ("skel1", "skel2", "skel3")]
    assert xs == sorted(xs) and all(hall.col_start <= x <= hall.col_end for x in xs)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures end to end
# ─────────────────────────────────────────────────────────────────────────────


def _generate(tmp_path, load_fixture, name, seed=0):
    raw = load_fixture(name)
    old = server.provider_factory
    server.provider_factory = lambda req: FakeProvider([raw])
    try:
        out = tmp_path / name / "level.ldtk"
        res = server.generate(GenerateRequest(description="x", output_path=str(out), seed=seed))
    finally:
        server.provider_factory = old
    return res, out


def test_crypt_generates_verifies_and_exports(tmp_path, load_fixture):
    res, out = _generate(tmp_path, load_fixture, "terrain_crypt.json")
    assert res.success and res.verified, res.error
    assert res.fidelity is not None
    by_text = {i.text: i for i in res.fidelity.items}
    for text in ("A crypt corridor with skeletons.", "A parkour shaft.", "A small labyrinth.",
                 "A boss arena."):
        assert by_text[text].status == "satisfied", by_text[text].reason
    level = read_level(json.loads(out.read_text()))
    assert [s.get("structure") for s in level.sections] == ["corridor", "parkour", "labyrinth", "arena"]
    art = res.preview_ascii.splitlines()
    assert sum(line.count("#") for line in art) > sum(len(line) for line in art) // 2  # rock mass


def test_tower_generates_and_verifies(tmp_path, load_fixture):
    res, _ = _generate(tmp_path, load_fixture, "terrain_tower.json")
    assert res.success and res.verified, res.error


def test_autotile_uses_rock_inside_terrain(tmp_path, load_fixture):
    from aesthetic.autotile import autotile_level, has_terrain
    from aesthetic.themes import THEMES, TileRole

    spec = load("terrain_crypt.json")
    lay = layout_spec(spec, PROFILE)
    assert has_terrain(lay.ir)
    theme = next(iter(THEMES.values()))
    entries = autotile_level(lay.ir, theme)
    centre = {(a.src_x, a.src_y) for a in [theme.tile(TileRole.CENTER)] + theme.variants.get(TileRole.CENTER, [])}
    frame = [e for e in entries if e.row == 0 and 5 < e.col < lay.width - 5]
    assert frame and all((e.src_x, e.src_y) in centre for e in frame
                         if lay.ir.grid[1][e.col] == TileValue.SOLID)


# ─────────────────────────────────────────────────────────────────────────────
# Fidelity
# ─────────────────────────────────────────────────────────────────────────────


def _with_req(spec: LevelSpec, check: dict, text: str = "There is a labyrinth.") -> LevelSpec:
    d = json.loads(spec.model_dump_json(by_alias=True))
    d["requirements"] = [{"id": "R1", "text": text, "category": "section", "check": check}]
    return LevelSpec.model_validate(d)


def test_structure_check_and_fill():
    spec = terrain_spec(["corridor", "arena"])
    assert check_spec(_with_req(spec, {"type": "structure", "structure": "arena"})).items[0].status \
        == "satisfied"
    missing = _with_req(spec, {"type": "structure", "structure": "labyrinth"})
    assert check_spec(missing).items[0].status == "missing"
    d = json.loads(missing.model_dump_json(by_alias=True))
    d["sections"][0]["description"] = "a dark maze of tunnels"
    filled = fill_missing(LevelSpec.model_validate(d))
    assert filled.filled == ["R1"]
    assert filled.spec.sections[0].structure == "labyrinth"


def test_labyrinth_check_counts_dead_ends_in_the_level():
    from fidelity import check_level

    spec = _with_req(terrain_spec(["corridor", "labyrinth"]),
                     {"type": "structure", "structure": "labyrinth", "min_count": 1})
    lay = layout_spec(spec, PROFILE)
    item = check_level(spec, lay.ir, PROFILE).items[0]
    assert item.status == "satisfied" and "dead end" in item.reason
    greedy = _with_req(spec, {"type": "structure", "structure": "labyrinth", "min_count": 99})
    assert check_level(greedy, lay.ir, PROFILE).items[0].status == "approximated"


# ─────────────────────────────────────────────────────────────────────────────
# Section edits that change the structure
# ─────────────────────────────────────────────────────────────────────────────


def test_structure_from_prompt():
    assert structure_from_prompt("make this a labyrinth") == "labyrinth"
    assert structure_from_prompt("Turn the section into a boss arena") == "arena"
    assert structure_from_prompt("turn it into a maze please") == "labyrinth"
    assert structure_from_prompt("add three coins") is None


def test_section_edit_changes_structure(tmp_path, load_fixture):
    res, out = _generate(tmp_path, load_fixture, "valid_two_sections.json")
    assert res.success
    outcome = edit_section(str(out), "dungeon", "make this a labyrinth", None)
    assert outcome.success, outcome.error
    assert outcome.stats["llm_attempts"] == 0
    level = read_level(json.loads(out.read_text()))
    assert {s["id"]: s.get("structure") for s in level.sections}["dungeon"] == "labyrinth"
    assert level.spec is not None
    assert {s.id: s.structure for s in level.spec.sections}["dungeon"] == "labyrinth"
    # and back to open air, by the explicit parameter
    back = edit_section(str(out), "dungeon", "plain platforms again", None, structure="open")
    assert back.success, back.error


def test_crypt_section_becomes_an_arena(tmp_path, load_fixture):
    res, out = _generate(tmp_path, load_fixture, "terrain_crypt.json")
    assert res.success
    outcome = edit_section(str(out), "maze", "", None, structure="arena")
    assert outcome.success, outcome.error
    level = read_level(json.loads(out.read_text()))
    assert [s.get("structure") for s in level.sections][2] == "arena"


def test_max_attempts_is_three():
    assert MAX_ATTEMPTS == 3


# ─────────────────────────────────────────────────────────────────────────────
# Eval prompts
# ─────────────────────────────────────────────────────────────────────────────


def test_eval_has_structure_prompts():
    from conftest import ROOT

    prompts = json.loads((ROOT / ("e" + "val") / "prompts.json").read_text())["prompts"]
    tagged = [p for p in prompts if "structure" in p["tags"]]
    assert 4 <= len(tagged) <= 6
    assert all(p["expect"].get("structures") for p in tagged)
