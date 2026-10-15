"""Deterministic requirement checker.

``check_spec`` runs on the LevelSpec before layout (and drives the
completeness fill); ``check_level`` runs on the final level and counts the
entities actually placed in ``ir.entities`` — placement can move or drop
entities, so the two can disagree.

Both reduce their input to a :class:`LevelView` (sections, size and a flat
dict per entity: ``kind`` plus the entity's fields) and run the same check per
requirement (see ``RequirementCheck`` in ``spec/level_spec.py`` for the
check types and :mod:`fidelity.report` for the statuses and the score).

Approximation is detected from the requirement alone: an ``entity_count``
whose ``subkind`` is not a valid value for its kind ("ghost" for an Enemy)
can only have been met by the nearest kind, so it is matched against the
entities' description / sprite text and reported ``approximated``.

Hazards are tiles (Hazard nodes), never entities.  ``hazard`` checks count
Hazard nodes, and an ``entity_count`` that names a hazard concept (its
subkind is "hazard", "spikes", "pit", "lava", …, or its text is and the
subkind is not a valid one for the kind) is checked as a ``hazard`` instead
(:func:`effective_check`), so the fill never adds an Enemy for "a spike pit".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple, get_args

from game.profile import GameProfile
from layout.ir import IRLevel
from spec.level_spec import (
    EnemyArchetype,
    InteractiveKind,
    LevelSpec,
    PickupKind,
    Requirement,
    RequirementCheck,
    Section,
)

from .report import FidelityItem, FidelityReport, FidelityStatus

MAX_WIDTH_TILES = 300  # LevelSize limits
MAX_HEIGHT_TILES = 100
GRAVITY_RANGE = (0.25, 2.0)  # Section.gravity_scale limits
GRAVITY_TOLERANCE = 0.05
SUPPORTED_ABILITIES = ("double_jump", "dash", "wall_jump")

# The field that holds an entity's subkind, and its valid values (None = free text).
SUBKIND_FIELDS: Dict[str, str] = {
    "Enemy": "archetype",
    "Pickup": "pickup_kind",
    "Interactive": "interactive_kind",
    "Weapon": "weapon_kind",
}
SUBKIND_VALUES: Dict[str, Optional[Tuple[str, ...]]] = {
    "Enemy": get_args(EnemyArchetype),
    "Pickup": get_args(PickupKind),
    "Interactive": get_args(InteractiveKind),
    "Weapon": None,
}

HAZARD_WORDS = re.compile(
    r"\b(hazards?|spikes?|spiky|pits?|lava|acid|magma|thorns?|brambles?|quicksand|traps?)\b",
    re.IGNORECASE,
)
"""Words that name a hazard concept (hazards are tiles, not entities)."""


# ─────────────────────────────────────────────────────────────────────────────
# Level view (what the checks look at)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class LevelView:
    sections: Dict[str, Section]
    width_tiles: int
    height_tiles: int
    entities: List[Dict[str, Any]] = field(default_factory=list)  # {"kind", "section", ...}
    where: str = "spec"  # "spec" | "level", used in reasons
    hazards: List[Dict[str, Any]] = field(default_factory=list)  # Hazard nodes: {"id", "section"}
    terrain: Optional[Dict[str, Dict[str, Any]]] = None  # level only: per-section generator stats
    scores: Optional[Dict[str, float]] = None  # level only: measured difficulty per section id + "*" overall
    mission: Optional[Dict[str, Any]] = None  # v4 Part 23: spec = planned graph, level = as built


def _hazard_nodes(spec: LevelSpec) -> List[Dict[str, Any]]:
    return [{"id": n.id, "section": n.section} for n in spec.nodes if n.type == "Hazard"]


def spec_view(spec: LevelSpec) -> LevelView:
    return LevelView(
        sections={s.id: s for s in spec.sections},
        width_tiles=spec.size.width_tiles,
        height_tiles=spec.size.height_tiles,
        entities=[e.model_dump() for e in spec.entities],
        where="spec",
        hazards=_hazard_nodes(spec),
        mission=_planned_mission(spec),
    )


def _planned_mission(spec: LevelSpec) -> Optional[Dict[str, Any]]:
    """The mission graph the grammar builds for *spec* (no layout), or None."""
    from mission import mission_of, plan_graph

    mission = mission_of(spec)
    if mission is None:
        return None
    graph, _warnings = plan_graph(mission, ("double_jump", "dash", "wall_jump"))
    return {**graph.to_dict(), "rules": list(graph.applied)}


def level_view(spec: LevelSpec, ir: IRLevel, mission: Optional[Dict[str, Any]] = None) -> LevelView:
    """Entities as placed in *ir*; hazards are the final spec's Hazard nodes
    (the layout keeps every node, it only moves them)."""
    return LevelView(
        sections={s.id: s for s in spec.sections},
        width_tiles=ir.width_tiles,
        height_tiles=ir.height_tiles,
        entities=[{**e.metadata, "kind": e.entity_type} for e in ir.entities],
        where="level",
        hazards=_hazard_nodes(spec),
        terrain=dict(ir.metadata.get("terrain") or {}),
        mission=mission,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entity matching (shared with the completeness fill)
# ─────────────────────────────────────────────────────────────────────────────


def resolve_subkind(kind: Optional[str], subkind: Optional[str]) -> Tuple[Optional[str], bool]:
    """``(normalised subkind, valid)`` for *kind*.

    A plural ("coins") resolves to its singular when that is valid.  Kinds
    without a subkind field ignore it: ``(None, True)``.
    """
    if not subkind or not subkind.strip() or kind not in SUBKIND_FIELDS:
        return None, True
    value = subkind.strip().lower()
    allowed = SUBKIND_VALUES[kind]
    if allowed is None:
        return value, True
    for candidate in (value, value[:-1] if value.endswith("s") else value):
        if candidate in allowed:
            return candidate, True
    return value, False


def _mentions(ent: Dict[str, Any], word: str) -> bool:
    text = f"{ent.get('description') or ''} {ent.get('sprite') or ''}".lower()
    return word in text


def matching_entities(
    entities: Iterable[Dict[str, Any]], check: RequirementCheck
) -> List[Dict[str, Any]]:
    """Entities that count towards an ``entity_count`` check."""
    kind = check.entity_kind
    subkind, valid = resolve_subkind(kind, check.subkind)
    out = []
    for ent in entities:
        if ent.get("kind") != kind:
            continue
        if check.section and ent.get("section") != check.section:
            continue
        if subkind is not None:
            if valid:
                own = str(ent.get(SUBKIND_FIELDS[kind]) or "").lower()
                if own != subkind and not (kind == "Weapon" and _mentions(ent, subkind)):
                    continue
            elif not _mentions(ent, subkind):
                continue
        out.append(ent)
    return out


def names_hazard(text: Optional[str]) -> bool:
    """True if *text* names a hazard concept (spikes, a pit, lava, ...)."""
    return bool(text) and HAZARD_WORDS.search(str(text)) is not None


def effective_check(req: Requirement) -> Optional[RequirementCheck]:
    """The check to run for *req*: an ``entity_count`` naming a hazard becomes ``hazard``.

    It names a hazard when its subkind does ("hazard", "spikes"), or when the
    requirement text does ("a spike pit in the cave") and the subkind is
    empty or not a valid value for the kind.
    """
    check = req.check
    if check is None or check.type != "entity_count":
        return check
    hazard = names_hazard(check.subkind)
    if not hazard and names_hazard(req.text):
        sub, valid = resolve_subkind(check.entity_kind, check.subkind)
        hazard = sub is None or not valid
    if not hazard:
        return check
    return check.model_copy(update={"type": "hazard", "entity_kind": None, "subkind": None})


def hazard_count(view: LevelView, check: RequirementCheck) -> int:
    """Hazard nodes that count towards a ``hazard`` check."""
    return sum(1 for h in view.hazards if not check.section or h["section"] == check.section)


def hazard_deficit(view: LevelView, check: RequirementCheck) -> int:
    """How many more Hazard nodes a ``hazard`` check needs (0 when met)."""
    need = 1 if check.min_count is None else check.min_count
    return max(0, need - hazard_count(view, check))


def entity_deficit(view: LevelView, check: RequirementCheck) -> int:
    """How many more entities the check needs (0 when met)."""
    need = 1 if check.min_count is None else check.min_count
    return max(0, need - len(matching_entities(view.entities, check)))


def start_items(view: LevelView) -> Optional[List[str]]:
    """The Start entity's weapon and items (lower case), or None without a Start."""
    start = next((e for e in view.entities if e.get("kind") == "Start"), None)
    if start is None:
        return None
    items = [str(i) for i in (start.get("starting_items") or [])]
    if start.get("starting_weapon"):
        items.append(str(start["starting_weapon"]))
    return [i.strip().lower() for i in items if i and i.strip()]


def has_start_item(view: LevelView, item: str) -> bool:
    item = item.strip().lower()
    return any(item in have or have in item for have in (start_items(view) or []))


# ─────────────────────────────────────────────────────────────────────────────
# Checks
# ─────────────────────────────────────────────────────────────────────────────

Result = Tuple[FidelityStatus, str]


def _describe(check: RequirementCheck) -> str:
    sub = f" {check.subkind}" if check.subkind else ""
    where = f" in section '{check.section}'" if check.section else ""
    return f"{check.entity_kind}{sub}{where}"


def _check_entity_count(check: RequirementCheck, view: LevelView) -> Result:
    if check.entity_kind is None:
        return "unchecked", "entity_count check without an entity_kind"
    if check.section and check.section not in view.sections:
        return "missing", f"section '{check.section}' does not exist"
    need = 1 if check.min_count is None else check.min_count
    have = len(matching_entities(view.entities, check))
    what = _describe(check)
    subkind, valid = resolve_subkind(check.entity_kind, check.subkind)
    if have >= need:
        if not valid:
            field_name = SUBKIND_FIELDS[check.entity_kind]
            return "approximated", (
                f"'{subkind}' is not a valid {check.entity_kind} {field_name}; mapped to the "
                f"nearest kind ({have} {check.entity_kind} entit{'y' if have == 1 else 'ies'} "
                f"described as '{subkind}')"
            )
        return "satisfied", f"{have} of {need} {what}"
    if have > 0:
        return "approximated", f"only {have} of {need} {what} in the {view.where}"
    return "missing", f"no {what} in the {view.where} (needs {need})"


def _check_hazard(check: RequirementCheck, view: LevelView, converted: bool) -> Result:
    if check.section and check.section not in view.sections:
        return "missing", f"section '{check.section}' does not exist"
    need = 1 if check.min_count is None else check.min_count
    have = hazard_count(view, check)
    where = f" in section '{check.section}'" if check.section else ""
    note = " (checked as a hazard: hazards are tiles, not entities)" if converted else ""
    if have >= need:
        return "satisfied", f"{have} of {need} hazard(s){where}{note}"
    if have > 0:
        return "approximated", f"only {have} of {need} hazard(s){where} in the {view.where}{note}"
    return "missing", f"no hazard{where} in the {view.where} (needs {need}){note}"


def _check_start_item(check: RequirementCheck, view: LevelView) -> Result:
    if not check.subkind:
        return "unchecked", "start_item check without an item (subkind)"
    items = start_items(view)
    if items is None:
        return "missing", "the level has no Start entity"
    if has_start_item(view, check.subkind):
        return "satisfied", f"the player starts with '{check.subkind}'"
    return "missing", (
        f"the Start entity does not carry '{check.subkind}' (has {items or 'nothing'})"
    )


def _check_section(check: RequirementCheck, view: LevelView) -> Result:
    if not check.section:
        return "unchecked", "section check without a section id"
    if check.section in view.sections:
        return "satisfied", f"section '{check.section}' ({view.sections[check.section].name}) exists"
    return "missing", (
        f"section '{check.section}' does not exist (sections: {sorted(view.sections)})"
    )


def _check_structure(check: RequirementCheck, view: LevelView) -> Result:
    """A section built as ``check.structure`` exists (v3 Part 21); a labyrinth also
    needs ``min_count`` (null = 1) dead ends in the final level."""
    want = check.structure
    if not want:
        return "unchecked", "structure check without a structure"
    pool = [view.sections[check.section]] if check.section in view.sections else (
        [] if check.section else list(view.sections.values()))
    if check.section and not pool:
        return "missing", f"section '{check.section}' does not exist"
    hits = [s for s in pool if (s.structure or "open") == want]
    if not hits:
        found = ", ".join(f"{s.id}={s.structure or 'open'}" for s in pool)
        return "missing", f"no section is built as {want} ({found})"
    if want == "labyrinth" and view.terrain is not None:
        need = 1 if check.min_count is None else check.min_count
        best = max(hits, key=lambda s: int((view.terrain or {}).get(s.id, {}).get("dead_ends", 0)))
        dead = int((view.terrain or {}).get(best.id, {}).get("dead_ends", 0))
        if dead < need:
            return "approximated", (
                f"section '{best.id}' is a labyrinth with {dead} dead end(s), {need} asked"
            )
        return "satisfied", f"section '{best.id}' is a labyrinth with {dead} dead end(s)"
    return "satisfied", f"section '{hits[0].id}' ({hits[0].name}) is built as {want}"


def _check_ability(check: RequirementCheck, profile: GameProfile) -> Result:
    if not check.ability or not check.ability.strip():
        return "unchecked", "ability check without an ability"
    name = check.ability.strip().lower().replace("-", "_").replace(" ", "_")
    if name not in SUPPORTED_ABILITIES:
        return "impossible", (
            f"'{check.ability}' is not an ability GLDtk supports "
            f"({', '.join(SUPPORTED_ABILITIES)}); the Game Profile '{profile.name}' cannot provide it"
        )
    if name in profile.abilities.enabled_names():
        return "satisfied", f"the Game Profile '{profile.name}' has {name}"
    return "impossible", (
        f"the Game Profile '{profile.name}' does not have {name} "
        "(abilities belong to the game: enable it in gldtk.game.json)"
    )


def _check_size(check: RequirementCheck, view: LevelView) -> Result:
    w, h = check.min_width_tiles, check.min_height_tiles
    if w is None and h is None:
        return "unchecked", "size check without a width or height"
    if (w or 0) > MAX_WIDTH_TILES or (h or 0) > MAX_HEIGHT_TILES:
        return "impossible", (
            f"asks for at least {w or '-'}×{h or '-'} tiles; the limit is "
            f"{MAX_WIDTH_TILES}×{MAX_HEIGHT_TILES}"
        )
    size = f"{view.width_tiles}×{view.height_tiles} tiles"
    if view.width_tiles >= (w or 0) and view.height_tiles >= (h or 0):
        return "satisfied", f"the level is {size}"
    return "missing", f"the level is {size}, needs at least {w or '-'}×{h or '-'}"


def _check_gravity(check: RequirementCheck, view: LevelView) -> Result:
    g = check.gravity_scale
    if g is None:
        return "unchecked", "gravity check without a gravity_scale"
    lo, hi = GRAVITY_RANGE
    if not lo <= g <= hi:
        return "impossible", f"gravity scale {g:g} is outside the limits {lo:g}..{hi:g}"
    if check.section:
        sec = view.sections.get(check.section)
        if sec is None:
            return "missing", f"section '{check.section}' does not exist"
        targets = [sec]
    else:
        targets = list(view.sections.values())
    hit = next((s for s in targets if abs(s.gravity_scale - g) <= GRAVITY_TOLERANCE), None)
    if hit is not None:
        return "satisfied", f"section '{hit.id}' has gravity scale {hit.gravity_scale:g}"
    found = ", ".join(f"{s.id}={s.gravity_scale:g}" for s in targets)
    return "missing", f"no section has gravity scale {g:g} ({found})"


COMPLEXITY_TOLERANCE = 1.5


def _check_complexity(check: RequirementCheck, view: LevelView) -> Result:
    """The measured difficulty is within ``COMPLEXITY_TOLERANCE`` of the stated N/10 (v4 Part 24)."""
    want = check.target_difficulty
    if want is None:
        return "unchecked", "complexity check without a target_difficulty"
    if view.scores is None:
        return "unchecked", "difficulty is measured on the built level"
    key = check.section or "*"
    if key not in view.scores:
        return "unchecked", f"no measured difficulty for '{key}'"
    got = view.scores[key]
    where = f"section '{check.section}'" if check.section else "the level"
    if abs(got - want) <= COMPLEXITY_TOLERANCE:
        return "satisfied", f"{where} scores {got:.1f}/10 for difficulty (asked {want:g}/10)"
    return "approximated", f"{where} scores {got:.1f}/10 for difficulty, the prompt asked {want:g}/10"


MISSION_KIND = {"lock": "lock", "loop": "shortcut", "gate": "gate", "treasure": "treasure",
                "boss": "boss"}


def _check_mission(check: RequirementCheck, view: LevelView) -> Result:
    """The mission structure ``subkind`` is realised (v4 Part 23); on the spec,
    planned by the grammar."""
    want = check.subkind
    if want not in MISSION_KIND:
        return "unchecked", f"mission check without a known feature ({', '.join(MISSION_KIND)})"
    if view.mission is None:
        if view.where == "spec":
            return "unchecked", "no mission planned yet"
        return "missing", "the level was built without a mission graph"
    if view.where == "spec":
        kinds = {n["kind"] for n in view.mission.get("nodes", [])}
        if MISSION_KIND[want] in kinds:
            return "satisfied", f"the mission graph plans a {want}"
        return "approximated", f"the mission graph has no {want} (rules: {view.mission.get('rules')})"
    feats = [f for f in view.mission.get("features", []) if f.get("kind") == want]
    if not feats:
        return "missing", f"the built mission has no {want}"

    def matches(f: Dict[str, Any]) -> bool:
        if want == "lock":
            return ((not check.section or f.get("key_section") == check.section)
                    and (not check.target_section or f.get("lock_section") == check.target_section))
        if want == "loop":
            return not check.section or f.get("section") == check.section
        return not check.section or f.get("section") == check.section

    good = [f for f in feats if f.get("ok") and matches(f)]
    if good:
        return "satisfied", str(good[0].get("reason"))
    near = [f for f in feats if f.get("ok")]
    if near:
        f = near[0]
        where = (f"key in '{f.get('key_section')}', lock in '{f.get('lock_section')}'"
                 if want == "lock" else f"in '{f.get('section')}'")
        return "approximated", f"a {want} is realised, but {where}"
    return "missing", str(feats[0].get("reason"))


def check_requirement(req: Requirement, view: LevelView, profile: GameProfile) -> Result:
    check = effective_check(req)
    if check is None or check.type == "none":
        return "unchecked", f"no structured check (category '{req.category}')"
    if check.type == "entity_count":
        return _check_entity_count(check, view)
    if check.type == "hazard":
        converted = req.check is not None and req.check.type != "hazard"
        return _check_hazard(check, view, converted)
    if check.type == "start_item":
        return _check_start_item(check, view)
    if check.type == "section":
        return _check_section(check, view)
    if check.type == "ability":
        return _check_ability(check, profile)
    if check.type == "size":
        return _check_size(check, view)
    if check.type == "gravity":
        return _check_gravity(check, view)
    if check.type == "structure":
        return _check_structure(check, view)
    if check.type == "complexity":
        return _check_complexity(check, view)
    if check.type == "mission":
        return _check_mission(check, view)
    return "unchecked", f"unknown check type '{check.type}'"


def _report(
    spec: LevelSpec,
    view: LevelView,
    profile: GameProfile,
    auto_filled: Iterable[str],
    verified: Optional[bool] = None,
) -> FidelityReport:
    filled = set(auto_filled)
    items = []
    for req in spec.requirements:
        status, reason = check_requirement(req, view, profile)
        items.append(
            FidelityItem(
                id=req.id,
                text=req.text,
                category=req.category,
                status=status,
                reason=reason,
                auto_filled=req.id in filled,
            )
        )
    return FidelityReport(items=items, verified=verified)


def check_spec(
    spec: LevelSpec,
    profile: Optional[GameProfile] = None,
    auto_filled: Iterable[str] = (),
) -> FidelityReport:
    """Check every requirement against the spec (before layout).

    *auto_filled* names the requirements the completeness fill added
    entities for; their items are flagged ``auto_filled``.
    """
    return _report(spec, spec_view(spec), profile or GameProfile(), auto_filled)


def check_level(
    spec: LevelSpec,
    ir: IRLevel,
    profile: Optional[GameProfile] = None,
    verified: bool = False,
    auto_filled: Iterable[str] = (),
    scores: Optional[Dict[str, float]] = None,
    mission: Optional[Dict[str, Any]] = None,
) -> FidelityReport:
    """Check every requirement against the final level.

    *scores* are the measured difficulty per section id (``"*"`` = whole level)
    for the ``complexity`` check (``fidelity.level_scores`` computes them).

    Entities are counted from ``ir.entities`` (what was actually placed);
    sections come from the spec, the size from the IR grid.  *verified* is
    recorded on the report (the grid was proven beatable).
    """
    view = level_view(spec, ir, mission)
    view.scores = scores
    return _report(spec, view, profile or GameProfile(), auto_filled, verified=verified)
