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

from . import complexity as cx
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


def _measure(spec_layout: SpecLayout, profile: GameProfile) -> Dict[str, Any]:
    from fidelity.difficulty import measure_difficulty

    rep = measure_difficulty(spec_layout.ir.grid, spec_layout.ir.entities, spec_layout.sections, profile)
    return {s.id: s.score for s in rep.sections}


def _scores_for(spec: LevelSpec, spec_layout: SpecLayout, profile: GameProfile) -> Optional[Dict[str, float]]:
    """Measured scores for the ``complexity`` fidelity check (None when no requirement uses it)."""
    if not any(r.check is not None and r.check.type == "complexity" for r in spec.requirements):
        return None
    from fidelity.difficulty import level_scores

    try:
        return level_scores(spec_layout.ir.grid, spec_layout.ir.entities, spec_layout.sections, profile)
    except Exception:  # noqa: BLE001 - the check stays unchecked
        return None


def _total_miss(spec: LevelSpec, scores: Dict[str, float]) -> float:
    total = 0.0
    for sec in spec.sections:
        c = cx.effective(spec, sec)
        if c is not None and sec.id in scores:
            total += abs(cx.miss(scores[sec.id], cx.targets(c)))
    return total


def _close_loop(spec, spec_layout, report, stage, profile, stats):
    """Measure; adjust sections that miss their score range by more than ``cx.TOLERANCE``
    (at most ``cx.MAX_ROUNDS`` rounds, each re-laid-out and re-verified; a round that
    fails or does not get closer is dropped); warn about what still misses."""
    notes: List[str] = []
    scores = _measure(spec_layout, profile)
    rounds = 0
    for _ in range(cx.MAX_ROUNDS):
        todo = []
        for sec in spec.sections:
            c = cx.effective(spec, sec)
            if c is None or sec.id not in scores:
                continue
            m = cx.miss(scores[sec.id], cx.targets(c))
            if abs(m) > cx.TOLERANCE:
                todo.append((sec.id, 1 if m < 0 else -1))
        if not todo:
            break
        jobs.report("complexity", "Adjusting the difficulty towards the target")
        trial, changes = spec, []
        for sid, direction in todo:
            trial, ch = cx.adjust_section(trial, sid, direction)
            changes += ch
        if not changes:
            break
        t_layout, t_report, _applied, t_notes = stage(trial)
        if not t_report.ok:
            notes.append("complexity: an adjustment broke verification and was dropped")
            break
        t_scores = _measure(t_layout, profile)
        if _total_miss(trial, t_scores) >= _total_miss(spec, scores):
            notes.append("complexity: an adjustment did not get closer to the target and was dropped")
            break
        spec, spec_layout, report, scores = trial, t_layout, t_report, t_scores
        notes += changes + t_notes
        rounds += 1
    still = {}
    for sec in spec.sections:
        c = cx.effective(spec, sec)
        if c is None or sec.id not in scores:
            continue
        t = cx.targets(c)
        still[sec.id] = {"complexity": c, "score": scores[sec.id], "target_min": t.score_min,
                         "target_max": t.score_max}
        if abs(cx.miss(scores[sec.id], t)) > cx.TOLERANCE:
            notes.append(f"complexity: section {sec.id} scores {scores[sec.id]:.1f}/10, outside its "
                         f"target {t.score_min:.1f}-{t.score_max:.1f}")
    stats["complexity"] = {"rounds": rounds, "sections": still}
    return spec, spec_layout, report, notes


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
    complexity: Optional[float] = None,
) -> BuiltLevel:
    """Generate, lay out, verify and repair a level (see the module docstring).

    *controller* is an ``LLMController`` (anything with ``generate_spec``,
    ``repair_spec``, ``stats`` and optionally ``complete_spec``).
    *size_override* ``(width, height)`` in tiles replaces ``spec.size``
    (either may be None); it is applied after the completeness fill, so it
    wins over a size requirement.  *densify* False skips the densify step.

    ``stats`` adds ``comfort_added`` (comfort-pass platforms kept), ``densify_added`` (fill platforms kept), ``fill_added`` (noise rock tiles kept; *fill* is a density or a ``layout.fill.FillParams``, None = off), ``completeness_reprompts`` (0 or 1) and ``entities_filled``
    (entities the deterministic fill added, over all fills).

    *complexity* (0..1, v4 Part 24) is the level's complexity target; it wins over the
    spec's own and is told to the LLM.  Null everywhere = nothing changes.  With a
    target, the build measures each section's difficulty and adjusts it at most
    ``cx.MAX_ROUNDS`` times (``stats["complexity"]``), then warns if it still misses.

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

    if complexity is not None:
        constraints = {**(constraints or {}), "complexity": cx.clamp01(complexity)}
    try:
        spec = controller.generate_spec(description, constraints)
    finally:
        absorb_llm_stats()
    if complexity is not None:
        spec = spec.model_copy(update={"complexity": cx.clamp01(complexity)})

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
    if cx.active(spec):
        spec = cx.apply_targets(spec)
    stats["completeness_reprompts"] = done.reprompts
    stats["entities_filled"] = done.entities_added
    filled: Set[str] = set(done.filled)
    notes: List[str] = list(done.notes)

    def stage(current: LevelSpec) -> Tuple[SpecLayout, GridReport, List[str], List[str]]:
        """Layout, verify, repair, then the comfort and densify passes (limits scaled by complexity)."""
        sl, rep, applied = _layout_and_verify(current, profile, seed)
        out_notes: List[str] = []
        pf = cx.scaled_profile(profile, cx.level_scale(current))
        if rep.ok:
            jobs.report("comfort", "Shortening long jumps")
            comfy = make_comfortable(sl, rep, pf, current)
            rep = comfy.report or rep
            stats["comfort_added"] = len(comfy.added)
            out_notes += comfy.notes
        if rep.ok and densify:
            jobs.report("densify", "Adding fill platforms")
            dense = densify_layout(sl, rep, pf, current, seed=seed)
            rep = dense.report or rep
            stats["densify_added"] = len(dense.added)
            out_notes += dense.notes
        return sl, rep, applied, out_notes

    repairs: List[str] = []
    report: Optional[GridReport] = None
    for attempt in range(max_layout_retries + 1):
        jobs.report("layout", "Laying out the level" + (f" (round {attempt + 1})" if attempt else ""))
        spec_layout, report, applied, snotes = stage(spec)
        regen = [a for a in applied if a.startswith("terrain:")]
        stats["grid_repairs"] += len(applied) - len(regen)
        if regen:
            stats["terrain_retries"] = stats.get("terrain_retries", 0) + len(regen)
        repairs += applied
        notes += snotes
        if report.ok and cx.active(spec):
            spec, spec_layout, report, cnotes = _close_loop(spec, spec_layout, report, stage, profile, stats)
            notes += cnotes
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
                    final, spec_layout.ir, profile, verified=report.ok, auto_filled=filled,
                    scores=_scores_for(final, spec_layout, profile),
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
        if complexity is not None:
            spec = cx.apply_targets(spec.model_copy(update={"complexity": cx.clamp01(complexity)}))

    assert report is not None
    codes = [e.code for e in report.errors]
    raise LevelBuildFailed(
        f"The level failed grid verification after {stats['layout_retries']} LLM "
        f"re-prompt(s) and {stats['grid_repairs']} grid repair(s): {codes}. "
        + " ".join(report.error_messages()),
        report=report,
        stats=stats,
    )
