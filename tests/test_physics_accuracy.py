"""v3 Part 11: cross-section gravity, headroom along walks, exact wall jumps."""

from __future__ import annotations

import math
import random

import pytest

from layout import layout_spec, place_spec_entities, repair_grid, verify_grid
from layout.repair import _keeps_headroom
from layout.verify import gravity_field_of
from spec_builders import make_spec, node, section
from symbolic import extract_graph, move_between, trajectory_clear
from symbolic.extractor import low_ceiling_cells
from symbolic.gravity import INF, Flight, GravityField
from symbolic.physics import check_wall_jump, reachable

from conftest import legacy_profile  # hand-computed for the pre-Part-28 physics

TS = 16


def _grid(h, w):
    return [[0] * w for _ in range(h)]


def _platform(grid, row, c0, c1, v=1):
    for c in range(c0, c1 + 1):
        grid[row][c] = v


def _surface(h, row):
    return float((h - 1 - row) * TS)


def _has_edge(graph, a, b):
    """Edge between the spans whose (row, col_start) are a and b."""
    ids = {(n.metadata["row"], n.metadata["col_start"]): n.id for n in graph.nodes()}
    return graph.nx_graph.has_edge(ids[a], ids[b])


# ─────────────────────────────────────────────────────────────────────────────
# Gravity field and flight
# ─────────────────────────────────────────────────────────────────────────────


def _brute(field, p0, vy, phases, t_end, n=20000):
    """Small-step integration with the gravity at the current feet point."""
    dt = t_end / n
    x, y, v, t = p0[0], p0[1], vy, 0.0
    for _ in range(n):
        vx = next((vv for te, vv in phases if t + dt / 2 < te), phases[-1][1])
        g = field.g_at(x, y)
        y += v * dt - 0.5 * g * dt * dt
        v -= g * dt
        x += vx * dt
        t += dt
    return x, y


@pytest.mark.parametrize("axis", ["x", "y"])
def test_flight_is_exact_piecewise_integration(axis):
    rng = random.Random(7)
    if axis == "x":
        field = GravityField([980.0, 490.0, 1500.0], [100.0, 180.0], "x")
    else:
        field = GravityField([980.0, 400.0], [60.0], "y")
    for _ in range(25):
        p0 = (rng.uniform(0, 200), rng.uniform(0, 100))
        vy = rng.uniform(-100, 600)
        phases = [(rng.uniform(0.05, 0.3), rng.uniform(-200, 200)), (INF, rng.uniform(-200, 200))]
        t_end = rng.uniform(0.1, 1.0)
        got = Flight(field, p0, vy, phases).point(t_end)
        want = _brute(field, p0, vy, phases, t_end)
        assert got == pytest.approx(want, abs=0.1)


def test_field_from_sections_both_orientations():
    prof = legacy_profile()
    horiz = [
        {"col_start": 0, "col_end": 9, "row_start": 0, "row_end": 9, "gravity_scale": 1.0,
         "orientation": "horizontal"},
        {"col_start": 10, "col_end": 19, "row_start": 0, "row_end": 9, "gravity_scale": 0.5,
         "orientation": "horizontal"},
    ]
    f = GravityField.from_sections(prof, horiz, 20, 10)
    assert f.axis == "x" and f.borders == [160.0]
    assert f.g_at(100.0, 0.0) == 980.0 and f.g_at(170.0, 0.0) == 490.0
    vert = [
        {"col_start": 0, "col_end": 9, "row_start": 5, "row_end": 9, "gravity_scale": 1.0,
         "orientation": "vertical"},
        {"col_start": 0, "col_end": 9, "row_start": 0, "row_end": 4, "gravity_scale": 0.5,
         "orientation": "vertical"},
    ]
    f = GravityField.from_sections(prof, vert, 10, 10)
    # border between rows 5 (below) and 4 (above): centre y = (10-1-5)*16 = 64, feet 64-12
    assert f.axis == "y" and f.borders == [52.0]
    assert f.g_at(0.0, 51.0) == 980.0 and f.g_at(0.0, 53.0) == 490.0
    uniform = [dict(horiz[0], gravity_scale=0.7), dict(horiz[1], gravity_scale=0.7)]
    assert GravityField.from_sections(prof, uniform, 20, 10).is_uniform


# ─────────────────────────────────────────────────────────────────────────────
# 1. Gravity across section borders
# ─────────────────────────────────────────────────────────────────────────────

H_ROW, W_ROW, BORDER_COL = 20, 50, 12


def _horizontal_case(scales):
    """Two column sections (border at column 12) and a 364 px flat jump across it."""
    g = _grid(H_ROW, W_ROW)
    _platform(g, 15, 9, 11)
    _platform(g, 15, 34, 36)
    field = GravityField.from_columns(
        980.0, [scales[0]] * BORDER_COL + [scales[1]] * (W_ROW - BORDER_COL), TS
    )
    p1 = (12 * TS - 6.0, _surface(H_ROW, 15))  # launch edge (hitbox on the platform)
    p2 = (34 * TS + 6.0, _surface(H_ROW, 15))  # 364 px further
    return g, field, p1, p2


H_COL, W_COL, BORDER_ROW = 40, 20, 30


def _vertical_case(scales, up_tiles):
    """Two row sections (rows ≥ 30 at the bottom) and a jump up_tiles up across the border."""
    g = _grid(H_COL, W_COL)
    _platform(g, 38, 4, 6)
    _platform(g, 38 - up_tiles, 8, 10)
    rows = [scales[1]] * BORDER_ROW + [scales[0]] * (H_COL - BORDER_ROW)
    field = GravityField.from_rows(980.0, rows, TS, legacy_profile().player.height_px)
    return g, field, (38, 4, 6), (38 - up_tiles, 8, 10)


def test_horizontal_low_to_normal_gravity_is_judged_by_the_real_arc():
    g, field, p1, p2 = _horizontal_case((0.5, 1.0))
    prof = legacy_profile()
    # The start section's 0.5 gravity alone would accept the 364 px jump ...
    assert trajectory_clear(g, p1, p2, prof, 0.5)
    assert reachable(p1, p2, prof, 0.5)
    # ... but past the border the arc falls at full gravity and comes up short.
    assert not trajectory_clear(g, p1, p2, prof, 0.5, gravity_field=field)
    assert not reachable(p1, p2, prof, 0.5, gravity_field=field)
    graph = extract_graph(g, profile=prof, collision=True,
                          gravity_scale_at_cell=lambda c, r: 0.5 if c < BORDER_COL else 1.0)
    assert not _has_edge(graph, (15, 9), (15, 34))


def test_horizontal_normal_to_low_gravity_is_judged_by_the_real_arc():
    g, field, p1, p2 = _horizontal_case((1.0, 0.5))
    prof = legacy_profile()
    assert not trajectory_clear(g, p1, p2, prof, 1.0)  # start gravity: 245 px reach
    assert trajectory_clear(g, p1, p2, prof, 1.0, gravity_field=field)
    assert reachable(p1, p2, prof, 1.0, gravity_field=field)
    graph = extract_graph(g, profile=prof, collision=True,
                          gravity_scale_at_cell=lambda c, r: 1.0 if c < BORDER_COL else 0.5)
    assert _has_edge(graph, (15, 9), (15, 34))
    edge = next(e for e in graph.edges() if e.metadata.get("land", (0, 0))[0] > 500)
    assert edge.metadata["gravity_scale"] == 1.0  # still the source section's scale


def _scale_rows(scales):
    return lambda c, r: scales[0] if r >= BORDER_ROW else scales[1]


def test_vertical_low_to_normal_gravity_is_judged_by_the_real_arc():
    # 288 px up: inside the 0.5-gravity apex (367 px), but the top section has full gravity.
    g, field, src, dst = _vertical_case((0.5, 1.0), 18)
    prof = legacy_profile()
    assert move_between(g, src, dst, prof, 0.5) is not None
    assert move_between(g, src, dst, prof, 0.5, field) is None
    graph = extract_graph(g, profile=prof, collision=True,
                          gravity_scale_at_cell=_scale_rows((0.5, 1.0)))
    assert not _has_edge(graph, (38, 4), (20, 8))


def test_vertical_normal_to_low_gravity_is_judged_by_the_real_arc():
    # 208 px up: beyond the plain apex (184 px), reachable once gravity halves above the border.
    g, field, src, dst = _vertical_case((1.0, 0.5), 13)
    prof = legacy_profile()
    assert move_between(g, src, dst, prof, 1.0) is None
    assert move_between(g, src, dst, prof, 1.0, field) is not None
    graph = extract_graph(g, profile=prof, collision=True,
                          gravity_scale_at_cell=_scale_rows((1.0, 0.5)))
    assert _has_edge(graph, (38, 4), (25, 8))


def test_moves_inside_one_section_use_the_closed_form():
    g, field, _p1, _p2 = _horizontal_case((0.5, 1.0))
    prof = legacy_profile()
    # both points deep in the 0.5 section: identical to the plain scale-0.5 check
    a, b = (2 * TS + 8.0, _surface(H_ROW, 15)), (8 * TS + 8.0, _surface(H_ROW, 12))
    assert reachable(a, b, prof, 0.5, gravity_field=field) == reachable(a, b, prof, 0.5)


@pytest.mark.parametrize(
    "scales, exit_col, ok",
    [
        ((1.0, 0.5), 28, True),  # only crossable because gravity halves after the border
        ((0.5, 1.0), 38, False),  # the start section's low gravity alone would accept it
    ],
)
def test_verify_uses_section_gravity_across_the_border(scales, exit_col, ok):
    nodes = [node("start", "Start", 9, 4, 3, "a"), node("exit", "Exit", exit_col, 4, 3, "b")]
    spec = make_spec(nodes, [("start", "exit", "Jump")],
                     sections=[section("a", 0, scales[0]), section("b", 1, scales[1])],
                     size=(50, 20))
    prof = legacy_profile()
    lay = layout_spec(spec, prof)
    place_spec_entities(spec, lay)
    field = gravity_field_of(lay, prof)
    assert field.axis == "x" and len(field.borders) == 1
    start_only = move_between(lay.ir.grid, lay.rect("start"), lay.rect("exit"), prof, scales[0])
    assert (start_only is not None) is not ok  # the v2 verdict was the opposite
    report = verify_grid(lay, prof, spec)
    assert report.ok is ok, report.errors
    if not ok:
        assert [e.code for e in report.errors] == ["NO_GRID_PATH"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Headroom along a walk
# ─────────────────────────────────────────────────────────────────────────────


def _tunnel_grid():
    g = _grid(15, 30)
    _platform(g, 12, 0, 20)  # floor
    for r in range(0, 11):  # a block hanging to ONE empty tile above the floor
        _platform(g, r, 8, 12)
    return g


def test_low_ceiling_splits_the_walk_for_a_two_tile_player():
    g = _tunnel_grid()
    prof = legacy_profile()  # 12×24 px → 2 tiles tall
    graph = extract_graph(g, profile=prof, collision=True)
    floor = sorted(
        (n.metadata["col_start"], n.metadata["col_end"])
        for n in graph.nodes() if n.metadata["row"] == 12
    )
    assert floor == [(0, 7), (13, 20)]  # no standable floor under the ceiling
    assert not _has_edge(graph, (12, 0), (12, 13))
    assert not _has_edge(graph, (12, 13), (12, 0))
    assert low_ceiling_cells(g, 12, 0, 20, 2) == [(c, 10) for c in range(8, 13)]


def test_low_ceiling_is_fine_for_a_one_tile_player():
    g = _tunnel_grid()
    prof = legacy_profile()
    prof.player.height_px = 16.0  # 1 tile tall
    graph = extract_graph(g, profile=prof, collision=True)
    floor = [(n.metadata["col_start"], n.metadata["col_end"])
             for n in graph.nodes() if n.metadata["row"] == 12]
    assert floor == [(0, 20)]  # one walkable span, under the ceiling too


def _ceiling_spec():
    # start: a long platform; the exit is to its right.  A stack of hazards
    # hangs from the top over columns 9..13, two free tiles above the start.
    nodes = [node("start", "Start", 0, 2, 15), node("exit", "Exit", 17, 2, 3)]
    nodes += [node(f"h{k}", "Hazard", 9, 5 + k, 5) for k in range(10)]
    return make_spec(nodes, [("start", "exit", "Walk")], size=(30, 15))


def _lower_h0(lay, spec):
    lay.node_layouts["h0"].row += 1  # now only ONE free tile above the start
    lay.rasterise()
    place_spec_entities(spec, lay)


def test_low_ceiling_over_the_golden_path_is_reported():
    spec = _ceiling_spec()
    prof = legacy_profile()
    lay = layout_spec(spec, prof)
    place_spec_entities(spec, lay)
    assert verify_grid(lay, prof, spec).ok  # two free tiles: the walk fits
    _lower_h0(lay, spec)
    rep = verify_grid(lay, prof, spec)
    assert [e.code for e in rep.errors] == ["LOW_CEILING"]
    err = rep.errors[0]
    row = lay.node_layouts["h0"].row
    assert f"Low ceiling at column 9, row {row} blocks the walk along 'start'" in err.message
    assert err.node_ids == ["start", "exit"]
    assert err.cells[0] == (9, row)


def test_repair_raises_a_low_ceiling_without_making_new_ones():
    spec = _ceiling_spec()
    prof = legacy_profile()
    lay = layout_spec(spec, prof)
    place_spec_entities(spec, lay)
    _lower_h0(lay, spec)
    rep = verify_grid(lay, prof, spec)
    lay, applied = repair_grid(lay, rep, prof, spec)
    assert verify_grid(lay, prof, spec).ok
    assert any(a.startswith("nudged 'h0'") and "low ceiling" in a for a in applied)
    _assert_no_low_ceilings(lay)


def _assert_no_low_ceilings(lay):
    ph = lay.player_tiles[1]
    for nid in lay.node_layouts:
        if lay.standable(nid):
            row, c0, c1 = lay.rect(nid)
            assert low_ceiling_cells(lay.ir.grid, row, c0, c1, ph) == [], nid


def test_stepping_stones_never_create_low_ceilings():
    spec = make_spec([node("start", "Start", 0, 2), node("exit", "Exit", 22, 2)],
                     [("start", "exit", "Jump")])
    prof = legacy_profile()
    lay = layout_spec(spec, prof)
    place_spec_entities(spec, lay)
    lay, applied = repair_grid(lay, verify_grid(lay, prof, spec), prof, spec)
    assert any("stepping stone" in a for a in applied)
    _assert_no_low_ceilings(lay)


def test_keeps_headroom_checks_the_grid_both_ways():
    spec = make_spec([node("start", "Start", 0, 2), node("exit", "Exit", 6, 2)],
                     [("start", "exit", "Jump")])
    lay = layout_spec(spec, legacy_profile())
    lay.rasterise()
    srow = lay.node_layouts["start"].row
    cols = range(0, 3)
    assert not _keeps_headroom(lay, cols, srow - 1, True, set())  # on the start's head
    assert not _keeps_headroom(lay, cols, srow - 2, True, set())  # one tile above it
    assert _keeps_headroom(lay, cols, srow - 3, True, set())  # two free tiles: fine
    lay.ir.grid[srow - 5][1] = 1  # a raw tile above a candidate stone
    assert not _keeps_headroom(lay, cols, srow - 4, True, set())


# ─────────────────────────────────────────────────────────────────────────────
# 3. Wall jumps
# ─────────────────────────────────────────────────────────────────────────────

H_SHAFT, W_SHAFT = 40, 20
SHAFT_SRC = (H_SHAFT - 1, 6, 8)  # floor inside the shaft
SHAFT_DST = (14, 10, 13)  # ledge 400 px up, right of the shaft


def _wall_profile():
    prof = legacy_profile()
    prof.abilities.wall_jump.enabled = True
    prof.abilities.wall_jump.max_climb_px = 400.0  # each kick is still capped by jump_v
    return prof


def _shaft(left_top=14, right_top=14, left=True, right=True):
    g = _grid(H_SHAFT, W_SHAFT)
    _platform(g, H_SHAFT - 1, 5, 9)
    for r in range(left_top, H_SHAFT):
        if left:
            g[r][5] = 1
    for r in range(right_top, H_SHAFT):
        if right:
            g[r][9] = 1
    _platform(g, 14, 10, 13)
    return g


def test_wall_jump_up_a_shaft_with_two_walls():
    prof = _wall_profile()
    move = move_between(_shaft(), SHAFT_SRC, SHAFT_DST, prof)
    assert move is not None and move[0] == "wall_jump"
    # and without the ability the ledge is out of reach
    assert move_between(_shaft(), SHAFT_SRC, SHAFT_DST, legacy_profile()) is None


@pytest.mark.parametrize(
    "shaft",
    [
        {"left": False},  # one wall removed
        {"right": False},
        {"left_top": 30},  # left wall too short to climb to the ledge
        {"right_top": 30},
    ],
)
def test_wall_jump_rejected_without_both_walls(shaft):
    assert move_between(_shaft(**shaft), SHAFT_SRC, SHAFT_DST, _wall_profile()) is None


def test_wall_jump_rejected_in_open_air_even_inside_the_envelope():
    prof = _wall_profile()
    g = _grid(15, 40)
    _platform(g, 12, 0, 2)
    _platform(g, 12, 20, 22)
    p1, p2 = (3 * TS - 6.0, _surface(15, 12)), (20 * TS + 6.0, _surface(15, 12))
    wj = prof.abilities.wall_jump
    phys = prof.physics
    # 278 px: beyond a plain jump (245 px), inside the envelope (245 + 48 px)
    assert check_wall_jump(p1, p2, phys.gravity, wj.jump_v, wj.max_climb_px, wj.push_px,
                           phys.jump_v, phys.run_speed)
    assert reachable(p1, p2, prof, ability="wall_jump")
    assert not trajectory_clear(g, p1, p2, prof, ability="wall_jump")
    assert move_between(g, (12, 0, 2), (12, 20, 22), prof) is None


def test_wall_jump_paths_are_collision_free_and_touch_walls():
    from symbolic.physics import trajectory_candidates, trajectory_hits

    prof = _wall_profile()
    g = _shaft()
    p1 = (8 * TS + 10.0, _surface(H_SHAFT, H_SHAFT - 1))
    p2 = (11 * TS + 8.0, _surface(H_SHAFT, 14))
    paths = list(trajectory_candidates(g, p1, p2, prof, ability="wall_jump"))
    wall_paths = [p for p in paths if any(abs(x - (6 * TS + 6.02)) < 0.1 for x, _ in p)]
    assert wall_paths, "expected a path that touches the left wall's face"
    hitbox = (prof.player.width_px, prof.player.height_px)
    for path in wall_paths:
        assert trajectory_hits(g, path, p2, TS, hitbox) == []
        assert path[0] == p1 and path[-1] == p2
        steps = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:])]
        assert max(steps) <= 6.0 + 1e-6
