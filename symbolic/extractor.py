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

Game Profile (v2)
-----------------
With ``profile`` given, classification uses the profile's tile size,
gravity, jump_v and run_speed (as both the walk speed and the |vx| cap), and
``gravity_scale_at(col)`` (default 1.0) scales gravity by the column of the
source span's centre (``gravity_scale_at_cell(col, row)`` also sees its row,
for levels whose sections are row bands).  When a plain jump fails but an enabled ability makes
the move (``best_ability``), the Jump edge carries ``metadata["ability"]``.
Every edge then carries ``metadata["gravity_scale"]``.  Without a profile the
behaviour is the v1 one.

Collision-aware mode (v2, Part 3)
---------------------------------
``collision=True`` keeps only moves whose trajectory is clear on the grid
(:func:`symbolic.physics.trajectory_clear`, the player hitbox swept along the
candidate arcs).  Each pair of spans is tried with a few launch / landing
points (:func:`span_point_pairs`: the edge facing the other span, the centre,
and for downward moves the walk-off point just past the ledge), first as a
plain move, then with each enabled ability.  Pairs further apart than the
profile can possibly reach are pruned before any arc is sampled.  Hazard spans
are kept as nodes but get no edges (nobody stands on, or lands on, a hazard).
Edges carry ``metadata["launch"]`` / ``["land"]`` (the feet points used).

Physics accuracy (v3, Part 11)
------------------------------
* **Headroom.**  In collision mode a surface tile is standable only with
  ``headroom`` empty tiles above it (default: the player's height in tiles,
  ``profile.player_tiles()[1]``).  Spans are split at blocked tiles, so no
  walk or launch point passes under a low ceiling (:func:`low_ceiling_cells`
  lists the blocked tiles).
* **Cross-section gravity.**  ``gravity_field`` (or one built from
  ``gravity_scale_at_cell`` / ``gravity_scale_at``) makes every move feel the
  gravity of the section the hitbox centre is in, switching at the border.
  ``metadata["gravity_scale"]`` is still the scale where the move starts.
* **Wall jumps** are accepted only by the exact grid search
  (``symbolic/walljump.py``).

Stairs (v3 Part 21)
-------------------
Terrain sections (``layout/terrain``) carve stairs and slopes as 1-tile steps.
With ``step_at`` given (collision mode), two spans whose rows differ by one
tile and whose ends touch (the upper one starts in the column right after the
lower one ends, or the reverse) are joined by a **Walk** edge in both
directions when ``step_at(col, row)`` is true for the surface tiles on both
sides of the step: the player walks up and down a staircase.  Moves in such
levels also try a launch point one tile short of the target
(``span_point_pairs(near_launch=True)``); since v4 Part 28 every upward move
tries it too.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence, Tuple

from .gravity import INF, GravityField
from .physics import (
    ABILITY_ORDER,
    ability_enabled,
    best_ability,
    check_jump_arc,
    double_jump_max_time,
    fall_time,
    flat_jump_reach,
    gravity_for,
    max_jump_height,
    required_launch_velocity,
    trajectory_clear,
)
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
GravityScaleAt = Callable[[int], float]
GravityScaleAtCell = Callable[[int, int], float]  # (col, row) -> gravity scale
StepAt = Callable[[int, int], bool]  # (col, row) -> may a 1-tile step be walked here

if TYPE_CHECKING:  # pragma: no cover
    from game.profile import GameProfile


# ---------------------------------------------------------------------------
# Internal span representation
# ---------------------------------------------------------------------------
@dataclass
class _Span:
    """A horizontal run of same-type tiles that becomes one LevelNode."""

    tile_type: int
    row: int
    col_start: int
    col_end: int  # inclusive

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
    profile: Optional["GameProfile"] = None,
    gravity_scale_at: Optional[GravityScaleAt] = None,
    collision: bool = False,
    gravity_scale_at_cell: Optional[GravityScaleAtCell] = None,
    gravity_field: Optional[GravityField] = None,
    headroom: Optional[int] = None,
    step_at: Optional[StepAt] = None,
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
    profile : optional Game Profile.  When given it overrides tile_size,
        gravity, jump_v, max_horizontal_speed (= run_speed) and walk_speed,
        and enables ability-annotated Jump edges.
    gravity_scale_at : optional ``col -> gravity_scale`` (default 1.0).
    collision : keep only edges whose trajectory is clear on the grid (uses
        *profile*, or the default GameProfile when None).
    gravity_scale_at_cell : optional ``(col, row) -> gravity_scale``; wins
        over *gravity_scale_at*.
    gravity_field : optional :class:`~symbolic.gravity.GravityField` for
        collision mode (moves across a section border feel both gravities).
        Built from the scale function when not given.
    headroom : empty tiles a surface tile needs above it to be standable
        (collision mode only; default ``profile.player_tiles()[1]``; 0 = off).
    step_at : optional ``(col, row) -> bool``: where 1-tile steps are walkable
        (collision mode only; see "Stairs" in the module doc).

    Returns
    -------
    AbstractLevelGraph with classified edges.
    """
    height = len(grid)
    if height == 0:
        return AbstractLevelGraph()
    width = len(grid[0])

    if profile is not None:
        tile_size = float(profile.tile_size)
        gravity = profile.physics.gravity
        jump_v = profile.physics.jump_v
        max_horizontal_speed = profile.physics.run_speed
        walk_speed = profile.physics.run_speed

    if collision and profile is None:
        from game.profile import GameProfile

        profile = GameProfile()
        tile_size = float(profile.tile_size)

    scale_at: Optional[GravityScaleAtCell] = gravity_scale_at_cell
    if scale_at is None and gravity_scale_at is not None:
        col_scale = gravity_scale_at

        def scale_at(col: int, _row: int) -> float:
            return col_scale(col)

    if collision:
        assert profile is not None
        if headroom is None:
            headroom = profile.player_tiles()[1]
    else:
        headroom = 0
    spans = _collect_spans(grid, height, width, headroom)
    nodes = _spans_to_nodes(spans, height, tile_size)

    graph = AbstractLevelGraph()
    for node in nodes.values():
        graph.add_node(node)

    if collision:
        assert profile is not None
        field = gravity_field
        if field is None and scale_at is not None:
            field = GravityField.from_cells(profile, width, height, scale_at)
        if ability_enabled(profile, "wall_jump"):
            from .walljump import cached_wall_runs

            with cached_wall_runs(grid):
                _add_collision_edges(graph, nodes, grid, profile, scale_at, field, step_at)
        else:
            _add_collision_edges(graph, nodes, grid, profile, scale_at, field, step_at)
        return graph

    _add_edges(
        graph,
        nodes,
        gravity,
        jump_v,
        max_horizontal_speed,
        walk_speed,
        profile,
        scale_at,
    )
    return graph


# ---------------------------------------------------------------------------
# Pass 1 — collect horizontal spans
# ---------------------------------------------------------------------------


def has_headroom(grid: Grid, col: int, row: int, headroom: int) -> bool:
    """True if the *headroom* tiles above ``(col, row)`` are empty (above the
    top row counts as open)."""
    for r in range(max(0, row - headroom), row):
        if grid[r][col] != TILE_EMPTY:
            return False
    return True


def low_ceiling_cells(
    grid: Grid, row: int, col_start: int, col_end: int, headroom: int
) -> List[Tuple[int, int]]:
    """``(col, ceiling_row)`` for each standable-looking tile of a run whose
    headroom is blocked; ``ceiling_row`` is the lowest blocking tile above it.

    Only solid (non-hazard) surface tiles are considered.
    """
    out: List[Tuple[int, int]] = []
    if headroom <= 0 or not grid:
        return out
    width = len(grid[0])
    for c in range(max(0, col_start), min(width - 1, col_end) + 1):
        if grid[row][c] not in _SOLID_TILES:
            continue
        for r in range(row - 1, max(-1, row - headroom - 1), -1):
            if grid[r][c] != TILE_EMPTY:
                out.append((c, r))
                break
    return out


def _collect_spans(grid: Grid, height: int, width: int, headroom: int = 0) -> List[_Span]:
    """Horizontal runs of surface tiles.

    With ``headroom > 0`` a solid / start / exit tile without that many empty
    tiles above it is not a surface (nobody can stand there), so runs split
    at it.  Hazard runs are kept regardless.
    """
    spans: List[_Span] = []
    for row in range(height):
        col = 0
        while col < width:
            tile = grid[row][col]
            if tile not in _SURFACE_TILES or (
                headroom > 0
                and tile != TILE_HAZARD
                and not has_headroom(grid, col, row, headroom)
            ):
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
            while (
                end + 1 < width
                and grid[row][end + 1] == tile
                and (
                    headroom <= 0
                    or tile == TILE_HAZARD
                    or has_headroom(grid, end + 1, row, headroom)
                )
            ):
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
            metadata={
                "row": span.row,
                "col_start": span.col_start,
                "col_end": span.col_end,
            },
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
    profile: Optional["GameProfile"] = None,
    gravity_scale: float = 1.0,
) -> Optional[LevelEdge]:
    """Return the cheapest valid edge from src to dst, or None.

    *gravity* is the base gravity; the effective one is gravity · gravity_scale.
    """
    p1 = _surface_point(src)
    p2 = _surface_point(dst)
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    meta: Dict[str, object] = {}
    if profile is not None:
        meta["gravity_scale"] = gravity_scale
    gravity = gravity * gravity_scale

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
            metadata=dict(meta),
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
                    metadata=dict(meta),
                )

    # --- Jump (destination is above, or lateral with upward arc) ---
    if check_jump_arc(p1, p2, gravity, jump_v, max_horizontal_speed):
        vel = required_launch_velocity(p1, p2, gravity, jump_v)
        v_launch: Tuple[float, float] = vel if vel is not None else (0.0, jump_v)
        return LevelEdge(
            source_id=src.id,
            target_id=dst.id,
            edge_type=EdgeType.JUMP,
            dx=dx,
            dy=dy,
            v_launch=v_launch,
            metadata=dict(meta),
        )

    # --- Jump with an ability (profile only) ---
    if profile is not None:
        ability = best_ability(p1, p2, profile, gravity_scale)
        if isinstance(ability, str):
            vx = math.copysign(walk_speed, dx) if dx else 0.0
            return LevelEdge(
                source_id=src.id,
                target_id=dst.id,
                edge_type=EdgeType.JUMP,
                dx=dx,
                dy=dy,
                v_launch=(vx, jump_v),
                metadata={**meta, "ability": ability},
            )

    return None


def _add_edges(
    graph: AbstractLevelGraph,
    nodes: Dict[str, LevelNode],
    gravity: float,
    jump_v: float,
    max_horizontal_speed: Optional[float],
    walk_speed: float,
    profile: Optional["GameProfile"] = None,
    gravity_scale_at: Optional[GravityScaleAtCell] = None,
) -> None:
    node_list = list(nodes.values())
    for i, src in enumerate(node_list):
        for dst in node_list:
            if src.id == dst.id:
                continue
            # Skip edges originating from hazard nodes (can't stand on them).
            if src.node_type == NodeType.HAZARD:
                continue
            scale = 1.0
            if gravity_scale_at is not None:
                col = (int(src.metadata["col_start"]) + int(src.metadata["col_end"])) // 2
                scale = float(gravity_scale_at(col, int(src.metadata["row"])))
            edge = _classify_edge(
                src,
                dst,
                gravity,
                jump_v,
                max_horizontal_speed,
                walk_speed,
                profile,
                scale,
            )
            if edge is not None:
                graph.add_edge(edge)


# ---------------------------------------------------------------------------
# Collision-aware edges (v2, Part 3)
# ---------------------------------------------------------------------------

Point = Tuple[float, float]


def span_point_pairs(
    src: Tuple[int, int, int],
    dst: Tuple[int, int, int],
    grid_height: int,
    tile_size: float,
    player_width: float,
    near_launch: bool = False,
) -> List[Tuple[Point, Point]]:
    """Launch/landing feet points for a move between two spans.

    *near_launch* (terrain levels) adds a launch point one tile short of the
    target's facing edge: the run-up to a ledge that starts right where the
    floor ends (a pillar beside a corridor) — the edge launch would rise along
    the pillar's face.  Upward moves always get it (v4 Part 28: short jumps).

    Spans are ``(row, col_start, col_end)``.  Launch points: the edge facing
    the target (hitbox fully on the platform), the centre, and — for downward
    moves — the walk-off point (hitbox just past the ledge).  Landing points:
    the launch x clamped onto the target, the target edge facing the source,
    and the target centre.  Ordered cheapest-first, de-duplicated.
    """
    half = player_width / 2.0
    y1 = (grid_height - 1 - src[0]) * tile_size
    y2 = (grid_height - 1 - dst[0]) * tile_size
    s_left, s_right = src[1] * tile_size, (src[2] + 1) * tile_size
    d_left, d_right = dst[1] * tile_size, (dst[2] + 1) * tile_size
    s_mid, d_mid = (s_left + s_right) / 2.0, (d_left + d_right) / 2.0
    d_lo, d_hi = d_left + half, d_right - half
    if d_lo > d_hi:
        d_lo = d_hi = d_mid
    rightward = d_mid >= s_mid
    if rightward:
        launches = [max(s_mid, s_right - half), s_mid]
        if y2 < y1:
            launches.insert(0, s_right + half)
        land_near = d_lo
    else:
        launches = [min(s_mid, s_left + half), s_mid]
        if y2 < y1:
            launches.insert(0, s_left - half)
        land_near = d_hi
    # v4 Part 28: with a ~4-tile jump the hitbox rises only ~16 px per tile it
    # moves, so an upward hop launched at the edge scrapes the ledge's face and
    # one from the centre may bump a roof: upward moves always try the run-up too.
    if near_launch or y2 > y1:
        s_lo, s_hi = s_left + half, s_right - half
        x = d_left - tile_size - half if rightward else d_right + tile_size + half
        if s_lo <= s_hi:
            launches.append(min(max(x, s_lo), s_hi))
    pairs: List[Tuple[Point, Point]] = []
    seen = set()
    for lx in launches:
        for tx in (min(max(lx, d_lo), d_hi), land_near, d_mid):
            key = (round(lx, 3), round(tx, 3))
            if key in seen:
                continue
            seen.add(key)
            pairs.append(((lx, y1), (tx, y2)))
    return pairs


def _max_flight_time(profile: "GameProfile", g: float, dy: float) -> Optional[float]:
    """Longest flight time of any move landing Δy higher (None if unreachable).

    Exact for the plain jump and the double jump (``double_jump_max_time``);
    a walk-off drop is never longer than the plain jump's descending root.
    """
    phys = profile.physics
    best: Optional[float] = None
    disc = phys.jump_v**2 - 2.0 * g * dy
    if disc >= 0.0:
        best = (phys.jump_v + math.sqrt(disc)) / g
    ab = profile.abilities
    if ab.double_jump.enabled:
        t = double_jump_max_time(dy, g, phys.jump_v, ab.double_jump.jump_v)
        if t is not None and (best is None or t > best):
            best = t
    return best


def _could_reach(profile: "GameProfile", g: float, gap: float, dy: float) -> bool:
    """Cheap necessary condition for any move across *gap* px rising *dy* px."""
    ab = profile.abilities
    vmax = profile.physics.run_speed
    slack = profile.player.width_px + profile.tile_size
    if ab.wall_jump.enabled and dy <= (
        max_jump_height(g, profile.physics.jump_v) + ab.wall_jump.max_climb_px + 1e-6
    ):
        reach = flat_jump_reach(g, profile.physics.jump_v, vmax) + ab.wall_jump.push_px
        if gap <= reach + slack:
            return True
    t = _max_flight_time(profile, g, dy)
    if t is None:
        return False
    extra = ab.dash.distance_px if ab.dash.enabled and ab.dash.air else 0.0
    return gap <= vmax * t + extra + slack


def _pruning_gravity(
    field: Optional[GravityField],
    profile: "GameProfile",
    gravity_scale: float,
    src: Tuple[int, int, int],
    dst: Tuple[int, int, int],
    height: int,
    ts: float,
) -> float:
    """A gravity no stronger than any the move can feel (safe for pruning)."""
    if field is None:
        return gravity_for(profile, gravity_scale)
    if field.is_uniform:
        return field.gs[0]
    if profile.abilities.wall_jump.enabled:
        return field.g_min  # kicks can leave the hop's box
    x_lo = min(src[1], dst[1]) * ts - profile.abilities.dash.distance_px
    x_hi = (max(src[2], dst[2]) + 1) * ts + profile.abilities.dash.distance_px
    y_lo = (height - 1 - max(src[0], dst[0])) * ts
    return field.g_min_over(x_lo, x_hi, y_lo, INF)


def move_between(
    grid: Grid,
    src: Tuple[int, int, int],
    dst: Tuple[int, int, int],
    profile: "GameProfile",
    gravity_scale: float = 1.0,
    gravity_field: Optional[GravityField] = None,
    near_launch: bool = False,
) -> Optional[Tuple[Optional[str], Point, Point]]:
    """Cheapest collision-free move between two spans, or None.

    *near_launch* as in :func:`span_point_pairs` (terrain levels).

    Returns ``(ability, launch, land)`` — ability None for a plain move.
    *gravity_scale* is the source section's scale; with *gravity_field* the
    move instead feels the gravity of each section it passes through.
    """
    height = len(grid)
    ts = float(profile.tile_size)
    g = _pruning_gravity(gravity_field, profile, gravity_scale, src, dst, height, ts)
    dy = (src[0] - dst[0]) * ts
    gap = max(0.0, (dst[1] - src[2] - 1) * ts, (src[1] - dst[2] - 1) * ts)
    if not _could_reach(profile, g, gap, dy):
        return None
    pairs = span_point_pairs(src, dst, height, ts, profile.player.width_px, near_launch)
    abilities: List[Optional[str]] = [None] + [
        a for a in ABILITY_ORDER if ability_enabled(profile, a)
    ]
    for ability in abilities:
        for p1, p2 in pairs:
            if trajectory_clear(
                grid, p1, p2, profile, gravity_scale, ability, ts, gravity_field
            ):
                return ability, p1, p2
    return None


def _add_collision_edges(
    graph: AbstractLevelGraph,
    nodes: Dict[str, LevelNode],
    grid: Grid,
    profile: "GameProfile",
    gravity_scale_at: Optional[GravityScaleAtCell],
    gravity_field: Optional[GravityField] = None,
    step_at: Optional[StepAt] = None,
) -> None:
    standable = [n for n in nodes.values() if n.node_type != NodeType.HAZARD]
    rects = {
        n.id: (int(n.metadata["row"]), int(n.metadata["col_start"]), int(n.metadata["col_end"]))
        for n in standable
    }
    walk_speed = profile.physics.run_speed
    for src in standable:
        s = rects[src.id]
        scale = 1.0
        if gravity_scale_at is not None:
            scale = float(gravity_scale_at((s[1] + s[2]) // 2, s[0]))
        for dst in standable:
            if dst.id == src.id:
                continue
            d = rects[dst.id]
            meta: Dict[str, object] = {"gravity_scale": scale}
            if s[0] == d[0] and (d[1] == s[2] + 1 or s[1] == d[2] + 1):
                vx = walk_speed if d[1] > s[1] else -walk_speed
                p1, p2 = _surface_point(src), _surface_point(dst)
                graph.add_edge(
                    LevelEdge(src.id, dst.id, EdgeType.WALK, p2[0] - p1[0], 0.0, (vx, 0.0), meta)
                )
                continue
            if step_at is not None and _is_step(s, d, step_at):
                vx = walk_speed if d[1] > s[1] else -walk_speed
                p1, p2 = _surface_point(src), _surface_point(dst)
                meta["step"] = True
                graph.add_edge(
                    LevelEdge(src.id, dst.id, EdgeType.WALK, p2[0] - p1[0], p2[1] - p1[1],
                              (vx, 0.0), meta)
                )
                continue
            move = move_between(grid, s, d, profile, scale, gravity_field,
                                near_launch=step_at is not None)
            if move is None:
                continue
            ability, p1, p2 = move
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            meta["launch"], meta["land"] = p1, p2
            if ability is not None:
                meta["ability"] = ability
            edge_type = EdgeType.FALL if dy < 0 and ability is None else EdgeType.JUMP
            vy = 0.0 if edge_type == EdgeType.FALL else profile.physics.jump_v
            vx = math.copysign(walk_speed, dx) if dx else 0.0
            graph.add_edge(LevelEdge(src.id, dst.id, edge_type, dx, dy, (vx, vy), meta))


def _is_step(s: Tuple[int, int, int], d: Tuple[int, int, int], step_at: StepAt) -> bool:
    """Spans ``(row, col_start, col_end)`` one row apart whose ends touch, both
    sides of the step in a place where steps are walkable."""
    if abs(s[0] - d[0]) != 1:
        return False
    if d[1] == s[2] + 1:
        a, b = (s[2], s[0]), (d[1], d[0])
    elif s[1] == d[2] + 1:
        a, b = (s[1], s[0]), (d[2], d[0])
    else:
        return False
    return bool(step_at(*a) and step_at(*b))
