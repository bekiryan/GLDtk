"""Prompt → verified level: LLM spec, layout, entities, grid verification, repair.

``build_level`` returns a level whose *final tile grid* has been proven
beatable (``report.ok``), or raises :class:`LevelBuildFailed`.  It never
returns an unverified level.

    1. ``controller.generate_spec``                       (LLM + spec validation)
    2. ``layout_spec`` → ``place_spec_entities`` → ``verify_grid``
       → ``repair_grid`` (deterministic, bounded) → ``verify_grid``
    3. still failing → ``controller.repair_spec`` with the grid errors
       (named by spec node ids) and back to 2, at most ``max_layout_retries``
       times
    4. then :class:`LevelBuildFailed` with the last report
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from game.profile import GameProfile
from layout.repair import repair_grid
from layout.spec_entities import place_spec_entities
from layout.spec_layout import SpecLayout, grid_to_spec, layout_spec
from layout.verify import GridReport, verify_grid
from spec.level_spec import LevelSpec, SpecNode


@dataclass
class BuiltLevel:
    spec: LevelSpec  # includes any stepping stones repair added
    spec_layout: SpecLayout
    report: GridReport
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)


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
    """Copy of *spec* with repair's stepping stones appended as Platform nodes."""
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


def _layout_and_verify(
    spec: LevelSpec, profile: GameProfile, seed: int
) -> Tuple[SpecLayout, GridReport, List[str]]:
    spec_layout = layout_spec(spec, profile, seed=seed)
    place_spec_entities(spec, spec_layout)
    report = verify_grid(spec_layout, profile, spec)
    applied: List[str] = []
    if not report.ok:
        spec_layout, applied = repair_grid(spec_layout, report, profile, spec)
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
) -> BuiltLevel:
    """Generate, lay out, verify and repair a level (see the module docstring).

    *controller* is an ``LLMController`` (anything with ``generate_spec``,
    ``repair_spec`` and ``stats``).  *size_override* ``(width, height)`` in
    tiles replaces ``spec.size`` (either may be None).

    Raises ``MaxRetriesExceeded`` (LLM) or :class:`LevelBuildFailed` (grid).
    """
    profile = profile or GameProfile()
    stats: Dict[str, Any] = {
        "llm_attempts": 0,
        "patches_applied": [],
        "layout_retries": 0,
        "grid_repairs": 0,
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
        spec = resize(controller.generate_spec(description, constraints))
    finally:
        absorb_llm_stats()

    repairs: List[str] = []
    report: Optional[GridReport] = None
    for attempt in range(max_layout_retries + 1):
        spec_layout, report, applied = _layout_and_verify(spec, profile, seed)
        stats["grid_repairs"] += len(applied)
        repairs += applied
        if report.ok:
            stats["verify"] = report.stats
            warnings = (
                list(spec_layout.adjustments)
                + list(spec_layout.entity_warnings)
                + repairs
                + list(report.warnings)
            )
            return BuiltLevel(
                spec=_with_added_nodes(spec, spec_layout),
                spec_layout=spec_layout,
                report=report,
                warnings=warnings,
                stats=stats,
            )
        if attempt == max_layout_retries:
            break
        stats["layout_retries"] += 1
        try:
            spec = resize(
                controller.repair_spec(description, spec, report.error_messages(), constraints)
            )
        finally:
            absorb_llm_stats()

    assert report is not None
    codes = [e.code for e in report.errors]
    raise LevelBuildFailed(
        f"The level failed grid verification after {stats['layout_retries']} LLM "
        f"re-prompt(s) and {stats['grid_repairs']} grid repair(s): {codes}. "
        + " ".join(report.error_messages()),
        report=report,
        stats=stats,
    )
