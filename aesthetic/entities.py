"""Entity Placement along the Golden Path for GLDtk.

Populates a level with collectibles (Coins, Keys), hazards (Enemies), and
Checkpoints by walking the Golden Path — the shortest traversal route from
Start to Exit — and weighting placement by a per-node difficulty score.

Difficulty model
----------------
Each node on the Golden Path receives a raw difficulty value:

    raw[0] = 0.0                              (Start)
    raw[i] = (i / (N−1))                     base positional score
             + Σ edge_bonus(edge[j])          cumulative jump/fall bonus

    edge_bonus: JUMP → +0.15,  FALL → +0.05,  WALK → 0

Values are then normalised to [0.0, 1.0].

Placement rules per node
-------------------------
Coins      — placed on every non-Hazard node whose difficulty < 0.90.
             Quantity = clamp(platform_width // 2, 1, 3), spread evenly.
Key        — one Key on the node whose difficulty is closest to 0.40.
Enemy      — placed on Platform nodes with difficulty > 0.35;
             probability = min(difficulty × theme.enemy_rate, 1.0).
             The selected archetype (e.g., Skeleton/Slime/Harpy) is stored
             in metadata["archetype"].
Checkpoint — one Checkpoint on the node closest to difficulty 0.50,
             only if the Golden Path has ≥ 5 nodes.

Entity row placement: one tile ABOVE the platform surface (layout.row − 1),
which positions the entity visually on top of the platform.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import networkx as nx

from symbolic.schema import AbstractLevelGraph, EdgeType, NodeType
from layout.ir import IREntity
from layout.sugiyama import NodeLayout
from .themes import Theme


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _find_golden_path(graph: AbstractLevelGraph) -> List[str]:
    """Shortest-hop path from any Start node to any Exit node."""
    starts = graph.nodes_by_type(NodeType.START)
    exits  = graph.nodes_by_type(NodeType.EXIT)
    best: List[str] = []
    for s in starts:
        for e in exits:
            try:
                path: List[str] = nx.shortest_path(graph.nx_graph, s.id, e.id)
                if not best or len(path) < len(best):
                    best = path
            except nx.NetworkXNoPath:
                pass
    return best


def _compute_difficulties(
    graph: AbstractLevelGraph,
    golden_path: List[str],
) -> Dict[str, float]:
    """Return normalised difficulty ∈ [0, 1] for each node on the path."""
    n = len(golden_path)
    if n == 0:
        return {}
    if n == 1:
        return {golden_path[0]: 0.0}

    _EDGE_BONUS = {EdgeType.JUMP: 0.15, EdgeType.FALL: 0.05, EdgeType.WALK: 0.0}

    raw: Dict[str, float] = {}
    cumulative = 0.0
    raw[golden_path[0]] = 0.0

    for i in range(1, n):
        src_id, dst_id = golden_path[i - 1], golden_path[i]
        try:
            edge = graph.get_edge(src_id, dst_id)
            cumulative += _EDGE_BONUS.get(edge.edge_type, 0.0)
        except KeyError:
            pass
        raw[dst_id] = i / (n - 1) + cumulative

    max_val = max(raw.values(), default=1.0) or 1.0
    return {nid: v / max_val for nid, v in raw.items()}


def _spread_cols(col_start: int, width: int, count: int) -> List[int]:
    """Spread *count* entity columns evenly across a platform [col_start, col_start+width)."""
    if count <= 0:
        return []
    step = max(1, width // (count + 1))
    return [col_start + step * (i + 1) for i in range(count)]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def place_entities(
    graph: AbstractLevelGraph,
    node_layouts: Dict[str, NodeLayout],
    theme: Theme,
    density: float = 1.0,
    seed: int = 42,
) -> List[IREntity]:
    """Generate all aesthetic entity placements for a level.

    Parameters
    ----------
    graph        : validated AbstractLevelGraph.
    node_layouts : {node_id: NodeLayout} mapping from ``sugiyama_layout``.
    theme        : active theme (supplies enemy archetype and enemy rate).
    density      : global multiplier for entity counts (0 = none, 1 = normal).
    seed         : RNG seed for deterministic output.

    Returns
    -------
    List[IREntity] to be appended to ``IRLevel.entities`` before serialisation.
    """
    rng = random.Random(seed)
    golden_path = _find_golden_path(graph)
    if not golden_path:
        return []

    difficulties = _compute_difficulties(graph, golden_path)
    entities: List[IREntity] = []

    # ── Pre-compute Key and Checkpoint targets ────────────────────────────
    path_with_layout = [nid for nid in golden_path if nid in node_layouts]
    key_target = min(
        path_with_layout,
        key=lambda nid: abs(difficulties.get(nid, 0.0) - 0.40),
        default=None,
    )
    checkpoint_target = (
        min(
            path_with_layout,
            key=lambda nid: abs(difficulties.get(nid, 0.0) - 0.50),
            default=None,
        )
        if len(path_with_layout) >= 5
        else None
    )

    # ── Walk the golden path ──────────────────────────────────────────────
    for nid in golden_path:
        if nid not in node_layouts:
            continue

        nl   = node_layouts[nid]
        node = graph.get_node(nid)
        diff = difficulties.get(nid, 0.0)

        # Row just above the platform surface (entities stand on top)
        entity_row = max(0, nl.row - 1)

        # ── Coins ─────────────────────────────────────────────────────────
        if node.node_type != NodeType.HAZARD and diff < 0.90:
            n_coins = max(1, int(nl.width // 2 * density))
            n_coins = min(n_coins, 3)
            for col in _spread_cols(nl.col, nl.width, n_coins):
                entities.append(IREntity(
                    entity_type="Coin",
                    col=col,
                    row=entity_row,
                    metadata={"value": 1, "difficulty": round(diff, 2)},
                ))

        # ── Key ───────────────────────────────────────────────────────────
        if nid == key_target and node.node_type != NodeType.HAZARD:
            center_col = nl.col + nl.width // 2
            entities.append(IREntity(
                entity_type="Key",
                col=center_col,
                row=entity_row,
                metadata={"unlocks": "exit"},
            ))

        # ── Enemy ─────────────────────────────────────────────────────────
        if node.node_type == NodeType.PLATFORM and diff > 0.35:
            spawn_prob = min(diff * theme.enemy_rate * density, 1.0)
            if rng.random() < spawn_prob:
                center_col = nl.col + nl.width // 2
                entities.append(IREntity(
                    entity_type="Enemy",
                    col=center_col,
                    row=entity_row,
                    metadata={
                        "difficulty": round(diff, 2),
                        "patrol": True,
                        "archetype": theme.enemy_type,
                    },
                ))

        # ── Checkpoint ────────────────────────────────────────────────────
        if nid == checkpoint_target and node.node_type != NodeType.HAZARD:
            center_col = nl.col + nl.width // 2
            entities.append(IREntity(
                entity_type="Checkpoint",
                col=center_col,
                row=entity_row,
                metadata={"difficulty": round(diff, 2)},
            ))

    return entities
