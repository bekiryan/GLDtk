"""Helpers that build LevelSpec dicts for layout / verification tests."""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

from spec import LevelSpec


def node(nid: str, kind: str, col: int, row_from_bottom: int, w_tiles: int = 3,
         section: str = "s1") -> Dict[str, Any]:
    """Node dict from tile coordinates (row_from_bottom 0 = bottom row)."""
    return {"id": nid, "type": kind, "x": col * 16, "y": row_from_bottom * 16,
            "w": w_tiles * 16, "h": 16, "section": section}


def entity(eid: str, kind: str, anchor: str, section: str = "s1", **fields: Any) -> Dict[str, Any]:
    return {"id": eid, "kind": kind, "anchor": anchor, "section": section,
            "description": eid, "sprite": "", **fields}


def section(sid: str = "s1", order: int = 0, gravity: float = 1.0, theme: str = "forest") -> Dict[str, Any]:
    return {"id": sid, "name": sid.title(), "theme": theme, "difficulty": "easy",
            "gravity_scale": gravity, "description": sid, "order": order}


def make_spec(
    nodes: List[Dict[str, Any]],
    edges: Sequence[Tuple[str, str, str]] = (),
    entities: Optional[List[Dict[str, Any]]] = None,
    sections: Optional[List[Dict[str, Any]]] = None,
    size: Tuple[int, int] = (30, 15),
    as_dict: bool = False,
):
    ids = {n["id"]: n for n in nodes}
    start = next(n["id"] for n in nodes if n["type"] == "Start")
    exits = [n["id"] for n in nodes if n["type"] == "Exit"]
    if entities is None:
        entities = [entity("player", "Start", start, ids[start]["section"], facing="right")]
        entities += [entity(f"door_{e}", "Exit", e, ids[e]["section"], requires_key=False) for e in exits]
    d = {
        "title": "Test",
        "size": {"width_tiles": size[0], "height_tiles": size[1]},
        "sections": sections or [section()],
        "nodes": nodes,
        "edges": [{"from": a, "to": b, "type": t, "vx": 0, "vy": 0, "ability": None}
                  for a, b, t in edges],
        "entities": entities,
        "requirements": [{"id": "R1", "text": "test", "category": "other"}],
    }
    return d if as_dict else LevelSpec.model_validate(d)


def edge_type(a: Dict[str, Any], b: Dict[str, Any]) -> str:
    if b["y"] > a["y"]:
        return "Jump"
    if b["y"] < a["y"]:
        return "Fall"
    touching = abs((a["x"] + a["w"]) - b["x"]) < 1e-6
    return "Walk" if touching else "Jump"


def chain_spec(rng: random.Random, width: int, height: int, n_platforms: int,
               n_sections: int = 1, extras: int = 0, step: Tuple[int, int] = (4, 6),
               dy_range: Tuple[int, int] = (-3, 2)) -> Dict[str, Any]:
    """Start → chain of platforms → Exit, left to right, plus off-path platforms.

    Positions are in tiles; every chain hop is a short jump/fall the default
    profile can make (callers still run validate_spec to be sure).
    """
    secs = [section(f"sec{i}", i) for i in range(n_sections)]
    nodes: List[Dict[str, Any]] = []
    col, row = 1, rng.randint(2, max(2, height // 3))
    total = n_platforms + 2
    per_section = max(1, -(-total // n_sections))
    for i in range(total):
        kind = "Start" if i == 0 else "Exit" if i == total - 1 else "Platform"
        w = rng.randint(2, 4)
        sec = secs[min(i // per_section, n_sections - 1)]["id"]
        nodes.append(node(["start", "exit"][kind == "Exit"] if kind != "Platform" else f"p{i}",
                          kind, col, row, w, sec))
        col += w + rng.randint(step[0], step[1]) - 2
        row = min(height - 4, max(0, row + rng.randint(*dy_range)))
    edges = [(a["id"], b["id"], edge_type(a, b)) for a, b in zip(nodes, nodes[1:])]
    chain = list(nodes)
    ents = [entity("player", "Start", "start", chain[0]["section"], facing="right"),
            entity("door", "Exit", "exit", chain[-1]["section"], requires_key=False)]
    for k in range(extras):
        host = chain[rng.randint(1, len(chain) - 2)] if len(chain) > 2 else chain[0]
        hc = host["x"] // 16
        hr = host["y"] // 16 + rng.choice([-3, 3, 4])  # within a ~4-tile jump (v4 Part 28)
        if not 0 <= hr <= height - 3:
            continue
        nid = f"x{k}"
        nodes.append(node(nid, "Platform", hc + rng.randint(-1, 1), hr, 3, host["section"]))
        ents.append(entity(f"coin{k}", "Pickup", nid, host["section"], pickup_kind="coin", value=1))
    for i, n in enumerate(chain[1:-1][:3]):
        if n["type"] == "Platform":
            ents.append(entity(f"gem{i}", "Pickup", n["id"], n["section"], pickup_kind="gem", value=5))
    return make_spec(nodes, edges, ents, secs, (width, height), as_dict=True)
