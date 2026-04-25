"""LLM Validator Loop for GLDtk.

Orchestrates:
  1. LLM call  →  raw JSON string
  2. JSON parse + structural validation
  3. Physics validation (Jump Oracle)
  4. Symbolic patch attempt (SymbolicPatcher)
  5. LLM repair re-prompt if patch fails
  6. Repeat up to max_retries

Physics constants default to the values embedded in the system prompt so the
Oracle and the prompt always agree.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import anthropic

from symbolic.physics import check_jump_arc, fall_time, required_launch_velocity
from symbolic.schema import (
    AbstractLevelGraph,
    EdgeType,
    LevelEdge,
    LevelNode,
    NodeType,
    Vec2,
)
from .prompt import SYSTEM_PROMPT, build_generate_messages, build_repair_messages


# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PhysicsParams:
    gravity: float = 980.0
    jump_v:  float = 600.0
    max_horizontal_speed: Optional[float] = None   # None = unconstrained


@dataclass
class ValidationError:
    code: str                              # machine-readable tag
    message: str                           # human/LLM-readable description
    node_ids: List[str] = field(default_factory=list)
    edge: Optional[Tuple[str, str]] = None # (source_id, target_id) if edge error


@dataclass
class ValidationResult:
    valid: bool
    errors: List[ValidationError]
    graph: Optional[AbstractLevelGraph]    # None only when JSON parse fails


class MaxRetriesExceeded(RuntimeError):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# JSON → graph parsing
# ─────────────────────────────────────────────────────────────────────────────

_NODE_TYPE_MAP: Dict[str, NodeType] = {
    "start":    NodeType.START,
    "exit":     NodeType.EXIT,
    "platform": NodeType.PLATFORM,
    "hazard":   NodeType.HAZARD,
}

_EDGE_TYPE_MAP: Dict[str, EdgeType] = {
    "walk": EdgeType.WALK,
    "jump": EdgeType.JUMP,
    "fall": EdgeType.FALL,
}


def _extract_json(raw: str) -> str:
    """Strip markdown fences and isolate the first JSON object."""
    # Remove ```json … ``` or ``` … ``` fences
    stripped = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
    # Find the outermost { … }
    start = stripped.find("{")
    end   = stripped.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in LLM output.")
    return stripped[start:end]


def _parse_graph(data: Dict[str, Any]) -> AbstractLevelGraph:
    """Build an AbstractLevelGraph from the parsed JSON dict."""
    graph = AbstractLevelGraph()

    for raw_node in data.get("nodes", []):
        node_type = _NODE_TYPE_MAP.get(str(raw_node["type"]).lower())
        if node_type is None:
            raise ValueError(f"Unknown node type: {raw_node['type']!r}")
        node = LevelNode(
            id=str(raw_node["id"]),
            node_type=node_type,
            position=Vec2(float(raw_node["x"]), float(raw_node["y"])),
            size=Vec2(float(raw_node.get("w", 48)), float(raw_node.get("h", 16))),
        )
        graph.add_node(node)

    for raw_edge in data.get("edges", []):
        edge_type = _EDGE_TYPE_MAP.get(str(raw_edge["type"]).lower())
        if edge_type is None:
            raise ValueError(f"Unknown edge type: {raw_edge['type']!r}")

        src = graph.get_node(str(raw_edge["from"]))
        dst = graph.get_node(str(raw_edge["to"]))
        dx  = dst.surface.x - src.surface.x
        dy  = dst.surface.y - src.surface.y     # derived, not trusted from LLM

        graph.add_edge(LevelEdge(
            source_id=src.id,
            target_id=dst.id,
            edge_type=edge_type,
            dx=dx,
            dy=dy,
            v_launch=(float(raw_edge.get("vx", 0.0)),
                      float(raw_edge.get("vy", 0.0))),
        ))

    return graph


# ─────────────────────────────────────────────────────────────────────────────
# Structural validation
# ─────────────────────────────────────────────────────────────────────────────

def _validate_structure(graph: AbstractLevelGraph) -> List[ValidationError]:
    errors: List[ValidationError] = []

    starts = graph.nodes_by_type(NodeType.START)
    exits  = graph.nodes_by_type(NodeType.EXIT)

    if len(starts) == 0:
        errors.append(ValidationError(
            code="MISSING_START",
            message="The graph has no 'Start' node. Add exactly one.",
        ))
    elif len(starts) > 1:
        errors.append(ValidationError(
            code="MULTIPLE_STARTS",
            message=f"Found {len(starts)} 'Start' nodes; exactly one is required.",
            node_ids=[n.id for n in starts],
        ))

    if len(exits) == 0:
        errors.append(ValidationError(
            code="MISSING_EXIT",
            message="The graph has no 'Exit' node. Add at least one.",
        ))

    # Connectivity: every Start must reach every Exit
    for s in starts:
        for e in exits:
            import networkx as nx
            if not nx.has_path(graph.nx_graph, s.id, e.id):
                errors.append(ValidationError(
                    code="NO_PATH",
                    message=(
                        f"No traversal path from '{s.id}' (Start) to '{e.id}' (Exit). "
                        "Add edges that connect them."
                    ),
                    node_ids=[s.id, e.id],
                ))

    return errors


# ─────────────────────────────────────────────────────────────────────────────
# Physics validation
# ─────────────────────────────────────────────────────────────────────────────

def _validate_physics(
    graph: AbstractLevelGraph,
    physics: PhysicsParams,
) -> List[ValidationError]:
    errors: List[ValidationError] = []

    for edge in graph.edges():
        src = graph.get_node(edge.source_id)
        dst = graph.get_node(edge.target_id)
        p1  = (src.surface.x, src.surface.y)
        p2  = (dst.surface.x, dst.surface.y)

        if edge.edge_type == EdgeType.JUMP:
            if edge.dy < -1e-6:          # target below source → should be FALL
                errors.append(ValidationError(
                    code="JUMP_GOES_DOWN",
                    message=(
                        f"Edge '{edge.source_id}'→'{edge.target_id}' is typed 'Jump' "
                        f"but target is {abs(edge.dy):.0f} px BELOW source (Δy={edge.dy:.0f}). "
                        "Change the edge type to 'Fall'."
                    ),
                    edge=(edge.source_id, edge.target_id),
                ))
                continue

            if not check_jump_arc(p1, p2, physics.gravity, physics.jump_v,
                                  physics.max_horizontal_speed):
                apex = physics.jump_v ** 2 / (2 * physics.gravity)
                errors.append(ValidationError(
                    code="IMPOSSIBLE_JUMP",
                    message=(
                        f"Edge '{edge.source_id}'→'{edge.target_id}': "
                        f"Jump is physically impossible. "
                        f"Δy={edge.dy:.0f} px exceeds the max apex of {apex:.0f} px "
                        f"(gravity={physics.gravity}, jump_v={physics.jump_v}). "
                        "Reduce the vertical gap or add an intermediate Platform node."
                    ),
                    edge=(edge.source_id, edge.target_id),
                ))

        elif edge.edge_type == EdgeType.FALL:
            if edge.dy > 1e-6:           # target above source → should be JUMP
                errors.append(ValidationError(
                    code="FALL_GOES_UP",
                    message=(
                        f"Edge '{edge.source_id}'→'{edge.target_id}' is typed 'Fall' "
                        f"but target is {edge.dy:.0f} px ABOVE source. "
                        "Change the edge type to 'Jump'."
                    ),
                    edge=(edge.source_id, edge.target_id),
                ))

        elif edge.edge_type == EdgeType.WALK:
            if abs(edge.dy) > 8.0:
                errors.append(ValidationError(
                    code="WALK_HEIGHT_MISMATCH",
                    message=(
                        f"Edge '{edge.source_id}'→'{edge.target_id}' is typed 'Walk' "
                        f"but nodes differ by {abs(edge.dy):.0f} px vertically "
                        "(Walk requires |Δy| ≤ 8 px). "
                        "Use 'Jump' or 'Fall' instead, or align the platform heights."
                    ),
                    edge=(edge.source_id, edge.target_id),
                ))

    return errors


# ─────────────────────────────────────────────────────────────────────────────
# Combined validation entry point
# ─────────────────────────────────────────────────────────────────────────────

def validate_graph(
    graph: AbstractLevelGraph,
    physics: PhysicsParams,
) -> ValidationResult:
    errors = _validate_structure(graph) + _validate_physics(graph, physics)
    return ValidationResult(valid=not errors, errors=errors, graph=graph)


def parse_and_validate(
    raw: str,
    physics: PhysicsParams,
) -> ValidationResult:
    """Parse raw LLM output and run full validation. Never raises."""
    try:
        json_str = _extract_json(raw)
        data     = json.loads(json_str)
        graph    = _parse_graph(data)
    except Exception as exc:
        return ValidationResult(
            valid=False,
            errors=[ValidationError(
                code="INVALID_JSON",
                message=(
                    f"Could not parse your output as a valid level graph JSON. "
                    f"Parser error: {exc}. "
                    "Output ONLY a JSON object matching the schema."
                ),
            )],
            graph=None,
        )

    return validate_graph(graph, physics)


# ─────────────────────────────────────────────────────────────────────────────
# LLM Controller
# ─────────────────────────────────────────────────────────────────────────────

class LLMController:
    """Generate→Validate→Patch→Repair loop.

    Usage
    -----
    controller = LLMController(physics=PhysicsParams())
    graph = controller.generate("A volcano level with lava pits and crumbling bridges")
    """

    def __init__(
        self,
        physics: Optional[PhysicsParams] = None,
        model: str = "claude-sonnet-4-6",
        max_retries: int = 3,
        api_key: Optional[str] = None,
    ) -> None:
        self._physics    = physics or PhysicsParams()
        self._model      = model
        self._max_retries = max_retries
        self._client     = anthropic.Anthropic(api_key=api_key)

        # Import here to avoid circular imports at module load time
        from .patcher import SymbolicPatcher
        self._patcher = SymbolicPatcher(self._physics)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, description: str) -> AbstractLevelGraph:
        """Generate a valid, physics-verified level graph from *description*.

        Raises
        ------
        MaxRetriesExceeded
            If no valid graph could be produced within *max_retries* attempts.
        """
        messages = build_generate_messages(description)
        last_raw = ""

        for attempt in range(self._max_retries):
            last_raw = self._call_llm(messages)
            result   = parse_and_validate(last_raw, self._physics)

            if result.valid:
                return result.graph  # type: ignore[return-value]

            # ── Symbolic patch: try to fix without another LLM call ──
            if result.graph is not None:
                patch = self._patcher.patch(result.graph, result.errors)
                if patch.success:
                    re_val = validate_graph(patch.graph, self._physics)
                    if re_val.valid:
                        return patch.graph

            # ── LLM repair re-prompt ──
            if attempt < self._max_retries - 1:
                error_msgs = [e.message for e in result.errors]
                messages   = build_repair_messages(
                    description, last_raw, error_msgs, messages
                )

        codes = [e.code for e in parse_and_validate(last_raw, self._physics).errors]
        raise MaxRetriesExceeded(
            f"Failed to produce a valid graph after {self._max_retries} attempts. "
            f"Remaining errors: {codes}"
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _call_llm(self, messages: List[Dict[str, Any]]) -> str:
        """Call the Anthropic API with prompt-cached system prompt."""
        response = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # Cache the large, immutable system prompt across retries
                    # and across calls with the same description batch.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
        )
        return response.content[0].text
