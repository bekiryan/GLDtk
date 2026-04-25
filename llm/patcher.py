"""Symbolic Patcher — First-Aid for minor level graph errors.

Tries to fix physics violations and structural defects deterministically,
without spending an LLM call.  Each Patch subclass targets one error code
and returns True if it successfully mutated the graph copy.

Patch application order
-----------------------
1. VelocityPatch        — recalculate v_launch on edges with wrong velocities
2. EdgeReclassifyPatch  — fix Jump/Fall confusion (wrong edge type for direction)
3. SteppingStonePatch   — insert intermediate platform for impossible jumps
4. DefaultTerminalsPatch— add missing Start or Exit at sensible default positions

The patcher stops and returns success as soon as the graph passes validation.
If any patch is applied the result is a *copy* — the original graph is never
mutated.
"""

from __future__ import annotations

import math
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from symbolic.physics import check_jump_arc, fall_time, required_launch_velocity
from symbolic.schema import (
    AbstractLevelGraph,
    EdgeType,
    LevelEdge,
    LevelNode,
    NodeType,
    Vec2,
)
from .validator import PhysicsParams, ValidationError, validate_graph


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PatchResult:
    success: bool
    graph: AbstractLevelGraph   # patched copy (or original if not success)
    applied: List[str] = field(default_factory=list)   # names of applied patches


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
        physics: PhysicsParams,
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
    _DEFAULT_WALK_SPEED = 200.0

    def applies_to(self, errors: List[ValidationError]) -> bool:
        return True   # always worth running

    def apply(
        self,
        graph: AbstractLevelGraph,
        errors: List[ValidationError],
        physics: PhysicsParams,
    ) -> bool:
        changed = False
        for edge in list(graph.edges()):
            src = graph.get_node(edge.source_id)
            dst = graph.get_node(edge.target_id)
            p1  = (src.surface.x, src.surface.y)
            p2  = (dst.surface.x, dst.surface.y)

            new_v: Tuple[float, float]

            if edge.edge_type == EdgeType.JUMP:
                vel = required_launch_velocity(p1, p2, physics.gravity, physics.jump_v)
                if vel is None:
                    continue   # impossible jump — SteppingStonePatch will handle it
                new_v = vel

            elif edge.edge_type == EdgeType.FALL:
                dy = dst.surface.y - src.surface.y
                ft = fall_time(dy, physics.gravity, initial_vy=0.0)
                if ft is None or ft <= 0.0:
                    continue
                dx  = dst.surface.x - src.surface.x
                vx  = dx / ft
                new_v = (vx, 0.0)

            else:  # WALK
                dx    = dst.surface.x - src.surface.x
                speed = self._DEFAULT_WALK_SPEED
                new_v = (math.copysign(speed, dx), 0.0)

            if new_v != edge.v_launch:
                # Replace the edge with corrected velocity
                graph.remove_edge(edge.source_id, edge.target_id)
                graph.add_edge(LevelEdge(
                    source_id=edge.source_id,
                    target_id=edge.target_id,
                    edge_type=edge.edge_type,
                    dx=edge.dx,
                    dy=edge.dy,
                    v_launch=new_v,
                ))
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
        physics: PhysicsParams,
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
                new_vy   = 0.0
            else:
                new_type = EdgeType.JUMP
                new_vy   = physics.jump_v

            graph.remove_edge(src_id, dst_id)
            graph.add_edge(LevelEdge(
                source_id=edge.source_id,
                target_id=edge.target_id,
                edge_type=new_type,
                dx=edge.dx,
                dy=edge.dy,
                v_launch=(edge.v_launch[0], new_vy),
            ))
            changed = True

        return changed


# ─────────────────────────────────────────────────────────────────────────────
# Patch 3 — insert stepping stone for impossible jumps
# ─────────────────────────────────────────────────────────────────────────────

class SteppingStonePatch(Patch):
    """Split an impossible jump into two feasible jumps via a midpoint platform.

    The new platform is placed at the midpoint of the source and target
    surfaces.  Both sub-arcs are verified before committing the change.
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
        physics: PhysicsParams,
    ) -> bool:
        changed = False
        for err in errors:
            if err.code != "IMPOSSIBLE_JUMP" or err.edge is None:
                continue
            src_id, dst_id = err.edge
            try:
                edge = graph.get_edge(src_id, dst_id)
                src  = graph.get_node(src_id)
                dst  = graph.get_node(dst_id)
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

            p_src   = (src.surface.x,   src.surface.y)
            p_stone = (stone.surface.x,  stone.surface.y)
            p_dst   = (dst.surface.x,   dst.surface.y)

            arc1_ok = check_jump_arc(p_src,   p_stone, physics.gravity, physics.jump_v,
                                     physics.max_horizontal_speed)
            arc2_ok = check_jump_arc(p_stone, p_dst,   physics.gravity, physics.jump_v,
                                     physics.max_horizontal_speed)

            if not (arc1_ok and arc2_ok):
                # Midpoint still too far; cannot patch automatically
                continue

            vel1 = required_launch_velocity(p_src, p_stone, physics.gravity, physics.jump_v)
            vel2 = required_launch_velocity(p_stone, p_dst,  physics.gravity, physics.jump_v)
            if vel1 is None or vel2 is None:
                continue

            # Commit: remove original impossible edge, add stone + two edges
            graph.remove_edge(src_id, dst_id)
            graph.add_node(stone)
            graph.add_edge(LevelEdge(
                source_id=src_id,
                target_id=stone.id,
                edge_type=EdgeType.JUMP,
                dx=p_stone[0] - p_src[0],
                dy=p_stone[1] - p_src[1],
                v_launch=vel1,
            ))
            graph.add_edge(LevelEdge(
                source_id=stone.id,
                target_id=dst_id,
                edge_type=EdgeType.JUMP,
                dx=p_dst[0] - p_stone[0],
                dy=p_dst[1] - p_stone[1],
                v_launch=vel2,
            ))
            changed = True

        return changed


# ─────────────────────────────────────────────────────────────────────────────
# Patch 4 — add missing terminals
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
        physics: PhysicsParams,
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
        EdgeReclassifyPatch,    # cheapest: only retype an edge
        VelocityPatch,          # recalculate velocities on valid topology
        SteppingStonePatch,     # topology change: add a node
        DefaultTerminalsPatch,  # structural: add Start or Exit
    ]

    def __init__(self, physics: PhysicsParams) -> None:
        self._physics = physics
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
            if patch.apply(work, current_errors, self._physics):
                applied.append(patch.name)
                # Re-validate after each patch to get fresh error list
                result = validate_graph(work, self._physics)
                if result.valid:
                    return PatchResult(success=True, graph=work, applied=applied)
                current_errors = result.errors

        # All patches ran — check if the graph is now valid
        final = validate_graph(work, self._physics)
        return PatchResult(
            success=final.valid,
            graph=work,
            applied=applied,
        )
