"""LLM Validator Loop for GLDtk.

Orchestrates:
  1. Provider call (schema-enforced structured output)  →  raw JSON string
  2. JSON parse + LevelSpec (pydantic) validation
  3. Spec semantics (ids, sections, anchors, entity refs, bounds, terminals)
  4. Graph structure + physics validation (Jump Oracle) on ``spec.to_graph()``
  5. Symbolic patch attempt (SymbolicPatcher + terminal-entity patch)
  6. LLM repair re-prompt if patch fails
  7. Repeat up to max_retries

The controller is provider-agnostic: it talks to an ``LLMProvider``
(``llm/providers/``) built by the registry or injected directly.

Physics constants default to the values embedded in the system prompt so the
Oracle and the prompt always agree.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import ValidationError as PydanticValidationError

from spec.level_spec import TILE_SIZE, LevelSpec, parse_graph
from symbolic.physics import check_jump_arc
from symbolic.schema import AbstractLevelGraph, EdgeType, NodeType

from .prompt import SYSTEM_PROMPT, build_generate_messages, build_repair_messages
from .providers import (
    _DEFAULT_PROVIDER_MODELS,
    LLMProvider,
    LLMProviderConfig,
    make_provider,
    make_provider_from_config,
)


# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PhysicsParams:
    gravity: float = 980.0
    jump_v: float = 600.0
    max_horizontal_speed: Optional[float] = None  # None = unconstrained


@dataclass
class ValidationError:
    code: str  # machine-readable tag
    message: str  # human/LLM-readable description
    node_ids: List[str] = field(default_factory=list)
    edge: Optional[Tuple[str, str]] = None  # (source_id, target_id) if edge error


@dataclass
class ValidationResult:
    valid: bool
    errors: List[ValidationError]
    graph: Optional[AbstractLevelGraph]  # None when parsing / graph building fails
    spec: Optional[LevelSpec] = None  # None when the output is not a LevelSpec


@dataclass
class GenerationStats:
    """Bookkeeping for one ``generate_spec`` run (Part 10 evaluation)."""

    attempts: int = 0  # LLM calls made
    patched: bool = False  # True if the returned spec came from the patcher
    patches_applied: List[str] = field(default_factory=list)
    error_codes_per_attempt: List[List[str]] = field(default_factory=list)
    final_error_codes: List[str] = field(default_factory=list)
    success: bool = False


class MaxRetriesExceeded(RuntimeError):
    def __init__(self, message: str, stats: Optional[GenerationStats] = None) -> None:
        super().__init__(message)
        self.stats = stats


# ─────────────────────────────────────────────────────────────────────────────
# Raw output → JSON
# ─────────────────────────────────────────────────────────────────────────────


def _extract_json(raw: str) -> str:
    """Strip markdown fences and isolate the first JSON object."""
    # Remove ```json … ``` or ``` … ``` fences
    stripped = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
    # Find the outermost { … }
    start = stripped.find("{")
    end = stripped.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in LLM output.")
    return stripped[start:end]


# ─────────────────────────────────────────────────────────────────────────────
# Structural validation
# ─────────────────────────────────────────────────────────────────────────────


def _validate_structure(graph: AbstractLevelGraph) -> List[ValidationError]:
    errors: List[ValidationError] = []

    starts = graph.nodes_by_type(NodeType.START)
    exits = graph.nodes_by_type(NodeType.EXIT)

    if len(starts) == 0:
        errors.append(
            ValidationError(
                code="MISSING_START",
                message="The graph has no 'Start' node. Add exactly one.",
            )
        )
    elif len(starts) > 1:
        errors.append(
            ValidationError(
                code="MULTIPLE_STARTS",
                message=f"Found {len(starts)} 'Start' nodes; exactly one is required.",
                node_ids=[n.id for n in starts],
            )
        )

    if len(exits) == 0:
        errors.append(
            ValidationError(
                code="MISSING_EXIT",
                message="The graph has no 'Exit' node. Add at least one.",
            )
        )

    # Connectivity: every Start must reach every Exit
    for s in starts:
        for e in exits:
            import networkx as nx

            if not nx.has_path(graph.nx_graph, s.id, e.id):
                errors.append(
                    ValidationError(
                        code="NO_PATH",
                        message=(
                            f"No traversal path from '{s.id}' (Start) to '{e.id}' (Exit). "
                            "Add edges that connect them."
                        ),
                        node_ids=[s.id, e.id],
                    )
                )

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
        p1 = (src.surface.x, src.surface.y)
        p2 = (dst.surface.x, dst.surface.y)

        if edge.edge_type == EdgeType.JUMP:
            if edge.dy < -1e-6:  # target below source → should be FALL
                errors.append(
                    ValidationError(
                        code="JUMP_GOES_DOWN",
                        message=(
                            f"Edge '{edge.source_id}'→'{edge.target_id}' is typed 'Jump' "
                            f"but target is {abs(edge.dy):.0f} px BELOW source (Δy={edge.dy:.0f}). "
                            "Change the edge type to 'Fall'."
                        ),
                        edge=(edge.source_id, edge.target_id),
                    )
                )
                continue

            if not check_jump_arc(
                p1, p2, physics.gravity, physics.jump_v, physics.max_horizontal_speed
            ):
                apex = physics.jump_v**2 / (2 * physics.gravity)
                errors.append(
                    ValidationError(
                        code="IMPOSSIBLE_JUMP",
                        message=(
                            f"Edge '{edge.source_id}'→'{edge.target_id}': "
                            f"Jump is physically impossible. "
                            f"Δy={edge.dy:.0f} px exceeds the max apex of {apex:.0f} px "
                            f"(gravity={physics.gravity}, jump_v={physics.jump_v}). "
                            "Reduce the vertical gap or add an intermediate Platform node."
                        ),
                        edge=(edge.source_id, edge.target_id),
                    )
                )

        elif edge.edge_type == EdgeType.FALL:
            if edge.dy > 1e-6:  # target above source → should be JUMP
                errors.append(
                    ValidationError(
                        code="FALL_GOES_UP",
                        message=(
                            f"Edge '{edge.source_id}'→'{edge.target_id}' is typed 'Fall' "
                            f"but target is {edge.dy:.0f} px ABOVE source. "
                            "Change the edge type to 'Jump'."
                        ),
                        edge=(edge.source_id, edge.target_id),
                    )
                )

        elif edge.edge_type == EdgeType.WALK:
            if abs(edge.dy) > 8.0:
                errors.append(
                    ValidationError(
                        code="WALK_HEIGHT_MISMATCH",
                        message=(
                            f"Edge '{edge.source_id}'→'{edge.target_id}' is typed 'Walk' "
                            f"but nodes differ by {abs(edge.dy):.0f} px vertically "
                            "(Walk requires |Δy| ≤ 8 px). "
                            "Use 'Jump' or 'Fall' instead, or align the platform heights."
                        ),
                        edge=(edge.source_id, edge.target_id),
                    )
                )

    return errors


# ─────────────────────────────────────────────────────────────────────────────
# Combined graph validation entry point
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
    """Parse a bare ``{nodes, edges}`` graph JSON (v1 contract). Never raises."""
    try:
        graph = parse_graph(json.loads(_extract_json(raw)))
    except Exception as exc:
        return ValidationResult(
            valid=False,
            errors=[
                ValidationError(
                    code="INVALID_JSON",
                    message=(
                        f"Could not parse your output as a valid level graph JSON. "
                        f"Parser error: {exc}. "
                        "Output ONLY a JSON object matching the schema."
                    ),
                )
            ],
            graph=None,
        )

    return validate_graph(graph, physics)


# ─────────────────────────────────────────────────────────────────────────────
# LevelSpec semantic validation
# ─────────────────────────────────────────────────────────────────────────────


def _duplicates(ids: List[str]) -> List[str]:
    return sorted(i for i, n in Counter(ids).items() if n > 1)


def _validate_spec_semantics(spec: LevelSpec) -> List[ValidationError]:
    errors: List[ValidationError] = []

    # ── unique ids ──
    for label, ids in (
        ("node", [n.id for n in spec.nodes]),
        ("entity", [e.id for e in spec.entities]),
        ("section", [s.id for s in spec.sections]),
        ("requirement", [r.id for r in spec.requirements]),
    ):
        dups = _duplicates(ids)
        if dups:
            errors.append(
                ValidationError(
                    code="DUPLICATE_ID",
                    message=(
                        f"Duplicate {label} id(s): {dups}. "
                        f"Every {label} id must be unique — rename the duplicates."
                    ),
                    node_ids=dups if label == "node" else [],
                )
            )

    section_ids = {s.id for s in spec.sections}
    nodes = {n.id: n for n in spec.nodes}
    entity_ids = {e.id for e in spec.entities}

    # ── nodes: sections + bounds ──
    max_x = spec.size.width_tiles * TILE_SIZE
    max_y = spec.size.height_tiles * TILE_SIZE
    for n in spec.nodes:
        if n.section not in section_ids:
            errors.append(
                ValidationError(
                    code="UNKNOWN_SECTION",
                    message=(
                        f"Node '{n.id}' references section '{n.section}', which does not "
                        f"exist. Use one of the section ids: {sorted(section_ids)}."
                    ),
                    node_ids=[n.id],
                )
            )
        if not (0 <= n.x <= max_x and 0 <= n.y <= max_y):
            errors.append(
                ValidationError(
                    code="NODE_OUT_OF_BOUNDS",
                    message=(
                        f"Node '{n.id}' at (x={n.x:.0f}, y={n.y:.0f}) is outside the level "
                        f"bounds x ∈ [0, {max_x}], y ∈ [0, {max_y}] "
                        f"({spec.size.width_tiles}×{spec.size.height_tiles} tiles of "
                        f"{TILE_SIZE} px). Move it inside, or enlarge 'size'."
                    ),
                    node_ids=[n.id],
                )
            )

    # ── edges: endpoints exist ──
    for e in spec.edges:
        missing = [nid for nid in (e.from_, e.to) if nid not in nodes]
        if missing:
            errors.append(
                ValidationError(
                    code="UNKNOWN_EDGE_NODE",
                    message=(
                        f"Edge '{e.from_}'→'{e.to}' references unknown node id(s) "
                        f"{missing}. Edges may only connect existing node ids."
                    ),
                    edge=(e.from_, e.to),
                )
            )

    # ── entities: sections, anchors, references ──
    for ent in spec.entities:
        if ent.section not in section_ids:
            errors.append(
                ValidationError(
                    code="UNKNOWN_SECTION",
                    message=(
                        f"Entity '{ent.id}' references section '{ent.section}', which does "
                        f"not exist. Use one of the section ids: {sorted(section_ids)}."
                    ),
                )
            )
        anchor = nodes.get(ent.anchor)
        if anchor is None:
            errors.append(
                ValidationError(
                    code="UNKNOWN_ANCHOR",
                    message=(
                        f"Entity '{ent.id}' is anchored on '{ent.anchor}', which is not a "
                        "node id. 'anchor' must be the id of the node the entity stands on."
                    ),
                )
            )
        elif anchor.type == "Hazard":
            errors.append(
                ValidationError(
                    code="ANCHOR_ON_HAZARD",
                    message=(
                        f"Entity '{ent.id}' is anchored on Hazard node '{ent.anchor}'. "
                        "Entities must stand on a Start, Exit or Platform node."
                    ),
                    node_ids=[ent.anchor],
                )
            )
        for ref_field in ("linked_to", "opens"):
            ref = getattr(ent, ref_field)
            if ref and ref not in entity_ids:
                errors.append(
                    ValidationError(
                        code="UNKNOWN_ENTITY_REF",
                        message=(
                            f"Entity '{ent.id}' has {ref_field}='{ref}', which is not an "
                            "entity id. Reference an existing entity or use null."
                        ),
                    )
                )

    # ── terminal entities ──
    start_nodes = {n.id for n in spec.nodes if n.type == "Start"}
    exit_nodes = {n.id for n in spec.nodes if n.type == "Exit"}
    start_ents = [e for e in spec.entities if e.kind == "Start"]
    exit_ents = [e for e in spec.entities if e.kind == "Exit"]
    if len(start_ents) != 1 or start_ents[0].anchor not in start_nodes:
        errors.append(
            ValidationError(
                code="START_ENTITY_MISMATCH",
                message=(
                    f"Found {len(start_ents)} 'Start' entit(y/ies) "
                    f"(anchors: {[e.anchor for e in start_ents]}). There must be exactly "
                    "one entity of kind 'Start' (the player spawn), anchored on the "
                    f"'Start' node {sorted(start_nodes)}."
                ),
            )
        )
    if not any(e.anchor in exit_nodes for e in exit_ents):
        errors.append(
            ValidationError(
                code="EXIT_ENTITY_MISMATCH",
                message=(
                    "There must be at least one entity of kind 'Exit' anchored on an "
                    f"'Exit' node {sorted(exit_nodes)}; found anchors "
                    f"{[e.anchor for e in exit_ents]}."
                ),
            )
        )

    # ── requirements ──
    if not spec.requirements:
        errors.append(
            ValidationError(
                code="NO_REQUIREMENTS",
                message=(
                    "'requirements' is empty. List every atomic, checkable claim the "
                    "prompt makes (size, sections, layout, entities, abilities, style)."
                ),
            )
        )

    return errors


def validate_spec(spec: LevelSpec, physics: PhysicsParams) -> ValidationResult:
    """Spec semantics + structural + physics checks on ``spec.to_graph()``."""
    errors = _validate_spec_semantics(spec)
    if any(e.code == "UNKNOWN_EDGE_NODE" for e in errors):
        return ValidationResult(valid=False, errors=errors, graph=None, spec=spec)
    try:
        graph = spec.to_graph()
    except Exception as exc:
        errors.append(
            ValidationError(
                code="INVALID_SPEC",
                message=f"Could not build the platform graph from 'nodes'/'edges': {exc}.",
            )
        )
        return ValidationResult(valid=False, errors=errors, graph=None, spec=spec)
    errors += validate_graph(graph, physics).errors
    return ValidationResult(valid=not errors, errors=errors, graph=graph, spec=spec)


def parse_spec(raw: str) -> Tuple[Optional[LevelSpec], List[ValidationError]]:
    """Parse raw LLM output into a LevelSpec. Never raises."""
    try:
        data = json.loads(_extract_json(raw))
    except Exception as exc:
        return None, [
            ValidationError(
                code="INVALID_JSON",
                message=(
                    f"Could not parse your output as JSON. Parser error: {exc}. "
                    "Output ONLY a JSON object matching the LevelSpec schema."
                ),
            )
        ]
    try:
        return LevelSpec.model_validate(data), []
    except PydanticValidationError as exc:
        return None, [
            ValidationError(
                code="INVALID_SPEC",
                message=(
                    "Your JSON does not match the LevelSpec schema:\n"
                    f"{exc}\nFix every listed field."
                ),
            )
        ]


def parse_and_validate_spec(raw: str, physics: PhysicsParams) -> ValidationResult:
    """Parse raw LLM output and run full LevelSpec validation. Never raises."""
    spec, errors = parse_spec(raw)
    if spec is None:
        return ValidationResult(valid=False, errors=errors, graph=None, spec=None)
    return validate_spec(spec, physics)


# ─────────────────────────────────────────────────────────────────────────────
# LLM Controller
# ─────────────────────────────────────────────────────────────────────────────


class LLMController:
    """Generate→Validate→Patch→Repair loop (provider-agnostic).

    Usage
    -----
    controller = LLMController(provider="gemini")
    spec = controller.generate_spec("A volcano level with lava pits and a boss")
    controller.stats  # attempts, patches_applied, final_error_codes, ...
    """

    def __init__(
        self,
        physics: Optional[PhysicsParams] = None,
        model: Optional[str] = None,
        max_retries: int = 3,
        api_key: Optional[str] = None,
        provider: Union[str, LLMProvider] = "gemini",
        base_url: Optional[str] = None,
        timeout_seconds: float = 120.0,
        provider_config: Optional[LLMProviderConfig] = None,
    ) -> None:
        self._physics = physics or PhysicsParams()
        self._max_retries = max_retries

        if provider_config is not None:
            self._provider: LLMProvider = make_provider_from_config(provider_config)
        elif isinstance(provider, str):
            self._provider = make_provider(
                provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
                timeout=timeout_seconds,
            )
        else:
            self._provider = provider

        self._schema = LevelSpec.json_schema()
        self.stats = GenerationStats()

        # Import here to avoid circular imports at module load time
        from .patcher import SymbolicPatcher

        self._patcher = SymbolicPatcher(self._physics)

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_spec(
        self,
        description: str,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> LevelSpec:
        """Generate a valid, physics-verified LevelSpec from *description*.

        Parameters
        ----------
        description : natural-language level description.
        constraints : optional structured overrides (theme, enemy_types,
                      platform_count, difficulty, width_tiles, height_tiles)
                      injected into the prompt.

        Raises
        ------
        MaxRetriesExceeded
            If no valid spec could be produced within *max_retries* attempts.
            ``exc.stats`` / ``self.stats`` hold the run's bookkeeping.
        """
        self.stats = stats = GenerationStats()
        messages = build_generate_messages(description, constraints)
        result: Optional[ValidationResult] = None

        for attempt in range(self._max_retries):
            raw = self._provider.complete_json(SYSTEM_PROMPT, messages, self._schema)
            stats.attempts += 1
            result = parse_and_validate_spec(raw, self._physics)
            stats.error_codes_per_attempt.append([e.code for e in result.errors])

            if result.valid:
                stats.success = True
                return result.spec  # type: ignore[return-value]

            # ── Symbolic patch: try to fix without another LLM call ──
            if result.spec is not None:
                patched = self._patch_spec(result)
                if patched is not None:
                    spec, applied = patched
                    stats.patched = True
                    stats.patches_applied = applied
                    stats.success = True
                    return spec

            # ── LLM repair re-prompt ──
            if attempt < self._max_retries - 1:
                error_msgs = [e.message for e in result.errors]
                messages = build_repair_messages(description, raw, error_msgs, messages)

        stats.final_error_codes = [e.code for e in result.errors] if result else []
        raise MaxRetriesExceeded(
            f"Failed to produce a valid level spec after {self._max_retries} attempts. "
            f"Remaining errors: {stats.final_error_codes}",
            stats=stats,
        )

    def generate(
        self,
        description: str,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> AbstractLevelGraph:
        """Compatibility wrapper: ``generate_spec(...).to_graph()``."""
        return self.generate_spec(description, constraints).to_graph()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _patch_spec(
        self, result: ValidationResult
    ) -> Optional[Tuple[LevelSpec, List[str]]]:
        """Deterministic repair. Returns ``(spec, patch names)`` or None."""
        from .patcher import TerminalEntityPatch

        spec = result.spec
        assert spec is not None
        applied: List[str] = []

        # 1. Graph-level patches (physics / structure) on the platform graph.
        if result.graph is not None:
            graph_errors = validate_graph(result.graph, self._physics).errors
            if graph_errors:
                patch = self._patcher.patch(result.graph, graph_errors)
                if not patch.success:
                    return None
                spec = spec.with_graph(patch.graph)
                applied += patch.applied

        # 2. Spec-level patch: missing / mis-anchored Start and Exit entities.
        errors = validate_spec(spec, self._physics).errors
        entity_patch = TerminalEntityPatch()
        if entity_patch.applies_to(errors):
            spec = entity_patch.apply_to_spec(spec)
            applied.append(entity_patch.name)

        final = validate_spec(spec, self._physics)
        if not final.valid or not applied:
            return None
        return spec, applied
