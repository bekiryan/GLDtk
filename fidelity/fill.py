"""Deterministic completeness fill — add what the LLM forgot, without an LLM call.

``fill_missing(spec)`` returns a copy of the spec where:

* every ``entity_count`` requirement short of entities gets the missing ones:
  the right kind and subkind, in the required section (or spread over all
  sections), spread across that area's non-Hazard nodes — least loaded node
  first, Platforms before the Start / Exit nodes, left to right — with fresh
  unique ids and the requirement text as ``description``;
* every unmet ``start_item`` puts the item on the Start entity
  (``starting_weapon`` when it is a known weapon and the slot is free,
  ``starting_items`` otherwise);
* an unmet ``size`` requirement (within the limits) enlarges ``spec.size``
  (nodes keep their coordinates, so the level only gains space).

Start / Exit counts, missing sections, gravity and abilities are not filled:
they need the LLM (or cannot be met at all).  An invalid subkind ("ghost")
leaves the kind field null (the catalog default) and becomes the ``sprite``,
so the checker still matches it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from spec.level_spec import LevelSpec, Requirement, SpecEntity, SpecNode

from .checker import (
    MAX_HEIGHT_TILES,
    MAX_WIDTH_TILES,
    SUBKIND_FIELDS,
    entity_deficit,
    has_start_item,
    resolve_subkind,
    spec_view,
)

UNFILLABLE_KINDS = {"Start", "Exit"}
KNOWN_WEAPONS = {
    "sword", "bow", "crossbow", "gun", "staff", "axe", "spear", "dagger", "knife",
    "hammer", "mace", "whip", "wand", "pistol", "rifle", "blaster", "sling", "slingshot",
}


@dataclass
class FillResult:
    spec: LevelSpec
    filled: List[str] = field(default_factory=list)  # requirement ids that were filled
    entities_added: int = 0
    notes: List[str] = field(default_factory=list)  # human-readable, for warnings


def _fresh_id(base: str, taken: Set[str]) -> str:
    n = 1
    while f"{base}_{n}" in taken:
        n += 1
    out = f"{base}_{n}"
    taken.add(out)
    return out


def _candidate_nodes(spec: LevelSpec, section: Optional[str]) -> List[SpecNode]:
    """Non-Hazard nodes of *section* (all sections when None), left to right."""
    nodes = [
        n for n in spec.nodes
        if n.type != "Hazard" and (section is None or n.section == section)
    ]
    return sorted(nodes, key=lambda n: (n.x, n.y, n.id))


def _new_entity(
    req: Requirement, node: SpecNode, eid: str
) -> SpecEntity:
    check = req.check
    assert check is not None and check.entity_kind is not None
    kind = check.entity_kind
    subkind, valid = resolve_subkind(kind, check.subkind)
    fields: Dict[str, object] = {}
    if subkind is not None and valid:
        fields[SUBKIND_FIELDS[kind]] = subkind
    return SpecEntity(
        id=eid,
        kind=kind,
        anchor=node.id,
        section=node.section,
        description=req.text,
        sprite=subkind or "",
        **fields,
    )


def _fill_entities(
    spec: LevelSpec, req: Requirement, entities: List[SpecEntity], taken: Set[str]
) -> int:
    check = req.check
    assert check is not None
    view = spec_view(spec.model_copy(update={"entities": entities}))
    deficit = entity_deficit(view, check)
    if deficit <= 0:
        return 0
    if check.section and check.section not in view.sections:
        return 0
    nodes = _candidate_nodes(spec, check.section)
    if not nodes:
        return 0
    load: Counter = Counter(e.anchor for e in entities)
    order = {n.id: i for i, n in enumerate(nodes)}
    base = f"auto_{req.id.lower()}"
    for _ in range(deficit):
        node = min(
            nodes,
            key=lambda n: (load[n.id], n.type != "Platform", order[n.id]),
        )
        entities.append(_new_entity(req, node, _fresh_id(base, taken)))
        load[node.id] += 1
    return deficit


def _fill_start_item(spec: LevelSpec, req: Requirement, entities: List[SpecEntity]) -> bool:
    check = req.check
    assert check is not None
    item = (check.subkind or "").strip()
    if not item:
        return False
    view = spec_view(spec.model_copy(update={"entities": entities}))
    if has_start_item(view, item):
        return False
    idx = next((i for i, e in enumerate(entities) if e.kind == "Start"), None)
    if idx is None:
        return False
    start = entities[idx]
    if item.lower() in KNOWN_WEAPONS and not start.starting_weapon:
        update: Dict[str, object] = {"starting_weapon": item}
    else:
        update = {"starting_items": [*(start.starting_items or []), item]}
    entities[idx] = start.model_copy(update=update)
    return True


def fill_missing(spec: LevelSpec) -> FillResult:
    """Deterministically fill the requirements the spec does not meet (see module doc)."""
    entities = list(spec.entities)
    taken = {e.id for e in entities}
    size = spec.size
    result = FillResult(spec=spec)

    for req in spec.requirements:
        check = req.check
        if check is None:
            continue
        if check.type == "entity_count":
            if check.entity_kind is None or check.entity_kind in UNFILLABLE_KINDS:
                continue
            added = _fill_entities(spec, req, entities, taken)
            if added:
                result.filled.append(req.id)
                result.entities_added += added
                result.notes.append(
                    f"completeness: added {added} {check.entity_kind} entit"
                    f"{'y' if added == 1 else 'ies'} for {req.id} ({req.text!r})"
                )
        elif check.type == "start_item":
            if _fill_start_item(spec, req, entities):
                result.filled.append(req.id)
                result.notes.append(
                    f"completeness: the player now starts with '{check.subkind}' ({req.id})"
                )
        elif check.type == "size":
            w = max(size.width_tiles, check.min_width_tiles or 0)
            h = max(size.height_tiles, check.min_height_tiles or 0)
            if (w, h) != (size.width_tiles, size.height_tiles) and (
                w <= MAX_WIDTH_TILES and h <= MAX_HEIGHT_TILES
            ):
                size = size.model_copy(update={"width_tiles": w, "height_tiles": h})
                result.filled.append(req.id)
                result.notes.append(f"completeness: enlarged the level to {w}×{h} tiles ({req.id})")

    if result.filled:
        result.spec = spec.model_copy(update={"entities": entities, "size": size})
    return result
