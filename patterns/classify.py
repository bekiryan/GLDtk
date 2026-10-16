"""Geometry structure classifier (v4 Part 26f, deterministic, no LLM).

The LLM labeller rarely says labyrinth, arena or hub, and :func:`patterns.ingest.structure_of`
never does, so the library had none.  This module reads a window's geometry and its movement
graph (the collision-aware span graph the route code extracts) and gives
``features["structure_geo"] = {"type", "confidence"}`` plus the measured ``features["geometry"]``.

Features (all on the spans reachable from the window's Start):

* **branching** - spans with 3 or more move targets, after merging A<->B pairs into one
  undirected edge and dropping the longest edge of every triangle (a step on a slope or a
  skipped platform is a shortcut, not a second way);
* **dead_ends** - spans whose only move goes back to a span that leads to them (not the Start
  or an Exit);
* **cycles** - the cycle rank E - V + C of that undirected movement graph;
* **corridor** - the share of reachable floor cells under a low ceiling: wall or ceiling rock
  (a rock component of ``MASSIVE`` cells or on the window border, not a floating platform) at
  most ``CEIL_MAX`` rows above the floor;
* **room** - the largest empty rectangle of *covered* air (air with open sky straight above it
  is left out): its size, the rock share of the ring row above it (roof) and of its side
  columns (walls), how flat its floor is (columns with rock at most one row below), and its
  *openings*: doors, i.e. air runs of 2+ cells in a ring side that is at least ``DOOR_WALL``
  rock.  A side on the window's left / right edge counts as wall with one door (windows are
  cut at walls; it is the way on); a room on both edges is a hall, not a room;
* **openings** - air runs of 2+ cells on the window border, per side, in the air region of the
  reachable floor;
* **rise / run** - the vertical and horizontal extent of the Start -> Exit route, in tiles.

Rules (thresholds tuned on the real index of 2,531 windows, 2026-10-16):

* **arena** - a room at least ``ARENA_W`` x ``ARENA_H`` (12 x 6) with a roof (``ROOF_ROCK``
  0.7), walls (``WALL_ROCK`` 0.5 on both sides), a flat floor (``FLAT_SHARE`` 0.8) and at most
  2 doors; the route reaches it;
* **hub** - a room at least ``HUB_W`` x ``HUB_H`` (8 x 5) with a roof, centred in the middle
  half of the window, with at least 3 doors on at least 2 sides; the route reaches it;
* **labyrinth** - enclosed (``corridor`` >= ``LAB_CORRIDOR``, 0.2) and either at least 2
  branching points and 2 dead ends, or a cycle rank of at least 2.  Without the enclosure
  gate, open outdoor windows with a few side alcoves or floating platforms qualify, and those
  read as parkour, not as mazes;
* **shaft** - the route's rise is at least 2x its run (and at least ``SHAFT_MIN_RISE`` tiles);
* otherwise the heuristic type of ``structure_of`` (parkour, corridor, open), confidence 0.5.

A rule's *margin* is the smallest value / threshold ratio of its size (arena, hub) or count
(labyrinth, shaft) criteria; its confidence is ``0.6 + 0.35 * (min(margin, 2) - 1)``: a window
that just meets a rule gets :data:`MIN_CONFIDENCE` (0.6, the cut retrieval uses), twice the
thresholds 0.95.  Ties go labyrinth, arena, hub, shaft.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

GENERIC = ("corridor", "open")
GEO_TYPES = ("labyrinth", "arena", "hub", "shaft")
MIN_CONFIDENCE = 0.6

ARENA_W, ARENA_H = 12, 6
HUB_W, HUB_H = 8, 5
ROOF_ROCK = 0.7  # share of the ring row above the room that is rock
WALL_ROCK = 0.5  # share of each side ring column that is rock (arena)
DOOR_WALL = 0.5  # a ring side counts its air runs as doors only when this share is rock
FLAT_SHARE = 0.8  # share of floor columns at most one row below the room
ARENA_MAX_OPENINGS = 2
HUB_MIN_OPENINGS, HUB_MIN_SIDES = 3, 2
LAB_BRANCH, LAB_DEAD, LAB_CYCLES = 2, 2, 2
LAB_CORRIDOR = 0.2
CEIL_MAX = 5  # a floor cell is "in a corridor" when rock is at most this many rows above it
MASSIVE = 24  # ... and that rock is a wall or ceiling (a component this big or on the border)
SHAFT_RATIO, SHAFT_MIN_RISE = 2.0, 8

_EMPTY, _SOLID, _HAZARD = 0, 1, 2


def _confidence(margin: float) -> float:
    return round(max(0.0, min(0.95, 0.6 + 0.35 * (min(margin, 2.0) - 1.0))), 3)


def _ratio(value: float, need: float) -> float:
    return value / need if need > 0 else 1.0


# ── geometry ────────────────────────────────────────────────────────────────


def _max_rect(grid: Sequence[Sequence[int]]) -> Optional[Tuple[int, int, int, int]]:
    """``(r0, r1, c0, c1)`` of the largest all-empty rectangle (largest area, then wider)."""
    h, w = len(grid), len(grid[0])
    heights = [0] * w
    best, key = None, (0, 0)
    for r in range(h):
        for c in range(w):
            heights[c] = heights[c] + 1 if grid[r][c] == _EMPTY else 0
        stack: List[int] = []
        for c in range(w + 1):
            cur = heights[c] if c < w else 0
            while stack and heights[stack[-1]] >= cur:
                top = stack.pop()
                hh = heights[top]
                left = stack[-1] + 1 if stack else 0
                ww = c - left
                k = (hh * ww, ww)
                if hh and k > key:
                    key, best = k, (r - hh + 1, r, left, c - 1)
            stack.append(c)
    return best


def _runs(cells: Sequence[bool], min_len: int = 2) -> int:
    n = run = 0
    for v in list(cells) + [False]:
        if v:
            run += 1
        else:
            n += run >= min_len
            run = 0
    return n


def _room(grid: Sequence[Sequence[int]], rect: Tuple[int, int, int, int]) -> Dict[str, Any]:
    h, w = len(grid), len(grid[0])
    r0, r1, c0, c1 = rect

    def cell(r: int, c: int) -> Optional[int]:
        return int(grid[r][c]) if 0 <= r < h and 0 <= c < w else None

    rows, cols = range(r0, r1 + 1), range(c0, c1 + 1)
    top = [cell(r0 - 1, c) for c in cols]
    left = [cell(r, c0 - 1) for r in rows]
    right = [cell(r, c1 + 1) for r in rows]
    bottom = [cell(r1 + 1, c) for c in cols]

    # a side on the window's left / right edge: windows are cut at walls, so it counts as rock,
    # with one opening (the way on into the next window); the top edge is open sky
    edge = {"left": c0 == 0, "right": c1 == w - 1}

    def rock_share(side: List[Optional[int]], at_edge: bool = False) -> float:
        if at_edge:
            return 1.0
        return sum(1 for v in side if v == _SOLID) / len(side)

    def openings(side: List[Optional[int]], at_edge: bool = False) -> int:
        """Doors: air runs in a side that is mostly rock (a mostly-air side is no wall at all)."""
        if at_edge:
            return 1
        if rock_share(side) < DOOR_WALL:
            return 0
        return _runs([v is None or v == _EMPTY for v in side])

    per_side = {"top": openings(top), "left": openings(left, edge["left"]),
                "right": openings(right, edge["right"]), "bottom": openings(bottom)}
    flat = 0
    for c in cols:
        d = 0
        while r1 + 1 + d < h and grid[r1 + 1 + d][c] != _SOLID:
            d += 1
        flat += d <= 1 and r1 + 1 + d < h
    return {"rect": list(rect), "w": c1 - c0 + 1, "h": r1 - r0 + 1,
            "roof": round(rock_share(top), 2),
            "walls": round(min(rock_share(left, edge["left"]), rock_share(right, edge["right"])), 2),
            "hall": edge["left"] and edge["right"],
            "flat": round(flat / len(cols), 2),
            "openings": sum(per_side.values()),
            "sides": sum(1 for v in per_side.values() if v),
            "centre": round(((c0 + c1) / 2 + 0.5) / w, 2)}


def _air_region(grid: Sequence[Sequence[int]], seeds: Sequence[Tuple[int, int]]) -> Set[Tuple[int, int]]:
    h, w = len(grid), len(grid[0])
    seen = {(r, c) for r, c in seeds if 0 <= r < h and 0 <= c < w and grid[r][c] != _SOLID}
    todo = deque(seen)
    while todo:
        r, c = todo.popleft()
        for n in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
            if n not in seen and 0 <= n[0] < h and 0 <= n[1] < w and grid[n[0]][n[1]] != _SOLID:
                seen.add(n)
                todo.append(n)
    return seen


def _massive_rock(grid: Sequence[Sequence[int]]) -> Set[Tuple[int, int]]:
    """Rock cells of components that touch the window border or have ``MASSIVE`` cells
    (walls and ceilings, not floating platforms)."""
    h, w = len(grid), len(grid[0])
    seen: Set[Tuple[int, int]] = set()
    out: Set[Tuple[int, int]] = set()
    for r in range(h):
        for c in range(w):
            if grid[r][c] != _SOLID or (r, c) in seen:
                continue
            comp, todo, edge = [(r, c)], deque([(r, c)]), False
            seen.add((r, c))
            while todo:
                y, x = todo.popleft()
                edge = edge or y in (0, h - 1) or x in (0, w - 1)
                for n in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if n not in seen and 0 <= n[0] < h and 0 <= n[1] < w and grid[n[0]][n[1]] == _SOLID:
                        seen.add(n)
                        comp.append(n)
                        todo.append(n)
            if edge or len(comp) >= MASSIVE:
                out.update(comp)
    return out


def _border_openings(grid: Sequence[Sequence[int]], region: Set[Tuple[int, int]]) -> Dict[str, int]:
    h, w = len(grid), len(grid[0])
    return {"left": _runs([(r, 0) in region for r in range(h)]),
            "right": _runs([(r, w - 1) in region for r in range(h)]),
            "top": _runs([(0, c) in region for c in range(w)]),
            "bottom": _runs([(h - 1, c) in region for c in range(w)])}


# ── classifier ──────────────────────────────────────────────────────────────


def geometry(grid: Sequence[Sequence[Any]], entities: Sequence[Any], profile: Any = None,
             graph: Any = None, route: Any = None) -> Dict[str, Any]:
    """The measured features of a window (see the module doc)."""
    import networkx as nx

    from edit.context import Spans
    from game.profile import GameProfile
    from layout.route import level_route
    from symbolic.extractor import extract_graph

    profile = profile or GameProfile()
    g = [[int(v) for v in row] for row in grid]
    h, w = len(g), len(g[0])
    if graph is None:
        graph = extract_graph(g, profile=profile, collision=True)
    if route is None:
        route = level_route(g, entities, profile, graph=graph)
    spans = Spans(graph)
    start = next((e for e in entities if e.entity_type == "Start"), None)
    exits = [e for e in entities if e.entity_type == "Exit"]
    start_span = spans.under(start, depth=1) if start else None
    exit_spans = {s for e in exits for s in [spans.under(e, depth=1)] if s}
    reach = spans.reach(start_span) if start_span else set()
    nxg = graph.nx_graph

    und = nx.Graph()
    und.add_nodes_from(reach)
    for a in reach:
        for b in nxg.successors(a):
            if b in reach and b != a:
                und.add_edge(a, b)
    # shortcuts: a move A-C next to A-B-C (a step on a slope, a skipped platform) is not a
    # second way; drop the longest edge of every triangle so only real loops are counted
    def length(e: Tuple[str, str]) -> Tuple[int, str, str]:
        (ra, a0, a1), (rb, b0, b1) = spans.rects[e[0]], spans.rects[e[1]]
        return (abs(ra - rb) + max(0, b0 - a1, a0 - b1), *sorted(e))

    for a, b in sorted(und.edges(), key=length, reverse=True):
        if set(und[a]) & set(und[b]):
            und.remove_edge(a, b)
    branching = sum(1 for n in und if und.degree(n) >= 3)
    dead = 0
    for n in reach:
        if n == start_span or n in exit_spans:
            continue
        outs = {b for b in nxg.successors(n) if b != n}
        if len(outs) == 1 and next(iter(outs)) in set(nxg.predecessors(n)):
            dead += 1
    cycles = (und.number_of_edges() - und.number_of_nodes() + nx.number_connected_components(und)) \
        if und.number_of_nodes() else 0

    floor_cells = [(row, c) for sid in reach for row, c0, c1 in [spans.rects[sid]]
                   for c in range(c0, c1 + 1)]
    massive = _massive_rock(g)
    low = 0
    for row, c in floor_cells:
        k = 1
        while row - k >= 0 and g[row - k][c] != _SOLID:
            k += 1
        low += row - k >= 0 and k - 1 <= CEIL_MAX and (row - k, c) in massive
    corridor = round(low / len(floor_cells), 2) if floor_cells else 0.0

    region = _air_region(g, [(row - 1, c) for row, c in floor_cells])
    border = _border_openings(g, region)

    # rooms are under a roof: air with open sky above it (up to the window top) is not searched
    covered = [row[:] for row in g]
    for c in range(w):
        r = 0
        while r < h and g[r][c] != _SOLID:
            covered[r][c] = _SOLID
            r += 1
    rect = _max_rect(covered)
    room = _room(g, rect) if rect else None
    if room is not None:
        r0, r1, c0, c1 = rect
        room["reached"] = any(r0 + 1 <= row <= r1 + 2 and c0 <= c <= c1 for row, c in floor_cells)

    xs = [p[0] for s in route.steps for p in s.points]
    ys = [p[1] for s in route.steps for p in s.points]
    ts = float(profile.tile_size)
    rise = round((max(ys) - min(ys)) / ts, 1) if ys else 0.0
    run = round((max(xs) - min(xs)) / ts, 1) if xs else 0.0
    return {"spans": len(reach), "branching": branching, "dead_ends": dead, "cycles": cycles,
            "corridor": corridor, "openings": border, "room": room, "rise": rise, "run": run,
            "route_ok": bool(route.ok), "size": [w, h]}


def decide(geo: Dict[str, Any], fallback: str) -> Dict[str, Any]:
    """``{"type", "confidence"}`` from :func:`geometry` features (see the rules in the module doc)."""
    cands: List[Tuple[float, int, str]] = []
    room = geo.get("room") or {}
    if room and room.get("reached") and not room.get("hall"):
        roofed = room["roof"] >= ROOF_ROCK
        m = min(_ratio(room["w"], ARENA_W), _ratio(room["h"], ARENA_H))
        if roofed and room["walls"] >= WALL_ROCK and room["flat"] >= FLAT_SHARE \
                and room["openings"] <= ARENA_MAX_OPENINGS and m >= 1:
            cands.append((_confidence(m), 1, "arena"))
        centred = 0.25 <= room["centre"] <= 0.75
        m = min(_ratio(room["w"], HUB_W), _ratio(room["h"], HUB_H))
        if roofed and centred and room["openings"] >= HUB_MIN_OPENINGS \
                and room["sides"] >= HUB_MIN_SIDES and m >= 1:
            cands.append((_confidence(m), 2, "hub"))
    m = max(min(_ratio(geo["branching"], LAB_BRANCH), _ratio(geo["dead_ends"], LAB_DEAD)),
            _ratio(geo["cycles"], LAB_CYCLES))
    m = min(m, _ratio(geo.get("corridor", 0.0), LAB_CORRIDOR))
    if m >= 1:
        cands.append((_confidence(m), 0, "labyrinth"))
    run = max(geo.get("run", 0.0), 1.0)
    rise = geo.get("rise", 0.0)
    m = min(_ratio(rise / run, SHAFT_RATIO), _ratio(rise, SHAFT_MIN_RISE))
    if geo.get("route_ok") and m >= 1:
        cands.append((_confidence(m), 3, "shaft"))
    if not cands:
        return {"type": fallback, "confidence": 0.5}
    conf, _, t = max(cands, key=lambda x: (x[0], -x[1]))
    return {"type": t, "confidence": conf}


def classify(grid: Sequence[Sequence[Any]], entities: Sequence[Any], profile: Any = None,
             graph: Any = None, route: Any = None, fallback: Optional[str] = None
             ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """``(structure_geo, geometry)`` of a window; *fallback* is its ``structure_of`` type."""
    if fallback is None:
        from patterns.ingest import _standable_cells, _surface_runs, structure_of

        hazards = sum(1 for row in grid for v in row if int(v) == _HAZARD)
        fallback = structure_of(grid, _surface_runs(_standable_cells(grid)), hazards)
    geo = geometry(grid, entities, profile, graph, route)
    return decide(geo, fallback), geo


def structure_matches(rec: Dict[str, Any], structure: str) -> bool:
    """Is *rec* a window of type *structure*?  Its label says so; or it has no label and the
    heuristic says so; or its label is missing or generic (corridor, open) and the geometry
    classifier says so with confidence >= :data:`MIN_CONFIDENCE`."""
    lab = (rec.get("label") or {}).get("structure_type")
    feats = rec.get("features") or {}
    if (lab or feats.get("structure")) == structure:
        return True
    if lab and lab not in GENERIC:
        return False
    geo = feats.get("structure_geo") or {}
    return geo.get("type") == structure and float(geo.get("confidence") or 0) >= MIN_CONFIDENCE


def fill_structure_geo(store: Any, profile: Any = None, only_missing: bool = False) -> int:
    """Classify every window record of *store* (from its ASCII; no LLM).  Returns the count."""
    from game.profile import GameProfile
    from patterns.ingest import ascii_to_grid, guess_start_exit

    profile = profile or GameProfile()
    n = 0
    for rec in list(store):
        if rec.get("kind", "pattern") != "pattern":
            continue
        feats = rec.setdefault("features", {})
        if only_missing and "structure_geo" in feats:
            continue
        parsed = ascii_to_grid(rec.get("ascii"))
        if parsed is None:
            continue
        grid, ents = parsed
        try:
            sg, geo = classify(grid, guess_start_exit(grid, ents), profile,
                               fallback=feats.get("structure"))
        except Exception:  # noqa: BLE001 - an odd window keeps its other features
            continue
        feats["structure_geo"] = sg
        feats["geometry"] = geo
        n += 1
    return n


__all__ = ["GENERIC", "GEO_TYPES", "MIN_CONFIDENCE", "classify", "decide", "fill_structure_geo",
           "geometry", "structure_matches"]
