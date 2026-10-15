"""Level complexity target (v4 Part 24): one number from 0 to 1 drives generation.

``c`` (shown to users as 1-10: ``c = (n - 1) / 9``) maps to concrete knobs by
:func:`targets`.  Every knob is linear in ``c`` (rounded where it is a count),
so the table is monotonic by construction:

=====================  ===========  ===========  ===========
knob                   c = 0 (1)    c = 0.5      c = 1 (10)
=====================  ===========  ===========  ===========
comfort_scale          0.50         0.75         1.00
long_move_share        0.05         0.28         0.50
hazard_per_100         2            11           20
enemies_per_100        0.5          3.25         6.0
threat_per_100         1.25         8.1          15.0
checkpoint_spacing     120 tiles    80 tiles     40 tiles
labyrinth_complexity   0.10         0.52         0.95
branches (hub)         0            2            4
pits (corridor/arena)  0            3            6
arena_platforms        1            4            6
score range (+-1)      0.5 - 2.5    3.5 - 5.5    6.5 - 8.5
=====================  ===========  ===========  ===========

*comfort_scale* is the share of the profile's hop, rise and drop limits
(``Design.comfort_*``, Part 20) the comfort pass may use, so low complexity
gives shorter hops.  *arena_platforms* has no generator knob yet: it is applied
as the arena's ``pits``.

The closed loop (:func:`adjust_section`, driven by ``pipeline/level.py``)
measures the built level with ``fidelity/difficulty.py``; a section whose score
misses its range by more than ``TOLERANCE`` is adjusted deterministically (at
most ``MAX_ROUNDS`` rounds: enemies, checkpoints, terrain params re-seeded) and
the level is laid out and verified again.  A section that still misses gets a
warning.  There is never an extra LLM call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from spec.level_spec import LevelSpec, Section, SpecEntity, StructureParams

TOLERANCE = 1.5  # a score further than this from its range is adjusted
MAX_ROUNDS = 2
SCORE_HALF_WIDTH = 1.0
_STEP = 0.25  # how far one adjustment round moves a terrain complexity param


def _lerp(a: float, b: float, c: float) -> float:
    return a + (b - a) * c


def clamp01(c: float) -> float:
    return max(0.0, min(1.0, float(c)))


def from_scale(n: float) -> float:
    """User-facing 1..10 -> 0..1."""
    return clamp01((float(n) - 1.0) / 9.0)


def to_scale(c: float) -> float:
    """0..1 -> user-facing 1..10."""
    return round(1.0 + 9.0 * clamp01(c), 1)


@dataclass(frozen=True)
class ComplexityTargets:
    complexity: float
    comfort_scale: float
    long_move_share: float
    hazard_per_100: float
    enemies_per_100: float
    threat_per_100: float
    checkpoint_spacing: int
    labyrinth_complexity: float
    branches: int
    pits: int
    arena_platforms: int
    score_min: float
    score_max: float

    @property
    def score_mid(self) -> float:
        return (self.score_min + self.score_max) / 2

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    def for_prompt(self) -> str:
        return (
            f"Complexity {to_scale(self.complexity):g}/10: aim for about {self.enemies_per_100:.1f} "
            f"enemies and {self.hazard_per_100:.0f} hazard tiles per 100 tiles of length, "
            f"{round(100 * self.long_move_share)}% long or precise jumps on the route, "
            f"a checkpoint every {self.checkpoint_spacing} tiles; "
            f"expected difficulty score {self.score_min:.1f} to {self.score_max:.1f} of 10."
        )


def targets(c: float) -> ComplexityTargets:
    """The knobs for complexity *c* (clamped to 0..1)."""
    c = clamp01(c)
    mid = _lerp(1.5, 7.5, c)
    return ComplexityTargets(
        complexity=c,
        comfort_scale=round(_lerp(0.5, 1.0, c), 3),
        long_move_share=round(_lerp(0.05, 0.5, c), 3),
        hazard_per_100=round(_lerp(2.0, 20.0, c), 2),
        enemies_per_100=round(_lerp(0.5, 6.0, c), 2),
        threat_per_100=round(_lerp(1.25, 15.0, c), 2),
        checkpoint_spacing=int(round(_lerp(120, 40, c))),
        labyrinth_complexity=round(_lerp(0.1, 0.95, c), 3),
        branches=int(round(_lerp(0, 4, c))),
        pits=int(round(_lerp(0, 6, c))),
        arena_platforms=int(round(_lerp(1, 6, c))),
        score_min=round(mid - SCORE_HALF_WIDTH, 2),
        score_max=round(mid + SCORE_HALF_WIDTH, 2),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Which complexity a section has
# ─────────────────────────────────────────────────────────────────────────────


def effective(spec: LevelSpec, section: Section) -> Optional[float]:
    """Section override, else the level's, else None (no target)."""
    for v in (section.complexity, spec.complexity):
        if v is not None:
            return clamp01(v)
    return None


def active(spec: LevelSpec) -> bool:
    return spec.complexity is not None or any(s.complexity is not None for s in spec.sections)


def level_scale(spec: LevelSpec) -> Optional[float]:
    """Mean comfort scale over the sections that have a target (None = no target at all)."""
    scales = [targets(c).comfort_scale for s in spec.sections
              if (c := effective(spec, s)) is not None]
    return sum(scales) / len(scales) if scales else None


def scaled_profile(profile: Any, scale: Optional[float]) -> Any:
    """A copy of *profile* whose design comfort limits are *scale* of the originals."""
    if scale is None or scale >= 0.999:
        return profile
    d = profile.design
    design = d.model_copy(update={
        "comfort_hop_tiles": max(1, round(d.comfort_hop_tiles * scale)),
        "comfort_rise_tiles": max(1, round(d.comfort_rise_tiles * scale)),
        "comfort_drop_tiles": max(1, round(d.comfort_drop_tiles * scale)),
    })
    return profile.model_copy(update={"design": design})


# ─────────────────────────────────────────────────────────────────────────────
# Terrain params from the targets
# ─────────────────────────────────────────────────────────────────────────────


def _params_for(section: Section, t: ComplexityTargets) -> Dict[str, Any]:
    s = section.structure
    out: Dict[str, Any] = {}
    if s in ("labyrinth", "corridor", "parkour", "hub"):
        out["complexity"] = t.labyrinth_complexity
    if s == "hub":
        out["branches"] = t.branches
    if s in ("corridor", "parkour"):
        out["pits"] = t.pits
    if s == "arena":
        out["pits"] = min(8, t.arena_platforms)
    return out


def apply_targets(spec: LevelSpec) -> LevelSpec:
    """Fill the *unset* structure params of terrain sections from the targets
    (the LLM's own params win).  Returns *spec* itself when nothing changes."""
    changed = False
    sections: List[Section] = []
    for sec in spec.sections:
        c = effective(spec, sec)
        if c is None or not sec.is_terrain:
            sections.append(sec)
            continue
        want = _params_for(sec, targets(c))
        have = sec.params.model_dump() if sec.params else {}
        fill = {k: v for k, v in want.items() if have.get(k) is None}
        if not fill:
            sections.append(sec)
            continue
        sections.append(sec.model_copy(update={"params": StructureParams(**{**have, **fill})}))
        changed = True
    return spec.model_copy(update={"sections": sections}) if changed else spec


# ─────────────────────────────────────────────────────────────────────────────
# Closed loop
# ─────────────────────────────────────────────────────────────────────────────


def miss(score: float, t: ComplexityTargets) -> float:
    """Signed distance from the score range: positive = too hard, negative = too easy, 0 inside."""
    if score > t.score_max:
        return score - t.score_max
    if score < t.score_min:
        return score - t.score_min
    return 0.0


def _protected(spec: LevelSpec, section_id: str) -> Dict[str, int]:
    """Entity kinds a requirement needs in the section, with the count."""
    need: Dict[str, int] = {}
    for r in spec.requirements:
        ck = r.check
        if ck is None or ck.type != "entity_count" or not ck.entity_kind:
            continue
        if ck.section in (None, section_id):
            need[ck.entity_kind] = max(need.get(ck.entity_kind, 0), ck.min_count or 1)
    return need


def _anchor_for(spec: LevelSpec, section_id: str) -> Optional[str]:
    for n in spec.nodes:
        if n.section == section_id and n.type == "Platform":
            return n.id
    return None


def _new_id(spec: LevelSpec, stem: str) -> str:
    ids = {e.id for e in spec.entities}
    k = 1
    while f"{stem}{k}" in ids:
        k += 1
    return f"{stem}{k}"


def adjust_section(spec: LevelSpec, section_id: str, direction: int) -> Tuple[LevelSpec, List[str]]:
    """One deterministic adjustment round for a section: *direction* +1 makes it harder,
    -1 easier.  Returns the new spec and what changed (empty = nothing to do)."""
    sec = next((s for s in spec.sections if s.id == section_id), None)
    if sec is None:
        return spec, []
    notes: List[str] = []
    entities = list(spec.entities)
    in_sec = [e for e in entities if e.section == section_id]
    enemies = [e for e in in_sec if e.kind == "Enemy"]
    checkpoints = [e for e in in_sec if e.kind == "Checkpoint"]
    need = _protected(spec, section_id)

    if direction < 0:
        if len(enemies) > need.get("Enemy", 0):
            gone = enemies[-1]
            entities.remove(gone)
            notes.append(f"complexity: removed enemy {gone.id} from {section_id}")
        elif not checkpoints:
            anchor = _anchor_for(spec, section_id)
            if anchor:
                cid = _new_id(spec, "cp_c")
                entities.append(SpecEntity(id=cid, kind="Checkpoint", anchor=anchor, section=section_id,
                                           description="checkpoint added for the complexity target"))
                notes.append(f"complexity: added checkpoint {cid} in {section_id}")
    else:
        if len(checkpoints) > need.get("Checkpoint", 0):
            gone = checkpoints[-1]
            entities.remove(gone)
            notes.append(f"complexity: removed checkpoint {gone.id} from {section_id}")
        else:
            proto = enemies[0] if enemies else None
            anchor = proto.anchor if proto else _anchor_for(spec, section_id)
            if anchor:
                eid = _new_id(spec, "enemy_c")
                fields: Dict[str, Any] = dict(
                    id=eid, kind="Enemy", anchor=anchor, section=section_id,
                    description="enemy added for the complexity target",
                    sprite=proto.sprite if proto else "",
                    archetype=(proto.archetype if proto else None) or "walker",
                    health=(proto.health if proto else None) or 3,
                    damage=(proto.damage if proto else None) or 1,
                    patrol_range=proto.patrol_range if proto else 3,
                    attack=(proto.attack if proto else None) or "melee",
                )
                if sec.is_terrain:
                    fields["place"] = "floor"
                    fields["position"] = round(((len(enemies) * 0.37 + 0.2) % 0.8) + 0.1, 2)
                entities.append(SpecEntity(**fields))
                notes.append(f"complexity: added enemy {eid} to {section_id}")

    new_sec = sec
    if sec.is_terrain:
        have = sec.params.model_dump() if sec.params else {}
        base = _params_for(sec, targets(effective(spec, sec) or 0.5))
        moved = dict(have)
        for key, val in base.items():
            cur = have.get(key) if have.get(key) is not None else val
            if key == "complexity":
                moved[key] = clamp01(cur + direction * _STEP)
            elif key == "branches":
                moved[key] = max(0, min(4, int(cur) + direction))
            elif key == "pits":
                moved[key] = max(0, min(8, int(cur) + 2 * direction))
        if moved != have:
            new_sec = sec.model_copy(update={"params": StructureParams(**moved)})
            notes.append(f"complexity: {'tightened' if direction > 0 else 'widened'} "
                         f"{section_id} terrain params")
    if not notes:
        return spec, []
    sections = [new_sec if s.id == section_id else s for s in spec.sections]
    return spec.model_copy(update={"entities": entities, "sections": sections}), notes
