"""Place every LevelSpec entity on its anchor platform.

An entity stands in the row just above its anchor's surface (``row = anchor
row − 1``); flyers hover 3 (else 2) tiles above the surface when there is room.
Entities sharing an anchor are spread evenly across its width, never two on
one cell.  A cell must be empty with headroom for the entity (1 tile, or the
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

    placed: List[IREntity] = []
    player_h = spec_layout.player_tiles[1]
    for anchor, ents in groups.items():
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
