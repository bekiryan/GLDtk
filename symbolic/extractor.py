"""Level-to-Graph Extractor.

Converts a 2-D tile grid into an AbstractLevelGraph so that real level data
can serve as a synthetic dataset seed.

Tile value legend
-----------------
  0 = empty
  1 = solid platform
  2 = hazard
  3 = start  (a single spawn tile)
  4 = exit   (a single exit tile)

Coordinate system
-----------------
  Column  →  world X  (right is positive)
  Row     →  world Y  (up is positive; row 0 is the TOP of the grid so
                        world_y = (grid_height − 1 − row) * tile_size)

The extractor runs three passes:
  1. Collect horizontal platform spans (and single-tile special nodes).
  2. Emit a LevelNode per span.
  3. Classify edges between every pair of nodes using the physics oracle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .physics import check_jump_arc, fall_time
from .schema import (
    AbstractLevelGraph,
    EdgeType,
    LevelEdge,
    LevelNode,
    NodeType,
    Vec2,
)

# ---------------------------------------------------------------------------
# Tile constants
# ---------------------------------------------------------------------------
TILE_EMPTY = 0
TILE_SOLID = 1
TILE_HAZARD = 2
TILE_START = 3
TILE_EXIT = 4

_SOLID_TILES = {TILE_SOLID, TILE_START, TILE_EXIT}
_SURFACE_TILES = {TILE_SOLID, TILE_HAZARD, TILE_START, TILE_EXIT}

Grid = Sequence[Sequence[int]]


# ---------------------------------------------------------------------------
# Internal span representation
# ---------------------------------------------------------------------------
@dataclass
class _Span:
    """A horizontal run of same-type tiles that becomes one LevelNode."""

    tile_type: int
    row: int
    col_start: int
    col_end: int        # inclusive

    @property
    def width_tiles(self) -> int:
        return self.col_end - self.col_start + 1


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_graph(
    grid: Grid,
    tile_size: float = 16.0,
    gravity: float = 980.0,
    jump_v: float = 600.0,
    max_horizontal_speed: Optional[float] = None,
    walk_speed: float = 200.0,
) -> AbstractLevelGraph:
    """Build an AbstractLevelGraph from a 2-D tile grid.

    Parameters
    ----------
    grid : row-major 2-D sequence of int tile values.
    tile_size : pixel size of one tile square.
    gravity : downward acceleration in px/s² (passed to the physics oracle).
    jump_v : maximum upward launch speed in px/s.
    max_horizontal_speed : optional cap on |vx| for jump feasibility checks.
    walk_speed : horizontal speed assumed for Walk edges (stored in v_launch).

    Returns
    -------
    AbstractLevelGraph with classified edges.
    """
    height = len(grid)
    if height == 0:
        return AbstractLevelGraph()
    width = len(grid[0])

    spans = _collect_spans(grid, height, width)
    nodes = _spans_to_nodes(spans, height, tile_size)

    graph = AbstractLevelGraph()
    for node in nodes.values():
        graph.add_node(node)

    _add_edges(graph, nodes, gravity, jump_v, max_horizontal_speed, walk_speed)
    return graph


# ---------------------------------------------------------------------------
# Pass 1 — collect horizontal spans
# ---------------------------------------------------------------------------

def _collect_spans(grid: Grid, height: int, width: int) -> List[_Span]:
    spans: List[_Span] = []
    for row in range(height):
        col = 0
        while col < width:
            tile = grid[row][col]
            if tile not in _SURFACE_TILES:
                col += 1
                continue

            # Extend the span as long as the tile type matches and is a
            # plain surface (start/exit are always single-tile spans).
            if tile in (TILE_START, TILE_EXIT):
                spans.append(_Span(tile, row, col, col))
                col += 1
                continue

            # For hazard and solid tiles, merge adjacent same-type runs.
            end = col
            while end + 1 < width and grid[row][end + 1] == tile:
                end += 1
            spans.append(_Span(tile, row, col, end))
            col = end + 1

    return spans


# ---------------------------------------------------------------------------
# Pass 2 — build LevelNodes from spans
# ---------------------------------------------------------------------------

_TILE_TO_NODE_TYPE: Dict[int, NodeType] = {
    TILE_SOLID: NodeType.PLATFORM,
    TILE_HAZARD: NodeType.HAZARD,
    TILE_START: NodeType.START,
    TILE_EXIT: NodeType.EXIT,
}


def _span_world_position(span: _Span, grid_height: int, tile_size: float) -> Vec2:
    """Top-left world position of a span (Y-up)."""
    world_x = span.col_start * tile_size
    # Row 0 is the visual top; convert to Y-up world coords.
    world_y = (grid_height - 1 - span.row) * tile_size
    return Vec2(world_x, world_y)


def _spans_to_nodes(
    spans: List[_Span], grid_height: int, tile_size: float
) -> Dict[str, LevelNode]:
    nodes: Dict[str, LevelNode] = {}
    for i, span in enumerate(spans):
        node_id = f"n{i:04d}"
        position = _span_world_position(span, grid_height, tile_size)
        size = Vec2(span.width_tiles * tile_size, tile_size)
        node = LevelNode(
            id=node_id,
            node_type=_TILE_TO_NODE_TYPE[span.tile_type],
            position=position,
            size=size,
            metadata={"row": span.row, "col_start": span.col_start,
                       "col_end": span.col_end},
        )
        nodes[node_id] = node
    return nodes


# ---------------------------------------------------------------------------
# Pass 3 — classify and add edges
# ---------------------------------------------------------------------------

def _surface_point(node: LevelNode) -> Tuple[float, float]:
    """Top-center standing point of a node."""
    s = node.surface
    return (s.x, s.y)


def _nodes_are_horizontally_adjacent(a: LevelNode, b: LevelNode) -> bool:
    """True when the two nodes share the same Y surface and touch/overlap."""
    if abs(a.surface.y - b.surface.y) > 1e-6:
        return False
    a_right = a.position.x + a.size.x
    b_right = b.position.x + b.size.x
    return a.position.x <= b_right and b.position.x <= a_right


def _classify_edge(
    src: LevelNode,
    dst: LevelNode,
    gravity: float,
    jump_v: float,
    max_horizontal_speed: Optional[float],
    walk_speed: float,
) -> Optional[LevelEdge]:
    """Return the cheapest valid edge from src to dst, or None."""
    p1 = _surface_point(src)
    p2 = _surface_point(dst)
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]

    # --- Walk (same surface height, horizontally contiguous) ---
    if _nodes_are_horizontally_adjacent(src, dst):
        vx = walk_speed if dx >= 0 else -walk_speed
        return LevelEdge(
            source_id=src.id,
            target_id=dst.id,
            edge_type=EdgeType.WALK,
            dx=dx,
            dy=dy,
            v_launch=(vx, 0.0),
        )

    # --- Fall (destination is strictly below source) ---
    if dy < 0.0:
        ft = fall_time(dy, gravity, initial_vy=0.0)
        if ft is not None and ft > 0.0:
            vx = dx / ft if ft > 0.0 else 0.0
            if max_horizontal_speed is None or abs(vx) <= max_horizontal_speed + 1e-9:
                return LevelEdge(
                    source_id=src.id,
                    target_id=dst.id,
                    edge_type=EdgeType.FALL,
                    dx=dx,
                    dy=dy,
                    v_launch=(vx, 0.0),
                )

    # --- Jump (destination is above, or lateral with upward arc) ---
    if check_jump_arc(p1, p2, gravity, jump_v, max_horizontal_speed):
        from .physics import required_launch_velocity
        vel = required_launch_velocity(p1, p2, gravity, jump_v)
        v_launch: Tuple[float, float] = vel if vel is not None else (0.0, jump_v)
        return LevelEdge(
            source_id=src.id,
            target_id=dst.id,
            edge_type=EdgeType.JUMP,
            dx=dx,
            dy=dy,
            v_launch=v_launch,
        )

    return None


def _add_edges(
    graph: AbstractLevelGraph,
    nodes: Dict[str, LevelNode],
    gravity: float,
    jump_v: float,
    max_horizontal_speed: Optional[float],
    walk_speed: float,
) -> None:
    node_list = list(nodes.values())
    for i, src in enumerate(node_list):
        for dst in node_list:
            if src.id == dst.id:
                continue
            # Skip edges originating from hazard nodes (can't stand on them).
            if src.node_type == NodeType.HAZARD:
                continue
            edge = _classify_edge(
                src, dst, gravity, jump_v, max_horizontal_speed, walk_speed
            )
            if edge is not None:
                graph.add_edge(edge)
