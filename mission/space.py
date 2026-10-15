"""Space graph (v4 Part 23): put a mission graph onto the level's sections.

Sections are taken in their layout order (``Section.order``).  The main line
runs through them left to right (bottom to top):

* each **lock** / **gate** gets its own section and sits at that section's
  entry, so everything after it is behind it (a lock is an ``Interactive``
  door ``linked_to`` its key; when there are more locks than sections, the
  last lock is the Exit itself, ``requires_key``);
* each **key** / **ability pickup** goes into a section between its branch's
  origin and its lock, preferring a hub (``room``) and then a labyrinth
  (``dead_end``); a **treasure** prefers a labyrinth dead end;
* the **boss** goes to the last arena (``arena_center``) after the last lock,
  else into the Exit's section;
* a **shortcut** is a one-way door after its lock, ``linked_to`` a door in the
  hub its key was found in, so the loop goes back through the hub.

The entities get ``place`` anchors (Part 21's placement resolves them) and the
links ``opens`` / ``linked_to`` / ``requires_key``.  *attempt* re-seeds the
choices (attempt 0 is the preferred mapping).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from spec.level_spec import LevelSpec, Section, SpecEntity

from .graph import BARRIER_KINDS, MissionGraph

# preference of a section's structure for each mission item (higher = better)
PREFER: Dict[str, Dict[str, int]] = {
    "key": {"hub": 4, "labyrinth": 3, "corridor": 2, "shaft": 1, "parkour": 1},
    "ability": {"hub": 3, "labyrinth": 3, "corridor": 2, "shaft": 1, "parkour": 1},
    "treasure": {"labyrinth": 4, "hub": 3, "corridor": 2},
}
PLACE_FOR = {"hub": "room", "labyrinth": "dead_end", "corridor": "room", "arena": "room",
             "shaft": "ledge", "parkour": "ledge"}


@dataclass
class SpacePlan:
    spec: LevelSpec
    graph: MissionGraph
    attempt: int
    entities: Dict[str, str] = field(default_factory=dict)  # mission node → entity id
    sections: Dict[str, str] = field(default_factory=dict)  # mission node → section id
    hub_doors: Dict[str, str] = field(default_factory=dict)  # shortcut node → hub door id
    locked_exit: Optional[str] = None  # the lock node realised as the locked Exit
    warnings: List[str] = field(default_factory=list)


def ordered_sections(spec: LevelSpec) -> List[Section]:
    return sorted(spec.sections, key=lambda s: (s.order, s.id))


def map_space(spec: LevelSpec, graph: MissionGraph, attempt: int = 0, seed: int = 0
              ) -> Optional[SpacePlan]:
    """The spec with the mission's entities added, or None when it does not fit."""
    g = graph.copy()
    secs = ordered_sections(spec)
    idx = {s.id: i for i, s in enumerate(secs)}
    rng = random.Random(f"{seed}:space:{attempt}")
    plan = SpacePlan(spec=spec, graph=g, attempt=attempt)

    start_node = next((n for n in spec.nodes if n.type == "Start"), None)
    exit_node = next((n for n in spec.nodes if n.type == "Exit"), None)
    start_sec = idx.get(start_node.section, 0) if start_node else 0
    exit_sec = idx.get(exit_node.section, len(secs) - 1) if exit_node else len(secs) - 1
    if exit_sec < start_sec:
        return None

    line = g.main_line()
    barriers = [n for n in line if g.nodes[n].kind in BARRIER_KINDS]
    avail = list(range(start_sec + 1, exit_sec + 1))
    if len(barriers) > len(avail):
        last = g.nodes[barriers[-1]]
        if len(barriers) == len(avail) + 1 and last.kind == "lock":
            plan.locked_exit = last.id
            barriers = barriers[:-1]
        else:
            return None
    sec_of: Dict[str, int] = {}
    picks = _barrier_sections(g, barriers, avail, idx, attempt, rng, plan.warnings)
    if picks is None:
        return None
    sec_of.update(zip(barriers, picks))
    if plan.locked_exit:
        sec_of[plan.locked_exit] = exit_sec

    # main-line sections: a node is in the section of the last barrier before it
    cur = start_sec
    for nid in line:
        if nid in sec_of:
            cur = sec_of[nid]
        elif nid == "exit":
            sec_of[nid] = exit_sec
        else:
            sec_of[nid] = cur

    # the boss: the last arena after the last barrier, else the Exit's section
    if "boss" in g.nodes:
        lo = sec_of.get("boss", start_sec)
        arenas = [i for i in range(lo, exit_sec + 1) if secs[i].structure == "arena"]
        sec_of["boss"] = arenas[-1] if arenas else exit_sec

    # side branches
    for node in g.nodes.values():
        if node.kind not in ("key", "ability", "treasure"):
            continue
        origin = g.branch_origin(node.id)
        lo = sec_of.get(origin or "start", start_sec)
        if node.kind == "treasure":
            hi = exit_sec
        else:
            barrier = next((b for b in g.nodes.values()
                            if b.kind in BARRIER_KINDS and b.item == node.item), None)
            hi = sec_of.get(barrier.id, exit_sec) - (0 if barrier and barrier.id == plan.locked_exit
                                                      else 1) if barrier else exit_sec
        if lo > hi:
            return None
        hint = node.params.get("key_section")
        if hint in idx and lo <= idx[hint] <= hi:
            sec_of[node.id] = idx[hint]
            continue
        if hint:
            plan.warnings.append(f"mission: the key in '{hint}' cannot come before its lock; "
                                 "it was put elsewhere")
        sec_of[node.id] = _pick(range(lo, hi + 1), secs, PREFER[node.kind], attempt, rng)

    # shortcut: from its lock's section back to the hub its key was found in
    for node in g.of_kind("shortcut"):
        k = line.index(node.id)
        barrier = g.nodes[line[k - 1]]
        item = g.item_node(barrier)
        hub = sec_of.get(item.id) if item else sec_of.get(node.params.get("hub", "start"))
        node.params["hub_section"] = secs[hub if hub is not None else start_sec].id

    for nid, i in sec_of.items():
        g.nodes[nid].params["section"] = secs[i].id
        plan.sections[nid] = secs[i].id
    plan.spec = _with_entities(spec, g, plan, rng)
    return plan


def _barrier_sections(g: MissionGraph, barriers: List[str], avail: List[int], idx: Dict[str, int],
                      attempt: int, rng: random.Random, warnings: List[str]) -> Optional[List[int]]:
    m = len(barriers)
    if m == 0:
        return []
    picks = avail[-m:] if attempt == 0 else sorted(rng.sample(avail, m))
    hinted = list(picks)
    for k, b in enumerate(barriers):
        want = g.nodes[b].params.get("lock_section")
        if want in idx and idx[want] in avail:
            hinted[k] = idx[want]
    if all(a < b for a, b in zip(hinted, hinted[1:])):
        return hinted
    warnings.append("mission: the lock sections asked for are out of order; they were moved")
    return picks


def _pick(rng_range: Any, secs: List[Section], prefer: Dict[str, int], attempt: int,
          rng: random.Random) -> int:
    cands = list(rng_range)
    score = {i: prefer.get(secs[i].structure or "open", 0) for i in cands}
    if attempt == 0:
        return max(cands, key=lambda i: (score[i], i))
    weights = [1 + score[i] for i in cands]
    return rng.choices(cands, weights=weights)[0]


# ─────────────────────────────────────────────────────────────────────────────
# Entities
# ─────────────────────────────────────────────────────────────────────────────


def _anchor(spec: LevelSpec, sec: Section, rng: random.Random, attempt: int) -> str:
    nodes = [n for n in spec.nodes if n.section == sec.id and n.type != "Hazard"]
    if not nodes:
        nodes = [n for n in spec.nodes if n.type != "Hazard"]
    nodes.sort(key=lambda n: (n.x, n.y, n.id))
    plats = [n for n in nodes if n.type == "Platform"] or nodes
    if attempt == 0:
        return plats[len(plats) // 2].id
    return plats[rng.randrange(len(plats))].id


def _with_entities(spec: LevelSpec, g: MissionGraph, plan: SpacePlan, rng: random.Random) -> LevelSpec:
    secs = {s.id: s for s in spec.sections}
    taken: Set[str] = {e.id for e in spec.entities}
    new: List[SpecEntity] = []
    entities = list(spec.entities)

    def fresh(base: str) -> str:
        eid, k = base, 2
        while eid in taken:
            eid, k = f"{base}_{k}", k + 1
        taken.add(eid)
        return eid

    def make(kind: str, base: str, sid: str, place: Optional[str], desc: str, **fields: Any) -> str:
        sec = secs[sid]
        terrain = sec.is_terrain
        if place is None and terrain:
            place = PLACE_FOR.get(sec.structure or "", "floor")
        extra: Dict[str, Any] = {}
        if terrain:
            extra["place"] = place
            if place == "floor":
                extra["position"] = 0.5
        eid = fresh(base)
        new.append(SpecEntity(id=eid, kind=kind, anchor=_anchor(spec, sec, rng, plan.attempt),
                              section=sid, description=desc, sprite="", **extra, **fields))
        return eid

    keys: Dict[str, str] = {}  # item → key entity id
    for node in g.of_kind("key"):
        sid = node.params["section"]
        keys[node.item or node.id] = make("Key", f"m_{node.id}", sid, None,
                                          f"mission key {node.id}")
        node.params["entity"] = keys[node.item or node.id]
    for node in g.of_kind("lock"):
        key_id = keys.get(node.item or "")
        if node.id == plan.locked_exit:
            ex = next((e for e in entities if e.kind == "Exit"), None)
            if ex is None:
                continue
            entities = [e.model_copy(update={"requires_key": True}) if e.id == ex.id else e
                        for e in entities]
            node.params["entity"] = ex.id
            target = ex.id
        else:
            target = make("Interactive", f"m_{node.id}", node.params["section"], "entry",
                          f"locked door {node.id}", interactive_kind="door", linked_to=key_id)
            node.params["entity"] = target
        new[:] = [e.model_copy(update={"opens": target}) if e.id == key_id else e for e in new]
    for node in g.of_kind("ability"):
        node.params["entity"] = make("Pickup", f"m_{node.id}", node.params["section"], None,
                                     f"{node.item} pickup", pickup_kind="powerup",
                                     grants_ability=node.item)
    for node in g.of_kind("treasure"):
        sid = node.params["section"]
        place = "dead_end" if secs[sid].structure in ("labyrinth", "hub") else None
        node.params["entity"] = make("Interactive", f"m_{node.id}", sid, place, "treasure chest",
                                     interactive_kind="chest", contents="treasure")
    for node in g.of_kind("boss"):
        boss = next((e for e in entities if e.kind == "Enemy" and e.archetype == "boss"), None)
        if boss is not None:
            node.params["entity"] = boss.id
            node.params["section"] = boss.section
            plan.sections[node.id] = boss.section
            continue
        sid = node.params["section"]
        place = "arena_center" if secs[sid].structure == "arena" else None
        node.params["entity"] = make("Enemy", "m_boss", sid, place, "the boss", archetype="boss")
    for node in g.of_kind("shortcut"):
        hub = node.params["hub_section"]
        back = make("Interactive", f"m_{node.id}_hub", hub, None, "shortcut door (hub side)",
                    interactive_kind="door")
        node.params["entity"] = make("Interactive", f"m_{node.id}", node.params["section"], None,
                                     "one-way shortcut door", interactive_kind="door",
                                     linked_to=back)
        plan.hub_doors[node.id] = back
    for nid, node in g.nodes.items():
        if node.params.get("entity"):
            plan.entities[nid] = node.params["entity"]
    return spec.model_copy(update={"entities": entities + new})


__all__ = ["SpacePlan", "map_space", "ordered_sections"]
