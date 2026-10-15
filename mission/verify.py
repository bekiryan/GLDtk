"""Is the mission realised on the built level?  (v4 Part 23)

Runs Part 22's item-state graph (``layout.softlock``) on the verified grid:

* **lock** — with the key taken out of the level, no Exit is reachable (the
  lock really blocks the way), and the door stands on a floor;
* **gate** — the same with the ability pickup taken out;
* **loop** — the shortcut door's span and the hub door's span reach each
  other in the movement graph (a cycle back through the hub);
* **boss** / **treasure** — the entity was placed.

The level itself must verify (``report.ok``: no soft-locks with every item).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

import networkx as nx

from layout.softlock import ItemModel, find_softlocks, items_from_entities
from layout.verify import SpanIndex, _anchor_span
from symbolic.schema import NodeType

from .space import SpacePlan


@dataclass
class MissionCheck:
    realised: bool
    features: List[Dict[str, Any]] = field(default_factory=list)  # {kind, ok, reason, ...}
    problems: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"realised": self.realised, "features": self.features, "problems": self.problems}


def _without(model: ItemModel, item: str) -> ItemModel:
    pickups = {s: set(its) - {item} for s, its in model.pickups.items()}
    return replace(model, pickups={s: its for s, its in pickups.items() if its})


def verify_mission(layout: Any, report: Any, plan: SpacePlan) -> MissionCheck:
    out = MissionCheck(realised=False)
    graph = report.graph
    if not report.ok or graph is None:
        out.problems.append("the level does not verify")
        return out
    index = SpanIndex(graph, layout)
    ents = [e for e in layout.ir.entities if "spec_id" in e.metadata or e.entity_type == "Start"]
    by_id = {str(e.metadata.get("spec_id")): e for e in ents if "spec_id" in e.metadata}

    def span(eid: Optional[str]) -> Optional[str]:
        e = by_id.get(eid or "")
        return _anchor_span(e, layout, index) if e is not None else None

    items = items_from_entities(ents, lambda e: _anchor_span(e, layout, index))
    g = plan.graph
    sub = graph.nx_graph.subgraph(
        [n for n, d in graph.nx_graph.nodes(data=True)
         if getattr(d.get("data"), "node_type", None) != NodeType.HAZARD])

    def blocks(item: str) -> bool:
        rep = find_softlocks(graph, report.start_span, report.exit_spans, _without(items, item))
        return rep.unbeatable

    for node in g.nodes.values():
        eid = plan.entities.get(node.id)
        feat: Dict[str, Any] = {"node": node.id, "section": plan.sections.get(node.id)}
        if node.kind == "lock":
            key = g.item_node(node)
            kid = plan.entities.get(key.id) if key else None
            feat.update(kind="lock", key=kid, lock=eid,
                        key_section=plan.sections.get(key.id) if key else None,
                        lock_section=plan.sections.get(node.id))
            if span(kid) is None or span(eid) is None:
                feat.update(ok=False, reason="the key or its lock was not placed on a floor")
            elif not blocks(f"key:{kid}"):
                feat.update(ok=False, reason=f"the Exit can be reached without key '{kid}'")
            else:
                feat.update(ok=True, reason=f"'{eid}' is only passable after key '{kid}'")
        elif node.kind == "gate":
            pick = g.item_node(node)
            pid = plan.entities.get(pick.id) if pick else None
            feat.update(kind="gate", ability=node.item, pickup=pid)
            if span(pid) is None:
                feat.update(ok=False, reason="the ability pickup was not placed on a floor")
            elif not blocks(f"ability:{node.item}"):
                feat.update(ok=False, reason=f"the Exit can be reached without {node.item}")
            else:
                feat.update(ok=True, reason=f"the way on needs {node.item}")
        elif node.kind == "shortcut":
            hub_door = plan.hub_doors.get(node.id)
            a, b = span(eid), span(hub_door)
            feat.update(kind="loop", door=eid, hub_door=hub_door,
                        section=node.params.get("hub_section"), from_section=plan.sections.get(node.id))
            if a is None or b is None:
                feat.update(ok=False, reason="a shortcut door was not placed on a floor")
            elif a in sub and b in sub and nx.has_path(sub, a, b) and nx.has_path(sub, b, a):
                feat.update(ok=True, reason="the shortcut loops back to the hub")
            else:
                feat.update(ok=False, reason="no way back from the shortcut to the hub")
        elif node.kind in ("boss", "treasure"):
            feat.update(kind=node.kind, entity=eid)
            ok = span(eid) is not None
            feat.update(ok=ok, reason="placed" if ok else "not placed on a floor")
        else:
            continue
        out.features.append(feat)
    out.problems = [f"{f['kind']} {f['node']}: {f['reason']}" for f in out.features if not f["ok"]]
    out.realised = not out.problems
    return out


__all__ = ["MissionCheck", "verify_mission"]
