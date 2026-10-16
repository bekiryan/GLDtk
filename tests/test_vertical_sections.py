"""Level orientation: vertical levels have row bands, bottom → top (M2 bug 5)."""

from __future__ import annotations

import json

from aesthetic import THEMES, build_aesthetic_layer
from fidelity import fill_missing
from game import GameProfile
from layout import layout_spec, read_level, to_ldtk_dict, verify_grid
from layout.ldtk_adapter import LevelMeta
from llm import FakeProvider, LLMController
from pipeline import build_level
from spec import LevelSpec
from spec_builders import entity, make_spec, node, section
from symbolic import extract_graph
from symbolic.schema import AbstractLevelGraph

from conftest import legacy_profile

W, H = 30, 60


def _climb(orientation=None, summit_low=False, size=(W, H)) -> LevelSpec:
    """A 30×60 climb: base (rows from the bottom 1..22), summit (27..50)."""
    s_rows = [27, 33, 39, 45, 50] if not summit_low else [1, 3, 5, 7, 9]
    nodes = [
        node("start", "Start", 2, 1, 4, "base"),
        node("b1", "Platform", 8, 6, 4, "base"),
        node("b2", "Platform", 2, 11, 4, "base"),
        node("b3", "Platform", 8, 16, 4, "base"),
        node("b4", "Platform", 2, 22, 4, "base"),
        node("s1", "Platform", 8, s_rows[0], 4, "summit"),
        node("s2", "Platform", 14, s_rows[1], 4, "summit"),
        node("s3", "Platform", 8, s_rows[2], 4, "summit"),
        node("s4", "Platform", 14, s_rows[3], 4, "summit"),
        node("exit", "Exit", 20, s_rows[4], 4, "summit"),
    ]
    ids = [n["id"] for n in nodes]
    edges = [(a, b, "Jump") for a, b in zip(ids, ids[1:])]
    ents = [entity("player", "Start", "start", "base", facing="right"),
            entity("door", "Exit", "exit", "summit", requires_key=False),
            entity("hermit", "NPC", "s4", "summit", name="Hermit", dialogue="You made it!")]
    d = make_spec(nodes, edges, ents,
                  [section("base", 0, theme="forest"), section("summit", 1, gravity=0.5, theme="ice")],
                  size, as_dict=True)
    d["orientation"] = orientation
    return LevelSpec.model_validate(d)


def test_orientation_is_derived_from_the_size_unless_stated():
    assert _climb().layout_orientation == "vertical"
    assert _climb(size=(60, 30)).layout_orientation == "horizontal"
    assert _climb(size=(40, 40)).layout_orientation == "horizontal"
    assert _climb("horizontal").layout_orientation == "horizontal"
    assert LevelSpec.json_schema()["properties"]["orientation"]["type"] == ["string", "null"]


def test_vertical_sections_are_row_bands_bottom_to_top():
    lay = layout_spec(_climb())
    base, summit = lay.sections
    assert lay.orientation == "vertical"
    assert (base.id, summit.id) == ("base", "summit")
    # full width, contiguous rows, base at the bottom
    assert (base.col_start, base.col_end, summit.col_start, summit.col_end) == (0, W - 1, 0, W - 1)
    assert summit.row_start == 0 and base.row_end == H - 1
    assert base.row_start == summit.row_end + 1
    # the LLM's coordinates are kept (no remap) and every node is in its own band
    assert not any("remapped" in a for a in lay.adjustments)
    for nid, nl in lay.node_layouts.items():
        assert lay.section_at(nl.col, nl.row).id == lay.node_sections[nid], nid
    assert lay.gravity_scale_at(5, 2) == 0.5 and lay.gravity_scale_at(5, H - 2) == 1.0
    meta = lay.ir.metadata["sections"]
    assert meta[0]["orientation"] == "vertical"
    assert {"col_start", "col_end", "row_start", "row_end"} <= set(meta[0])


def test_horizontal_levels_keep_column_bands_with_full_rows():
    lay = layout_spec(_climb("horizontal"))
    base, summit = lay.sections
    assert base.col_start == 0 and summit.col_end == W - 1
    assert (base.row_start, base.row_end) == (0, H - 1)
    assert lay.sections[0].as_dict()["orientation"] == "horizontal"


def test_vertical_out_of_order_sections_are_remapped_into_row_bands():
    # The summit's nodes sit at the bottom: they move into the top band.
    lay = layout_spec(_climb(summit_low=True))
    base, summit = lay.sections
    assert any("remapped section" in a and "rows" in a for a in lay.adjustments)
    assert summit.row_end < base.row_start
    for nid in ("s1", "s2", "s3", "s4", "exit"):
        nl = lay.node_layouts[nid]
        assert summit.row_start <= nl.row <= summit.row_end, nid
    for nid in ("start", "b1", "b2"):
        assert lay.node_layouts[nid].row >= base.row_start


def test_vertical_climb_builds_verified_with_row_gravity():
    spec = _climb()
    profile = legacy_profile()  # the 5-6 row climbs are sized for the pre-Part-28 jump
    ctrl = LLMController(profile=profile,
                         provider=FakeProvider([json.dumps(spec.model_dump(by_alias=True))]))
    built = build_level("an icy climb", ctrl, profile, densify=False)
    assert built.report.ok, built.report.error_messages()
    lay = built.spec_layout
    # the extractor sees the summit's gravity through the row: its edges carry 0.5
    graph = extract_graph(lay.ir.grid, profile=profile,
                          gravity_scale_at_cell=lay.gravity_scale_at, collision=True)
    top = lay.sections[1]
    scales = {e.metadata["gravity_scale"] for e in graph.edges()
              if top.row_start <= graph.get_node(e.source_id).metadata["row"] <= top.row_end}
    assert scales == {0.5}
    assert verify_grid(lay, profile, built.spec).ok


def test_vertical_section_themes_follow_rows():
    lay = layout_spec(_climb())
    ir = lay.ir
    for band in ir.metadata["sections"]:
        band["mapped_theme"] = "forest" if band["id"] == "base" else "ice"
    result = build_aesthetic_layer("x", AbstractLevelGraph(), {}, ir, place_random_entities=False)
    assert result.theme.identifier == "forest"  # the first (bottom) section is the primary one
    forest, ice = THEMES["forest"].tileset_uid, THEMES["ice"].tileset_uid
    summit = lay.sections[1]
    for te in result.aesthetic.tile_entries:
        want = ice if te.row <= summit.row_end else forest
        assert (te.tileset_uid or forest) == want, (te.col, te.row)
    doc = to_ldtk_dict(ir, aesthetic=result.aesthetic, level_meta=LevelMeta(spec_json=_climb()))
    layers = [li["__identifier"] for li in doc["levels"][0]["layerInstances"]]
    assert "Tiles_Ice" in layers
    back = read_level(doc)
    assert back.sections[1]["row_end"] == summit.row_end
    assert back.spec.orientation is None and back.spec.layout_orientation == "vertical"


def test_old_sections_metadata_without_rows_still_reads():
    lay = layout_spec(_climb("horizontal"))
    ir = lay.ir
    for band in ir.metadata["sections"]:
        for k in ("row_start", "row_end", "orientation"):
            band.pop(k)
        band["mapped_theme"] = "forest" if band["id"] == "base" else "ice"
    result = build_aesthetic_layer("x", AbstractLevelGraph(), {}, ir, place_random_entities=False)
    ice = THEMES["ice"].tileset_uid
    split = lay.sections[1].col_start
    assert all((te.tileset_uid == ice) == (te.col >= split) for te in result.aesthetic.tile_entries)


def test_vertical_fill_places_entities_bottom_to_top():
    spec = _climb()
    d = spec.model_dump(by_alias=True)
    d["requirements"] = [{"id": "R1", "text": "a gem", "category": "entity",
                          "check": {"type": "entity_count", "entity_kind": "Pickup",
                                    "subkind": "gem", "min_count": 1, "section": "summit"}}]
    out = fill_missing(LevelSpec.model_validate(d))
    gem = next(e for e in out.spec.entities if e.id.startswith("auto_"))
    assert gem.anchor == "s1"  # the summit's lowest platform (s1 is not the leftmost)
