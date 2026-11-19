"""Requirements the prompt states outright, added when the LLM left them out.

The LLM writes ``spec.requirements`` itself, and it can miss what the prompt
says in plain words: "a central hub room" with no ``structure`` requirement,
"a locked door ... whose key lies in a cave" with no lock in the mission.
``from_prompt(spec, prompt)`` reads the prompt deterministically and adds:

* one ``structure`` requirement (id ``P_<structure>``) per structure type the
  prompt names (:func:`prompt_structures`) that no requirement of the spec
  asks for yet.  An LLM ``structure`` requirement for a type the prompt does
  not name, whose own text names a requested type that nothing asks for yet,
  is pointed at that type instead ("a parkour shaft climbing up" asked as
  ``shaft`` becomes ``parkour``);
* a ``lock_key_cycle`` in the mission (and a ``mission`` / ``lock``
  requirement, id ``P_lock``) when the prompt asks for a key and a lock and
  the spec plans none.  The key goes in the first section whose name or
  description mentions the key, the lock in the first that mentions a lock
  (or a door).

The completeness fill (``fidelity/fill.py``) then retypes sections for the
structure requirements; nothing here adds a section.  A prompt that names no
structure and no key leaves the spec unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from spec.level_spec import LevelSpec, MissionLock, MissionSpec, Requirement, Section

# Words that name a structure type outright.  Looser words ("cave", "room",
# "tower", "climb") are left to the LLM: they describe more than one structure.
STRUCTURE_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("corridor", r"corridors?"),
    ("parkour", r"parkour"),
    ("labyrinth", r"labyrinths?|mazes?"),
    ("arena", r"arenas?"),
    ("shaft", r"shafts?"),
    ("hub", r"hubs?"),
    ("open", r"open[- ](?:sky|air)|outdoors?"),
)
_STRUCTURE_RE = re.compile(
    "|".join(rf"(?P<{name}>\b(?:{pat})\b)" for name, pat in STRUCTURE_PATTERNS), re.IGNORECASE
)
_KEY_RE = re.compile(r"\bkeys?\b", re.IGNORECASE)
_LOCK_RE = re.compile(r"\b(?:locked|locks?|unlocks?|doors?|gates?)\b", re.IGNORECASE)
_LOCK_WORD_RE = re.compile(r"\b(?:locked|locks?)\b", re.IGNORECASE)
_DOOR_RE = re.compile(r"\b(?:doors?|gates?)\b", re.IGNORECASE)
_MULTI_SECTION_RE = re.compile(
    r"\b(?:two|three|four|five|six|2|3|4|5|6|several|multiple)\s+(?:sections?|areas?|parts?|floors?|rooms?|stages?|zones?)\b"
    r"|\b(?:first|second|third|fourth)\s+(?:section|area|part|floor)\b"
    r"|\b(?:then|followed\s+by|finally)\b",
    re.IGNORECASE,
)
_TOWER_RE = re.compile(r"\btowers?\b", re.IGNORECASE)


@dataclass
class PromptResult:
    spec: LevelSpec
    added: List[str] = field(default_factory=list)  # requirement ids added or re-pointed
    notes: List[str] = field(default_factory=list)


def _matches(text: str) -> List[Tuple[int, int, str]]:
    return [(m.start(), m.end(), m.lastgroup or "") for m in _STRUCTURE_RE.finditer(text or "")]


def prompt_structures(prompt: str) -> List[str]:
    """The structure types *prompt* names, in order of first mention.

    Structure words next to each other name one section: the last word is
    the type ("an open-air arena" asks for ``arena``), except that "parkour"
    always wins, being how the section plays ("a parkour shaft" asks for
    ``parkour``).
    """
    text = prompt or ""
    groups: List[List[str]] = []
    prev_end = None
    for start, end, name in _matches(text):
        if prev_end is not None and text[prev_end:start].strip(" -") == "":
            groups[-1].append(name)
        else:
            groups.append([name])
        prev_end = end
    out: List[str] = []
    for group in groups:
        name = "parkour" if "parkour" in group else group[-1]
        if name not in out:
            out.append(name)
    return out


def names_structure(text: str, structure: str) -> bool:
    return any(name == structure for _, _, name in _matches(text))


def prompt_wants_lock(prompt: str) -> bool:
    """True if *prompt* asks for a key and something it opens (a lock, a door, a gate)."""
    return bool(_KEY_RE.search(prompt or "")) and bool(_LOCK_RE.search(prompt or ""))


def _structure_reqs(spec: LevelSpec) -> List[Requirement]:
    return [r for r in spec.requirements
            if r.check is not None and r.check.type == "structure" and r.check.structure]


def _has_lock(spec: LevelSpec) -> bool:
    m = spec.mission
    if m is not None and ("lock_key_cycle" in m.rules or (m.keys or 0) > 0):
        return True
    return any(r.check is not None and r.check.type == "mission" and r.check.subkind == "lock"
               for r in spec.requirements)


def _first_section(sections: List[Section], pattern: "re.Pattern[str]",
                   avoid: Optional[str] = None) -> Optional[str]:
    hits = [s.id for s in sorted(sections, key=lambda s: s.order)
            if pattern.search(f"{s.name} {s.description}")]
    return next((h for h in hits if h != avoid), hits[0] if hits else None)


def _add_structures(spec: LevelSpec, prompt: str, out: PromptResult) -> List[Requirement]:
    reqs = list(spec.requirements)
    wanted = prompt_structures(prompt)
    if not wanted:
        return reqs
    asked = {r.check.structure for r in _structure_reqs(spec)}  # type: ignore[union-attr]
    for i, req in enumerate(reqs):
        chk = req.check
        if chk is None or chk.type != "structure" or not chk.structure or chk.structure in wanted:
            continue
        to = next((w for w in wanted if w not in asked and names_structure(req.text, w)), None)
        if to is None:
            continue
        reqs[i] = req.model_copy(update={"check": chk.model_copy(update={"structure": to})})
        asked.add(to)
        out.added.append(req.id)
        out.notes.append(f"completeness: {req.id} asks for {to} (the prompt says so), not {chk.structure}")
    taken = {r.id for r in reqs}
    for w in wanted:
        if w in asked:
            continue
        rid = f"P_{w}"
        n = 2
        while rid in taken:
            rid, n = f"P_{w}_{n}", n + 1
        taken.add(rid)
        reqs.append(Requirement.model_validate({
            "id": rid, "text": f"The prompt asks for a {w} section.", "category": "layout",
            "check": {"type": "structure", "structure": w},
        }))
        out.added.append(rid)
    return reqs


def _collapse_unrequested_tower_sections(
    spec: LevelSpec, prompt: str, out: PromptResult,
) -> LevelSpec:
    """Merge homogeneous extra sections in a single-tower vertical prompt into one."""
    if (
        len(spec.sections) <= 1
        or spec.layout_orientation != "vertical"
        or not _TOWER_RE.search(prompt or "")
    ):
        return spec
    if (
        len(prompt_structures(prompt)) > 1
        or _MULTI_SECTION_RE.search(prompt or "")
        or prompt_wants_lock(prompt)
    ):
        return spec
    if any(
        r.check is not None and (r.check.section is not None or r.check.type in ("section", "structure"))
        for r in spec.requirements
    ):
        return spec
    if spec.mission is not None and (spec.mission.locks or (spec.mission.keys or 0) > 0):
        return spec
    signatures = {
        ((s.theme or "").strip().lower(), s.structure or "open", round(float(s.gravity_scale), 3))
        for s in spec.sections
    }
    if len(signatures) != 1:
        return spec
    keep = sorted(spec.sections, key=lambda s: s.order)[0].model_copy(update={"order": 0})
    nodes = [n.model_copy(update={"section": keep.id}) for n in spec.nodes]
    entities = [e.model_copy(update={"section": keep.id}) for e in spec.entities]
    out.added.append("P_single_tower")
    out.notes.append("completeness: merged unrequested extra tower sections into one")
    return spec.model_copy(update={"sections": [keep], "nodes": nodes, "entities": entities})


def from_prompt(spec: LevelSpec, prompt: Optional[str]) -> PromptResult:
    """*spec* with the requirements *prompt* states outright (see the module doc)."""
    out = PromptResult(spec=spec)
    if not prompt or not prompt.strip():
        return out
    reqs = _add_structures(spec, prompt, out)
    mission = spec.mission
    if prompt_wants_lock(prompt) and not _has_lock(spec):
        sections = list(spec.sections)
        key_sec = _first_section(sections, _KEY_RE)
        lock_sec = (_first_section(sections, _LOCK_WORD_RE, avoid=key_sec)
                    or _first_section(sections, _DOOR_RE, avoid=key_sec))
        if mission is not None:
            keys = mission.keys + 1 if mission.keys is not None else None
            mission = mission.model_copy(update={
                "rules": ["lock_key_cycle", *mission.rules], "keys": keys,
                "locks": [MissionLock(key_section=key_sec, lock_section=lock_sec),
                          *(mission.locks or [])],
            })
        rid = "P_lock" if "P_lock" not in {r.id for r in reqs} else "P_lock_2"
        reqs.append(Requirement.model_validate({
            "id": rid, "text": "The prompt asks for a key that opens a lock.", "category": "other",
            "check": {"type": "mission", "subkind": "lock", "section": key_sec,
                      "target_section": lock_sec},
        }))
        out.added.append(rid)
        out.notes.append(f"completeness: added a key and lock ({rid}: key in "
                         f"{key_sec or 'any section'}, lock in {lock_sec or 'any section'})")
    updated = spec.model_copy(update={"requirements": reqs, "mission": mission}) if out.added else spec
    collapsed = _collapse_unrequested_tower_sections(updated, prompt, out)
    if out.added:
        out.spec = collapsed
    return out


__all__ = ["PromptResult", "from_prompt", "names_structure", "prompt_structures",
           "prompt_wants_lock"]
