"""Sugiyama Layout Engine for GLDtk.

Converts an AbstractLevelGraph into an IRLevel by assigning tile-grid
coordinates to each node using a four-phase Sugiyama-style algorithm adapted
for platformer level design:

  Phase 1 — Cycle Removal    : DFS-based back-edge removal → DAG
  Phase 2 — Layer Assignment : Critical-path ranking; golden path forms spine
  Phase 3 — Crossing Minim.  : Barycenter ordering within each layer
  Phase 4 — Coordinate Assign: Map (layer, order) → (col, row) in tile space

The "Golden Path" (shortest Start→Exit route) is always laid out on the
central spine; off-path nodes are inserted around it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import networkx as nx

from symbolic.schema import AbstractLevelGraph, EdgeType, NodeType
from .ir import IREntity, IRLevel, TileValue


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class LayoutConfig:
    """All sizing and spacing parameters for the layout engine."""

    # Level canvas
    width_tiles: int  = 60
    height_tiles: int = 20
    tile_size: int    = 16

    # Per-platform dimensions (solid tiles)
    platform_width: int  = 5   # tiles wide
    platform_height: int = 1   # tiles tall (always 1 for flat platforms)

    # Spacing
    h_gap: int = 5   # empty tile columns between platform columns
    v_step: int = 4  # row shift per JUMP edge along the golden path

    # Baseline row (row index of the "floor" platforms, measured from top)
    baseline_row: int = 15

    # Maximum row index platforms can occupy (prevents overflow)
    @property
    def max_row(self) -> int:
        return self.height_tiles - 2

    @property
    def layer_stride(self) -> int:
        """Tile columns consumed by one layer slot (platform + gap)."""
        return self.platform_width + self.h_gap


# ---------------------------------------------------------------------------
# Internal data types
# ---------------------------------------------------------------------------

@dataclass
class NodeLayout:
    """Resolved tile-grid position for one AbstractLevelGraph node."""
    node_id: str
    col: int     # tile column of platform top-left corner
    row: int     # tile row  of platform top-left corner
    width: int   # in tiles
    height: int  # in tiles


# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------

def _remove_back_edges(dag: nx.DiGraph) -> nx.DiGraph:
    """Return a copy of *dag* with DFS back-edges removed."""
    result = dag.copy()
    visited: Set[str] = set()
    stack:   Set[str] = set()
    back_edges: List[Tuple[str, str]] = []

    def dfs(v: str) -> None:
        visited.add(v)
        stack.add(v)
        for w in list(result.successors(v)):
            if w not in visited:
                dfs(w)
            elif w in stack:
                back_edges.append((v, w))
        stack.discard(v)

    for n in list(result.nodes()):
        if n not in visited:
            dfs(n)

    result.remove_edges_from(back_edges)
    return result


def _find_golden_path(
    graph: AbstractLevelGraph,
    dag: nx.DiGraph,
) -> List[str]:
    """Shortest (fewest hops) path from any Start node to any Exit node."""
    starts = graph.nodes_by_type(NodeType.START)
    exits  = graph.nodes_by_type(NodeType.EXIT)

    best: List[str] = []
    for s in starts:
        for e in exits:
            if not nx.has_path(dag, s.id, e.id):
                continue
            path: List[str] = nx.shortest_path(dag, s.id, e.id)
            if not best or len(path) < len(best):
                best = path
    return best


def _assign_layers(
    dag: nx.DiGraph,
    golden_path: List[str],
) -> Dict[str, int]:
    """Assign a non-negative integer layer (x-index) to every node.

    Golden path nodes occupy layers 0 … n-1 in order.
    Off-path nodes are inserted using a longest-path rule that respects the
    spine layers already occupied.
    """
    layers: Dict[str, int] = {}

    # Spine
    for i, nid in enumerate(golden_path):
        layers[nid] = i

    # Off-path nodes — topological order guarantees predecessors are ready
    try:
        topo = list(nx.topological_sort(dag))
    except nx.NetworkXUnfeasible:
        topo = list(dag.nodes())

    for nid in topo:
        if nid in layers:
            continue
        preds = [p for p in dag.predecessors(nid) if p in layers]
        succs = [s for s in dag.successors(nid)  if s in layers]
        if preds:
            layers[nid] = max(layers[p] for p in preds) + 1
        elif succs:
            layers[nid] = min(layers[s] for s in succs) - 1
        else:
            layers[nid] = 0

    # Normalise so the minimum layer index is 0
    min_l = min(layers.values(), default=0)
    return {nid: l - min_l for nid, l in layers.items()}


def _assign_rows_golden_path(
    graph: AbstractLevelGraph,
    golden_path: List[str],
    config: LayoutConfig,
) -> Dict[str, int]:
    """Walk the golden path and set row based on cumulative edge-type deltas."""
    rows: Dict[str, int] = {}
    if not golden_path:
        return rows

    rows[golden_path[0]] = config.baseline_row

    for i in range(1, len(golden_path)):
        src_id = golden_path[i - 1]
        dst_id = golden_path[i]
        prev_row = rows[src_id]

        try:
            edge = graph.get_edge(src_id, dst_id)
            etype = edge.edge_type
        except KeyError:
            etype = EdgeType.WALK

        if etype == EdgeType.JUMP:
            row = prev_row - config.v_step       # visually higher → smaller row
        elif etype == EdgeType.FALL:
            row = prev_row + config.v_step       # visually lower  → larger row
        else:
            row = prev_row

        rows[dst_id] = max(1, min(config.max_row, row))

    return rows


def _barycenter_order(
    layer_nodes: List[str],
    dag: nx.DiGraph,
    fixed_rows: Dict[str, int],
    all_layers: Dict[str, int],
    current_layer: int,
) -> List[str]:
    """Sort *layer_nodes* by the average row of their already-placed neighbours."""

    def _barycenter(nid: str) -> float:
        neighbours = list(dag.predecessors(nid)) + list(dag.successors(nid))
        placed = [fixed_rows[n] for n in neighbours if n in fixed_rows]
        return sum(placed) / len(placed) if placed else float("inf")

    return sorted(layer_nodes, key=_barycenter)


def _assign_rows_off_path(
    dag: nx.DiGraph,
    layers: Dict[str, int],
    golden_path: List[str],
    golden_rows: Dict[str, int],
    config: LayoutConfig,
) -> Dict[str, int]:
    """Assign rows to off-path nodes using the barycenter heuristic."""
    rows: Dict[str, int] = dict(golden_rows)
    golden_set: Set[str] = set(golden_path)

    # Group non-golden nodes by layer
    layer_groups: Dict[int, List[str]] = {}
    for nid, layer in layers.items():
        if nid not in golden_set:
            layer_groups.setdefault(layer, []).append(nid)

    # Process layers in order so predecessors are already assigned
    for layer_idx in sorted(layer_groups.keys()):
        off_nodes = _barycenter_order(
            layer_groups[layer_idx], dag, rows, layers, layer_idx
        )
        # Find the golden-path row at this layer (if one exists); use baseline
        # as default
        golden_at_layer = [golden_rows[n] for n in golden_path if layers.get(n) == layer_idx]
        center_row = golden_at_layer[0] if golden_at_layer else config.baseline_row

        # Spread off-path nodes above / below the center
        spread = config.v_step
        for j, nid in enumerate(off_nodes):
            offset = (j + 1) * spread * (1 if j % 2 == 0 else -1)
            row = center_row + offset
            rows[nid] = max(1, min(config.max_row, row))

    return rows


# ---------------------------------------------------------------------------
# IR builder
# ---------------------------------------------------------------------------

def _build_ir(
    graph: AbstractLevelGraph,
    node_layouts: Dict[str, NodeLayout],
    config: LayoutConfig,
) -> IRLevel:
    """Rasterise node layouts into an IRLevel tile grid."""
    ir = IRLevel.empty(
        "Level_0",
        config.width_tiles,
        config.height_tiles,
        config.tile_size,
    )

    _entity_types = {
        NodeType.START:  ("Start",  TileValue.SOLID),
        NodeType.EXIT:   ("Exit",   TileValue.SOLID),
        NodeType.PLATFORM: (None,   TileValue.SOLID),
        NodeType.HAZARD:  (None,    TileValue.HAZARD),
    }

    for nid, nl in node_layouts.items():
        node = graph.get_node(nid)
        entity_label, tile_val = _entity_types[node.node_type]

        ir.fill_rect(nl.col, nl.row, nl.width, nl.height, tile_val)

        if entity_label:
            anchor_col = nl.col + nl.width // 2
            ir.entities.append(
                IREntity(
                    entity_type=entity_label,
                    col=anchor_col,
                    row=nl.row,
                    metadata={"node_id": nid},
                )
            )

    return ir


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def sugiyama_layout(
    graph: AbstractLevelGraph,
    config: Optional[LayoutConfig] = None,
) -> Tuple[Dict[str, NodeLayout], IRLevel]:
    """Run the Sugiyama layout pipeline on *graph*.

    Returns
    -------
    (node_layouts, ir_level)
        node_layouts : mapping from node_id → NodeLayout (tile positions).
        ir_level     : ready-to-serialize IRLevel with platforms rasterised.
    """
    cfg = config or LayoutConfig()
    raw_dag = _remove_back_edges(graph.nx_graph)

    golden_path = _find_golden_path(graph, raw_dag)
    layers      = _assign_layers(raw_dag, golden_path)
    golden_rows = _assign_rows_golden_path(graph, golden_path, cfg)
    all_rows    = _assign_rows_off_path(raw_dag, layers, golden_path, golden_rows, cfg)

    node_layouts: Dict[str, NodeLayout] = {}
    for nid, layer in layers.items():
        col = layer * cfg.layer_stride
        row = all_rows.get(nid, cfg.baseline_row)
        node_layouts[nid] = NodeLayout(
            node_id=nid,
            col=col,
            row=row,
            width=cfg.platform_width,
            height=cfg.platform_height,
        )

    ir = _build_ir(graph, node_layouts, cfg)
    return node_layouts, ir
