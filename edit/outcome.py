"""Shared end of every edit: spec bookkeeping, fidelity, stitch + write.

``finish`` takes the verified final grid and entities, refreshes the stored
LevelSpec and requirements, re-checks the fidelity report (warning about
requirements the edit turned missing), stitches the edit into the file
(after a history snapshot) and returns an :class:`EditOutcome` the server
turns into the response.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from fidelity import FidelityReport, check_level
from layout.ir import IREntity, IRLevel
from spec.level_spec import (
    LevelSize,
    LevelSpec,
    Requirement,
    Section,
    SpecEntity,
)

from . import history, stitch
from .check import LevelCheck
from .context import EditContext, Rect

_DIFFICULTIES = ("easy", "medium", "hard")
_SPEC_ENTITY_FIELDS = set(SpecEntity.model_fields)


@dataclass
class EditOutcome:
    success: bool
    mode: str
    error: Optional[str] = None
    rect: Optional[Rect] = None
    snapshot: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    ir: Optional[IRLevel] = None
    spec: Optional[LevelSpec] = None
    fidelity: Optional[FidelityReport] = None
    nodes: int = 0  # walkable spans in the final grid graph
    edges: int = 0
    theme: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    profile_path: Optional[str] = None

    @property
    def entity_counts(self) -> Dict[str, int]:
        return dict(collections.Counter(e.entity_type for e in self.ir.entities)) if self.ir else {}


def failure(mode: str, error: str, rect: Optional[Rect] = None,
            warnings: Iterable[str] = (), stats: Optional[Dict[str, Any]] = None) -> EditOutcome:
    return EditOutcome(False, mode, error=error, rect=rect, warnings=list(warnings),
                       stats=dict(stats or {}))


# ─────────────────────────────────────────────────────────────────────────────
# Spec bookkeeping
# ─────────────────────────────────────────────────────────────────────────────


def base_spec(ctx: EditContext) -> LevelSpec:
    """The file's LevelSpec, or a minimal one (sections from the bands) for files without it."""
    if ctx.spec is not None:
        return ctx.spec
    sections = [
        Section(
            id=b.id, name=b.name, theme=b.theme or "default",
            difficulty=b.difficulty if b.difficulty in _DIFFICULTIES else "medium",
            gravity_scale=min(2.0, max(0.25, b.gravity_scale)), description="", order=i,
        )
        for i, b in enumerate(ctx.bands)
    ]
    return LevelSpec.model_construct(
        title=ctx.level.identifier,
        size=LevelSize.model_construct(width_tiles=ctx.width, height_tiles=ctx.height),
        orientation=None,
        sections=sections,
        nodes=[],
        edges=[],
        entities=[],
        requirements=[],
    )


def add_requirements(spec: LevelSpec, new: Iterable[Requirement]) -> LevelSpec:
    """Append *new* requirements with fresh ids (``R<n>``) and de-duplicated texts."""
    reqs = list(spec.requirements)
    taken = {r.id for r in reqs}
    texts = {r.text.strip().lower() for r in reqs}
    n = len(reqs)
    for r in new:
        if r.text.strip().lower() in texts:
            continue
        n += 1
        while f"R{n}" in taken:
            n += 1
        taken.add(f"R{n}")
        texts.add(r.text.strip().lower())
        reqs.append(r.model_copy(update={"id": f"R{n}"}))
    return spec.model_copy(update={"requirements": reqs})


def spec_entity(ent: IREntity, anchor: str, section: str) -> Optional[SpecEntity]:
    """A SpecEntity for an IR entity (None for kinds outside the catalog)."""
    meta = ent.metadata
    data = {k: v for k, v in meta.items() if k in _SPEC_ENTITY_FIELDS}
    data.update(id=str(meta.get("spec_id") or meta.get("iid") or ent.entity_type),
                kind=ent.entity_type, anchor=anchor, section=section,
                description=str(meta.get("description") or ""), sprite=str(meta.get("sprite") or ""))
    try:
        return SpecEntity.model_validate(data)
    except ValueError:
        return None


def entity_ids(spec: LevelSpec) -> set:
    return {e.id for e in spec.entities}


# ─────────────────────────────────────────────────────────────────────────────
# Finish
# ─────────────────────────────────────────────────────────────────────────────


def _fidelity_warnings(before: Optional[FidelityReport], after: FidelityReport) -> List[str]:
    if before is None:
        return []
    old = {i.id: i.status for i in before.items}
    out = []
    for item in after.items:
        if old.get(item.id) in ("satisfied", "approximated") and item.status == "missing":
            out.append(f"Requirement {item.id} \"{item.text}\" is no longer met after the edit: "
                       f"{item.reason}.")
    return out


def finish(
    ctx: EditContext,
    *,
    mode: str,
    label_prompt: str,
    grid: List[List[Any]],
    entities: List[IREntity],
    spec: LevelSpec,
    check: LevelCheck,
    rect: Optional[Rect],
    removed: Iterable[str] = (),
    modified: Iterable[str] = (),
    added: Iterable[str] = (),
    warnings: Iterable[str] = (),
    stats: Optional[Dict[str, Any]] = None,
) -> EditOutcome:
    """Stitch and write a verified edit (see the module docstring)."""
    warnings = list(warnings) + list(check.warnings)
    ir_before = ctx.to_ir()
    before = check_level(base_spec(ctx), ir_before, ctx.profile, verified=True) \
        if base_spec(ctx).requirements else None
    ir = ctx.to_ir(grid, entities)
    fidelity = check_level(spec, ir, ctx.profile, verified=True)
    warnings += _fidelity_warnings(before, fidelity)

    stitched = stitch.splice(
        ctx, grid, entities, spec=spec, requirements=list(spec.requirements), rect=rect,
        removed=removed, modified=modified, added=added,
    )
    warnings += stitched.warnings
    if stitched.rebuilt:
        warnings.append("This file had no GLDtk v2 data, so it was re-exported as a whole "
                        "(entities keep their iids and fields; tiles were re-autotiled).")
    snap = stitch.write(ctx, stitched, history.make_label(mode, label_prompt))

    theme = stitch.band_theme(ctx, ctx.band_at(rect.col, rect.row) if rect else ctx.bands[0])
    return EditOutcome(
        success=True,
        mode=mode,
        rect=rect,
        snapshot=str(snap) if snap else None,
        warnings=warnings,
        ir=ir,
        spec=spec,
        fidelity=fidelity,
        nodes=len(check.spans.rects),
        edges=len(check.spans.graph.edges()),
        theme=theme.display_name,
        stats=dict(stats or {}),
        verified=True,
        profile_path=str(ctx.profile_path),
    )


__all__ = [
    "EditOutcome",
    "add_requirements",
    "base_spec",
    "failure",
    "finish",
    "spec_entity",
]
