"""Layout v2: coordinates, sections, conflicts, entity placement."""

from __future__ import annotations

from game import GameProfile
from layout import (
    TileValue,
    grid_to_spec,
    layout_spec,
    place_spec_entities,
    spec_to_grid,
)
from spec_builders import entity, make_spec, node, section


def test_coordinate_round_trip():
    H = 20
    for col, row, w in [(0, 19, 1), (5, 0, 3), (12, 7, 4), (29, 10, 2)]:
        x, y, wpx = grid_to_spec(col, row, w, H)
        assert spec_to_grid(x, y, wpx, H) == (col, row, w)
    # y = 0 is the bottom row; y = surface height → top row of the platform
    assert spec_to_grid(0, 0, 48, H) == (0, 19, 3)
    assert spec_to_grid(32, 64, 10, H) == (2, 15, 1)  # w rounds, min 1 tile


def test_surface_matches_extractor_convention():
    from symbolic import extract_graph

    spec = make_spec([node("start", "Start", 2, 3), node("exit", "Exit", 8, 3)],
                     [("start", "exit", "Jump")])
    lay = layout_spec(spec)
    graph = extract_graph(lay.ir.grid, profile=GameProfile())
    ys = sorted({n.position.y for n in graph.nodes()})
    assert ys == [3 * 16]  # spec y of both nodes


def test_size_honoured_and_nodes_rasterised():
    spec = make_spec([node("start", "Start", 0, 2), node("spikes", "Hazard", 5, 0),
                      node("exit", "Exit", 9, 2)], [("start", "exit", "Jump")], size=(40, 18))
    lay = layout_spec(spec)
    assert (lay.ir.width_tiles, lay.ir.height_tiles) == (40, 18)
    assert lay.ir.grid[15][0:3] == [TileValue.SOLID] * 3
    assert lay.ir.grid[17][5:8] == [TileValue.HAZARD] * 3


def _bands_ok(lay):
    bands = lay.sections
    assert bands[0].col_start == 0 and bands[-1].col_end == lay.width - 1
    for a, b in zip(bands, bands[1:]):
        assert b.col_start == a.col_end + 1


def test_section_bands_ordered_contiguous_full_width():
    secs = [section("a", 0, gravity=1.0), section("b", 1, gravity=0.5)]
    spec = make_spec(
        [node("start", "Start", 0, 2, section="a"), node("p", "Platform", 6, 2, section="a"),
         node("q", "Platform", 20, 2, section="b"), node("exit", "Exit", 26, 2, section="b")],
        [("start", "p", "Jump"), ("p", "q", "Jump"), ("q", "exit", "Jump")],
        sections=secs, size=(40, 15))
    lay = layout_spec(spec)
    _bands_ok(lay)
    assert [b.id for b in lay.sections] == ["a", "b"]
    assert lay.sections[0].col_end == (8 + 20) // 2
    assert lay.gravity_scale_at(1) == 1.0 and lay.gravity_scale_at(30) == 0.5
    assert not any("remapped" in a for a in lay.adjustments)
    # positional intent kept
    assert lay.node_layouts["q"].col == 20


def test_out_of_order_sections_are_remapped():
    secs = [section("first", 0), section("second", 1)]
    # LLM put the "second" section's nodes left of the "first" section's.
    spec = make_spec(
        [node("start", "Start", 20, 2, section="first"), node("exit", "Exit", 25, 2, section="first"),
         node("a", "Platform", 0, 2, section="second"), node("b", "Platform", 5, 2, section="second")],
        [("start", "exit", "Jump")], sections=secs, size=(60, 15))
    lay = layout_spec(spec)
    _bands_ok(lay)
    first, second = lay.sections
    for nid in ("start", "exit"):
        nl = lay.node_layouts[nid]
        assert first.col_start <= nl.col and nl.col + nl.width - 1 <= first.col_end
    for nid in ("a", "b"):
        nl = lay.node_layouts[nid]
        assert second.col_start <= nl.col and nl.col + nl.width - 1 <= second.col_end
    assert all(b.col_end - b.col_start + 1 >= 8 for b in lay.sections)
    assert sum("remapped" in a for a in lay.adjustments) == 2
    # relative spacing inside a section is kept (translation)
    assert lay.node_layouts["exit"].col - lay.node_layouts["start"].col == 5


def test_overlap_moves_the_off_path_node():
    spec = make_spec(
        [node("start", "Start", 0, 3), node("p", "Platform", 6, 3), node("deco", "Platform", 7, 3),
         node("exit", "Exit", 12, 3)],
        [("start", "p", "Jump"), ("p", "exit", "Jump")])
    lay = layout_spec(spec)
    assert lay.node_layouts["p"].row == 11  # path node untouched
    assert lay.node_layouts["deco"].row != 11
    assert any("'deco'" in a for a in lay.adjustments)
    assert lay.conflict("p", "deco") is None


def test_low_clearance_moves_off_path_node():
    # deco sits one row above p: no room for a 2-tile-tall player on p.
    spec = make_spec(
        [node("start", "Start", 0, 3), node("p", "Platform", 6, 3), node("deco", "Platform", 6, 5),
         node("exit", "Exit", 12, 3)],
        [("start", "p", "Jump"), ("p", "exit", "Jump")])
    lay = layout_spec(spec)
    assert lay.node_layouts["p"].row == 11
    assert lay.conflict("p", "deco") is None
    assert any("clearance" in a for a in lay.adjustments)


# ── entities ─────────────────────────────────────────────────────────────────


def _entity_spec(extra_ents, w_tiles=6):
    nodes = [node("start", "Start", 0, 3), node("p", "Platform", 6, 3, w_tiles),
             node("exit", "Exit", 16, 3)]
    ents = [entity("player", "Start", "start", facing="right"),
            entity("door", "Exit", "exit", requires_key=False)] + extra_ents
    return make_spec(nodes, [("start", "p", "Jump"), ("p", "exit", "Jump")], ents)


def test_several_entities_on_one_platform_get_distinct_cells():
    ents = [entity(f"c{i}", "Pickup", "p", pickup_kind="coin", value=1) for i in range(4)]
    spec = _entity_spec(ents)
    lay = layout_spec(spec)
    placed = place_spec_entities(spec, lay)
    coins = [e for e in placed if e.entity_type == "Pickup"]
    cells = {(e.col, e.row) for e in coins}
    assert len(cells) == 4
    nl = lay.node_layouts["p"]
    assert all(e.row == nl.row - 1 and nl.col <= e.col < nl.col + nl.width for e in coins)
    meta = coins[0].metadata
    assert meta["pickup_kind"] == "coin" and meta["value"] == 1
    assert meta["spec_id"] == "c0" and meta["anchor"] == "p" and meta["section"] == "s1"
    assert "archetype" not in meta  # null fields are dropped


def test_flyer_is_placed_above_the_surface():
    spec = _entity_spec([entity("bat", "Enemy", "p", archetype="flyer", health=1)])
    lay = layout_spec(spec)
    [bat] = [e for e in place_spec_entities(spec, lay) if e.entity_type == "Enemy"]
    assert lay.node_layouts["p"].row - bat.row in (2, 3)


def test_start_and_exit_are_not_duplicated():
    spec = _entity_spec([])
    lay = layout_spec(spec)
    place_spec_entities(spec, lay)
    place_spec_entities(spec, lay)  # idempotent
    types = [e.entity_type for e in lay.ir.entities]
    assert types.count("Start") == 1 and types.count("Exit") == 1
    assert all("spec_id" in e.metadata for e in lay.ir.entities)


def test_patrol_range_is_clamped_with_warning():
    spec = _entity_spec([entity("orc", "Enemy", "p", archetype="walker", patrol_range=20)], w_tiles=4)
    lay = layout_spec(spec)
    [orc] = [e for e in place_spec_entities(spec, lay) if e.entity_type == "Enemy"]
    assert orc.metadata["patrol_range"] == 4
    assert any("patrol_range" in w and "orc" in w for w in lay.entity_warnings)


def test_entity_on_blocked_anchor_is_flagged():
    spec = _entity_spec([entity("c", "Pickup", "p", pickup_kind="coin")], w_tiles=3)
    lay = layout_spec(spec)
    nl = lay.node_layouts["p"]
    for c in range(nl.col, nl.col + nl.width):
        lay.ir.grid[nl.row - 1][c] = TileValue.SOLID
    [coin] = [e for e in place_spec_entities(spec, lay) if e.entity_type == "Pickup"]
    assert coin.metadata.get("blocked") is True
    assert any("'c'" in w for w in lay.entity_warnings)
