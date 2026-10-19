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

Spec-level patches (applied by the controller around the graph patches)
------------------------------------------------------------------------
SpecNormalizePatch  — before: clamp out-of-bounds nodes, resolve / drop
                      dangling entity references, move entities off hazards.
TerminalEntityPatch — after: add / re-anchor the Start and Exit *entities* so
                      they stand on the Start / Exit nodes.

The controller keeps every patch that leaves no more errors than before, even
when some errors remain; it then re-prompts with only those.

The patcher stops and returns success as soon as the graph passes validation.
If any patch is applied the result is a *copy* — the original graph is never
mutated.
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from game.profile import GameProfile
from spec.level_spec import LevelSpec, SpecEntity
from symbolic.physics import (
    LAUNCH_FRACTIONS,
    best_ability,
    check_jump_arc,
    fall_time,
    gravity_for,
    max_jump_height,
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
    • Walk edge with a step higher than ``walk_step_px`` → Jump when it
      climbs, Fall when it drops
    """

    name = "EdgeReclassifyPatch"
    _TARGET_CODES = {"JUMP_GOES_DOWN", "FALL_GOES_UP", "WALK_HEIGHT_MISMATCH"}

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

            if err.code == "JUMP_GOES_DOWN" or (
                err.code == "WALK_HEIGHT_MISMATCH" and edge.dy < 0
            ):
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
    """Split an impossible jump into feasible jumps via evenly spaced platforms.

    One stone goes at the midpoint of the source and target surfaces; when that
    is not enough (short default jumps since v4 Part 28) 2 ... ``MAX_STONES``
    evenly spaced stones are tried.  All sub-arcs are verified as *plain* jumps (effective gravity
    of the edge's section, ``run_speed`` cap) before committing the change.
    If a sub-arc is still impossible with MAX_STONES the patch is skipped for that edge
    (reported as non-patchable → LLM repair needed).
    """

    name = "SteppingStonePatch"
    MAX_STONES = 6
    _PLATFORM_W = 48.0
    _PLATFORM_H = 16.0

    def applies_to(self, errors: List[ValidationError]) -> bool:
        return any(e.code == "IMPOSSIBLE_JUMP" for e in errors)

    def _plan(
        self, src: LevelNode, dst: LevelNode, g: float, v: float, vmax: float,
        profile: GameProfile, others: Sequence[LevelNode] = (),
    ) -> Optional[Tuple[List[Tuple[float, float]], List[Tuple[float, float]], List[float]]]:
        """Stone surface points, launch velocities and stone widths from *src* to
        *dst*, or None.

        With 1, 2, ... ``MAX_STONES`` stones, each stone goes as close as possible
        to the even split of what is left of the climb, among positions (on a
        half-tile grid around the two platforms, 3 or 2 tiles wide) that

        * do not crowd the source, the target or any other node (stones included): a stone may not
          overlap one horizontally closer than the player's height plus a tile
          (and half a tile of rounding margin) — the layout would push it away —
          nor cover the lower one (all but 1.5 tiles) closer than the shortest
          hop needs;
        * are a plain jump from the previous point (and the last one a plain jump
          from the target).

        A steep climb between two close columns therefore zig-zags beside them.
        """
        ts = float(profile.tile_size)
        hop = max_jump_height(g, v * min(LAUNCH_FRACTIONS)) + profile.player.height_px
        stand = (math.ceil(profile.player.height_px / ts) + 1) * ts + ts / 2  # + rounding
        clear = (math.ceil(hop / ts) + 1) * ts  # room for the shortest hop under a stone
        p_src = (src.surface.x, src.surface.y)
        p_dst = (dst.surface.x, dst.surface.y)
        fixed = [(n.position.x, n.position.x + n.size.x, n.surface.y) for n in (src, dst)]
        near = [(n.position.x, n.position.x + n.size.x, n.surface.y) for n in others
                if n.id not in (src.id, dst.id) and n.node_type != NodeType.HAZARD]
        x_lo = min(r[0] for r in fixed) - 4 * ts
        x_hi = max(r[1] for r in fixed) + 4 * ts
        xs = [x_lo + k * ts / 2 for k in range(int((x_hi - x_lo) / (ts / 2)) + 1)]

        def crowded(x: float, y: float, w: float, rects) -> bool:  # type: ignore[no-untyped-def]
            for r in rects:
                overlap = min(x + w / 2, r[1]) - max(x - w / 2, r[0])
                if overlap <= 0:
                    continue
                dy = abs(y - r[2])
                lower_w = (r[1] - r[0]) if r[2] < y else w
                # standing room always; hop room unless the lower one keeps
                # 1.5 tiles uncovered to jump from
                if dy < stand or (dy < clear and lower_w - overlap < 1.5 * ts):
                    return True
            return False

        def arc(a, b):  # type: ignore[no-untyped-def]
            if not check_jump_arc(a, b, g, v, vmax):
                return None
            return required_launch_velocity(a, b, g, v)

        for n in range(1, self.MAX_STONES + 1):
            pts = [p_src]
            vels: List[Tuple[float, float]] = []
            widths: List[float] = []
            rects = fixed + near
            for k in range(1, n + 1):
                left = n - k + 2  # hops still to make from the previous point
                ix = pts[-1][0] + (p_dst[0] - pts[-1][0]) / left
                iy = pts[-1][1] + (p_dst[1] - pts[-1][1]) / left
                best = None
                for w in (self._PLATFORM_W, 2 * ts):
                    for dy in (0.0, ts / 2, -ts / 2, ts, -ts):
                        y = iy + dy
                        for x in xs:
                            cost = abs(x - ix) + 2 * abs(dy) + (w != self._PLATFORM_W) * ts
                            if best is not None and cost >= best[0]:
                                continue
                            if crowded(x, y, w, rects):
                                continue
                            vel = arc(pts[-1], (x, y))
                            if vel is None or (k == n and arc((x, y), p_dst) is None):
                                continue
                            best = (cost, x, y, w, vel)
                if best is None:
                    break
                _c, x, y, w, vel = best
                pts.append((x, y))
                vels.append(vel)
                widths.append(w)
                rects.append((x - w / 2, x + w / 2, y))
            else:
                vel = arc(pts[-1], p_dst)
                if vel is not None:
                    return pts + [p_dst], vels + [vel], widths
        return None

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

            p_src = (src.surface.x, src.surface.y)
            p_dst = (dst.surface.x, dst.surface.y)
            g = gravity_for(profile, edge_gravity_scale(_edge))
            v = profile.physics.jump_v
            vmax = profile.physics.run_speed

            # v4 Part 28: with short jumps one midpoint is often not enough —
            # try 1, 2, ... evenly spaced stones until every sub-arc is a plain jump.
            plan = self._plan(src, dst, g, v, vmax, profile, graph.nodes())
            if plan is None:
                # still too far even with MAX_STONES stones; cannot patch automatically
                continue
            pts, vels, widths = plan

            # Commit: remove the impossible edge, add the stones and their edges
            meta = {
                k: v
                for k, v in _edge.metadata.items()
                if k != "ability" and v is not None
            }
            graph.remove_edge(src_id, dst_id)
            ids = [src_id]
            for k, ((x, y), w) in enumerate(zip(pts[1:-1], widths)):
                # deterministic ids: the same spec patches to the same level
                key = f"{src_id}>{dst_id}#{k}"
                sid = f"stone_{hashlib.sha1(key.encode()).hexdigest()[:6]}"
                while graph.nx_graph.has_node(sid):
                    key += "'"
                    sid = f"stone_{hashlib.sha1(key.encode()).hexdigest()[:6]}"
                stone = LevelNode(
                    id=sid,
                    node_type=NodeType.PLATFORM,
                    # top-left so that the stone's surface is at (x, y)
                    position=Vec2(x - w / 2.0, y),
                    size=Vec2(w, self._PLATFORM_H),
                    metadata={"auto_patched": True},
                )
                graph.add_node(stone)
                ids.append(stone.id)
            ids.append(dst_id)
            for (a_id, b_id), (a, b), vel in zip(zip(ids, ids[1:]), zip(pts, pts[1:]), vels):
                graph.add_edge(
                    LevelEdge(
                        source_id=a_id,
                        target_id=b_id,
                        edge_type=EdgeType.JUMP,
                        dx=b[0] - a[0],
                        dy=b[1] - a[1],
                        v_launch=vel,
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


class SpecNormalizePatch:
    """Deterministic spec-level fixes run before the graph patches.

    • NODE_OUT_OF_BOUNDS   → clamp the node inside the level (x so the whole
      platform fits; y below the top with ``_TOP_MARGIN_TILES`` of headroom).
    • UNKNOWN_ENTITY_REF   → resolve "a, b" lists to the first known entity id,
      else drop the reference (null).
    • ANCHOR_ON_HAZARD     → re-anchor the entity on the nearest non-Hazard
      node, preferring its own section.

    ``apply_to_spec`` returns the spec and the names of the fixes it made (an
    empty list when nothing needed fixing; the spec is then returned as is).
    """

    name = "SpecNormalizePatch"
    _TOP_MARGIN_TILES = 3
    _TARGET_CODES = {"NODE_OUT_OF_BOUNDS", "UNKNOWN_ENTITY_REF", "ANCHOR_ON_HAZARD"}

    def applies_to(self, errors: List[ValidationError]) -> bool:
        return any(e.code in self._TARGET_CODES for e in errors)

    def apply_to_spec(self, spec: LevelSpec) -> Tuple[LevelSpec, List[str]]:
        from spec.level_spec import TILE_SIZE

        fixes: List[str] = []
        max_x = float(spec.size.width_tiles * TILE_SIZE)
        max_y = float(spec.size.height_tiles * TILE_SIZE)
        top = max(0.0, max_y - self._TOP_MARGIN_TILES * TILE_SIZE)

        nodes = []
        for n in spec.nodes:
            if 0 <= n.x <= max_x and 0 <= n.y <= max_y:
                nodes.append(n)
                continue
            x = min(max(n.x, 0.0), max(0.0, max_x - n.w))
            y = min(max(n.y, 0.0), top)
            nodes.append(n.model_copy(update={"x": x, "y": y}))
            fixes.append(f"clamped node '{n.id}'")

        by_id = {n.id: n for n in nodes}
        entity_ids = {e.id for e in spec.entities}
        entities = []
        for ent in spec.entities:
            update: Dict[str, Any] = {}
            for ref_field in ("linked_to", "opens"):
                ref = getattr(ent, ref_field)
                if not ref or ref in entity_ids:
                    continue
                parts = [p for p in ref.replace(";", ",").replace(" ", ",").split(",") if p]
                update[ref_field] = next((p for p in parts if p in entity_ids), None)
                fixes.append(f"{ref_field} of '{ent.id}'")
            anchor = by_id.get(ent.anchor)
            if anchor is not None and anchor.type == "Hazard":
                safe = [n for n in nodes if n.type != "Hazard"]
                if safe:
                    best = min(
                        safe,
                        key=lambda n: (
                            n.section != ent.section,
                            (n.x - anchor.x) ** 2 + (n.y - anchor.y) ** 2,
                            n.id,
                        ),
                    )
                    update.update(anchor=best.id, section=best.section)
                    fixes.append(f"moved '{ent.id}' off a hazard")
            entities.append(ent.model_copy(update=update) if update else ent)

        if not fixes:
            return spec, []
        return spec.model_copy(update={"nodes": nodes, "entities": entities}), fixes


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
