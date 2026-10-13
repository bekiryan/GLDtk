"""LevelSpec — the full LLM output contract for GLDtk v2.

The LLM no longer emits a bare ``{nodes, edges}`` graph.  It emits a
``LevelSpec``: the whole level as described by the prompt — size, named
sections, the platform graph, every entity the prompt asked for, and the list
of atomic requirements extracted from the prompt.

Downstream stages (layout, aesthetic, export) still consume an
``AbstractLevelGraph``; :meth:`LevelSpec.to_graph` produces it.

Coordinates follow the v1 contract: pixels, Y-up, ``x``/``y`` are the
top-left corner of a platform (``y`` = top surface height).

Entities are deliberately a *flat* model (one ``kind`` enum plus optional
per-kind fields) rather than a discriminated union: ``anyOf`` support differs
across structured-output providers, a flat object is portable.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from symbolic.schema import (
    AbstractLevelGraph,
    EdgeType,
    LevelEdge,
    LevelNode,
    NodeType,
    Vec2,
)

TILE_SIZE = 16  # px — matches the physics contract in llm/prompt.py


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations (string literals so the JSON schema carries plain enums)
# ─────────────────────────────────────────────────────────────────────────────

NodeKind = Literal["Start", "Exit", "Platform", "Hazard"]
EdgeKind = Literal["Walk", "Jump", "Fall"]
AbilityName = Literal["double_jump", "dash", "wall_jump"]
Difficulty = Literal["easy", "medium", "hard"]
EntityKind = Literal[
    "Start", "Exit", "Enemy", "Pickup", "Weapon", "Interactive", "NPC", "Checkpoint", "Key"
]
Facing = Literal["left", "right"]
EnemyArchetype = Literal["walker", "flyer", "jumper", "turret", "boss"]
EnemyAttack = Literal["melee", "ranged", "none"]
PickupKind = Literal["coin", "gem", "health", "ammo", "powerup"]
InteractiveKind = Literal["chest", "door", "lever", "switch", "sign"]
RequirementCategory = Literal[
    "size", "section", "layout", "entity", "ability", "style", "other"
]
CheckType = Literal[
    "entity_count", "start_item", "section", "ability", "size", "gravity", "none"
]


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


class _SpecModel(BaseModel):
    # Lenient on input (unknown keys from the LLM are dropped); the emitted
    # JSON schema is strict (additionalProperties: false) — see json_schema().
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class LevelSize(_SpecModel):
    width_tiles: int = Field(..., ge=30, le=300, description="Level width in 16 px tiles.")
    height_tiles: int = Field(..., ge=15, le=100, description="Level height in 16 px tiles.")


class Section(_SpecModel):
    id: str = Field(..., description="Unique section id, no spaces.")
    name: str = Field(..., description="Human-readable section name.")
    theme: str = Field(..., description='Free-text visual theme, e.g. "dungeon", "ice cave".')
    difficulty: Difficulty
    gravity_scale: float = Field(1.0, ge=0.25, le=2.0, description="1.0 = normal gravity.")
    description: str = Field(..., description="What happens in this section.")
    order: int = Field(..., ge=0, description="Left-to-right position of the section, 0-based.")


class SpecNode(_SpecModel):
    id: str = Field(..., description="Unique node id, no spaces.")
    type: NodeKind
    x: float = Field(..., description="Left edge in pixels.")
    y: float = Field(..., description="Top-surface height in pixels (Y-up).")
    w: float = Field(48.0, description="Width in pixels.")
    h: float = Field(16.0, description="Height in pixels (always 16).")
    section: str = Field(..., description="Section.id this node belongs to.")


class SpecEdge(_SpecModel):
    from_: str = Field(..., alias="from", description="Source node id.")
    to: str = Field(..., description="Target node id.")
    type: EdgeKind
    vx: float = Field(0.0, description="Horizontal launch speed in px/s (signed).")
    vy: float = Field(0.0, description="Upward launch speed in px/s (0 for Walk/Fall).")
    ability: Optional[AbilityName] = Field(
        None, description="Ability a Jump needs (from the game profile); null = plain jump."
    )


class SpecEntity(_SpecModel):
    id: str = Field(..., description="Unique entity id, no spaces.")
    kind: EntityKind
    anchor: str = Field(..., description="Id of the non-Hazard node the entity stands on.")
    section: str = Field(..., description="Section.id this entity belongs to.")
    description: str = Field("", description="Original wording from the prompt.")
    sprite: str = Field("", description='Free-text sprite hint, may be "".')

    # ── Start ──
    facing: Optional[Facing] = None
    starting_items: Optional[List[str]] = None
    starting_weapon: Optional[str] = None
    # ── Exit ──
    requires_key: Optional[bool] = None
    # ── Enemy ──
    archetype: Optional[EnemyArchetype] = None
    health: Optional[int] = None
    damage: Optional[int] = None
    patrol_range: Optional[int] = Field(None, description="Patrol range in tiles.")
    attack: Optional[EnemyAttack] = None
    # ── Pickup ──
    pickup_kind: Optional[PickupKind] = None
    value: Optional[int] = None
    # ── Weapon ──
    weapon_kind: Optional[str] = Field(None, description="sword, bow, crossbow, gun, staff, ...")
    range: Optional[int] = Field(None, description="Weapon range in tiles.")
    # ── Interactive ──
    interactive_kind: Optional[InteractiveKind] = None
    linked_to: Optional[str] = Field(None, description="Entity id this interactive controls.")
    contents: Optional[str] = None
    # ── NPC ──
    name: Optional[str] = None
    dialogue: Optional[str] = None
    # ── Key ──
    opens: Optional[str] = Field(None, description="Entity id this key opens.")


class RequirementCheck(_SpecModel):
    """Machine-checkable form of a requirement (``fidelity/`` runs it).

    Flat on purpose (like ``SpecEntity``): ``type`` says which fields matter,
    every other field is null.

    * ``entity_count`` — at least ``min_count`` (null = 1) entities of
      ``entity_kind``, of ``subkind`` when set (the archetype, pickup_kind,
      interactive_kind or weapon_kind value), in ``section`` (null = anywhere).
    * ``start_item``   — the player starts with ``subkind`` (the Start
      entity's starting_weapon or one of its starting_items).
    * ``section``      — a section with id ``section`` exists.
    * ``ability``      — the Game Profile has ``ability`` (free text, so an
      unsupported ability such as "fly" can still be stated).
    * ``size``         — the level is at least ``min_width_tiles`` ×
      ``min_height_tiles`` (either may be null).
    * ``gravity``      — ``section`` (null = any section) has gravity scale
      ``gravity_scale``.
    * ``none``         — no structured check (style, other); reported as
      unchecked.
    """

    type: CheckType
    entity_kind: Optional[EntityKind] = None
    subkind: Optional[str] = Field(
        None, description="archetype / pickup_kind / interactive_kind / weapon_kind, or the start item."
    )
    min_count: Optional[int] = Field(None, ge=0, description="Minimum entity count (null = 1).")
    section: Optional[str] = Field(None, description="Section.id it applies to; null = anywhere.")
    ability: Optional[str] = Field(
        None, description='"double_jump", "dash", "wall_jump", or the ability named in the prompt.'
    )
    min_width_tiles: Optional[int] = None
    min_height_tiles: Optional[int] = None
    gravity_scale: Optional[float] = None


class Requirement(_SpecModel):
    id: str = Field(..., description='"R1", "R2", ...')
    text: str = Field(..., description="One atomic, checkable statement taken from the prompt.")
    category: RequirementCategory
    check: Optional[RequirementCheck] = Field(
        None, description="Structured check; null when the requirement cannot be checked."
    )


class LevelSpec(_SpecModel):
    title: str
    size: LevelSize
    sections: List[Section] = Field(..., min_length=1)
    nodes: List[SpecNode]
    edges: List[SpecEdge]
    entities: List[SpecEntity]
    requirements: List[Requirement]

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    @classmethod
    def json_schema(cls) -> Dict[str, Any]:
        """Canonical, provider-portable JSON schema.

        * every ``$ref`` is inlined and ``$defs`` removed;
        * optionals are expressed as ``"type": [T, "null"]``;
        * every property is listed in ``required`` (nullable when optional) and
          ``additionalProperties`` is false — the shape OpenAI strict mode needs
          and that Gemini/Anthropic/Ollama accept;
        * ``title`` / ``default`` noise is dropped.

        Per-provider adaptation lives in the provider modules.
        """
        raw = cls.model_json_schema(by_alias=True)
        defs = raw.pop("$defs", {})
        return _normalise_schema(_inline_refs(raw, defs))

    # ------------------------------------------------------------------
    # Graph conversion
    # ------------------------------------------------------------------

    def to_graph(self) -> AbstractLevelGraph:
        """Build the AbstractLevelGraph (Δx/Δy re-derived from positions).

        Each edge carries ``metadata["ability"]`` (when set) and
        ``metadata["gravity_scale"]`` — the gravity scale of the *source*
        node's section — so physics validation does not need the spec.

        Raises ``ValueError`` / ``KeyError`` on dangling edge endpoints;
        ``validate_spec`` reports those before calling this.
        """
        scales = {s.id: s.gravity_scale for s in self.sections}
        node_scale = {n.id: scales.get(n.section, 1.0) for n in self.nodes}
        edges = []
        for e in self.edges:
            raw = e.model_dump(by_alias=True)
            raw["gravity_scale"] = node_scale.get(e.from_, 1.0)
            edges.append(raw)
        return parse_graph(
            {"nodes": [n.model_dump() for n in self.nodes], "edges": edges}
        )

    def with_graph(self, graph: AbstractLevelGraph) -> "LevelSpec":
        """Return a copy of this spec whose nodes/edges come from *graph*.

        Nodes the patcher added (not present in the spec) inherit the section
        of the source node of an incoming edge; isolated additions fall back to
        the first section (for a Start) or the last section (anything else).
        """
        known_sections = {n.id: n.section for n in self.nodes}
        ordered = sorted(self.sections, key=lambda s: s.order)

        def section_of(node: LevelNode, seen: frozenset = frozenset()) -> str:
            sec = node.metadata.get("section") or known_sections.get(node.id)
            if sec:
                return str(sec)
            for pred in graph.nx_graph.predecessors(node.id):
                if pred in seen:
                    continue
                return section_of(graph.get_node(pred), seen | {node.id})
            fallback = ordered[0] if node.node_type == NodeType.START else ordered[-1]
            return fallback.id

        nodes = [
            SpecNode(
                id=n.id,
                type=_NODE_TYPE_NAMES[n.node_type],
                x=n.position.x,
                y=n.position.y,
                w=n.size.x,
                h=n.size.y,
                section=section_of(n),
            )
            for n in graph.nodes()
        ]
        edges = [
            SpecEdge(
                from_=e.source_id,
                to=e.target_id,
                type=_EDGE_TYPE_NAMES[e.edge_type],
                vx=float(e.v_launch[0]),
                vy=float(e.v_launch[1]),
                ability=e.metadata.get("ability") or None,
            )
            for e in graph.edges()
        ]
        return self.model_copy(update={"nodes": nodes, "edges": edges}, deep=True)


# ─────────────────────────────────────────────────────────────────────────────
# JSON → graph parsing  (single implementation, shared with llm.validator)
# ─────────────────────────────────────────────────────────────────────────────

_NODE_TYPE_MAP: Dict[str, NodeType] = {
    "start": NodeType.START,
    "exit": NodeType.EXIT,
    "platform": NodeType.PLATFORM,
    "hazard": NodeType.HAZARD,
}

_EDGE_TYPE_MAP: Dict[str, EdgeType] = {
    "walk": EdgeType.WALK,
    "jump": EdgeType.JUMP,
    "fall": EdgeType.FALL,
}

_NODE_TYPE_NAMES: Dict[NodeType, str] = {
    NodeType.START: "Start",
    NodeType.EXIT: "Exit",
    NodeType.PLATFORM: "Platform",
    NodeType.HAZARD: "Hazard",
}

_EDGE_TYPE_NAMES: Dict[EdgeType, str] = {
    EdgeType.WALK: "Walk",
    EdgeType.JUMP: "Jump",
    EdgeType.FALL: "Fall",
}


def parse_graph(data: Dict[str, Any]) -> AbstractLevelGraph:
    """Build an AbstractLevelGraph from a ``{nodes, edges}`` dict.

    Δx/Δy are always re-derived from node surfaces — never trusted from the
    LLM.  A node's ``section`` (if present) is stored in ``metadata``; an
    edge's ``ability`` and ``gravity_scale`` (if present) in its ``metadata``.
    """
    graph = AbstractLevelGraph()

    for raw_node in data.get("nodes", []):
        node_type = _NODE_TYPE_MAP.get(str(raw_node["type"]).lower())
        if node_type is None:
            raise ValueError(f"Unknown node type: {raw_node['type']!r}")
        metadata: Dict[str, object] = {}
        if raw_node.get("section"):
            metadata["section"] = str(raw_node["section"])
        node = LevelNode(
            id=str(raw_node["id"]),
            node_type=node_type,
            position=Vec2(float(raw_node["x"]), float(raw_node["y"])),
            size=Vec2(float(raw_node.get("w", 48)), float(raw_node.get("h", 16))),
            metadata=metadata,
        )
        graph.add_node(node)

    for raw_edge in data.get("edges", []):
        edge_type = _EDGE_TYPE_MAP.get(str(raw_edge["type"]).lower())
        if edge_type is None:
            raise ValueError(f"Unknown edge type: {raw_edge['type']!r}")

        src = graph.get_node(str(raw_edge["from"]))
        dst = graph.get_node(str(raw_edge["to"]))
        dx = dst.surface.x - src.surface.x
        dy = dst.surface.y - src.surface.y  # derived, not trusted from LLM

        edge_meta: Dict[str, object] = {}
        if raw_edge.get("ability"):
            edge_meta["ability"] = str(raw_edge["ability"])
        if raw_edge.get("gravity_scale") is not None:
            edge_meta["gravity_scale"] = float(raw_edge["gravity_scale"])

        graph.add_edge(
            LevelEdge(
                source_id=src.id,
                target_id=dst.id,
                edge_type=edge_type,
                dx=dx,
                dy=dy,
                v_launch=(
                    float(raw_edge.get("vx", 0.0)),
                    float(raw_edge.get("vy", 0.0)),
                ),
                metadata=edge_meta,
            )
        )

    return graph


# ─────────────────────────────────────────────────────────────────────────────
# Schema post-processing
# ─────────────────────────────────────────────────────────────────────────────

_DROP_KEYS = {"title", "default"}


def _inline_refs(node: Any, defs: Dict[str, Any]) -> Any:
    """Recursively replace ``{"$ref": "#/$defs/X"}`` with a copy of ``defs[X]``."""
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            target = copy.deepcopy(defs[name])
            extra = {k: v for k, v in node.items() if k != "$ref"}
            target.update(extra)
            return _inline_refs(target, defs)
        return {k: _inline_refs(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def _normalise_schema(node: Any) -> Any:
    """Collapse ``anyOf [T, null]`` to ``type: [T, "null"]``; strict objects."""
    if isinstance(node, list):
        return [_normalise_schema(v) for v in node]
    if not isinstance(node, dict):
        return node

    out: Dict[str, Any] = {}
    for k, v in node.items():
        if k == "properties":  # property *names* are data, never dropped
            out[k] = {name: _normalise_schema(sub) for name, sub in v.items()}
        elif k not in _DROP_KEYS:
            out[k] = _normalise_schema(v)

    any_of = out.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2:
        non_null = [s for s in any_of if s.get("type") != "null"]
        if len(non_null) == 1:
            inner = dict(non_null[0])
            inner_type = inner.get("type")
            inner["type"] = [inner_type, "null"] if isinstance(inner_type, str) else "null"
            if "enum" in inner:
                inner["enum"] = [*inner["enum"], None]
            del out["anyOf"]
            out = {**inner, **out}

    if out.get("type") == "object" and "properties" in out:
        out["required"] = list(out["properties"].keys())
        out["additionalProperties"] = False

    return out
