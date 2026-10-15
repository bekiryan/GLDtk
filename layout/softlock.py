"""Soft-lock detection (v4 Part 22): from every place the player can reach, an
Exit must still be reachable — including the items the player holds.

Two checks run on the movement graph the verifier already builds
(``symbolic.extractor.extract_graph(..., collision=True)``: falls and drops are
one-way edges, hazard nodes are not places the player can stand):

1. **SCC check.**  The strongly connected components of the span graph are
   condensed into a DAG; a component is *live* when it contains an Exit or
   has a live successor (reverse topological order, linear in the graph).
   A span reachable from the Start (an Exit ends the level, so the search
   does not go on from an Exit span) that is not live is a soft-lock.  The
   soft-locked spans are grouped into *traps* (weakly connected areas), each
   with its entry move: the edge from a live span that leads in.
2. **Item-state check.**  A product graph of ``(span, items held)``: arriving
   on a span picks up its Keys and ability pickups; a locked Exit
   (``requires_key``) is only a goal with its key, a locked door (an
   ``Interactive`` door with ``linked_to`` a Key, or a Key that ``opens`` it)
   only lets the player onto its span with the key, and a move that needs an
   ability that is picked up in the level (an entity with ``grants_ability``,
   or a Pickup with ``ability``) needs it held.  Reachable states from which
   no goal state is reachable are soft-locks too ("dropped past the key").
   A Key that cannot be reached without opening its own door is reported as
   well.  At most ``MAX_ITEMS`` distinct items are tracked (2^6 states per
   span); beyond that the item check is skipped and ``too_many_items`` set.

A Start that cannot reach any Exit at all is *unbeatable*, not a soft-lock:
the verifier's path errors cover it, so no trap is reported then.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

import networkx as nx

from symbolic.schema import AbstractLevelGraph, EdgeType, NodeType

MAX_ITEMS = 6
ABILITIES = ("double_jump", "dash", "wall_jump")

Rect = Tuple[int, int, int]  # (row, col_start, col_end)
State = Tuple[str, FrozenSet[str]]
CellName = Callable[[int, int], str]


def _default_name(col: int, row: int) -> str:
    return f"column {col}, row {row}"


# ─────────────────────────────────────────────────────────────────────────────
# Items
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ItemModel:
    """Keys, locks and ability pickups, placed on spans."""

    pickups: Dict[str, Set[str]] = field(default_factory=dict)  # span → items picked up there
    doors: Dict[str, FrozenSet[str]] = field(default_factory=dict)  # span → keys that open it
    exit_locks: Dict[str, FrozenSet[str]] = field(default_factory=dict)  # exit span → keys
    gated_abilities: Set[str] = field(default_factory=set)  # abilities that must be picked up
    start_items: FrozenSet[str] = frozenset()
    labels: Dict[str, str] = field(default_factory=dict)  # item → "Key 'k1'"
    item_entities: Dict[str, str] = field(default_factory=dict)  # item → entity id

    @property
    def names(self) -> Set[str]:
        out: Set[str] = set()
        for items in self.pickups.values():
            out |= items
        return out - set(self.start_items)

    @property
    def active(self) -> bool:
        return bool(self.doors or self.exit_locks or self.gated_abilities)


def entity_id(ent: Any) -> str:
    m = ent.metadata
    return str(m.get("spec_id") or m.get("iid") or f"{ent.entity_type}@{ent.col},{ent.row}")


def _granted_ability(ent: Any) -> Optional[str]:
    m = ent.metadata
    ab = m.get("grants_ability")
    if not ab and ent.entity_type == "Pickup":
        ab = m.get("ability")
    return str(ab) if ab in ABILITIES else None


def items_from_entities(entities: Sequence[Any], span_of: Callable[[Any], Optional[str]]
                        ) -> ItemModel:
    """The item model of a level's entities; ``span_of(ent)`` is the span it stands on."""
    model = ItemModel()
    keys = [e for e in entities if e.entity_type == "Key"]
    key_ids = {entity_id(k) for k in keys}
    by_id = {entity_id(e): e for e in entities}

    for k in keys:
        name = f"key:{entity_id(k)}"
        model.labels[name] = f"Key '{entity_id(k)}'"
        model.item_entities[name] = entity_id(k)
        sid = span_of(k)
        if sid is not None:
            model.pickups.setdefault(sid, set()).add(name)

    def opened_by(target: str) -> Set[str]:
        return {f"key:{entity_id(k)}" for k in keys if str(k.metadata.get("opens") or "") == target}

    door_keys: Set[str] = set()
    for e in entities:
        if e.entity_type != "Interactive" or e.metadata.get("interactive_kind") != "door":
            continue
        need = opened_by(entity_id(e))
        link = str(e.metadata.get("linked_to") or "")
        if link in key_ids:
            need.add(f"key:{link}")
        sid = span_of(e)
        if need and sid is not None:
            model.doors[sid] = model.doors.get(sid, frozenset()) | frozenset(need)
            door_keys |= need

    for e in entities:
        if e.entity_type != "Exit" or not e.metadata.get("requires_key"):
            continue
        need = opened_by(entity_id(e))
        if not need:  # any Key that does not open a door (or any Key at all)
            free = {f"key:{entity_id(k)}" for k in keys
                    if not k.metadata.get("opens") or k.metadata.get("opens") not in by_id}
            need = (free - door_keys) or free or {f"key:{i}" for i in key_ids}
        sid = span_of(e)
        if sid is not None:
            model.exit_locks[sid] = frozenset(need)

    start = next((e for e in entities if e.entity_type == "Start"), None)
    owned = {str(s) for s in ((start.metadata.get("starting_items") or []) if start else [])}
    for e in entities:
        ab = _granted_ability(e)
        if ab is None:
            continue
        name = f"ability:{ab}"
        model.labels[name] = f"the {ab.replace('_', ' ')} pickup '{entity_id(e)}'"
        model.item_entities[name] = entity_id(e)
        if ab in owned:
            continue
        model.gated_abilities.add(ab)
        sid = span_of(e)
        if sid is not None:
            model.pickups.setdefault(sid, set()).add(name)
    held = {f"ability:{s}" for s in owned if s in ABILITIES}
    held |= {f"key:{s}" for s in owned if s in key_ids}
    model.start_items = frozenset(held)
    return model


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Trap:
    kind: str  # "pit" | "sealed" | "item" | "key_behind_door"
    spans: List[str]
    entry: Optional[Tuple[str, str]] = None  # the move (live span → trap span) that leads in
    missing: List[str] = field(default_factory=list)  # items the player lacks (item traps)
    message: str = ""


@dataclass
class SoftlockReport:
    traps: List[Trap] = field(default_factory=list)
    unbeatable: bool = False  # the Start itself cannot reach an Exit (path errors cover it)
    too_many_items: bool = False
    live: Set[str] = field(default_factory=set)  # spans from which an Exit is reachable
    reach: Set[str] = field(default_factory=set)  # spans reachable from the Start
    stats: Dict[str, Any] = field(default_factory=dict)
    items: Optional[ItemModel] = None  # the model that was checked
    start_span: Optional[str] = None
    exit_spans: Set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not self.traps

    @property
    def span_ids(self) -> List[str]:
        seen: List[str] = []
        for t in self.traps:
            seen += [s for s in t.spans if s not in seen]
        return seen


def _rect(graph: AbstractLevelGraph, sid: str) -> Rect:
    m = graph.get_node(sid).metadata
    return (int(m["row"]), int(m["col_start"]), int(m["col_end"]))


def _landing(graph: AbstractLevelGraph, u: str, v: str) -> Tuple[int, int]:
    """(col, row) where the move u → v lands: v's column nearest u's centre."""
    ra, rb = _rect(graph, u), _rect(graph, v)
    mid = (ra[1] + ra[2]) // 2
    return (min(max(mid, rb[1]), rb[2]), rb[0])


def _is_drop(graph: AbstractLevelGraph, u: str, v: str) -> bool:
    edge = graph.nx_graph.edges[u, v].get("data")
    if edge is not None and edge.edge_type == EdgeType.FALL:
        return True
    return _rect(graph, v)[0] > _rect(graph, u)[0]


def _describe_entry(graph: AbstractLevelGraph, u: str, v: str, name: CellName) -> Tuple[str, str]:
    col, row = _landing(graph, u, v)
    if _is_drop(graph, u, v):
        return "pit", f"falling into the pit at {name(col, row)}"
    return "sealed", f"jumping to the platform at {name(col, row)}"


def _items_text(model: ItemModel, items: Iterable[str]) -> str:
    labels = [model.labels.get(i, i) for i in sorted(items)]
    return " or ".join(labels) if labels else "an item"


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def find_softlocks(
    graph: AbstractLevelGraph,
    start_span: Optional[str],
    exit_spans: Iterable[str],
    items: Optional[ItemModel] = None,
    *,
    name: CellName = _default_name,
    max_items: int = MAX_ITEMS,
) -> SoftlockReport:
    """Soft-locks of the level whose movement graph is *graph* (see the module docstring)."""
    g = graph.nx_graph
    standable = {n for n, d in g.nodes(data=True)
                 if getattr(d.get("data"), "node_type", None) != NodeType.HAZARD}
    exits = {s for s in exit_spans if s in standable}
    report = SoftlockReport(items=items, start_span=start_span, exit_spans=exits)
    if start_span is None or start_span not in standable or not exits:
        report.unbeatable = True
        return report
    sub = g.subgraph(standable)

    # ── SCC check ──
    cond = nx.condensation(sub)
    member = cond.graph["mapping"]
    live_c: Set[int] = set()
    for c in reversed(list(nx.topological_sort(cond))):
        if any(m in exits for m in cond.nodes[c]["members"]) or any(
            s in live_c for s in cond.successors(c)
        ):
            live_c.add(c)
    live = {n for n in standable if member[n] in live_c}
    reach = {start_span}
    todo = [start_span]
    while todo:
        cur = todo.pop()
        if cur in exits and cur != start_span:
            continue
        for nxt in sub.successors(cur):
            if nxt not in reach:
                reach.add(nxt)
                todo.append(nxt)
    report.live, report.reach = live, reach
    report.stats = {"sccs": cond.number_of_nodes()}
    if start_span not in live:
        report.unbeatable = True
        return report

    trapped = reach - live
    for comp in sorted(nx.weakly_connected_components(sub.subgraph(trapped)), key=min):
        entries = sorted((u, v) for v in comp for u in sub.predecessors(v)
                         if u in reach and u in live)
        entry = entries[0] if entries else None
        if entry is not None:
            kind, what = _describe_entry(graph, *entry, name)
            tail = "(no way back up)" if kind == "pit" else "(no way back to the Exit)"
            msg = f"{what[0].upper()}{what[1:]} traps the player {tail}."
        else:
            kind, msg = "sealed", "An area of the level traps the player (no way back to the Exit)."
        report.traps.append(Trap(kind, sorted(comp), entry, message=msg))

    # ── item-state check ──
    model = items or ItemModel()
    if not model.active:
        return report
    if len(model.names) > max_items:
        report.too_many_items = True
        return report
    _item_check(graph, sub, start_span, exits, model, trapped, report, name)
    return report


def _item_check(
    graph: AbstractLevelGraph,
    sub: Any,
    start_span: str,
    exits: Set[str],
    model: ItemModel,
    trapped: Set[str],
    report: SoftlockReport,
    name: CellName,
) -> None:
    empty: FrozenSet[str] = frozenset()

    def pick(s: str, held: FrozenSet[str]) -> FrozenSet[str]:
        got = model.pickups.get(s)
        return held | got if got else held

    def goal(s: str, held: FrozenSet[str]) -> bool:
        if s not in exits:
            return False
        lock = model.exit_locks.get(s)
        return lock is None or bool(held & lock)

    def moves(s: str, held: FrozenSet[str]) -> Iterable[State]:
        for v in sub.successors(s):
            door = model.doors.get(v)
            if door is not None and v != s and not held & door:
                continue
            ab = getattr(sub.edges[s, v].get("data"), "metadata", {}).get("ability")
            if ab in model.gated_abilities and f"ability:{ab}" not in held:
                continue
            yield v, pick(v, held)

    start: State = (start_span, pick(start_span, model.start_items or empty))
    seen: Set[State] = {start}
    back: Dict[State, List[State]] = {}
    goals: List[State] = []
    todo = [start]
    while todo:
        st = todo.pop()
        if goal(*st):
            goals.append(st)
            continue
        for nxt in moves(*st):
            back.setdefault(nxt, []).append(st)
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    alive: Set[State] = set(goals)
    todo = list(goals)
    while todo:
        st = todo.pop()
        for prev in back.get(st, ()):
            if prev not in alive:
                alive.add(prev)
                todo.append(prev)
    report.stats["item_states"] = len(seen)

    # A Key that cannot be reached without opening its own door.
    reached_spans = {s for s, _h in seen}
    for door_span, keys in sorted(model.doors.items()):
        for item in sorted(keys):
            key_spans = [s for s, its in model.pickups.items() if item in its]
            if not key_spans:
                continue
            ks = key_spans[0]
            without = _reach_avoiding(sub, start_span, exits, door_span)
            if ks in without or ks not in reach_plain(sub, start_span, exits):
                continue
            row, c0, _c1 = _rect(graph, door_span)
            report.traps.append(Trap(
                "key_behind_door", [ks], None, [item],
                f"The {model.labels.get(item, item)} is behind its own door at "
                f"{name(c0, row)}: the player cannot reach the key without opening the door, "
                "so the door never opens.",
            ))

    if start not in alive:
        if not report.traps:
            report.unbeatable = True
        return
    dead = seen - alive
    if not dead:
        return
    dead_spans = {s for s, _h in dead} - trapped
    if not dead_spans:
        return
    entries: List[Tuple[State, State]] = []
    for st in sorted(dead, key=lambda x: (x[0], sorted(x[1]))):
        for prev in back.get(st, ()):
            if prev in alive:
                entries.append((prev, st))
    needed: Set[str] = set(model.names)
    for comp in sorted(nx.weakly_connected_components(sub.subgraph(dead_spans)), key=min):
        ent = next((e for e in sorted(entries, key=lambda e: (e[0][0], e[1][0]))
                    if e[1][0] in comp), None)
        if ent is None:
            continue
        (u, held), (v, _h2) = ent
        missing = sorted(needed - set(held))
        _kind, what = _describe_entry(graph, u, v, name)
        msg = (f"{what[0].upper()}{what[1:]} without {_items_text(model, missing)} traps the "
               "player: the way on needs it and there is no way back to get it.")
        report.traps.append(Trap("item", sorted(comp), (u, v), missing, msg))


def reach_plain(sub: Any, start: str, exits: Set[str]) -> Set[str]:
    return _reach_avoiding(sub, start, exits, None)


def _reach_avoiding(sub: Any, start: str, exits: Set[str], avoid: Optional[str]) -> Set[str]:
    seen = {start}
    todo = [start]
    while todo:
        cur = todo.pop()
        if cur in exits and cur != start:
            continue
        for nxt in sub.successors(cur):
            if nxt != avoid and nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


__all__ = [
    "ItemModel",
    "MAX_ITEMS",
    "SoftlockReport",
    "Trap",
    "entity_id",
    "find_softlocks",
    "items_from_entities",
]
