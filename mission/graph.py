"""The mission graph (v4 Part 23): a small directed graph of the player's goals.

Node kinds: ``start``, ``key`` → ``lock`` (a door or a locked Exit), ``ability``
(a pickup) → ``gate`` (a move that needs it), ``treasure`` (optional),
``boss``, ``exit`` and ``shortcut`` (a one-way door that loops back).

Edge kinds:

* ``path``     — the player goes from one goal to the next.  A side branch
  (a key, an ability pickup or a treasure) has a ``path`` edge in and one back
  out to its origin, so the player returns to the main line.
* ``unlock``   — a key (or an ability) opens a lock (or a gate).
* ``shortcut`` — a one-way move back to an earlier hub.

``solvable`` plays the graph: starting with no items, a lock or a gate can only
be entered with its item; the graph is solvable when every goal (the Exit
included) can be reached.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

NODE_KINDS = ("start", "key", "lock", "ability", "gate", "treasure", "boss", "exit", "shortcut")
BRANCH_KINDS = ("key", "ability", "treasure")  # side branches off the main line
BARRIER_KINDS = ("lock", "gate")


@dataclass
class MissionNode:
    id: str
    kind: str
    item: Optional[str] = None  # key / lock: the key's item; ability / gate: the ability
    params: Dict[str, Any] = field(default_factory=dict)  # e.g. key_section / lock_section hints


@dataclass
class MissionEdge:
    src: str
    dst: str
    kind: str = "path"  # "path" | "unlock" | "shortcut"


@dataclass
class MissionGraph:
    nodes: Dict[str, MissionNode] = field(default_factory=dict)
    edges: List[MissionEdge] = field(default_factory=list)
    applied: List[str] = field(default_factory=list)  # rule names, in order

    @classmethod
    def initial(cls) -> "MissionGraph":
        g = cls()
        g.add("start", "start")
        g.add("exit", "exit")
        g.link("start", "exit")
        return g

    # ── building ──────────────────────────────────────────────────────

    def add(self, nid: str, kind: str, item: Optional[str] = None, **params: Any) -> MissionNode:
        assert kind in NODE_KINDS, kind
        node = MissionNode(nid, kind, item, dict(params))
        self.nodes[nid] = node
        return node

    def link(self, src: str, dst: str, kind: str = "path") -> None:
        self.edges.append(MissionEdge(src, dst, kind))

    def unlink(self, src: str, dst: str, kind: str = "path") -> None:
        self.edges = [e for e in self.edges if not (e.src == src and e.dst == dst and e.kind == kind)]

    def fresh(self, base: str) -> str:
        k = 1
        while f"{base}{k}" in self.nodes:
            k += 1
        return f"{base}{k}"

    def of_kind(self, *kinds: str) -> List[MissionNode]:
        return [n for n in self.nodes.values() if n.kind in kinds]

    def copy(self) -> "MissionGraph":
        return copy.deepcopy(self)

    # ── reading ───────────────────────────────────────────────────────

    def successors(self, nid: str, kind: str = "path") -> List[str]:
        return [e.dst for e in self.edges if e.src == nid and e.kind == kind]

    def main_line(self) -> List[str]:
        """Start → Exit along ``path`` edges, skipping the side branches."""
        line = ["start"]
        seen = {"start"}
        cur = "start"
        while cur != "exit":
            nxt = [d for d in self.successors(cur)
                   if self.nodes[d].kind not in BRANCH_KINDS and d not in seen]
            if not nxt:
                break
            cur = nxt[0]
            line.append(cur)
            seen.add(cur)
        return line

    def branch_origin(self, nid: str) -> Optional[str]:
        """The main-line node a side branch leaves from."""
        return next((e.src for e in self.edges if e.dst == nid and e.kind == "path"), None)

    def item_node(self, barrier: MissionNode) -> Optional[MissionNode]:
        """The key (ability) node whose item opens *barrier*."""
        want = "key" if barrier.kind == "lock" else "ability"
        return next((n for n in self.nodes.values() if n.kind == want and n.item == barrier.item), None)

    def solvable(self) -> bool:
        held: Set[str] = set()
        reached = {"start"}
        changed = True
        while changed:
            changed = False
            for e in self.edges:
                if e.kind == "unlock" or e.src not in reached or e.dst in reached:
                    continue
                n = self.nodes[e.dst]
                if n.kind in BARRIER_KINDS and n.item not in held:
                    continue
                reached.add(e.dst)
                if n.kind in ("key", "ability") and n.item:
                    held.add(n.item)
                changed = True
        return "exit" in reached and reached == set(self.nodes)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [{"id": n.id, "kind": n.kind, "item": n.item,
                       **({"section": n.params["section"]} if n.params.get("section") else {}),
                       **({"entity": n.params["entity"]} if n.params.get("entity") else {})}
                      for n in self.nodes.values()],
            "edges": [{"from": e.src, "to": e.dst, "kind": e.kind} for e in self.edges],
        }


__all__ = ["BARRIER_KINDS", "BRANCH_KINDS", "MissionEdge", "MissionGraph", "MissionNode", "NODE_KINDS"]
