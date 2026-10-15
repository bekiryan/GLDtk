"""extract_graph with a Game Profile (ability-annotated edges)."""

from __future__ import annotations

from game import GameProfile
from symbolic import EdgeType, extract_graph

# Two 3-tile platforms on the bottom row; centres 272 px apart (17 tiles).
# Plain flat reach is 244.9 px, with a 64 px dash 308.9 px.
W = 20
GRID = [[0] * W for _ in range(4)] + [[1, 1, 1] + [0] * 14 + [1, 1, 1]]


def _edge_between_platforms(graph):
    left = next(n for n in graph.nodes() if n.metadata["col_start"] == 0)
    right = next(n for n in graph.nodes() if n.metadata["col_start"] == 17)
    try:
        return graph.get_edge(left.id, right.id)
    except KeyError:
        return None


def test_dash_gap_produces_dash_edge_only_with_dash():
    with_dash = GameProfile()
    with_dash.abilities.dash.enabled = True
    edge = _edge_between_platforms(extract_graph(GRID, profile=with_dash))
    assert edge is not None
    assert edge.edge_type == EdgeType.JUMP
    assert edge.metadata["ability"] == "dash"
    assert edge.metadata["gravity_scale"] == 1.0

    assert _edge_between_platforms(extract_graph(GRID, profile=GameProfile())) is None


def test_gravity_scale_at_makes_gap_plain():
    edge = _edge_between_platforms(
        extract_graph(GRID, profile=GameProfile(), gravity_scale_at=lambda col: 0.5)
    )
    assert edge is not None and "ability" not in edge.metadata
    assert edge.metadata["gravity_scale"] == 0.5


def test_without_profile_behaviour_unchanged():
    # v1 default: no horizontal cap → the gap is a plain jump, no metadata.
    edge = _edge_between_platforms(extract_graph(GRID))
    assert edge is not None and edge.edge_type == EdgeType.JUMP
    assert edge.metadata == {}
