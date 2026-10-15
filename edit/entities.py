"""Entity-only edits: add, remove or modify entities; the tile grid is unchanged.

The scope is the selected entities (``entity_iids``), else the entities and
platforms inside ``rect``, else the whole level.  The LLM sees the entities
in scope as ``E1, E2, …`` and the platforms in scope as ``P1, P2, …`` and
returns an :class:`EntityEdit` (a list of operations).  New entities are
placed on their platform with the usual rules (one per cell, headroom, spread
over the platform, flyers hover); modified ones keep their iid, position and
every field the operation does not change.  The whole level is re-verified;
an added or moved entity the player cannot reach is moved to a reachable
platform in scope, then at most one LLM re-prompt, else nothing is written.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from pydantic import Field, create_model

from layout.ir import IREntity, TileValue
from layout.spec_entities import FLYER_HEIGHTS, entity_metadata, spread_cols
from llm.providers import LLMProvider
from spec.level_spec import Requirement, SpecEntity, _SpecModel

from .check import LevelCheck, make_baseline, preexisting_blocker, verify_level
from .context import Cell, EditContext, EditError, Rect, SpanRect, load_context
from .llm import EditLLMFailed, run_structured
from .outcome import EditOutcome, add_requirements, base_spec, failure, finish, spec_entity
from .prompts import ENTITY_SYSTEM_PROMPT

_NOT_FIELDS = {"id", "anchor", "section"}

# Every SpecEntity field (kind, description, sprite, kind-specific ones), all optional.
EntityFields = create_model(
    "EntityFields",
    __base__=_SpecModel,
    **{
        name: (Optional[f.annotation], Field(None, description=f.description))
        for name, f in SpecEntity.model_fields.items()
        if name not in _NOT_FIELDS
    },
)


class EntityOp(_SpecModel):
    op: Literal["add", "remove", "modify"]
    target: Optional[str] = Field(None, description='Entity id ("E3") for remove / modify.')
    anchor: Optional[str] = Field(None, description='Platform id ("P2") for add, or to move.')
    entity: Optional[EntityFields] = Field(  # type: ignore[valid-type]
        None, description="add: the new entity; modify: only the fields to change."
    )


class EntityEdit(_SpecModel):
    operations: List[EntityOp]
    requirements: List[Requirement]


# ─────────────────────────────────────────────────────────────────────────────
# Scope
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Scope:
    entities: Dict[str, IREntity]  # "E1" -> entity (from ctx.entities)
    platforms: Dict[str, SpanRect]  # "P1" -> (row, col_start, col_end), cut to the selection
    rect: Optional[Rect] = None
    warnings: List[str] = field(default_factory=list)


def _occupied(entities: List[IREntity]) -> Set[Cell]:
    return {(e.col, e.row) for e in entities}


def _free_cols(ctx: EditContext, span: SpanRect, taken: Set[Cell]) -> List[int]:
    row, c0, c1 = span
    return [c for c in range(c0, c1 + 1)
            if row >= 1 and int(ctx.grid[row - 1][c]) == TileValue.EMPTY and (c, row - 1) not in taken]


def make_scope(ctx: EditContext, spans, rect: Optional[Rect], iids: Optional[List[str]]) -> Scope:
    warnings: List[str] = []
    if iids:
        wanted = set(iids)
        ents = [e for e in ctx.entities if e.metadata.get("iid") in wanted]
        missing = wanted - {e.metadata.get("iid") for e in ents}
        if not ents:
            raise EditError("None of the selected entities is in the file (save the level in "
                            "LDtk first).")
        if missing:
            warnings.append(f"{len(missing)} selected entit{'y is' if len(missing) == 1 else 'ies are'} "
                            "not in the file and were ignored")
        sids = [spans.under(e) for e in ents]
        plats = [spans.rects[s] for s in dict.fromkeys(s for s in sids if s)]
    elif rect is not None:
        ents = [e for e in ctx.entities if rect.contains(e.col, e.row)]
        plats = []
        for row, c0, c1 in spans.rects.values():
            lo, hi = max(c0, rect.col), min(c1, rect.right)
            if lo <= hi and rect.contains(lo, row) and rect.contains(lo, row - 1):
                plats.append((row, lo, hi))
    else:
        ents = list(ctx.entities)
        plats = list(spans.rects.values())
    taken = _occupied(ctx.entities)
    plats = sorted((p for p in plats if _free_cols(ctx, p, taken)), key=lambda p: (p[0], p[1]))
    return Scope(
        entities={f"E{i}": e for i, e in enumerate(ents, 1)},
        platforms={f"P{i}": p for i, p in enumerate(plats, 1)},
        rect=rect,
        warnings=warnings,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Prompt + validation
# ─────────────────────────────────────────────────────────────────────────────

_HIDDEN = {"iid", "anchor", "original_anchor", "blocked", "node_id", "structural"}


def _brief(eid: str, e: IREntity) -> str:
    fields = {k: v for k, v in e.metadata.items() if k not in _HIDDEN and v not in (None, "", [])}
    return f"  {eid}: {e.entity_type} at column {e.col}, row {e.row} " + json.dumps(fields, ensure_ascii=False)


def build_entity_messages(ctx: EditContext, scope: Scope, prompt: str) -> List[Dict[str, Any]]:
    taken = _occupied(ctx.entities)
    if scope.rect is not None:
        where = f"the selected area (columns {scope.rect.col}..{scope.rect.right}, rows " \
                f"{scope.rect.row}..{scope.rect.bottom})"
    elif len(scope.entities) < len(ctx.entities):
        where = "the selected entities"
    else:
        where = "the whole level"
    lines = [f"REQUEST: {prompt.strip()}", "", f"SCOPE: {where} of a {ctx.width} × {ctx.height} level "
             "(row 0 is the top)."]
    if ctx.level.prompt:
        lines.append(f"The level was generated from: \"{ctx.level.prompt.strip()}\"")
    lines.append("SECTIONS:")
    for b in ctx.bands:
        lines.append(f"  - {b.id} \"{b.name}\": theme {b.theme or 'default'}, difficulty "
                     f"{b.difficulty}, columns {b.col_start}..{b.col_end}, rows {b.row_start}..{b.row_end}")
    lines.append("ENTITIES IN SCOPE:")
    lines += [_brief(eid, e) for eid, e in scope.entities.items()] or ["  none"]
    lines.append("PLATFORMS IN SCOPE (where new entities can stand):")
    for pid, (row, c0, c1) in scope.platforms.items():
        n = len(_free_cols(ctx, (row, c0, c1), taken))
        lines.append(f"  {pid}: row {row}, columns {c0}..{c1}, section {ctx.band_at(c0, row).id}, "
                     f"{n} free cell(s)")
    if not scope.platforms:
        lines.append("  none (entities can be modified or removed, not added)")
    return [{"role": "user", "content": "\n".join(lines)}]


def _parser(scope: Scope):
    def parse(edit: EntityEdit):
        errors: List[str] = []
        notes: List[str] = []
        ops: List[EntityOp] = []
        for i, op in enumerate(edit.operations, 1):
            if op.op in ("remove", "modify"):
                if op.target not in scope.entities:
                    errors.append(f"operation {i}: target {op.target!r} is not an entity in scope "
                                  f"({', '.join(scope.entities) or 'none'}).")
                    continue
                ent = scope.entities[op.target]
                if op.op == "remove" and ent.entity_type == "Start":
                    errors.append(f"operation {i}: the Start entity cannot be removed.")
                    continue
                if op.op == "modify":
                    if op.entity is None and op.anchor is None:
                        errors.append(f"operation {i}: modify needs 'entity' (the changed fields).")
                        continue
                    if op.anchor is not None and op.anchor not in scope.platforms:
                        notes.append(f"ignored the move of {op.target} to unknown platform {op.anchor!r}")
                        op = op.model_copy(update={"anchor": None})
                    if op.entity is not None and op.entity.kind not in (None, ent.entity_type):
                        notes.append(f"ignored the kind change of {op.target} "
                                     f"({ent.entity_type} → {op.entity.kind})")
                        op = op.model_copy(update={"entity": op.entity.model_copy(update={"kind": None})})
            else:  # add
                if op.entity is None or op.entity.kind is None:
                    errors.append(f"operation {i}: add needs 'entity' with a 'kind'.")
                    continue
                if op.entity.kind in ("Start", "Exit"):
                    errors.append(f"operation {i}: a {op.entity.kind} cannot be added.")
                    continue
                if not scope.platforms:
                    errors.append("There is no free platform in scope, so nothing can be added.")
                    continue
                if op.anchor not in scope.platforms:
                    pid = next(iter(scope.platforms))
                    notes.append(f"anchored an added {op.entity.kind} on {pid} (unknown {op.anchor!r})")
                    op = op.model_copy(update={"anchor": pid})
            ops.append(op)
        if errors:
            return None, errors, notes
        return EntityEdit(operations=ops, requirements=list(edit.requirements)), [], notes

    return parse


# ─────────────────────────────────────────────────────────────────────────────
# Apply
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _Applied:
    entities: List[IREntity]
    removed: Set[str]
    modified: Set[str]
    added: Set[str]
    warnings: List[str]


def _cell_on(ctx: EditContext, span: SpanRect, want: int, taken: Set[Cell], flyer: bool) -> Optional[Cell]:
    cols = sorted(_free_cols(ctx, span, taken), key=lambda c: (abs(c - want), c))
    if not cols:
        return None
    row, col = span[0], cols[0]
    if flyer:
        for h in FLYER_HEIGHTS:
            if all(row - d >= 0 and int(ctx.grid[row - d][col]) == TileValue.EMPTY
                   and (col, row - d) not in taken for d in range(1, h + 1)):
                return (col, row - h)
    return (col, row - 1)


def _fresh_id(base: str, taken: Set[str]) -> str:
    k = 1
    while f"{base}{k}" in taken:
        k += 1
    taken.add(f"{base}{k}")
    return f"{base}{k}"


def apply_ops(ctx: EditContext, scope: Scope, edit: EntityEdit) -> _Applied:
    entities = [IREntity(e.entity_type, e.col, e.row, dict(e.metadata)) for e in ctx.entities]
    by_iid = {e.metadata.get("iid"): e for e in entities}
    taken = _occupied(entities)
    ids = {str(e.metadata.get("spec_id")) for e in entities if e.metadata.get("spec_id")}
    removed: Set[str] = set()
    modified: Set[str] = set()
    added: Set[str] = set()
    warnings: List[str] = []

    adds_per: Dict[str, int] = {}
    for op in edit.operations:
        if op.op == "add":
            adds_per[op.anchor] = adds_per.get(op.anchor, 0) + 1
    spread = {pid: iter(spread_cols(scope.platforms[pid][1],
                                    scope.platforms[pid][2] - scope.platforms[pid][1] + 1, n))
              for pid, n in adds_per.items()}

    for op in edit.operations:
        if op.op == "remove":
            iid = scope.entities[op.target].metadata.get("iid")
            ent = by_iid.get(iid)
            if ent is not None and iid not in removed:
                entities.remove(ent)
                taken.discard((ent.col, ent.row))
                removed.add(iid)
                modified.discard(iid)
        elif op.op == "modify":
            iid = scope.entities[op.target].metadata.get("iid")
            ent = by_iid.get(iid)
            if ent is None or iid in removed:
                continue
            if op.entity is not None:
                changes = {k: v for k, v in op.entity.model_dump().items()
                           if v is not None and k != "kind"}
                ent.metadata.update(changes)
            if op.anchor is not None:
                span = scope.platforms[op.anchor]
                taken.discard((ent.col, ent.row))
                cell = _cell_on(ctx, span, (span[1] + span[2]) // 2, taken,
                                ent.entity_type == "Enemy" and ent.metadata.get("archetype") == "flyer")
                if cell is None:
                    warnings.append(f"could not move {op.target}: {op.anchor} has no free cell")
                    taken.add((ent.col, ent.row))
                else:
                    ent.col, ent.row = cell
                    taken.add(cell)
            modified.add(iid)
        else:
            f = op.entity
            span = scope.platforms[op.anchor]
            want = next(spread[op.anchor], (span[1] + span[2]) // 2)
            cell = _cell_on(ctx, span, want, taken, f.kind == "Enemy" and f.archetype == "flyer")
            if cell is None:  # the platform is full: any platform in scope with room
                for pid, other in scope.platforms.items():
                    cell = _cell_on(ctx, other, (other[1] + other[2]) // 2, taken, False)
                    if cell is not None:
                        warnings.append(f"placed the new {f.kind} on {pid} ({op.anchor} is full)")
                        break
            if cell is None:
                warnings.append(f"could not add a {f.kind}: no free cell in scope")
                continue
            data = {k: v for k, v in f.model_dump().items() if v is not None}
            base = (data.get("pickup_kind") or data.get("archetype") or data.get("interactive_kind")
                    or f.kind).lower()
            sid = _fresh_id(str(base), ids)
            section = ctx.band_at(*cell).id
            se = SpecEntity.model_validate({**data, "id": sid, "anchor": op.anchor,
                                            "section": section,
                                            "description": data.get("description") or "",
                                            "sprite": data.get("sprite") or ""})
            meta = entity_metadata(se, op.anchor)
            meta["iid"] = str(uuid.uuid4())
            entities.append(IREntity(f.kind, cell[0], cell[1], meta))
            taken.add(cell)
            added.add(meta["iid"])
    return _Applied(entities, removed, modified, added, warnings)


def _repair(ctx: EditContext, scope: Scope, applied: _Applied, check: LevelCheck, baseline, name
            ) -> Tuple[LevelCheck, List[str]]:
    """Move added / modified entities the player cannot reach onto a reachable platform."""
    done: List[str] = []
    mine = applied.added | applied.modified
    spans = check.spans
    reachable = [p for p in scope.platforms.values()
                 if spans.at(p[1], p[0]) in check.reach] or \
                [spans.rects[s] for s in check.reach if s in spans.rects]
    taken = _occupied(applied.entities)
    for err in check.errors:
        if err.entity_iid not in mine:
            continue
        ent = next(e for e in applied.entities if e.metadata.get("iid") == err.entity_iid)
        for span in sorted(reachable, key=lambda p: abs(p[0] - ent.row) + abs((p[1] + p[2]) / 2 - ent.col)):
            cell = _cell_on(ctx, span, ent.col, taken, False)
            if cell:
                done.append(f"moved {ent.metadata.get('spec_id') or ent.entity_type} from column "
                            f"{ent.col}, row {ent.row} to column {cell[0]}, row {cell[1]} (it was "
                            "unreachable)")
                taken.discard((ent.col, ent.row))
                ent.col, ent.row = cell
                taken.add(cell)
                break
    if done:
        check = verify_level(ctx, ctx.grid, applied.entities, baseline, name, spans=check.spans)
    return check, done


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def _updated_spec(ctx: EditContext, applied: _Applied, spans, requirements):
    spec = base_spec(ctx)
    gone = {str(e.metadata.get("spec_id")) for e in ctx.entities
            if e.metadata.get("iid") in applied.removed | applied.modified}
    ents = [e for e in spec.entities if e.id not in gone]
    for e in applied.entities:
        if e.metadata.get("iid") not in applied.added | applied.modified:
            continue
        sid = spans.under(e)
        anchor = str(e.metadata.get("anchor") or sid or "")
        se = spec_entity(e, anchor, str(e.metadata.get("section") or ctx.band_at(e.col, e.row).id))
        if se is not None:
            ents.append(se)
    return add_requirements(spec.model_copy(update={"entities": ents}), requirements)


def edit_entities(
    path: str,
    prompt: str,
    provider: LLMProvider,
    *,
    rect: Optional[Rect] = None,
    entity_iids: Optional[List[str]] = None,
    max_retries: int = 3,
) -> EditOutcome:
    mode = "entities"
    try:
        ctx = load_context(path)
        if rect is not None:
            clipped = rect.clipped(ctx.width, ctx.height)
            if clipped is None:
                raise EditError(f"The rectangle {rect.as_dict()} lies outside the level.")
            rect = clipped
        spans = ctx.extract(ctx.grid)
        scope = make_scope(ctx, spans, None if entity_iids else rect, entity_iids)
    except EditError as exc:
        return failure(mode, str(exc), rect)
    baseline, before = make_baseline(ctx, None, spans)
    blocker = preexisting_blocker(before, ctx)
    if blocker:
        return failure(mode, blocker, rect)

    def name(col: int, row: int) -> str:
        return f"column {col}, row {row}"

    stats: Dict[str, Any] = {"llm_attempts": 0, "grid_repairs": 0, "layout_retries": 0}
    parse = _parser(scope)
    try:
        run = run_structured(provider, ENTITY_SYSTEM_PROMPT, build_entity_messages(ctx, scope, prompt),
                             EntityEdit, parse, max_retries)
        stats["llm_attempts"] += run.attempts
        applied = apply_ops(ctx, scope, run.value)
        check = verify_level(ctx, ctx.grid, applied.entities, baseline, name, spans=spans)
        repairs: List[str] = []
        if not check.ok:
            check, repairs = _repair(ctx, scope, applied, check, baseline, name)
        if not check.ok:
            stats["layout_retries"] = 1
            numbered = "\n".join(f"  {i + 1}. {m}" for i, m in enumerate(check.messages()))
            retry = run.messages + [
                {"role": "assistant", "content": run.raw},
                {"role": "user", "content": (
                    "LEVEL VERIFICATION FAILED after applying your operations:\n"
                    f"{numbered}\n\nOutput a corrected EntityEdit that avoids these problems "
                    "(use platforms the player can reach). Output ONLY the JSON object.")},
            ]
            run = run_structured(provider, ENTITY_SYSTEM_PROMPT, retry, EntityEdit, parse, max_retries)
            stats["llm_attempts"] += run.attempts
            applied = apply_ops(ctx, scope, run.value)
            check = verify_level(ctx, ctx.grid, applied.entities, baseline, name, spans=spans)
            if not check.ok:
                check, repairs = _repair(ctx, scope, applied, check, baseline, name)
    except EditLLMFailed as exc:
        return failure(mode, str(exc), rect, warnings=scope.warnings, stats=stats)
    except Exception as exc:  # provider errors
        return failure(mode, f"LLM error: {exc}", rect, warnings=scope.warnings, stats=stats)

    stats["grid_repairs"] = len(repairs)
    if not check.ok:
        return failure(mode, "The edited level failed verification, so the file was not changed: "
                       + " ".join(check.messages()), rect,
                       warnings=scope.warnings + applied.warnings + repairs, stats=stats)
    if not (applied.removed or applied.modified or applied.added or run.value.requirements):
        return failure(mode, "The request changed nothing, so the file was not written.", rect,
                       warnings=scope.warnings + run.notes + applied.warnings, stats=stats)

    spec = _updated_spec(ctx, applied, spans, run.value.requirements)
    out = finish(
        ctx, mode=mode, label_prompt=prompt, grid=ctx.grid, entities=applied.entities, spec=spec,
        check=check, rect=None, removed=applied.removed, modified=applied.modified,
        added=applied.added, warnings=scope.warnings + run.notes + applied.warnings + repairs,
        stats=stats,
    )
    out.rect = rect if not entity_iids else None
    return out


__all__ = ["EntityEdit", "EntityFields", "EntityOp", "apply_ops", "edit_entities", "make_scope"]
