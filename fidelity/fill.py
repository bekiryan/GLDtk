"""Deterministic completeness fill — add what the LLM forgot, without an LLM call.

``fill_missing(spec)`` returns a copy of the spec where:

* every ``entity_count`` requirement short of entities gets the missing ones:
  the right kind and subkind, in the required section (or spread over all
  sections), spread across that area's non-Hazard nodes — least loaded node
  first, Platforms before the Start / Exit nodes, left to right (bottom to
  top in a vertical level) — with fresh unique ids and the requirement text
  as ``description``;
* every unmet ``start_item`` puts the item on the Start entity
  (``starting_weapon`` when it is a known weapon and the slot is free,
  ``starting_items`` otherwise);
* an unmet ``size`` requirement (within the limits) enlarges ``spec.size``
  (nodes keep their coordinates, so the level only gains space);
* every unmet ``hazard`` requirement (including an ``entity_count`` that
  names a hazard, see ``checker.effective_check``) gets Hazard *nodes* — never
  an Enemy — placed under a gap of its section (:func:`_hazard_node`).  Hazard
  nodes have no edges, so the graph stays valid.  A section without a gap
  wide enough gets none: the requirement stays missing for the re-prompt.

Start / Exit counts, missing sections, gravity and abilities are not filled:
they need the LLM (or cannot be met at all).  An invalid subkind ("ghost")
leaves the kind field null (the catalog default) and becomes the ``sprite``,
so the checker still matches it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from spec.level_spec import TILE_SIZE, LevelSpec, Requirement, SpecEntity, SpecNode

from .checker import (
    MAX_HEIGHT_TILES,
    MAX_WIDTH_TILES,
    SUBKIND_FIELDS,
    effective_check,
    entity_deficit,
    has_start_item,
    hazard_deficit,
    resolve_subkind,
    spec_view,
)

UNFILLABLE_KINDS = {"Start", "Exit"}
KNOWN_WEAPONS = {
    "sword", "bow", "crossbow", "gun", "staff", "axe", "spear", "dagger", "knife",
    "hammer", "mace", "whip", "wand", "pistol", "rifle", "blaster", "sling", "slingshot",
}
HAZARD_MAX_TILES = 5  # widest filled hazard
HAZARD_MIN_GAP_TILES = 3  # gap between two platforms needed for a hazard (1-tile margins)
HAZARD_CLEAR_TILES = 3  # rows above a filled hazard kept free of other nodes


@dataclass
class FillResult:
    spec: LevelSpec
    filled: List[str] = field(default_factory=list)  # requirement ids that were filled
    entities_added: int = 0
    nodes_added: int = 0  # Hazard nodes added for hazard requirements
    notes: List[str] = field(default_factory=list)  # human-readable, for warnings


def _fresh_id(base: str, taken: Set[str]) -> str:
    n = 1
    while f"{base}_{n}" in taken:
        n += 1
    out = f"{base}_{n}"
    taken.add(out)
    return out


def _candidate_nodes(spec: LevelSpec, section: Optional[str]) -> List[SpecNode]:
    """Non-Hazard nodes of *section* (all sections when None), left to right
    (bottom to top in a vertical level)."""
    nodes = [
        n for n in spec.nodes
        if n.type != "Hazard" and (section is None or n.section == section)
    ]
    if spec.layout_orientation == "vertical":
        return sorted(nodes, key=lambda n: (n.y, n.x, n.id))
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


def _hazard_node(spec: LevelSpec, nodes: List[SpecNode], section: str, nid: str) -> Optional[SpecNode]:
    """A Hazard node under the widest free gap between two platforms of *section*.

    The gap is between neighbouring (by x) non-Hazard nodes, at least
    ``HAZARD_MIN_GAP_TILES`` wide; the hazard keeps a 1-tile margin on both
    sides and is at most ``HAZARD_MAX_TILES`` wide.  Horizontal levels put it
    on the floor (y = 0, a real pit, below every jump arc); vertical levels at
    the lower neighbour's height (y = 0 would be in the bottom section).  No
    other node may overlap its columns within ``HAZARD_CLEAR_TILES`` rows of
    it, so it never sits in a platform's headroom.
    """
    ts = TILE_SIZE
    vertical = spec.layout_orientation == "vertical"
    standable = sorted(
        (n for n in nodes if n.section == section and n.type != "Hazard"),
        key=lambda n: (n.x, n.y, n.id),
    )
    gaps = []
    for a, b in zip(standable, standable[1:]):
        lo = int(round((a.x + a.w) / ts))  # first free column
        hi = int(round(b.x / ts)) - 1  # last free column
        if hi - lo + 1 < HAZARD_MIN_GAP_TILES:
            continue
        gaps.append((hi - lo + 1, lo, hi, min(a.y, b.y)))
    gaps.sort(key=lambda g: (-g[0], g[1]))
    for width, lo, hi, low_y in gaps:
        w = min(HAZARD_MAX_TILES, width - 2)
        col = lo + 1 + (width - 2 - w) // 2
        x, y = float(col * ts), (float(low_y) if vertical else 0.0)
        clear = HAZARD_CLEAR_TILES * ts
        blocked = any(
            n.x < x + w * ts and x < n.x + n.w and y - clear <= n.y <= y + clear
            for n in nodes
        )
        if not blocked:
            return SpecNode(id=nid, type="Hazard", x=x, y=y, w=float(w * ts), h=float(ts),
                            section=section)
    return None


def _fill_hazards(
    spec: LevelSpec, req: Requirement, nodes: List[SpecNode], taken: Set[str]
) -> int:
    check = effective_check(req)
    assert check is not None
    view = spec_view(spec.model_copy(update={"nodes": nodes}))
    if check.section and check.section not in view.sections:
        return 0
    sections = [check.section] if check.section else [
        s.id for s in sorted(spec.sections, key=lambda s: s.order)
    ]
    added = 0
    for _ in range(hazard_deficit(view, check)):
        for sid in sections:
            node = _hazard_node(spec, nodes, sid, _fresh_id(f"auto_hazard_{req.id.lower()}", set(taken)))
            if node is not None:
                taken.add(node.id)
                nodes.append(node)
                added += 1
                break
        else:
            break
    return added


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


STRUCTURE_WORDS = {
    "corridor": ("corridor", "hall", "tunnel", "passage", "cave"),
    "parkour": ("parkour", "gap", "jumps"),
    "labyrinth": ("labyrinth", "maze", "catacomb"),
    "arena": ("arena", "boss", "fight", "chamber"),
    "shaft": ("shaft", "climb", "tower", "well", "descent"),
    "hub": ("hub", "rooms", "room"),
    "open": ("sky", "outdoor", "open"),
}


def _asked_structures(spec: LevelSpec) -> List[str]:
    """The structure types the spec's ``structure`` requirements ask for, in order."""
    out: List[str] = []
    for req in spec.requirements:
        chk = req.check
        if chk is not None and chk.type == "structure" and chk.structure and chk.structure not in out:
            out.append(chk.structure)
    return out


def _retypable(sections: List, asked: List[str], pinned: Set[str]) -> List:  # type: ignore[type-arg]
    """Sections whose structure can change without losing an asked-for one: not
    pinned by a section-specific requirement, and either built as a type nobody
    asks for or not the only section built as its type."""
    built = Counter(s.structure or "open" for s in sections)
    return [s for s in sections if s.id not in pinned and (
        (s.structure or "open") not in asked or built[s.structure or "open"] > 1)]


def _fill_structure(spec: LevelSpec, req: Requirement, sections: List) -> bool:  # type: ignore[type-arg]
    """Build a section as the requirement's structure (v3 Part 21).

    The named section; else, among the sections that can change (see
    :func:`_retypable`), the first whose name or description mentions the
    structure, else the one whose position matches the structure's place among
    the asked-for structures (the 2nd of 4 asked → the section a third of the
    way along).  Never adds a section."""
    check = req.check
    want = check.structure if check else None
    if not want:
        return False
    pool = [s for s in sections if not check.section or s.id == check.section]
    if any((s.structure or "open") == want for s in pool):
        return False
    target = pool[0] if check.section and pool else None
    if target is None and not check.section:
        asked = _asked_structures(spec)
        pinned = {r.check.section for r in spec.requirements
                  if r.check is not None and r.check.type == "structure" and r.check.section
                  and r.check.structure != want}
        free = _retypable(sections, asked, pinned)
        words = STRUCTURE_WORDS.get(want, (want,))
        target = next((s for s in sorted(free, key=lambda s: s.order) if any(
            w in f"{s.id} {s.name} {s.description}".lower() for w in words)), None)
        if target is None and free:
            ordered = sorted(sections, key=lambda s: s.order)
            rank = {s.id: i for i, s in enumerate(ordered)}
            i = asked.index(want) if want in asked else len(asked) - 1
            goal = i * (len(ordered) - 1) / max(len(asked) - 1, 1)
            target = min(free, key=lambda s: (abs(rank[s.id] - goal), rank[s.id]))
    if target is None:
        return False
    i = sections.index(target)
    sections[i] = target.model_copy(update={"structure": want})
    return True


def fill_missing(spec: LevelSpec) -> FillResult:
    """Deterministically fill the requirements the spec does not meet (see module doc)."""
    sections = list(spec.sections)
    entities = list(spec.entities)
    nodes = list(spec.nodes)
    taken = {e.id for e in entities}
    node_ids = {n.id for n in nodes}
    size = spec.size
    result = FillResult(spec=spec)

    for req in spec.requirements:
        check = effective_check(req)
        if check is None:
            continue
        if check.type == "hazard":
            added = _fill_hazards(spec, req, nodes, node_ids)
            if added:
                result.filled.append(req.id)
                result.nodes_added += added
                result.notes.append(
                    f"completeness: added {added} Hazard node(s) for {req.id} ({req.text!r})"
                )
        elif check.type == "entity_count":
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
        elif check.type == "structure":
            if _fill_structure(spec, req, sections):
                result.filled.append(req.id)
                result.notes.append(
                    f"completeness: built a section as {check.structure} ({req.id})"
                )

    if result.filled:
        result.spec = spec.model_copy(update={"entities": entities, "nodes": nodes, "size": size,
                                              "sections": sections})
    return result
