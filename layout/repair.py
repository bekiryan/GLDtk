"""Deterministic grid repair — fix what ``verify_grid`` found, without the LLM.

``repair_grid`` runs at most ``MAX_ROUNDS`` rounds of repair → re-rasterise →
re-place entities → verify.  Each round fixes the path first (a level without
a path makes reachability-based entity fixes premature), then entities:

* START_BLOCKED / blocked hop / blocked entity cell: clear the headroom or the
  arc by nudging the *blocking* platform (never the hop's own nodes) to the
  nearest conflict-free row that keeps the swept cells empty;
* NO_GRID_PATH / WALL_MISSING: if no nudge clears the hop, insert a 3-tile
  stepping stone (new node ``stoneN`` in the section that owns its column) in
  the gap, at the position nearest the hop's midpoint that the collision-aware
  oracle accepts from both ends (or, failing that, from the source end and
  closer to the target — the next round continues from it);
* LOW_CEILING: nudge the platform that forms the low ceiling out of the
  walker's headroom; if that fails, repair the failing hop as above;
* ENTITY_UNREACHABLE / ENTITY_BLOCKED / KEY_LOCK_ORDER: move the entity to the
  nearest reachable non-hazard platform in its section (warning
  "moved X from A to B");
* SOFTLOCK: ``layout.softlock_repair`` (a stone chain out of the trap, or the
  key moved before it); a round of soft-lock fixes that makes the level worse
  is undone, and the next rounds go on without them.

No stone and no nudge may create a low ceiling: a platform never lands where
it would leave a standable tile (its own or one below it) with fewer empty
tiles above than the player is tall (:func:`_keeps_headroom`).  Moves are
checked with the level's gravity per section (``gravity_field_of``).

The layout is mutated in place and returned.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from game.profile import GameProfile
from spec.level_spec import LevelSpec
from symbolic.extractor import move_between, span_point_pairs
from symbolic.physics import ABILITY_ORDER, ability_enabled, trajectory_candidates

from .ir import TileValue
from .spec_entities import place_spec_entities
from .spec_layout import Rect, SpecLayout
from .sugiyama import NodeLayout
from .verify import GridError, GridReport, gravity_field_of, verify_grid

MAX_ROUNDS = 5
STONE_WIDTH = 3
_MAX_BLOCKER_CANDIDATES = 48
_MAX_STONE_CANDIDATES = 500

Cell = Tuple[int, int]  # (col, row)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _swept_cells(layout: SpecLayout, samples, hitbox: Tuple[float, float]) -> Set[Cell]:
    """Every in-grid cell the hitbox covers along *samples*."""
    ts = layout.tile_size
    height, width = layout.height, layout.width
    w, h = hitbox
    cells: Set[Cell] = set()
    for x, y in samples:
        c0 = int(math.floor((x - w / 2 + 0.01) / ts))
        c1 = int(math.floor((x + w / 2 - 0.01) / ts))
        r_bottom = height - 2 - int(math.floor((y + 0.01) / ts))
        r_top = height - 2 - int(math.floor((y + h - 0.01) / ts))
        for r in range(max(0, r_top), min(height - 1, r_bottom) + 1):
            for c in range(max(0, c0), min(width - 1, c1) + 1):
                cells.add((c, r))
    return cells


def _keeps_headroom(
    layout: SpecLayout, cols: range, row: int, standable: bool, own: Set[Cell]
) -> bool:
    """Would solid tiles at ``(cols, row)`` keep every walk's headroom on the grid?

    The new tiles need ``player_h`` empty tiles above them (if standable), and
    no standable tile below them may end up with less.  ``own`` are the tiles
    the platform occupies now (they will be cleared).
    """
    grid = layout.ir.grid
    height = layout.height
    ph = layout.player_tiles[1]
    for c in cols:
        if not 0 <= c < layout.width:
            return False
        if standable:
            for r in range(max(0, row - ph), row):
                if grid[r][c] != TileValue.EMPTY and (c, r) not in own:
                    return False
        for r in range(row + 1, min(height, row + ph + 1)):
            if grid[r][c] == TileValue.SOLID and (c, r) not in own:
                return False
    return True


def _node_cells(layout: SpecLayout, node_id: str) -> Set[Cell]:
    nl = layout.node_layouts[node_id]
    return {(c, nl.row) for c in range(nl.col, nl.col + nl.width)}


def _nearest_safe_row(
    layout: SpecLayout, node_id: str, prefer_down: bool, avoid: Set[Cell]
) -> Optional[int]:
    """``nearest_free_row`` (excluding the current row) that also keeps headroom."""
    nl = layout.node_layouts[node_id]
    own = _node_cells(layout, node_id)
    cols = range(nl.col, nl.col + nl.width)
    standable = layout.standable(node_id)
    for d in range(1, layout.height):
        for cand in ((nl.row + d, nl.row - d) if prefer_down else (nl.row - d, nl.row + d)):
            if layout.row_is_free(node_id, cand, avoid) and _keeps_headroom(
                layout, cols, cand, standable, own
            ):
                return cand
    return None


def _nudge(
    layout: SpecLayout, node_id: str, avoid: Set[Cell], reason: str, applied: List[str]
) -> bool:
    nl = layout.node_layouts[node_id]
    # Move away from the cells to keep clear: down if they are mostly above.
    prefer_down = bool(avoid) and sum(r for _c, r in avoid) / len(avoid) <= nl.row
    row = _nearest_safe_row(layout, node_id, prefer_down, avoid)
    if row is None:
        return False
    old = nl.row
    nl.row = row
    applied.append(
        f"nudged '{node_id}' {'up' if row < old else 'down'} {abs(row - old)} tile(s) "
        f"to {reason}"
    )
    return True


def _centre(layout: SpecLayout, rect: Rect) -> Tuple[float, float]:
    return ((rect[1] + rect[2] + 1) / 2.0, float(rect[0]))


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ─────────────────────────────────────────────────────────────────────────────
# Fixes
# ─────────────────────────────────────────────────────────────────────────────


def _clear_headroom(
    layout: SpecLayout, col: int, row: int, height: int, protect: Set[str], applied: List[str]
) -> bool:
    """Nudge whatever occupies ``(col, row-height+1 .. row)`` out of the way."""
    cells = {(col, r) for r in range(row - height + 1, row + 1) if r >= 0}
    blockers = {layout.node_at(c, r) for c, r in cells} - {None}
    if not blockers or blockers & protect:
        return False
    return all(
        _nudge(layout, b, cells, f"clear headroom at column {col}", applied)  # type: ignore[arg-type]
        for b in sorted(blockers)  # type: ignore[arg-type]
    )


def _nudge_hop_blockers(
    layout: SpecLayout, profile: GameProfile, a: str, b: str, applied: List[str]
) -> bool:
    """Move the platforms blocking the hop a → b (never a or b themselves)."""
    grid = layout.ir.grid
    ts = float(layout.tile_size)
    hitbox = (profile.player.width_px, profile.player.height_px)
    ra, rb = layout.rect(a), layout.rect(b)
    scale = layout.gravity_scale_of(ra)
    gfield = gravity_field_of(layout, profile)
    golden = set(layout.golden_path)
    abilities = [None] + [x for x in ABILITY_ORDER if ability_enabled(profile, x)]
    best: Optional[Tuple[Tuple[int, int], Set[str], Set[Cell]]] = None
    tried = 0
    for p1, p2 in span_point_pairs(ra, rb, layout.height, ts, hitbox[0]):
        for ability in abilities:
            for samples in trajectory_candidates(
                grid, p1, p2, profile, scale, ability, gfield
            ):
                tried += 1
                if tried > _MAX_BLOCKER_CANDIDATES:
                    break
                swept = _swept_cells(layout, samples, hitbox)
                if any(c < 0 or c >= layout.width for c, _ in swept):
                    continue
                blockers: Set[str] = set()
                ok = True
                for c, r in swept:
                    if grid[r][c] == TileValue.EMPTY:
                        continue
                    if r == rb[0] and rb[1] <= c <= rb[2]:
                        continue  # landing on the target
                    nid = layout.node_at(c, r)
                    if nid is None or nid in (a, b):
                        ok = False
                        break
                    blockers.add(nid)
                if not ok or not blockers:
                    continue
                key = (len(blockers & golden), len(blockers))
                if best is None or key < best[0]:
                    best = (key, blockers, swept)
    if best is None:
        return False
    _key, blockers, swept = best
    snapshot = {n: layout.node_layouts[n].row for n in blockers}
    local: List[str] = []
    for nid in sorted(blockers):
        if not _nudge(layout, nid, swept, f"clear the hop '{a}'→'{b}'", local):
            for n, row in snapshot.items():
                layout.node_layouts[n].row = row
            return False
    applied += local
    return True


def find_stone(
    layout: SpecLayout,
    profile: GameProfile,
    ra: Rect,
    rb: Rect,
    *,
    widths: Sequence[int] = (STONE_WIDTH,),
    accept: Optional[Callable[[Rect], bool]] = None,
    links: Optional[Callable[[Rect], bool]] = None,
    allowed: Optional[Callable[[Rect], bool]] = None,
    max_candidates: int = _MAX_STONE_CANDIDATES,
) -> Optional[Tuple[Rect, bool]]:
    """Where to put a stepping stone between the spans *ra* and *rb*, or None.

    Candidates lie in the window around the two spans, nearest the hop's
    midpoint first (wider first at equal distance).  A stone must keep every
    walk's headroom (:func:`_keeps_headroom`), conflict with no node and be
    reachable from *ra* (``move_between``, collision-aware, the level's
    gravity).  Returns ``(stone, True)`` for the first stone that also reaches
    *rb*, else ``(stone, False)`` for the reachable stone nearest *rb* that is
    at least 2 tiles closer to it than *ra* (the caller continues from it).

    The size bound (v3 Part 20): *widths* are the stone widths tried; *accept*
    filters candidates before any physics check (the comfort pass keeps only
    stones within comfortable reach of *ra*); *links* is an extra condition on
    the stone → *rb* move for a full link (comfortable as well); *allowed*
    restricts where a stone may go (an edit's rectangle).  With the defaults
    this is repair's 3-tile stepping stone.  The grid is left unchanged.
    """
    grid = layout.ir.grid
    height, width = layout.height, layout.width
    min_row = layout.player_tiles[1]
    target = _centre(layout, rb)
    mid = ((_centre(layout, ra)[0] + target[0]) / 2.0, (ra[0] + rb[0]) / 2.0)
    r_lo = max(min_row, min(ra[0], rb[0]) - 2)
    r_hi = min(height - 1, max(ra[0], rb[0]) + 4)
    c_lo = max(0, min(ra[1], rb[1]) - 2)
    cands: List[Tuple[float, int, int, int, int]] = []
    for w in widths:
        c_hi = min(width - w, max(ra[2], rb[2]) + 2)
        for r in range(r_lo, r_hi + 1):
            for c in range(c_lo, c_hi + 1):
                rect: Rect = (r, c, c + w - 1)
                if accept is not None and not accept(rect):
                    continue
                if allowed is not None and not allowed(rect):
                    continue
                cands.append((_dist((c + w / 2.0, r), mid), -w, r, c, w))
    cands.sort()

    stone_id = "stone1"
    k = 1
    while stone_id in layout.node_layouts:
        k += 1
        stone_id = f"stone{k}"
    scale_a = layout.gravity_scale_of(ra)
    gfield = gravity_field_of(layout, profile)
    base = _dist(_centre(layout, ra), target)
    partial: Optional[Tuple[float, Rect]] = None

    layout.node_kinds[stone_id] = "Platform"
    layout.node_layouts[stone_id] = NodeLayout(stone_id, 0, 0, STONE_WIDTH, 1)
    try:
        for _d, _w, row, col, w in cands[:max_candidates]:
            nl = layout.node_layouts[stone_id]
            nl.row, nl.col, nl.width = row, col, w
            if not layout.row_is_free(stone_id, row):
                continue
            if not _keeps_headroom(layout, range(col, col + w), row, True, set()):
                continue  # never create a low ceiling (or a stone without headroom)
            stone: Rect = (row, col, col + w - 1)
            saved = grid[row][col : col + w]
            grid[row][col : col + w] = [TileValue.SOLID] * w
            try:
                if move_between(grid, ra, stone, profile, scale_a, gfield) is None:
                    continue
                scale_s = layout.gravity_scale_of(stone)
                if (links is None or links(stone)) and move_between(
                    grid, stone, rb, profile, scale_s, gfield
                ) is not None:
                    return stone, True
                d = _dist(_centre(layout, stone), target)
                if d < base - 2 and (partial is None or d < partial[0]):
                    partial = (d, stone)
            finally:
                grid[row][col : col + w] = saved
    finally:
        del layout.node_layouts[stone_id]
        del layout.node_kinds[stone_id]
    return (partial[1], False) if partial is not None else None


def _insert_stepping_stone(
    layout: SpecLayout, profile: GameProfile, spec: LevelSpec, a: str, b: str, applied: List[str]
) -> bool:
    found = find_stone(layout, profile, layout.rect(a), layout.rect(b))
    if found is None:
        return False
    row, col, _end = found[0]
    stone_id = "stone1"
    k = 1
    while stone_id in layout.node_layouts:
        k += 1
        stone_id = f"stone{k}"
    layout.node_layouts[stone_id] = NodeLayout(stone_id, col, row, STONE_WIDTH, 1)
    layout.node_kinds[stone_id] = "Platform"
    section = layout.section_at(col + STONE_WIDTH // 2, row).id
    layout.node_sections[stone_id] = section
    layout.added_nodes.append(stone_id)
    path = layout.golden_path
    if b in path:
        path.insert(path.index(b), stone_id)
    applied.append(
        f"inserted stepping stone '{stone_id}' at column {col}, row {row} (section "
        f"'{section}') between '{a}' and '{b}'"
    )
    return True


def _clear_low_ceiling(layout: SpecLayout, err: GridError, applied: List[str]) -> bool:
    """Nudge the platform(s) forming a LOW_CEILING out of the walker's headroom."""
    grid = layout.ir.grid
    ph = layout.player_tiles[1]
    keep: Set[Cell] = set()
    walkers: Set[str] = set()
    for col, ceiling in err.cells:
        surface = next(
            (r for r in range(ceiling + 1, layout.height) if grid[r][col] != TileValue.EMPTY),
            None,
        )
        if surface is None:
            continue
        keep |= {(col, r) for r in range(max(0, surface - ph), surface)}
        walker = layout.node_at(col, surface)
        if walker is not None:
            walkers.add(walker)
    blockers = {layout.node_at(c, r) for c, r in keep if grid[r][c] != TileValue.EMPTY}
    blockers.discard(None)
    if not blockers or blockers & walkers:
        return False
    snapshot = {n: layout.node_layouts[n].row for n in blockers}  # type: ignore[index]
    local: List[str] = []
    for nid in sorted(blockers):  # type: ignore[arg-type]
        if not _nudge(layout, nid, keep, "raise a low ceiling", local):
            for n, row in snapshot.items():
                layout.node_layouts[n].row = row  # type: ignore[index]
            return False
    applied += local
    return True


def _move_entity(
    layout: SpecLayout,
    spec: LevelSpec,
    entity_id: str,
    allowed: Set[str],
    applied: List[str],
) -> bool:
    ent = next((e for e in spec.entities if e.id == entity_id), None)
    if ent is None or ent.kind in ("Start", "Exit"):
        return False
    current = layout.entity_anchors.get(ent.id, ent.anchor)
    if current not in layout.node_layouts:
        return False
    here = _centre(layout, layout.rect(current))
    pool = [
        n
        for n in sorted(allowed)
        if n != current and layout.standable(n) and layout.node_kinds.get(n) != "Exit"
    ]
    same = [n for n in pool if layout.node_sections.get(n) == ent.section]
    pool = same or pool
    if not pool:
        return False
    new = min(pool, key=lambda n: (_dist(_centre(layout, layout.rect(n)), here), n))
    layout.entity_anchors[ent.id] = new
    applied.append(f"moved {ent.id} from {current} to {new}")
    return True


def _repair_round(
    layout: SpecLayout, report: GridReport, profile: GameProfile, spec: LevelSpec,
    softlocks: bool = True,
) -> List[str]:
    applied: List[str] = []
    by_code: Dict[str, List[GridError]] = {}
    for err in report.errors:
        by_code.setdefault(err.code, []).append(err)

    for err in by_code.get("START_BLOCKED", []):
        start = next((e for e in layout.ir.entities if e.entity_type == "Start"), None)
        if start is not None:
            _clear_headroom(
                layout, start.col, start.row, layout.player_tiles[1],
                set(err.node_ids), applied,
            )

    path_errors = (
        by_code.get("LOW_CEILING", [])
        + by_code.get("NO_GRID_PATH", [])
        + by_code.get("WALL_MISSING", [])
    )
    for err in path_errors[:1]:
        if err.code == "LOW_CEILING" and _clear_low_ceiling(layout, err, applied):
            continue
        if len(err.node_ids) == 2 and err.node_ids[0] != err.node_ids[1]:
            a, b = err.node_ids
            if not _nudge_hop_blockers(layout, profile, a, b, applied):
                _insert_stepping_stone(layout, profile, spec, a, b, applied)
    if path_errors:
        return applied  # entity fixes wait until the path is sound

    for err in by_code.get("ENTITY_BLOCKED", []):
        for sid in err.entity_ids:
            ent = next((e for e in layout.ir.entities if e.metadata.get("spec_id") == sid), None)
            if ent is not None and _clear_headroom(
                layout, ent.col, ent.row, 1, set(layout.golden_path), applied
            ):
                continue
            _move_entity(layout, spec, sid, report.reachable_nodes, applied)
    for err in by_code.get("ENTITY_UNREACHABLE", []):
        for sid in err.entity_ids:
            _move_entity(layout, spec, sid, report.reachable_nodes, applied)
    for err in by_code.get("KEY_LOCK_ORDER", []):
        for sid in err.entity_ids[:1]:
            _move_entity(layout, spec, sid, report.pre_exit_nodes, applied)
    if by_code.get("SOFTLOCK") and softlocks:
        from .softlock_repair import repair_softlocks  # (imports this module)

        applied += repair_softlocks(layout, report, profile, spec)
    return applied


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def repair_grid(
    spec_layout: SpecLayout,
    report: GridReport,
    profile: Optional[GameProfile],
    spec: LevelSpec,
) -> Tuple[SpecLayout, List[str]]:
    """Repair *spec_layout* until it verifies or ``MAX_ROUNDS`` rounds pass.

    Returns ``(spec_layout, applied)`` — the (mutated) layout and a description
    of every repair, in order.  Call ``verify_grid`` again for the final report.
    """
    from .softlock_repair import error_score, restore, snapshot

    profile = profile or GameProfile()
    applied: List[str] = []
    soft_ok = True
    for _ in range(MAX_ROUNDS):
        if report.ok:
            break
        soft = soft_ok and any(e.code == "SOFTLOCK" for e in report.errors)
        snap = snapshot(spec_layout) if soft else None
        done = _repair_round(spec_layout, report, profile, spec, softlocks=soft)
        if not done:
            break
        spec_layout.rasterise()
        place_spec_entities(spec, spec_layout)
        new = verify_grid(spec_layout, profile, spec)
        before, after = error_score(report), error_score(new)
        if snap is not None and (after[0] > before[0] or after[1] > before[1]):
            restore(spec_layout, snap)  # a soft-lock repair never makes the level worse:
            spec_layout.rasterise()  # undo the round and go on without soft-lock fixes
            place_spec_entities(spec, spec_layout)
            soft_ok = False
            continue
        applied += done
        report = new
    return spec_layout, applied
