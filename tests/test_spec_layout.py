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



# ── crowding: widen, then relocate ───────────────────────────────────────────


def _coins(n, anchor="p", prefix="c"):
    return [entity(f"{prefix}{i}", "Pickup", anchor, pickup_kind="coin", value=1) for i in range(n)]


def _crowd_spec(ents, walls=False):
    """start | p (3 wide) | q | r | exit; *walls* puts q and start flush against p."""
    p_col = 3 if walls else 8
    nodes = [node("start", "Start", 0, 3), node("p", "Platform", p_col, 3, 3),
             node("q", "Platform", p_col + 3 if walls else 15, 3, 3),
             node("r", "Platform", 20, 3, 4), node("exit", "Exit", 27, 3)]
    base = [entity("player", "Start", "start", facing="right"),
            entity("door", "Exit", "exit", requires_key=False)]
    edges = [("start", "p", "Walk"), ("p", "q", "Walk"), ("q", "r", "Jump"), ("r", "exit", "Jump")]
    return make_spec(nodes, edges, base + ents, size=(40, 15))


def _on(placed, nid, lay):
    nl = lay.node_layouts[nid]
    return [e for e in placed if e.row == nl.row - 1 and nl.col <= e.col < nl.col + nl.width
            and e.entity_type == "Pickup"]


def test_capacity_is_one_entity_per_two_tiles():
    from layout.spec_entities import capacity

    assert [capacity(w) for w in (1, 2, 3, 4, 5, 6)] == [1, 1, 1, 2, 2, 3]


def test_crowded_platform_is_widened_not_emptied():
    spec = _crowd_spec(_coins(3))
    lay = layout_spec(spec)
    assert lay.node_layouts["p"].width == 3
    placed = place_spec_entities(spec, lay)
    nl = lay.node_layouts["p"]
    assert nl.width == 6  # 2 tiles per entity
    assert len(_on(placed, "p", lay)) == 3
    assert len({(e.col, e.row) for e in placed}) == len(placed)
    assert any("widened 'p' from 3 to 6" in a for a in lay.adjustments)
    assert not any("moved" in w for w in lay.entity_warnings)
    # the new cells are solid in the grid
    assert all(lay.ir.get_tile(c, nl.row) == TileValue.SOLID for c in range(nl.col, nl.col + nl.width))
    # idempotent: placing again does not widen further or warn again
    place_spec_entities(spec, lay)
    assert lay.node_layouts["p"].width == 6
    assert sum("widened" in a for a in lay.adjustments) == 1


def test_uncrowded_platform_is_left_alone():
    spec = _crowd_spec(_coins(1))
    lay = layout_spec(spec)
    place_spec_entities(spec, lay)
    assert lay.node_layouts["p"].width == 3
    assert not any("widened" in a for a in lay.adjustments)


def test_overflow_moves_to_nearest_platform_in_the_same_section_with_a_warning():
    spec = _crowd_spec(_coins(3), walls=True)  # p cannot grow: start and q touch it
    lay = layout_spec(spec)
    placed = place_spec_entities(spec, lay)
    assert lay.node_layouts["p"].width == 3
    by_id = {e.metadata["spec_id"]: e for e in placed}
    assert by_id["c0"].metadata["anchor"] == "p"
    assert by_id["c1"].metadata["anchor"] == "q" and by_id["c1"].metadata["original_anchor"] == "p"
    assert by_id["c2"].metadata["anchor"] == "r"
    assert len(_on(placed, "q", lay)) == 1 and len(_on(placed, "r", lay)) == 1
    moved = [w for w in lay.entity_warnings if w.startswith("moved")]
    assert len(moved) == 2 and "'c1' from 'p' to 'q'" in moved[0]
    # Start and Exit stay on their own nodes
    assert by_id["player"].metadata["anchor"] == "start" and by_id["door"].metadata["anchor"] == "exit"
    assert not [e for e in placed if e.entity_type not in ("Start", "Exit")
                and e.metadata["anchor"] in ("start", "exit")]
    # the relocation is deterministic: a second placement reports it again
    place_spec_entities(spec, lay)
    assert len([w for w in lay.entity_warnings if w.startswith("moved")]) == 2


def test_relocation_stays_inside_the_section():
    secs = [section("a", 0), section("b", 1)]
    nodes = [node("start", "Start", 0, 3, section="a"), node("p", "Platform", 3, 3, section="a"),
             node("q", "Platform", 6, 3, section="a"), node("far", "Platform", 30, 3, 4, section="b"),
             node("exit", "Exit", 36, 3, section="b")]
    ents = [entity("player", "Start", "start", "a", facing="right"),
            entity("door", "Exit", "exit", "b", requires_key=False)]
    ents += [entity(f"c{i}", "Pickup", "p", "a", pickup_kind="coin") for i in range(4)]
    spec = make_spec(nodes, [("start", "p", "Walk"), ("p", "q", "Walk"), ("q", "far", "Jump"),
                             ("far", "exit", "Walk")], ents, sections=secs, size=(50, 15))
    lay = layout_spec(spec)
    placed = place_spec_entities(spec, lay)
    assert not [e for e in placed if e.metadata.get("anchor") == "far"]  # never crosses sections
    for nid, nl in lay.node_layouts.items():  # widening never leaves the section band
        band = next(b for b in lay.sections if b.id == lay.node_sections[nid])
        assert band.col_start <= nl.col and nl.col + nl.width - 1 <= band.col_end, nid


def test_crowding_fix_keeps_the_level_verifiable():
    from game import GameProfile
    from layout import repair_grid, verify_grid

    spec = _crowd_spec(_coins(4))
    profile = GameProfile()
    lay = layout_spec(spec, profile)
    place_spec_entities(spec, lay)
    report = verify_grid(lay, profile, spec)
    if not report.ok:
        lay, _ = repair_grid(lay, report, profile, spec)
        report = verify_grid(lay, profile, spec)
    assert report.ok, report.error_messages()
