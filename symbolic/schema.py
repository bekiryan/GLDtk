"""Abstract Level Graph schema for GLDtk.

Nodes represent semantic gameplay locations; edges represent traversal actions
with their kinematic requirements stored as first-class data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple
import uuid

import networkx as nx


class NodeType(str, Enum):
    START = "start"
    EXIT = "exit"
    PLATFORM = "platform"
    HAZARD = "hazard"


class EdgeType(str, Enum):
    WALK = "walk"
    JUMP = "jump"
    FALL = "fall"


@dataclass
class Vec2:
    x: float
    y: float

    def __add__(self, other: Vec2) -> Vec2:
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vec2) -> Vec2:
        return Vec2(self.x - other.x, self.y - other.y)

    def __repr__(self) -> str:
        return f"Vec2({self.x}, {self.y})"


@dataclass
class LevelNode:
    """A semantic node in the abstract level graph."""

    id: str
    node_type: NodeType
    position: Vec2          # world-space top-left corner (pixels or tiles)
    size: Vec2              # width × height
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def center(self) -> Vec2:
        return Vec2(self.position.x + self.size.x / 2.0,
                    self.position.y + self.size.y / 2.0)

    @property
    def surface(self) -> Vec2:
        """Top-center point — the standing surface used for physics checks."""
        return Vec2(self.position.x + self.size.x / 2.0, self.position.y)

    @classmethod
    def make(
        cls,
        node_type: NodeType,
        position: Tuple[float, float],
        size: Tuple[float, float] = (16.0, 16.0),
        node_id: Optional[str] = None,
        **metadata: object,
    ) -> LevelNode:
        return cls(
            id=node_id or str(uuid.uuid4()),
            node_type=node_type,
            position=Vec2(*position),
            size=Vec2(*size),
            metadata=metadata,
        )


@dataclass
class LevelEdge:
    """A directed traversal action between two nodes.

    dx, dy   — displacement from source surface to target surface.
    v_launch — (vx, vy) initial velocity required to execute the action.
                Walk: (walk_speed, 0). Jump: computed by oracle. Fall: (vx, 0).
    """

    source_id: str
    target_id: str
    edge_type: EdgeType
    dx: float               # Δx: target.surface.x − source.surface.x
    dy: float               # Δy: target.surface.y − source.surface.y  (up = positive)
    v_launch: Tuple[float, float] = (0.0, 0.0)   # (vx, vy) at launch
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def delta(self) -> Vec2:
        return Vec2(self.dx, self.dy)

    @property
    def launch_velocity(self) -> Vec2:
        return Vec2(*self.v_launch)


class AbstractLevelGraph:
    """NetworkX-backed directed graph of LevelNodes and LevelEdges.

    Nodes and edges are stored in the NetworkX graph as attribute dicts so
    that all graph-theory algorithms (path finding, connectivity, etc.) work
    directly on this object.
    """

    def __init__(self) -> None:
        self._g: nx.DiGraph = nx.DiGraph()

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_node(self, node: LevelNode) -> None:
        self._g.add_node(node.id, data=node)

    def add_edge(self, edge: LevelEdge) -> None:
        if edge.source_id not in self._g:
            raise ValueError(f"Source node '{edge.source_id}' not in graph.")
        if edge.target_id not in self._g:
            raise ValueError(f"Target node '{edge.target_id}' not in graph.")
        self._g.add_edge(edge.source_id, edge.target_id, data=edge)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> LevelNode:
        return self._g.nodes[node_id]["data"]

    def get_edge(self, source_id: str, target_id: str) -> LevelEdge:
        return self._g.edges[source_id, target_id]["data"]

    def nodes(self) -> List[LevelNode]:
        return [d["data"] for _, d in self._g.nodes(data=True)]

    def edges(self) -> List[LevelEdge]:
        return [d["data"] for _, _, d in self._g.edges(data=True)]

    def nodes_by_type(self, node_type: NodeType) -> List[LevelNode]:
        return [n for n in self.nodes() if n.node_type == node_type]

    # ------------------------------------------------------------------
    # Graph-theory queries (delegates to NetworkX)
    # ------------------------------------------------------------------

    def is_solvable(self) -> bool:
        """True if every Start node can reach every Exit node."""
        starts = self.nodes_by_type(NodeType.START)
        exits = self.nodes_by_type(NodeType.EXIT)
        if not starts or not exits:
            return False
        return all(
            nx.has_path(self._g, s.id, e.id)
            for s in starts
            for e in exits
        )

    def remove_edge(self, source_id: str, target_id: str) -> None:
        self._g.remove_edge(source_id, target_id)

    def remove_node(self, node_id: str) -> None:
        self._g.remove_node(node_id)

    def copy(self) -> AbstractLevelGraph:
        """Shallow copy — safe as long as existing node/edge data is not mutated."""
        new_g = AbstractLevelGraph()
        new_g._g = self._g.copy()
        return new_g

    def strongly_connected_components(self) -> List[List[str]]:
        return list(nx.strongly_connected_components(self._g))

    @property
    def nx_graph(self) -> nx.DiGraph:
        """Raw NetworkX graph for external algorithm access."""
        return self._g

    def __repr__(self) -> str:
        return (
            f"AbstractLevelGraph("
            f"nodes={self._g.number_of_nodes()}, "
            f"edges={self._g.number_of_edges()})"
        )
