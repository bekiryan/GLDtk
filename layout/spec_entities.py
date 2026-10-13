"""Place every LevelSpec entity on its anchor platform.

An entity stands in the row just above its anchor's surface (``row = anchor
row − 1``); flyers hover 3 (else 2) tiles above the surface when there is room.
Entities sharing an anchor are spread evenly across its width, never two on
one cell.

A platform holds about one entity per ``ENTITY_SPACING`` (2) tiles of width.
When an anchor is over capacity it is widened first (if the cells next to it
are free and stay inside its section); the overflow that still does not fit
moves to the nearest non-hazard platform of the same section that has room,
with a warning per relocation.  Start and Exit never move, and nothing is
relocated onto a Start or Exit node.  A cell must be empty with headroom for the entity (1 tile, or the
player hitbox for Start); otherwise the other cells of the anchor are tried
and, if none works, the entity is placed anyway, flagged
``metadata["blocked"] = True`` and a warning is recorded (grid verification
reports it and repair moves it).

Spec Start / Exit entities replace the structural Start / Exit markers that
``SpecLayout.rasterise`` adds, so there is never a second Start.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from spec.level_spec import LevelSpec, SpecEntity

from .ir import IREntity, TileValue
from .spec_layout import SpecLayout

FLYER_HEIGHTS = (3, 2)  # tiles above the surface, preferred first
ENTITY_SPACING = 2  # tiles of platform width per entity
_FIXED_KINDS = ("Start", "Exit")  # never relocated
_BASE_FIELDS = {"id", "kind", "anchor", "section", "description", "sprite"}


def spread_cols(col_start: int, width: int, count: int) -> List[int]:
    """*count* columns spread evenly over ``[col_start, col_start + width)``."""
    if count <= 0:
        return []
    return [col_start + int((i + 0.5) * width / count) for i in range(count)]


def entity_metadata(ent: SpecEntity, anchor: str) -> Dict[str, object]:
    """Every non-null kind field, plus description, sprite, spec_id, section, anchor."""
    meta: Dict[str, object] = {
        k: v for k, v in ent.model_dump().items() if v is not None and k not in _BASE_FIELDS
    }
    meta.update(
        description=ent.description,
        sprite=ent.sprite,
        spec_id=ent.id,
        section=ent.section,
        anchor=anchor,
    )
    if anchor != ent.anchor:
        meta["original_anchor"] = ent.anchor
    return meta


def capacity(width: int) -> int:
    """How many entities a platform *width* tiles wide holds."""
    return max(1, width // ENTITY_SPACING)


def _section_limits(layout: SpecLayout, node_id: str) -> Tuple[int, int]:
    """Inclusive column range the node may grow into (its section's band)."""
    sid = layout.node_sections.get(node_id)
    for band in layout.sections:
        if band.id == sid:
            return max(0, band.col_start), min(layout.width - 1, band.col_end)
    return 0, layout.width - 1


def widen_node(layout: SpecLayout, node_id: str, target_width: int) -> int:
    """Grow a standable node towards *target_width* tiles; returns the new width.

    Columns are added alternately on the right and the left, only where the
    node's row is empty, no other node would conflict with the wider rectangle
    and the column stays inside the node's section band.  The grid is updated
    in place.
    """
    nl = layout.node_layouts[node_id]
    if not layout.standable(node_id):
        return nl.width
    lo, hi = _section_limits(layout, node_id)
    ir = layout.ir

    def can_add(col: int) -> bool:
        if not (lo <= col <= hi) or ir.get_tile(col, nl.row) != TileValue.EMPTY:
            return False
        rect = (nl.row, min(nl.col, col), max(nl.col + nl.width - 1, col))
        return all(
            layout.conflict(node_id, other, rect) is None
            for other in layout.node_layouts
            if other != node_id
        )

    side = 0
    while nl.width < target_width:
        for attempt in (side, 1 - side):
            col = nl.col + nl.width if attempt == 0 else nl.col - 1
            if can_add(col):
                ir.set_tile(col, nl.row, TileValue.SOLID)
                if attempt == 1:
                    nl.col -= 1
                nl.width += 1
                side = 1 - attempt
                break
        else:
            break
    return nl.width


def _relocation_target(
    layout: SpecLayout, anchor: str, groups: Dict[str, List[SpecEntity]]
) -> Optional[str]:
    """Nearest same-section non-hazard platform with room (not a Start / Exit node)."""
    src = layout.node_layouts[anchor]
    section = layout.node_sections.get(anchor)
    golden = set(layout.golden_path)
    best: Optional[Tuple[Tuple[float, int, str], str]] = None
    for nid, nl in layout.node_layouts.items():
        if nid == anchor or layout.node_sections.get(nid) != section:
            continue
        if not layout.standable(nid) or layout.node_kinds.get(nid) in _FIXED_KINDS:
            continue
        if len(groups.get(nid, [])) >= capacity(nl.width):
            continue
        dist = abs((nl.col + nl.width / 2) - (src.col + src.width / 2)) + abs(nl.row - src.row)
        key = (dist, 0 if nid in golden else 1, nid)
        if best is None or key < best[0]:
            best = (key, nid)
    return best[1] if best else None


def _cell_ok(layout: SpecLayout, col: int, row: int, height: int, taken: Set[Tuple[int, int]]) -> bool:
    ir = layout.ir
    if (col, row) in taken:
        return False
    for k in range(height):
        r = row - k
        if r < 0 or not ir.in_bounds(col, r) or ir.get_tile(col, r) != TileValue.EMPTY:
            return False
    return True


def place_spec_entities(spec: LevelSpec, spec_layout: SpecLayout) -> List[IREntity]:
    """Place every spec entity; returns the new entities (also appended to ``ir.entities``).

    Idempotent: previously placed spec entities are removed first.  Warnings
    go to ``spec_layout.entity_warnings`` (reset on every call).
    """
    ir = spec_layout.ir
    warnings: List[str] = []
    kinds = {e.kind for e in spec.entities}
    ir.entities = [
        e
        for e in ir.entities
        if "spec_id" not in e.metadata
        and not (e.metadata.get("structural") and e.entity_type in kinds)
    ]
    taken: Set[Tuple[int, int]] = {(e.col, e.row) for e in ir.entities}

    groups: Dict[str, List[SpecEntity]] = {}
    for ent in spec.entities:
        anchor = spec_layout.entity_anchors.get(ent.id, ent.anchor)
        groups.setdefault(anchor, []).append(ent)

    # Crowding: widen over-capacity platforms, then move the overflow elsewhere.
    for anchor in list(groups):
        nl = spec_layout.node_layouts.get(anchor)
        ents = groups[anchor]
        if nl is None or len(ents) <= capacity(nl.width):
            continue
        old_w = nl.width
        widen_node(spec_layout, anchor, ENTITY_SPACING * len(ents))
        if nl.width != old_w:
            spec_layout.adjustments.append(
                f"widened '{anchor}' from {old_w} to {nl.width} tile(s) to fit {len(ents)} "
                "entities"
            )
    for anchor in list(groups):
        nl = spec_layout.node_layouts.get(anchor)
        ents = groups[anchor]
        if nl is None or len(ents) <= capacity(nl.width):
            continue
        # the last entities of the anchor overflow, in spec order
        movable = [e for e in ents if e.kind not in _FIXED_KINDS]
        overflow = movable[max(0, len(movable) - (len(ents) - capacity(nl.width))):]
        for ent in overflow:
            target = _relocation_target(spec_layout, anchor, groups)
            if target is None:
                warnings.append(
                    f"'{anchor}' is crowded ({len(ents)} entities on {nl.width} tile(s)) and "
                    f"no other platform in its section has room for '{ent.id}'"
                )
                break
            ents.remove(ent)
            groups.setdefault(target, []).append(ent)
            warnings.append(f"moved '{ent.id}' from '{anchor}' to '{target}' ({anchor} was crowded)")

    placed: List[IREntity] = []
    player_h = spec_layout.player_tiles[1]
    for anchor, ents in groups.items():
        if not ents:
            continue
        nl = spec_layout.node_layouts.get(anchor)
        if nl is None:
            warnings.append(f"entity anchor '{anchor}' is not a laid-out node; skipped {[e.id for e in ents]}")
            continue
        cols_on_anchor = list(range(nl.col, nl.col + nl.width))
        for ent, pref in zip(ents, spread_cols(nl.col, nl.width, len(ents))):
            height = player_h if ent.kind == "Start" else 1
            rows = [nl.row - 1]
            if ent.kind == "Enemy" and ent.archetype == "flyer":
                rows = [nl.row - k for k in FLYER_HEIGHTS] + rows
            cols = sorted(cols_on_anchor, key=lambda c: (abs(c - pref), c))
            spot: Optional[Tuple[int, int]] = None
            for row in rows:
                for col in cols:
                    if _cell_ok(spec_layout, col, row, height, taken):
                        spot = (col, row)
                        break
                if spot:
                    break
            meta = entity_metadata(ent, anchor)
            if spot is None:
                spot = (pref, nl.row - 1)
                meta["blocked"] = True
                warnings.append(
                    f"no free cell with headroom for '{ent.id}' on '{anchor}'; placed it "
                    f"at column {pref} anyway"
                )
            if ent.patrol_range is not None and ent.patrol_range > nl.width:
                meta["patrol_range"] = nl.width
                warnings.append(
                    f"clamped patrol_range of '{ent.id}' from {ent.patrol_range} to "
                    f"{nl.width} tiles (the width of '{anchor}')"
                )
            taken.add(spot)
            placed.append(IREntity(entity_type=ent.kind, col=spot[0], row=spot[1], metadata=meta))

    ir.entities.extend(placed)
    spec_layout.entity_warnings = warnings
    return placed
