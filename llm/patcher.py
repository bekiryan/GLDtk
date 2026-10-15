"""Symbolic Patcher — First-Aid for minor level graph errors.

Tries to fix physics violations and structural defects deterministically,
without spending an LLM call.  Each Patch subclass targets one error code
and returns True if it successfully mutated the graph copy.

Patch application order
-----------------------
1. EdgeReclassifyPatch  — fix Jump/Fall confusion (wrong edge type for direction)
2. VelocityPatch        — recalculate v_launch on edges with wrong velocities
3. AbilityAssignPatch   — give an impossible jump an enabled ability that makes
                          it possible; drop abilities the Game Profile lacks
4. SteppingStonePatch   — insert intermediate platform for impossible jumps
5. DefaultTerminalsPatch— add missing Start or Exit at sensible default positions

Every check uses the Game Profile: effective gravity
``gravity · gravity_scale`` (of the edge's source section, stored in
``edge.metadata``) and ``run_speed`` as the |vx| cap.  Patches that replace an
edge keep its metadata (ability, gravity_scale).

Spec-level patch (applied by the controller after the graph patches)
---------------------------------------------------------------------
TerminalEntityPatch — add / re-anchor the Start and Exit *entities* so they
                      stand on the Start / Exit nodes.

The patcher stops and returns success as soon as the graph passes validation.
If any patch is applied the result is a *copy* — the original graph is never
mutated.
"""

from __future__ import annotations

import math
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from game.profile import GameProfile
from spec.level_spec import LevelSpec, SpecEntity
from symbolic.physics import (
    best_ability,
    check_jump_arc,
    fall_time,
    gravity_for,
    required_launch_velocity,
)
from symbolic.schema import (
    AbstractLevelGraph,
    EdgeType,
    LevelEdge,
    LevelNode,
    NodeType,
    Vec2,
)
from .validator import ValidationError, edge_gravity_scale, validate_graph


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PatchResult:
    success: bool
    graph: AbstractLevelGraph  # patched copy (or original if not success)
    applied: List[str] = field(default_factory=list)  # names of applied patches


def _replace_edge(graph: AbstractLevelGraph, edge: LevelEdge, **changes: Any) -> LevelEdge:
    """Swap *edge* for a copy with *changes*; metadata is copied, never shared.

    ``graph.copy()`` is shallow, so edges must never be mutated in place —
    the original graph would see the change.
    """
    metadata: Dict[str, object] = dict(edge.metadata)
    metadata.update(changes.pop("metadata", {}))
    new = LevelEdge(
        source_id=edge.source_id,
        target_id=edge.target_id,
        edge_type=changes.pop("edge_type", edge.edge_type),
        dx=edge.dx,
        dy=edge.dy,
        v_launch=changes.pop("v_launch", edge.v_launch),
        metadata={k: v for k, v in metadata.items() if v is not None},
    )
    graph.remove_edge(edge.source_id, edge.target_id)
    graph.add_edge(new)
    return new


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────────────────────────────────────


class Patch(ABC):
    """A single, focused fix strategy."""

    name: str = "Patch"

    @abstractmethod
    def applies_to(self, errors: List[ValidationError]) -> bool:
        """Return True if this patch should be attempted for *errors*."""

    @abstractmethod
    def apply(
        self,
        graph: AbstractLevelGraph,
        errors: List[ValidationError],
        profile: GameProfile,
    ) -> bool:
        """Mutate *graph* in-place.  Return True if any change was made."""


# ─────────────────────────────────────────────────────────────────────────────
# Patch 1 — recalculate wrong launch velocities
# ─────────────────────────────────────────────────────────────────────────────


class VelocityPatch(Patch):
    """Recompute v_launch for every edge using the physics oracle.

    Applied unconditionally so that even "valid" edges get corrected velocities
    before the graph is returned.  This cleans up LLM approximation errors in
    vx/vy without changing the topology.
    """

    name = "VelocityPatch"

    def applies_to(self, errors: List[ValidationError]) -> bool:
        return True  # always worth running

    def apply(
        self,
        graph: AbstractLevelGraph,
        errors: List[ValidationError],
        profile: GameProfile,
    ) -> bool:
        changed = False
        phys = profile.physics
        for edge in list(graph.edges()):
            src = graph.get_node(edge.source_id)
            dst = graph.get_node(edge.target_id)
            p1 = (src.surface.x, src.surface.y)
            p2 = (dst.surface.x, dst.surface.y)
            g = gravity_for(profile, edge_gravity_scale(edge))

            new_v: Tuple[float, float]

            if edge.edge_type == EdgeType.JUMP:
                if edge.metadata.get("ability"):
                    continue  # ability moves keep their (first-phase) launch velocity
                vel = required_launch_velocity(p1, p2, g, phys.jump_v)
                if vel is None:
                    continue  # impossible jump — later patches handle it
                new_v = vel

            elif edge.edge_type == EdgeType.FALL:
                dy = dst.surface.y - src.surface.y
                ft = fall_time(dy, g, initial_vy=0.0)
                if ft is None or ft <= 0.0:
                    continue
                dx = dst.surface.x - src.surface.x
                vx = dx / ft
                new_v = (vx, 0.0)

            else:  # WALK
                dx = dst.surface.x - src.surface.x
                new_v = (math.copysign(phys.run_speed, dx), 0.0)

            if new_v != edge.v_launch:
                _replace_edge(graph, edge, v_launch=new_v)
                changed = True

        return changed


# ─────────────────────────────────────────────────────────────────────────────
# Patch 2 — reclassify mis-typed edges (Jump↔Fall)
# ─────────────────────────────────────────────────────────────────────────────


class EdgeReclassifyPatch(Patch):
    """Fix edges whose direction contradicts their type.

    • Jump edge going DOWN  → reclassify as Fall (and zero vy)
    • Fall edge going UP    → reclassify as Jump (and set vy = jump_v)
    """

    name = "EdgeReclassifyPatch"
    _TARGET_CODES = {"JUMP_GOES_DOWN", "FALL_GOES_UP"}

    def applies_to(self, errors: List[ValidationError]) -> bool:
        return any(e.code in self._TARGET_CODES for e in errors)

    def apply(
        self,
        graph: AbstractLevelGraph,
        errors: List[ValidationError],
        profile: GameProfile,
    ) -> bool:
        changed = False
        for err in errors:
            if err.code not in self._TARGET_CODES or err.edge is None:
                continue
            src_id, dst_id = err.edge
            try:
                edge = graph.get_edge(src_id, dst_id)
            except KeyError:
                continue

            if err.code == "JUMP_GOES_DOWN":
                new_type = EdgeType.FALL
                new_vy = 0.0
            else:
                new_type = EdgeType.JUMP
                new_vy = profile.physics.jump_v

            _replace_edge(
                graph, edge, edge_type=new_type, v_launch=(edge.v_launch[0], new_vy)
            )
            changed = True

        return changed


# ─────────────────────────────────────────────────────────────────────────────
# Patch 3 — assign / drop abilities
# ─────────────────────────────────────────────────────────────────────────────


class AbilityAssignPatch(Patch):
    """Make jump edges use the abilities the Game Profile actually has.

    • ABILITY_NOT_IN_PROFILE → clear the ability; if the move still needs one,
      assign the cheapest *enabled* ability that works (``best_ability``).
    • IMPOSSIBLE_JUMP        → assign the cheapest enabled ability that works.

    If nothing in the profile makes the move possible the ability is left
    null and SteppingStonePatch gets the resulting IMPOSSIBLE_JUMP.
    """

    name = "AbilityAssignPatch"
    _TARGET_CODES = {"ABILITY_NOT_IN_PROFILE", "IMPOSSIBLE_JUMP"}

    def applies_to(self, errors: List[ValidationError]) -> bool:
        return any(e.code in self._TARGET_CODES for e in errors)

    def apply(
        self,
        graph: AbstractLevelGraph,
        errors: List[ValidationError],
        profile: GameProfile,
    ) -> bool:
        changed = False
        for err in errors:
            if err.code not in self._TARGET_CODES or err.edge is None:
                continue
            src_id, dst_id = err.edge
            try:
                edge = graph.get_edge(src_id, dst_id)
                src = graph.get_node(src_id)
                dst = graph.get_node(dst_id)
            except KeyError:
                continue

            current: Optional[str] = edge.metadata.get("ability") or None  # type: ignore[assignment]
            new: Optional[str] = None
            if edge.edge_type == EdgeType.JUMP:
                best = best_ability(
                    (src.surface.x, src.surface.y),
                    (dst.surface.x, dst.surface.y),
                    profile,
                    edge_gravity_scale(edge),
                )
                new = best if isinstance(best, str) else None
            if new == current:
                continue

            v_launch = edge.v_launch
            if new is not None:
                # First-phase launch: full jump, horizontal speed at the cap.
                vx = math.copysign(profile.physics.run_speed, edge.dx) if edge.dx else 0.0
                v_launch = (vx, profile.physics.jump_v)
            _replace_edge(graph, edge, v_launch=v_launch, metadata={"ability": new})
            changed = True

        return changed


# ─────────────────────────────────────────────────────────────────────────────
# Patch 4 — insert stepping stone for impossible jumps
# ─────────────────────────────────────────────────────────────────────────────


class SteppingStonePatch(Patch):
    """Split an impossible jump into two feasible jumps via a midpoint platform.

    The new platform is placed at the midpoint of the source and target
    surfaces.  Both sub-arcs are verified as *plain* jumps (effective gravity
    of the edge's section, ``run_speed`` cap) before committing the change.
    If either sub-arc is still impossible the patch is skipped for that edge
    (reported as non-patchable → LLM repair needed).
    """

    name = "SteppingStonePatch"
    _PLATFORM_W = 48.0
    _PLATFORM_H = 16.0

    def applies_to(self, errors: List[ValidationError]) -> bool:
        return any(e.code == "IMPOSSIBLE_JUMP" for e in errors)

    def apply(
        self,
        graph: AbstractLevelGraph,
        errors: List[ValidationError],
        profile: GameProfile,
    ) -> bool:
        changed = False
        for err in errors:
            if err.code != "IMPOSSIBLE_JUMP" or err.edge is None:
                continue
            src_id, dst_id = err.edge
            try:
                # Get edge details if needed in the future, or to verify it exists
                _edge = graph.get_edge(src_id, dst_id)
                src = graph.get_node(src_id)
                dst = graph.get_node(dst_id)
            except KeyError:
                continue

            mid_x = (src.surface.x + dst.surface.x) / 2.0
            mid_y = (src.surface.y + dst.surface.y) / 2.0

            # Platform top-left so that its surface is at (mid_x, mid_y)
            stone_pos = Vec2(mid_x - self._PLATFORM_W / 2.0, mid_y)
            stone = LevelNode(
                id=f"stone_{uuid.uuid4().hex[:6]}",
                node_type=NodeType.PLATFORM,
                position=stone_pos,
                size=Vec2(self._PLATFORM_W, self._PLATFORM_H),
                metadata={"auto_patched": True},
            )

            p_src = (src.surface.x, src.surface.y)
            p_stone = (stone.surface.x, stone.surface.y)
            p_dst = (dst.surface.x, dst.surface.y)

            g = gravity_for(profile, edge_gravity_scale(_edge))
            v = profile.physics.jump_v
            vmax = profile.physics.run_speed
            arc1_ok = check_jump_arc(p_src, p_stone, g, v, vmax)
            arc2_ok = check_jump_arc(p_stone, p_dst, g, v, vmax)

            if not (arc1_ok and arc2_ok):
                # Midpoint still too far; cannot patch automatically
                continue

            vel1 = required_launch_velocity(p_src, p_stone, g, v)
            vel2 = required_launch_velocity(p_stone, p_dst, g, v)
            if vel1 is None or vel2 is None:
                continue

            # Commit: remove original impossible edge, add stone + two edges
            meta = {
                k: v
                for k, v in _edge.metadata.items()
                if k != "ability" and v is not None
            }
            graph.remove_edge(src_id, dst_id)
            graph.add_node(stone)
            graph.add_edge(
                LevelEdge(
                    source_id=src_id,
                    target_id=stone.id,
                    edge_type=EdgeType.JUMP,
                    dx=p_stone[0] - p_src[0],
                    dy=p_stone[1] - p_src[1],
                    v_launch=vel1,
                    metadata=dict(meta),
                )
            )
            graph.add_edge(
                LevelEdge(
                    source_id=stone.id,
                    target_id=dst_id,
                    edge_type=EdgeType.JUMP,
                    dx=p_dst[0] - p_stone[0],
                    dy=p_dst[1] - p_stone[1],
                    v_launch=vel2,
                    metadata=dict(meta),
                )
            )
            changed = True

        return changed


# ─────────────────────────────────────────────────────────────────────────────
# Patch 5 — add missing terminals
# ─────────────────────────────────────────────────────────────────────────────


class DefaultTerminalsPatch(Patch):
    """Add a default Start or Exit node when one is missing.

    Start is placed at (0, 0); Exit is placed to the right of the rightmost
    node so it is reachable by a Walk edge.
    """

    name = "DefaultTerminalsPatch"
    _TARGET_CODES = {"MISSING_START", "MISSING_EXIT"}

    def applies_to(self, errors: List[ValidationError]) -> bool:
        return any(e.code in self._TARGET_CODES for e in errors)

    def apply(
        self,
        graph: AbstractLevelGraph,
        errors: List[ValidationError],
        profile: GameProfile,
    ) -> bool:
        changed = False
        codes = {e.code for e in errors}

        if "MISSING_START" in codes:
            start = LevelNode.make(
                NodeType.START,
                position=(0.0, 0.0),
                size=(48.0, 16.0),
                node_id="auto_start",
            )
            graph.add_node(start)
            changed = True

        if "MISSING_EXIT" in codes:
            # Place the exit just to the right of all existing nodes
            all_nodes = graph.nodes()
            max_x = max((n.position.x + n.size.x for n in all_nodes), default=0.0)
            exit_node = LevelNode.make(
                NodeType.EXIT,
                position=(max_x + 32.0, 0.0),
                size=(48.0, 16.0),
                node_id="auto_exit",
            )
            graph.add_node(exit_node)
            changed = True

        return changed


# ─────────────────────────────────────────────────────────────────────────────
# Spec-level patch — Start / Exit entities
# ─────────────────────────────────────────────────────────────────────────────


class TerminalEntityPatch:
    """Make the spec carry exactly one Start entity on the Start node and at
    least one Exit entity on an Exit node.

    • Start: keep the first Start entity (re-anchored onto the Start node),
      drop any extras; create one if none exists.
    • Exit: if no Exit entity stands on an Exit node, re-anchor the first Exit
      entity onto the first Exit node, or create one.
    """

    name = "TerminalEntityPatch"
    _TARGET_CODES = {"START_ENTITY_MISMATCH", "EXIT_ENTITY_MISMATCH"}

    def applies_to(self, errors: List[ValidationError]) -> bool:
        return any(e.code in self._TARGET_CODES for e in errors)

    def apply_to_spec(self, spec: LevelSpec) -> LevelSpec:
        nodes = {n.id: n for n in spec.nodes}
        start_node = next((n for n in spec.nodes if n.type == "Start"), None)
        exit_nodes = [n for n in spec.nodes if n.type == "Exit"]
        entities = [e.model_copy() for e in spec.entities]
        taken = {e.id for e in entities}

        def fresh_id(base: str) -> str:
            candidate, i = base, 1
            while candidate in taken:
                i += 1
                candidate = f"{base}_{i}"
            taken.add(candidate)
            return candidate

        if start_node is not None:
            starts = [e for e in entities if e.kind == "Start"]
            if starts:
                keep = starts[0]
                keep.anchor, keep.section = start_node.id, start_node.section
                entities = [e for e in entities if e.kind != "Start" or e is keep]
            else:
                entities.insert(
                    0,
                    SpecEntity(
                        id=fresh_id("player_start"),
                        kind="Start",
                        anchor=start_node.id,
                        section=start_node.section,
                        description="Player spawn (auto-added)",
                        facing="right",
                    ),
                )

        if exit_nodes:
            exit_ids = {n.id for n in exit_nodes}
            exits = [e for e in entities if e.kind == "Exit"]
            if not any(e.anchor in exit_ids for e in exits):
                target = exit_nodes[0]
                if exits:
                    exits[0].anchor, exits[0].section = target.id, target.section
                else:
                    entities.append(
                        SpecEntity(
                            id=fresh_id("level_exit"),
                            kind="Exit",
                            anchor=target.id,
                            section=nodes[target.id].section,
                            description="Level exit (auto-added)",
                            requires_key=False,
                        )
                    )

        return spec.model_copy(update={"entities": entities})


# ─────────────────────────────────────────────────────────────────────────────
# Public patcher
# ─────────────────────────────────────────────────────────────────────────────


class SymbolicPatcher:
    """Apply a prioritised sequence of patches to a graph copy.

    Patches are attempted in declaration order.  After each successful patch
    the graph is re-validated; if it's now clean the loop exits early.

    Errors whose codes are not handled by any patch are left for the LLM
    repair step.
    """

    _PIPELINE: List[type] = [
        EdgeReclassifyPatch,  # cheapest: only retype an edge
        VelocityPatch,  # recalculate velocities on valid topology
        AbilityAssignPatch,  # use / drop profile abilities on jump edges
        SteppingStonePatch,  # topology change: add a node
        DefaultTerminalsPatch,  # structural: add Start or Exit
    ]

    def __init__(self, profile: Optional[GameProfile] = None) -> None:
        self._profile = profile or GameProfile()
        self._patches: List[Patch] = [cls() for cls in self._PIPELINE]

    def patch(
        self,
        graph: AbstractLevelGraph,
        errors: List[ValidationError],
    ) -> PatchResult:
        """Return a PatchResult with a (possibly modified) graph copy."""
        work = graph.copy()
        applied: List[str] = []

        current_errors = list(errors)

        for patch in self._patches:
            if not patch.applies_to(current_errors):
                continue
            if patch.apply(work, current_errors, self._profile):
                applied.append(patch.name)
                # Re-validate after each patch to get fresh error list
                result = validate_graph(work, self._profile)
                if result.valid:
                    return PatchResult(success=True, graph=work, applied=applied)
                current_errors = result.errors

        # All patches ran — check if the graph is now valid
        final = validate_graph(work, self._profile)
        return PatchResult(
            success=final.valid,
            graph=work,
            applied=applied,
        )
