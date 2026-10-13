"""Collision-aware arcs: trajectory_clear and extract_graph(collision=True)."""

from __future__ import annotations

import pytest

from game import GameProfile
from symbolic import extract_graph, trajectory_clear

H, W, TS = 15, 30, 16


def _grid():
    return [[0] * W for _ in range(H)]


def _surface(row):
    return (H - 1 - row) * TS


def _platform(grid, row, c0, c1, v=1):
    for c in range(c0, c1 + 1):
        grid[row][c] = v


def test_open_jump_passes():
    g = _grid()
    _platform(g, 12, 0, 2)
    _platform(g, 10, 8, 10)
    p1, p2 = (40.0, _surface(12)), (136.0, _surface(10))
    assert trajectory_clear(g, p1, p2, GameProfile())


def test_jump_under_a_low_ceiling_fails():
    g = _grid()
    _platform(g, 12, 0, 12)
    _platform(g, 12, 15, 17)
    _platform(g, 9, 0, 29)  # ceiling: only 2 empty rows above the floor
    p1, p2 = (6 * 16.0, _surface(12)), (15 * 16 + 8.0, _surface(12))
    assert not trajectory_clear(g, p1, p2, GameProfile())
    g[9] = [0] * W  # remove the ceiling
    assert trajectory_clear(g, p1, p2, GameProfile())


def test_walk_off_ledge_drop_passes():
    g = _grid()
    _platform(g, 6, 0, 4)
    _platform(g, 12, 5, 9)
    p1 = (5 * 16 + 6.0, _surface(6))  # just past the ledge
    p2 = (5 * 16 + 30.0, _surface(12))
    assert trajectory_clear(g, p1, p2, GameProfile())


def test_downward_moves_are_capped_by_run_speed():
    g = _grid()
    _platform(g, 4, 0, 1)
    _platform(g, 5, 27, 29)
    # 1 tile down but ~26 tiles across: beyond any arc at run_speed 200 px/s.
    assert not trajectory_clear(g, (24.0, _surface(4)), (440.0, _surface(5)), GameProfile())


def test_dash_through_a_wall_fails():
    prof = GameProfile()
    prof.abilities.dash.enabled = True
    g = _grid()
    _platform(g, 12, 0, 2)
    _platform(g, 12, 18, 20)
    p1, p2 = (40.0, _surface(12)), (18 * 16 + 6.0, _surface(12))
    # 248 px: beyond a plain jump (245 px) but within reach with the dash.
    assert not trajectory_clear(g, p1, p2, GameProfile())
    assert trajectory_clear(g, p1, p2, prof, ability="dash")
    for r in range(0, 12):  # full-height wall between the platforms
        g[r][10] = 1
    assert not trajectory_clear(g, p1, p2, prof, ability="dash")


def test_double_jump_uses_sampled_tau():
    prof = GameProfile()
    prof.abilities.double_jump.enabled = True
    g = [[0] * W for _ in range(25)]
    def surf(r):
        return (25 - 1 - r) * TS
    _platform(g, 22, 0, 2)
    _platform(g, 9, 4, 6)  # 208 px up: beyond a plain jump (184 px)
    p1, p2 = (40.0, surf(22)), (72.0, surf(9))
    assert not trajectory_clear(g, p1, p2, prof)
    assert trajectory_clear(g, p1, p2, prof, ability="double_jump")


def test_extract_graph_collision_drops_blocked_edges():
    g = _grid()
    _platform(g, 12, 0, 12)
    _platform(g, 12, 15, 17)
    _platform(g, 9, 0, 29)
    plain = extract_graph(g, profile=GameProfile())
    coll = extract_graph(g, profile=GameProfile(), collision=True)
    def has(graph, c_from, c_to):
        ids = {n.metadata["col_start"]: n.id for n in graph.nodes() if n.metadata["row"] == 12}
        return graph.nx_graph.has_edge(ids[c_from], ids[c_to])
    assert has(plain, 0, 15)
    assert not has(coll, 0, 15)


def test_profile_tile_size_and_hitbox():
    p = GameProfile()
    assert (p.player.width_px, p.player.height_px) == (12, 24)
    assert p.player_tiles() == (1, 2)
    with pytest.raises(ValueError, match="only 16 px tiles"):
        GameProfile(tile_size=32)
