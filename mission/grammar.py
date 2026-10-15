"""Graph-rewriting grammar for mission graphs (v4 Part 23, after Dormans).

A rule rewrites the graph in place and returns True, or returns False when it
does not apply (nothing changed).  ``rewrite`` applies the requested rules
with a seeded ``random.Random`` up to a budget and checks after every rewrite
that the graph is still solvable (a rewrite that breaks it is undone and
reported).  The same requests and seed always give the same graph.

* ``lock_key_cycle``   — split a main-line edge a → b into a → lock → b and
  hang the key on a side branch from a (a → key → a): the player goes out for
  the key and comes back to the lock (a cycle).
* ``ability_gate``     — an ability pickup on a side branch, then a gate on a
  later main-line edge; with ``backtrack`` the pickup sits one step further
  back so the player returns past where the gate was seen.
* ``shortcut_loop``    — after a lock or a gate, a one-way shortcut back to
  the hub its key branch left from.
* ``treasure_branch``  — an optional treasure on a side branch.
* ``boss_before_exit`` — a boss right before the Exit.
"""

from __future__ import annotations

import random
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .graph import BARRIER_KINDS, MissionGraph

Rule = Callable[[MissionGraph, random.Random, Dict[str, Any]], bool]
Request = Tuple[str, Dict[str, Any]]

RULE_ORDER = ("lock_key_cycle", "ability_gate", "treasure_branch", "shortcut_loop", "boss_before_exit")
DEFAULT_BUDGET = 8


def _main_edges(g: MissionGraph) -> List[Tuple[str, str]]:
    line = g.main_line()
    return list(zip(line, line[1:]))


def _split(g: MissionGraph, a: str, b: str, mid: str) -> None:
    g.unlink(a, b)
    g.link(a, mid)
    g.link(mid, b)


def _branch(g: MissionGraph, origin: str, nid: str) -> None:
    g.link(origin, nid)
    g.link(nid, origin)


def lock_key_cycle(g: MissionGraph, rng: random.Random, p: Dict[str, Any]) -> bool:
    # the lock goes on an edge after the last barrier, so keys come in order
    edges = _main_edges(g)
    if not edges:
        return False
    line = g.main_line()
    last = max((i for i, n in enumerate(line) if g.nodes[n].kind in BARRIER_KINDS), default=0)
    cands = [(a, b) for a, b in edges if line.index(a) >= last and g.nodes[b].kind != "boss"] or edges
    a, b = cands[rng.randrange(len(cands))] if p.get("random") else cands[0]
    lock = g.fresh("lock")
    key = lock.replace("lock", "key")
    item = key
    g.add(lock, "lock", item, **{k: v for k, v in p.items() if k == "lock_section" and v})
    g.add(key, "key", item, **{k: v for k, v in p.items() if k == "key_section" and v})
    _split(g, a, b, lock)
    _branch(g, a, key)
    g.link(key, lock, "unlock")
    g.applied.append("lock_key_cycle")
    return True


def ability_gate(g: MissionGraph, rng: random.Random, p: Dict[str, Any]) -> bool:
    ability = p.get("ability")
    if not ability or any(n.item == ability for n in g.of_kind("ability")):
        return False
    edges = _main_edges(g)
    line = g.main_line()
    last = max((i for i, n in enumerate(line) if g.nodes[n].kind in BARRIER_KINDS), default=0)
    cands = [(a, b) for a, b in edges if line.index(a) >= last and g.nodes[b].kind != "boss"]
    if not cands:
        return False
    a, b = cands[rng.randrange(len(cands))] if p.get("random") else cands[0]
    gate = g.fresh("gate")
    pick = gate.replace("gate", "ability")
    g.add(gate, "gate", ability)
    g.add(pick, "ability", ability)
    _split(g, a, b, gate)
    origin = a
    if p.get("backtrack"):
        k = line.index(a)
        origin = line[k - 1] if k > 0 and g.nodes[line[k - 1]].kind != "start" else a
    _branch(g, origin, pick)
    g.link(pick, gate, "unlock")
    g.applied.append("ability_gate")
    return True


def shortcut_loop(g: MissionGraph, rng: random.Random, p: Dict[str, Any]) -> bool:
    line = g.main_line()
    used = {g.successors(s, "shortcut")[0] for s in (n.id for n in g.of_kind("shortcut"))
            if g.successors(s, "shortcut")}
    for i, nid in enumerate(line[:-1]):
        node = g.nodes[nid]
        if node.kind not in BARRIER_KINDS:
            continue
        key = g.item_node(node)
        hub = g.branch_origin(key.id) if key else None
        if hub is None or hub in used or g.nodes[line[i + 1]].kind == "shortcut":
            continue
        sc = g.fresh("shortcut")
        g.add(sc, "shortcut", None, hub=hub)
        _split(g, nid, line[i + 1], sc)
        g.link(sc, hub, "shortcut")
        g.applied.append("shortcut_loop")
        return True
    return False


def treasure_branch(g: MissionGraph, rng: random.Random, p: Dict[str, Any]) -> bool:
    line = [n for n in g.main_line() if n != "exit"]
    if not line:
        return False
    origin = line[rng.randrange(len(line))] if p.get("random") else line[len(line) // 2]
    node = g.add(g.fresh("treasure"), "treasure")
    _branch(g, origin, node.id)
    g.applied.append("treasure_branch")
    return True


def boss_before_exit(g: MissionGraph, rng: random.Random, p: Dict[str, Any]) -> bool:
    if g.of_kind("boss"):
        return False
    line = g.main_line()
    if len(line) < 2:
        return False
    _split(g, line[-2], "exit", "boss")
    g.add("boss", "boss")
    g.applied.append("boss_before_exit")
    return True


RULES: Dict[str, Rule] = {
    "lock_key_cycle": lock_key_cycle,
    "ability_gate": ability_gate,
    "shortcut_loop": shortcut_loop,
    "treasure_branch": treasure_branch,
    "boss_before_exit": boss_before_exit,
}


def rewrite(
    requests: Sequence[Request],
    seed: int = 0,
    budget: int = DEFAULT_BUDGET,
    graph: Optional[MissionGraph] = None,
) -> Tuple[MissionGraph, List[str]]:
    """Apply *requests* (rule name, params) in order; returns the graph and warnings."""
    g = graph.copy() if graph else MissionGraph.initial()
    rng = random.Random(f"{seed}:mission")
    warnings: List[str] = []
    for k, (name, params) in enumerate(requests):
        if k >= budget:
            warnings.append(f"mission: rule budget of {budget} reached, {len(requests) - k} rule(s) left out")
            break
        rule = RULES.get(name)
        if rule is None:
            warnings.append(f"mission: unknown rule '{name}'")
            continue
        before = g.copy()
        if not rule(g, rng, dict(params)):
            warnings.append(f"mission: rule '{name}' does not apply here")
            continue
        if not g.solvable():
            g = before
            warnings.append(f"mission: rule '{name}' would make the mission unsolvable, left out")
    return g, warnings


def requests_from(mission: Any, abilities: Sequence[str] = ()) -> Tuple[List[Request], List[str]]:
    """Rule requests from a ``MissionSpec``, in the canonical order (keys before shortcuts)."""
    warnings: List[str] = []
    rules = list(mission.rules)
    n_keys = rules.count("lock_key_cycle")
    if mission.keys is not None:
        n_keys = mission.keys
    locks = list(mission.locks or [])
    out: List[Request] = []
    for name in RULE_ORDER:
        if name == "lock_key_cycle":
            for k in range(n_keys):
                hint = locks[k] if k < len(locks) else None
                out.append((name, {"key_section": getattr(hint, "key_section", None),
                                   "lock_section": getattr(hint, "lock_section", None)}))
            continue
        for _ in range(rules.count(name)):
            params: Dict[str, Any] = {}
            if name == "ability_gate":
                ability = mission.ability or next(iter(abilities), None)
                if ability is None:
                    warnings.append("mission: ability_gate needs an ability the Game Profile "
                                    "enables; left out")
                    continue
                if ability not in abilities:
                    warnings.append(f"mission: the Game Profile does not enable {ability}; "
                                    "ability_gate left out")
                    continue
                params["ability"] = ability
            out.append((name, params))
    return out, warnings


__all__ = ["RULES", "RULE_ORDER", "Request", "requests_from", "rewrite"]
