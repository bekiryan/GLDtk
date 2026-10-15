"""Golden route: the cheapest Start → Exit path on the final tile grid (v3 Part 13).

The route is shown over the level in LDtk, so the user can see why a level is
beatable.  It uses exactly the spans and moves that verification uses: the
collision-aware platform graph (``symbolic.extractor``; ``edit.context`` for a
file), the Start / Exit footing of ``edit.check`` and the trajectories the
extractor accepted (``symbolic.physics.trajectory_candidates`` /
``trajectory_hits``).  No physics is re-implemented here.

* The path is the cheapest one by *effort* (v3 Part 20, :func:`move_cost`):
  walking is cheap and grows with distance, a hop grows with the square of
  its gap, rise and drop measured against the Game Profile's comfort limits
  (``profile.design``), and a hop beyond those limits pays a penalty larger
  than two comfortable hops plus the walk over the same ground, so a route
  that can use intermediate platforms uses them.  An ability move costs more
  again.  See "Move cost" below.
* Every step has a ``kind`` (walk, jump, fall, double_jump, dash, wall_jump),
  ``from`` / ``to`` cells and ``points``: a polyline of the player's feet in
  level pixels (x right, y down, the LDtk convention).  An arc is the
  trajectory the extractor accepted for the edge, resampled every ~4 px; a
  walk is a straight segment along the span.
* ``reachable_spans`` are the spans reachable from the Start (to shade them),
  and ``unreachable_required`` the iids of required entities (``REQUIRED_KINDS``,
  signs exempt) the player cannot reach.

Coordinates: the extractor works in Y-up pixels where the surface of tile row
``r`` is at ``(H − 1 − r) · ts``; in level pixels (Y-down) that surface is at
``r · ts``, so ``y_down = (H − 1) · ts − y_up``.

Move cost
---------
For a move from span A to span B (rects ``(row, col0, col1)``), in tiles:

  g = empty columns between A and B (0 when they overlap horizontally)
  r = rows B is above A (rise),  d = rows B is below A (drop)
  x = |Δx| of the move (launch → landing): the ground it covers
  H, R, D = design.comfort_hop_tiles, comfort_rise_tiles, comfort_drop_tiles

  walk           W·x                                    (W = WALK_COST_PER_TILE = 0.1)
  fall / jump    base + (g/H)² + (r/R)² + (d/D)² + W·x
                 + LONG_PENALTY   when g > H or r > R or d > D
  ability move   MOVE_COST["ability"] + (g/H)² + (r/R)² + (d/D)² + W·x + LONG_PENALTY

  base = MOVE_COST["fall"] (0.8) or MOVE_COST["jump"] (1.0).

A comfortable hop costs at most ``COMFORT_HOP_MAX`` = 1 + 2 = 3 plus its walk
term (at most two of the three ratios are non-zero, each ≤ 1).  A hop beyond
the limits has a ratio > 1, so it costs more than 1 + 1 + LONG_PENALTY + W·x =
9 + W·x, with ``LONG_PENALTY = 2·COMFORT_HOP_MAX + 1`` = 7: more than two (even
three) comfortable hops plus the walk over the same ground.  On A, B, C in
a row with comfortable gaps, A→B→C therefore beats one long jump A→C, and an
ability move (MOVE_COST["ability"] = 3 > 1) costs more than the same plain jump
even when that jump is long.  Walking along one span is not an edge and is not
charged; it is about the same for every route over the same ground.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import networkx as nx

from game.profile import GameProfile
from symbolic.extractor import extract_graph
from symbolic.physics import trajectory_candidates, trajectory_hits
from symbolic.schema import AbstractLevelGraph, EdgeType, LevelEdge

from .ir import IREntity
from .verify import REQUIRED_KINDS

Point = Tuple[float, float]
Cell = Tuple[int, int]  # (col, row)
Rect = Tuple[int, int, int]  # (row, col_start, col_end)

POINT_SPACING_PX = 4.0
MOVE_COST: Dict[str, float] = {"walk": 0.0, "fall": 0.8, "jump": 1.0, "ability": 3.0}
WALK_COST_PER_TILE = 0.1  # W: every move pays for the ground it covers
COMFORT_HOP_MAX = MOVE_COST["jump"] + 2.0  # most a comfortable hop costs (minus W·x)
LONG_PENALTY = 2 * COMFORT_HOP_MAX + 1.0  # > two comfortable hops over the same ground


@dataclass
class RouteStep:
    kind: str  # walk | jump | fall | double_jump | dash | wall_jump
    from_cell: Cell
    to_cell: Cell
    points: List[Point]  # feet polyline, level px (x right, y down)
    ability: Optional[str] = None

    @property
    def length_px(self) -> float:
        return sum(math.dist(a, b) for a, b in zip(self.points, self.points[1:]))

    def as_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "kind": self.kind,
            "from": list(self.from_cell),
            "to": list(self.to_cell),
            "points": [[round(x, 1), round(y, 1)] for x, y in self.points],
        }
        if self.ability:
            d["ability"] = self.ability
        return d


@dataclass
class Route:
    ok: bool
    error: Optional[str] = None
    steps: List[RouteStep] = field(default_factory=list)
    reachable_spans: List[Tuple[int, int, int]] = field(default_factory=list)  # (col, row, w)
    unreachable_required: List[str] = field(default_factory=list)  # entity iids
    level_iid: Optional[str] = None

    @property
    def length_px(self) -> float:
        return sum(s.length_px for s in self.steps)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "success": self.ok,
            "error": self.error,
            "steps": [s.as_dict() for s in self.steps],
            "length_px": round(self.length_px, 1),
            "reachable_spans": [{"col": c, "row": r, "w": w} for c, r, w in self.reachable_spans],
            "unreachable_required": list(self.unreachable_required),
            "level_iid": self.level_iid,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _edge_kind(edge: LevelEdge) -> Tuple[str, Optional[str]]:
    ability = edge.metadata.get("ability")
    if ability:
        return str(ability), str(ability)
    if edge.edge_type == EdgeType.WALK:
        return "walk", None
    if edge.edge_type == EdgeType.FALL:
        return "fall", None
    return "jump", None


def span_rect(graph: AbstractLevelGraph, span_id: str) -> Rect:
    """``(row, col_start, col_end)`` of a span of an extracted graph."""
    m = graph.get_node(span_id).metadata
    return (int(m["row"]), int(m["col_start"]), int(m["col_end"]))


def move_measure(src: Rect, dst: Rect) -> Tuple[int, int, int]:
    """``(gap, rise, drop)`` in tiles of a move between two spans (see "Move cost")."""
    gap = max(0, dst[1] - src[2] - 1, src[1] - dst[2] - 1)
    return gap, max(0, src[0] - dst[0]), max(0, dst[0] - src[0])


def is_comfortable(src: Rect, dst: Rect, profile: GameProfile) -> bool:
    """Is the move between the spans within the profile's comfort limits?"""
    return profile.design.is_comfortable(*move_measure(src, dst))


def move_cost(edge: LevelEdge, src: Rect, dst: Rect, profile: GameProfile) -> float:
    """Effort of the move *edge* from span *src* to span *dst* (see "Move cost")."""
    kind, ability = _edge_kind(edge)
    walk = WALK_COST_PER_TILE * abs(edge.dx) / float(profile.tile_size)
    if kind == "walk":
        return walk
    d = profile.design
    gap, rise, drop = move_measure(src, dst)
    effort = ((gap / d.comfort_hop_tiles) ** 2 + (rise / d.comfort_rise_tiles) ** 2
              + (drop / d.comfort_drop_tiles) ** 2)
    if ability:
        return MOVE_COST["ability"] + effort + walk + LONG_PENALTY
    long = 0.0 if d.is_comfortable(gap, rise, drop) else LONG_PENALTY
    return MOVE_COST[kind] + effort + walk + long


def cheapest_path(
    graph: AbstractLevelGraph, source: str, targets: Sequence[str], profile: GameProfile
) -> Optional[List[str]]:
    """The cheapest span path (by :func:`move_cost`) from *source* to any of *targets*."""
    g = graph.nx_graph
    rects = {sid: span_rect(graph, sid) for sid in g.nodes}
    costs, paths = nx.single_source_dijkstra(
        g, source,
        weight=lambda u, v, data: move_cost(data["data"], rects[u], rects[v], profile),
    )
    reached = [t for t in targets if t in costs]
    if not reached:
        return None
    return paths[min(reached, key=lambda t: (costs[t], t))]


def _resample(points: Sequence[Point], spacing: float = POINT_SPACING_PX) -> List[Point]:
    """The polyline with extra points so no two neighbours are > *spacing* apart."""
    out: List[Point] = [points[0]]
    for a, b in zip(points, points[1:]):
        n = max(1, math.ceil(math.dist(a, b) / spacing))
        for i in range(1, n + 1):
            out.append((a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n))
    return out


def _accepted_trajectory(
    grid: Sequence[Sequence[Any]], edge: LevelEdge, profile: GameProfile
) -> Optional[List[Point]]:
    """The first collision-free candidate for the edge (Y-up), as the extractor found it."""
    p1, p2 = edge.metadata.get("launch"), edge.metadata.get("land")
    if p1 is None or p2 is None:
        return None
    ts = float(profile.tile_size)
    hitbox = (profile.player.width_px, profile.player.height_px)
    scale = float(edge.metadata.get("gravity_scale", 1.0) or 1.0)
    ability = edge.metadata.get("ability")
    for samples in trajectory_candidates(grid, p1, p2, profile, scale, ability):
        if not trajectory_hits(grid, samples, p2, ts, hitbox):
            return list(samples)
    return [tuple(p1), tuple(p2)]  # the grid changed since extraction; show a straight hop


def _is_required(ent: IREntity) -> bool:
    if ent.entity_type == "Interactive" and ent.metadata.get("interactive_kind") == "sign":
        return False
    return ent.entity_type in REQUIRED_KINDS


def _entity_key(ent: IREntity) -> Optional[str]:
    return ent.metadata.get("iid") or ent.metadata.get("spec_id")


# ─────────────────────────────────────────────────────────────────────────────
# Entry points
# ─────────────────────────────────────────────────────────────────────────────


def level_route(
    grid: Sequence[Sequence[Any]],
    entities: Sequence[IREntity],
    profile: Optional[GameProfile] = None,
    gravity_scale_at: Optional[Callable[[int, int], float]] = None,
    graph: Optional[AbstractLevelGraph] = None,
    step_at: Optional[Callable[[int, int], bool]] = None,
) -> Route:
    """The golden route of the level made of *grid* and *entities*.

    *graph* is the collision-aware graph extracted from *grid* (as the
    verifier extracted it); it is extracted here when None.
    ``gravity_scale_at(col, row)`` gives the section gravity scale;
    ``step_at(col, row)`` where 1-tile steps are walkable (terrain sections, v3
    Part 21: a walk up or down a step is drawn as a walk).
    """
    from edit.context import Spans  # the span index edit verification uses

    profile = profile or GameProfile()
    ts = float(profile.tile_size)
    height = len(grid)
    if graph is None:
        graph = extract_graph(grid, profile=profile, gravity_scale_at_cell=gravity_scale_at,
                              collision=True, step_at=step_at)
    spans = Spans(graph)

    start = next((e for e in entities if e.entity_type == "Start"), None)
    exits = [e for e in entities if e.entity_type == "Exit"]
    start_span = spans.under(start, depth=1) if start else None
    exit_spans = {s: e for e in exits for s in [spans.under(e, depth=1)] if s}
    reach = spans.reach(start_span)

    route = Route(ok=False)
    route.reachable_spans = sorted(
        (c0, row, c1 - c0 + 1) for sid, (row, c0, c1) in spans.rects.items() if sid in reach
    )
    route.unreachable_required = [
        key for e in entities if _is_required(e) and (key := _entity_key(e))
        and spans.under(e, depth=1 if e.entity_type == "Exit" else 4) not in reach
    ]

    if start is None:
        route.error = "the level has no Start entity"
        return route
    if start_span is None:
        route.error = f"the Start (column {start.col}, row {start.row}) has no platform under it"
        return route
    if not exits:
        route.error = "the level has no Exit entity"
        return route
    if not exit_spans:
        route.error = "the Exit has no platform under it"
        return route

    g = graph.nx_graph
    path = cheapest_path(graph, start_span, sorted(exit_spans), profile)
    if path is None:
        e0 = exits[0]
        route.error = (f"the Exit (column {e0.col}, row {e0.row}) cannot be reached from the "
                       "Start (a jump is out of reach or its arc is blocked)")
        return route
    target = path[-1]
    exit_ent = exit_spans[target]

    def to_px(p: Point) -> Point:
        return (float(p[0]), (height - 1) * ts - float(p[1]))

    def cell_of(x: float, row: int) -> Cell:
        return (int(min(max(x // ts, 0), len(grid[0]) - 1)), row)

    steps: List[RouteStep] = []

    def walk(a: Point, b: Point, row: int) -> None:
        if abs(a[0] - b[0]) < 0.5:
            return
        last = steps[-1] if steps else None
        if last and last.kind == "walk" and abs(last.points[-1][1] - b[1]) < 0.5:
            last.points[-1] = b  # one straight segment along the surface
            last.to_cell = cell_of(b[0], row)
            return
        steps.append(RouteStep("walk", cell_of(a[0], row), cell_of(b[0], row), [a, b]))

    span_row = spans.rects[start_span][0]
    cur: Point = ((start.col + 0.5) * ts, span_row * ts)
    for u, v in zip(path, path[1:]):
        edge: LevelEdge = g.edges[u, v]["data"]
        kind, ability = _edge_kind(edge)
        dst_row = spans.rects[v][0]
        if kind == "walk":
            if dst_row != span_row:  # a step up or down (terrain stairs)
                (_r0, a0, a1), (_r1, b0, b1) = spans.rects[u], spans.rects[v]
                edge_x = (a1 + 1) * ts if b0 > a1 else a0 * ts
                walk(cur, (edge_x, cur[1]), span_row)
                nxt = (edge_x, dst_row * ts)
                steps.append(RouteStep("walk", cell_of(edge_x - (1 if b0 > a1 else 0), span_row),
                                       cell_of(edge_x + (0 if b0 > a1 else -1), dst_row),
                                       [(edge_x, cur[1]), nxt]))
                cur, span_row = nxt, dst_row
            continue  # adjacent span on the same row: the walk just goes on
        traj = _accepted_trajectory(grid, edge, profile)
        if traj is None:
            continue
        pts = [to_px(p) for p in traj]
        walk(cur, (pts[0][0], cur[1]), span_row)
        # A walk-off launches past the span's edge: the step's cells stay on its spans.
        (_r0, a0, a1), (_r1, b0, b1) = spans.rects[u], spans.rects[v]
        c_from = min(max(cell_of(pts[0][0], span_row)[0], a0), a1)
        c_to = min(max(cell_of(pts[-1][0], dst_row)[0], b0), b1)
        steps.append(RouteStep(kind, (c_from, span_row), (c_to, dst_row), _resample(pts), ability))
        cur, span_row = pts[-1], dst_row
    walk(cur, ((exit_ent.col + 0.5) * ts, cur[1]), span_row)

    route.ok = True
    route.steps = steps
    return route


def file_route(path: Union[str, Path], level: Optional[str] = None) -> Route:
    """The golden route of an ``.ldtk`` file, read like an edit reads it (hand edits count).

    *level* (an iid or identifier; None = the first level) picks the level of a world.
    """
    from edit.context import EditError, load_context

    try:
        ctx = load_context(path, level)
    except EditError as exc:
        return Route(ok=False, error=str(exc))
    spans = ctx.extract(ctx.grid)
    route = level_route(ctx.grid, ctx.entities, ctx.profile, ctx.gravity_scale_at, spans.graph,
                        step_at=ctx.step_at())
    route.level_iid = ctx.level.level_iid
    return route


def unsaved_route(path: Union[str, Path], project: Dict[str, Any],
                  level: Optional[str] = None) -> Route:
    """The golden route of *project* (the LDtk JSON open in the editor, not saved yet).

    The JSON is written to a hidden temporary file next to *path*, so the Game
    Profile (stored relative to the ``.ldtk``) resolves exactly as for the
    saved file, then read with :func:`file_route` and deleted.
    """
    import json
    import os
    import tempfile

    p = Path(path)
    if not p.parent.is_dir():
        return Route(ok=False, error=f"{p.parent} does not exist.")
    fd, tmp = tempfile.mkstemp(prefix=f".{p.stem}.gldtk-route-", suffix=".ldtk", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(project, f)
        route = file_route(tmp, level)
        if route.error:
            route.error = route.error.replace(Path(tmp).name, p.name).replace(tmp, str(p))
        return route
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


__all__ = [
    "MOVE_COST",
    "Route",
    "RouteStep",
    "cheapest_path",
    "file_route",
    "is_comfortable",
    "level_route",
    "move_cost",
    "move_measure",
    "span_rect",
    "unsaved_route",
]
