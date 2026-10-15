"""Prompt → verified level: LLM spec, layout, entities, grid verification, repair.

``build_level`` returns a level whose *final tile grid* has been proven
beatable (``report.ok``), or raises :class:`LevelBuildFailed`.  It never
returns an unverified level.

    1. ``controller.generate_spec``                       (LLM + spec validation)
    2. completeness (``pipeline/completeness.py``): deterministic fill of
       missing requirements, then at most one ``controller.complete_spec``
       re-prompt for what the fill cannot meet
    3. ``layout_spec`` (sections with a structure are carved from rock by
       ``layout/terrain``, v3 Part 21) → ``place_spec_entities`` → ``verify_grid``;
       a terrain level that fails is carved again with another generator seed
       for the failing sections (at most ``TERRAIN_ATTEMPTS`` seeds each)
       → ``repair_grid`` (deterministic, bounded) → ``verify_grid``;
       once verified, the comfort pass (``layout/comfort.py``) adds ``hop_N``
       platforms until the golden path is short hops (outside parkour
       sections, and only in open-air sections; it never fails a level), then ``densify``
       (``layout/densify.py``) adds ``fill_N`` platforms to the empty regions of
       open-air sections and re-verifies (it drops what breaks
       verification, so it never fails a level)
    4. still failing → ``controller.repair_spec`` with the grid errors
       (named by spec node ids), deterministic fill again, and back to 3, at
       most ``max_layout_retries`` times
    5. then :class:`LevelBuildFailed` with the last report

A built level carries a :class:`~fidelity.FidelityReport` from ``check_level``
on the final layout (entities as actually placed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from api import jobs
from fidelity import FidelityReport, check_level, fill_missing
from game.profile import GameProfile
from layout.comfort import make_comfortable
from layout.densify import densify as densify_layout
from layout.repair import repair_grid
from layout.spec_entities import place_spec_entities
from layout.spec_layout import SpecLayout, grid_to_spec, layout_spec
from layout.terrain import MAX_ATTEMPTS as TERRAIN_ATTEMPTS
from layout.verify import GridReport, verify_grid
from spec.level_spec import LevelSpec, SpecNode

from .completeness import ensure_complete


@dataclass
class BuiltLevel:
    spec: LevelSpec  # includes any stepping stones repair added
    spec_layout: SpecLayout
    report: GridReport
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    fidelity: Optional[FidelityReport] = None  # check_level on the final layout


class LevelBuildFailed(RuntimeError):
    def __init__(
        self,
        message: str,
        report: Optional[GridReport] = None,
        stats: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.report = report
        self.stats = stats or {}


def _with_added_nodes(spec: LevelSpec, layout: SpecLayout) -> LevelSpec:
    """Copy of *spec* with repair's stepping stones and densify's fill platforms
    appended as Platform nodes."""
    if not layout.added_nodes:
        return spec
    ts = layout.tile_size
    nodes = list(spec.nodes)
    for nid in layout.added_nodes:
        nl = layout.node_layouts[nid]
        x, y, w = grid_to_spec(nl.col, nl.row, nl.width, layout.height, ts)
        nodes.append(
            SpecNode(id=nid, type="Platform", x=x, y=y, w=w, h=float(ts),
                     section=layout.node_sections[nid])
        )
    return spec.model_copy(update={"nodes": nodes})


def _failing_sections(layout: SpecLayout, report: GridReport) -> Set[str]:
    """Terrain sections a failed verification points at (all of them when unclear)."""
    terrain = {b.id for b in layout.sections if b.is_terrain}
    hit: Set[str] = set()
    for err in report.errors:
        for nid in err.node_ids:
            sid = layout.node_sections.get(nid)
            if sid in terrain:
                hit.add(sid)
        for col, row in err.cells:
            sid = layout.section_at(col, row).id
            if sid in terrain:
                hit.add(sid)
    return hit or terrain


def _layout_and_verify(
    spec: LevelSpec, profile: GameProfile, seed: int
) -> Tuple[SpecLayout, GridReport, List[str]]:
    attempts: Dict[str, int] = {}
    retries = 0
    while True:
        spec_layout = layout_spec(spec, profile, seed=seed, terrain_attempts=attempts)
        place_spec_entities(spec, spec_layout)
        jobs.report("verify", "Verifying the grid")
        report = verify_grid(spec_layout, profile, spec)
        if report.ok or not spec_layout.has_terrain:
            break
        # v3 Part 21: another seed for the failing sections' generators first
        bump = [sid for sid in sorted(_failing_sections(spec_layout, report))
                if attempts.get(sid, 0) + 1 < TERRAIN_ATTEMPTS]
        if not bump:
            break
        for sid in bump:
            attempts[sid] = attempts.get(sid, 0) + 1
        retries += 1
        jobs.report("terrain", f"Carving the terrain again (try {retries + 1})")
    applied: List[str] = [f"terrain: regenerated {sid} with seed attempt {n}"
                          for sid, n in sorted(attempts.items())]
    if not report.ok:
        jobs.report("repair", f"Repairing the grid ({len(report.errors)} problem(s))")
        spec_layout, fixes = repair_grid(spec_layout, report, profile, spec)
        applied += fixes
        report = verify_grid(spec_layout, profile, spec)
    return spec_layout, report, applied


def build_level(
    description: str,
    controller: Any,
    profile: Optional[GameProfile] = None,
    constraints: Optional[Dict[str, Any]] = None,
    seed: int = 0,
    max_layout_retries: int = 2,
    size_override: Optional[Tuple[Optional[int], Optional[int]]] = None,
    densify: bool = True,
    fill: Any = None,
) -> BuiltLevel:
    """Generate, lay out, verify and repair a level (see the module docstring).

    *controller* is an ``LLMController`` (anything with ``generate_spec``,
    ``repair_spec``, ``stats`` and optionally ``complete_spec``).
    *size_override* ``(width, height)`` in tiles replaces ``spec.size``
    (either may be None); it is applied after the completeness fill, so it
    wins over a size requirement.  *densify* False skips the densify step.

    ``stats`` adds ``comfort_added`` (comfort-pass platforms kept), ``densify_added`` (fill platforms kept), ``fill_added`` (noise rock tiles kept; *fill* is a density or a ``layout.fill.FillParams``, None = off), ``completeness_reprompts`` (0 or 1) and ``entities_filled``
    (entities the deterministic fill added, over all fills).

    Raises ``MaxRetriesExceeded`` (LLM) or :class:`LevelBuildFailed` (grid).
    """
    profile = profile or GameProfile()
    rock_fill = fill  # the retry loop below reuses the name ``fill``
    stats: Dict[str, Any] = {
        "llm_attempts": 0,
        "patches_applied": [],
        "layout_retries": 0,
        "grid_repairs": 0,
        "completeness_reprompts": 0,
        "entities_filled": 0,
        "densify_added": 0,
        "comfort_added": 0,
        "fill_added": 0,
    }

    def absorb_llm_stats() -> None:
        s = controller.stats
        stats["llm_attempts"] += s.attempts
        stats["patches_applied"] += list(s.patches_applied)

    def resize(spec: LevelSpec) -> LevelSpec:
        if not size_override or not any(size_override):
            return spec
        w = size_override[0] or spec.size.width_tiles
        h = size_override[1] or spec.size.height_tiles
        size = spec.size.model_copy(update={"width_tiles": w, "height_tiles": h})
        return spec.model_copy(update={"size": size})

    try:
        spec = controller.generate_spec(description, constraints)
    finally:
        absorb_llm_stats()

    # ── completeness: deterministic fill, then at most one LLM re-prompt ──
    complete_spec = getattr(controller, "complete_spec", None)

    def reprompt(current: LevelSpec, missing: List[str]) -> LevelSpec:
        jobs.report("complete", f"{len(missing)} requirement(s) missing, asking the LLM once more")
        try:
            return complete_spec(description, current, missing, constraints)
        finally:
            absorb_llm_stats()

    done = ensure_complete(spec, profile, reprompt if complete_spec else None)
    spec = resize(done.spec)
    stats["completeness_reprompts"] = done.reprompts
    stats["entities_filled"] = done.entities_added
    filled: Set[str] = set(done.filled)
    notes: List[str] = list(done.notes)

    repairs: List[str] = []
    report: Optional[GridReport] = None
    for attempt in range(max_layout_retries + 1):
        jobs.report("layout", "Laying out the level" + (f" (round {attempt + 1})" if attempt else ""))
        spec_layout, report, applied = _layout_and_verify(spec, profile, seed)
        regen = [a for a in applied if a.startswith("terrain:")]
        stats["grid_repairs"] += len(applied) - len(regen)
        if regen:
            stats["terrain_retries"] = stats.get("terrain_retries", 0) + len(regen)
        repairs += applied
        if report.ok:
            jobs.report("comfort", "Shortening long jumps")
            comfy = make_comfortable(spec_layout, report, profile, spec)
            report = comfy.report or report
            stats["comfort_added"] = len(comfy.added)
            notes += comfy.notes
        if report.ok and densify:
            jobs.report("densify", "Adding fill platforms")
            dense = densify_layout(spec_layout, report, profile, spec, seed=seed)
            report = dense.report or report
            stats["densify_added"] = len(dense.added)
            notes += dense.notes
        if report.ok and rock_fill is not None:  # v4 Part 25a: noise rock (FillParams or density)
            from layout.fill import fill_layout
            jobs.report("fill", "Filling empty space with rock")
            rock = fill_layout(spec_layout, report, profile, spec, rock_fill, seed=seed)
            report, stats["fill_added"] = rock.report or report, rock.added
            notes += rock.notes
        if report.ok:
            stats["verify"] = report.stats
            warnings = (
                notes
                + list(spec_layout.adjustments)
                + list(spec_layout.entity_warnings)
                + repairs
                + list(report.warnings)
            )
            final = _with_added_nodes(spec, spec_layout)
            return BuiltLevel(
                spec=final,
                spec_layout=spec_layout,
                report=report,
                warnings=warnings,
                stats=stats,
                fidelity=check_level(
                    final, spec_layout.ir, profile, verified=report.ok, auto_filled=filled
                ),
            )
        if attempt == max_layout_retries:
            break
        stats["layout_retries"] += 1
        jobs.report("repair", f"The grid still fails verification, asking the LLM to repair it "
                              f"(round {attempt + 1})")
        try:
            repaired = controller.repair_spec(
                description, spec, report.error_messages(), constraints
            )
        finally:
            absorb_llm_stats()
        # The repaired spec may drop auto-filled entities: fill again (no LLM).
        fill = fill_missing(repaired)
        stats["entities_filled"] += fill.entities_added
        filled |= set(fill.filled)
        notes += fill.notes
        spec = resize(fill.spec)

    assert report is not None
    codes = [e.code for e in report.errors]
    raise LevelBuildFailed(
        f"The level failed grid verification after {stats['layout_retries']} LLM "
        f"re-prompt(s) and {stats['grid_repairs']} grid repair(s): {codes}. "
        + " ".join(report.error_messages()),
        report=report,
        stats=stats,
    )
