"""Whole-level verification of an edited file (grid + entities).

The same checks as ``layout.verify.verify_grid`` (Start headroom, a
Start → Exit path on the collision-aware graph extracted from the final grid,
reachable gameplay entities, key before the locked exit), but on a *file*:
there is no LevelSpec layout to map back to, so everything is expressed in
grid spans and entity positions.  Two seam checks are added for region
edits:

  SEAM_BROKEN    a platform outside the region that the player could reach
                 from the Start before the edit cannot be reached any more
  SEAM_DEAD_END  a platform outside the region from which the Exit could be
                 reached before the edit no longer leads to the Exit

A problem the file already had before the edit (a hand-edited level may be
broken) is reported as a warning, never as an error: an edit must not make
the level worse, but it does not have to fix what it did not touch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Set, Tuple

from layout.ir import IREntity, TileValue
from layout.verify import REQUIRED_KINDS

from .context import Cell, EditContext, Rect, SpanRect, Spans


@dataclass
class CheckError:
    code: str
    message: str
    entity_iid: Optional[str] = None
    span: Optional[str] = None  # the span the error is about (unreached target / dead end)


@dataclass
class LevelCheck:
    ok: bool
    errors: List[CheckError]
    warnings: List[str]
    spans: Spans
    reach: Set[str]
    coreach: Set[str]  # spans from which an Exit is reachable
    start_span: Optional[str] = None
    exit_spans: Set[str] = field(default_factory=set)

    def messages(self) -> List[str]:
        return [e.message for e in self.errors]

    @property
    def codes(self) -> List[str]:
        return [e.code for e in self.errors]


@dataclass
class Baseline:
    """What held before the edit (see the module docstring)."""

    reach_cells: List[Cell] = field(default_factory=list)  # outside spans reachable from Start
    exit_cells: List[Cell] = field(default_factory=list)  # outside spans that lead to an Exit
    failing: Set[str] = field(default_factory=set)  # level-wide codes already failing
    bad_entities: Set[str] = field(default_factory=set)  # iids already unreachable / blocked


CellName = Callable[[int, int], str]


def _default_name(col: int, row: int) -> str:
    return f"column {col}, row {row}"


def _is_required(ent: IREntity) -> bool:
    if ent.entity_type == "Interactive" and ent.metadata.get("interactive_kind") == "sign":
        return False
    return ent.entity_type in REQUIRED_KINDS and ent.entity_type != "Exit"


def _label(ent: IREntity) -> str:
    sid = ent.metadata.get("spec_id")
    return f"'{sid}' ({ent.entity_type})" if sid else ent.entity_type


def verify_level(
    ctx: EditContext,
    grid: List[List[Any]],
    entities: List[IREntity],
    baseline: Optional[Baseline] = None,
    name: CellName = _default_name,
    spans: Optional[Spans] = None,
) -> LevelCheck:
    """Check the level made of *grid* and *entities* (see the module docstring)."""
    spans = spans or ctx.extract(grid)
    ph = ctx.player_h
    errors: List[CheckError] = []
    warnings: List[str] = []

    starts = [e for e in entities if e.entity_type == "Start"]
    exits = [e for e in entities if e.entity_type == "Exit"]
    start = starts[0] if starts else None
    start_span = spans.under(start, depth=1) if start else None
    exit_spans = {s for s in (spans.under(e, depth=1) for e in exits) if s}
    reach = spans.reach(start_span)
    pre_exit = spans.reach(start_span, stop=exit_spans)
    coreach = spans.coreach(exit_spans)

    if start is None:
        errors.append(CheckError("NO_START", "The level has no Start entity."))
    elif start_span is None:
        errors.append(CheckError(
            "START_NO_GROUND",
            f"The Start entity at {name(start.col, start.row)} "
            + ("is inside a solid tile." if 0 <= start.row < len(grid)
               and int(grid[start.row][start.col]) != TileValue.EMPTY
               else "has no platform under it."),
            start.metadata.get("iid")))
    else:
        rows = range(start.row - ph + 1, start.row + 1)
        if any(r < 0 or grid[r][start.col] != TileValue.EMPTY for r in rows):
            errors.append(CheckError(
                "START_BLOCKED",
                f"The player has no headroom at the Start ({name(start.col, start.row)}): "
                f"{ph} empty tiles are needed above the platform.",
                start.metadata.get("iid")))

    if not exits:
        errors.append(CheckError("NO_EXIT", "The level has no Exit entity."))
    elif start_span is not None and not (exit_spans & reach):
        e0 = exits[0]
        errors.append(CheckError(
            "NO_PATH",
            f"No Start→Exit path: the Exit at {name(e0.col, e0.row)} cannot be reached from "
            "the Start on the final tile grid (a jump is out of reach or its arc is blocked).",
            span=next(iter(exit_spans), None)))

    for ent in entities:
        if not _is_required(ent):
            continue
        sid = spans.under(ent)
        if sid is None or sid not in reach:
            where = name(ent.col, ent.row)
            msg = (f"Entity {_label(ent)} at {where} "
                   + ("has no platform under it." if sid is None
                      else "stands on a platform the player cannot reach from the Start."))
            errors.append(CheckError("ENTITY_UNREACHABLE", msg, ent.metadata.get("iid"), sid))
        elif int(grid[ent.row][ent.col]) != TileValue.EMPTY:
            errors.append(CheckError(
                "ENTITY_BLOCKED",
                f"Entity {_label(ent)} at {name(ent.col, ent.row)} is inside a solid tile.",
                ent.metadata.get("iid"), sid))

    locked = [e for e in exits if e.metadata.get("requires_key")]
    if locked and start_span is not None:
        keys = [e for e in entities if e.entity_type == "Key"]
        if not any(spans.under(k) in pre_exit for k in keys):
            errors.append(CheckError(
                "KEY_LOCK_ORDER",
                f"The Exit at {name(locked[0].col, locked[0].row)} requires a key, but "
                + ("no Key is reachable before it." if keys else "there is no Key entity.")))

    if baseline is not None:
        for c, r in baseline.reach_cells:
            sid = spans.at(c, r)
            if sid is None or sid not in reach:
                errors.append(CheckError(
                    "SEAM_BROKEN",
                    f"The platform at {name(c, r)} was reachable from the Start before the edit "
                    "and is not any more: connect the region to it again.", span=sid))
        for c, r in baseline.exit_cells:
            sid = spans.at(c, r)
            if sid is None or sid not in coreach:
                errors.append(CheckError(
                    "SEAM_DEAD_END",
                    f"From the platform at {name(c, r)} the player could reach the Exit before "
                    "the edit and cannot any more (the region traps the player).", span=sid))

        kept: List[CheckError] = []
        for err in errors:
            already = (err.entity_iid in baseline.bad_entities if err.entity_iid
                       and err.code.startswith("ENTITY") else err.code in baseline.failing)
            if already:
                warnings.append(f"{err.message} (the level already had this problem before the edit)")
            else:
                kept.append(err)
        errors = kept

    return LevelCheck(
        ok=not errors, errors=errors, warnings=warnings, spans=spans, reach=reach,
        coreach=coreach, start_span=start_span, exit_spans=exit_spans,
    )


def _outside_cell(rect: Optional[Rect], span: SpanRect) -> Optional[Cell]:
    row, c0, c1 = span
    for c in range(c0, c1 + 1):
        if rect is None or not rect.contains(c, row):
            return (c, row)
    return None


def make_baseline(ctx: EditContext, rect: Optional[Rect], spans: Optional[Spans] = None
                  ) -> Tuple[Baseline, LevelCheck]:
    """Check the file as it is now and record what must still hold after the edit.

    With *rect* None (entity edits) there are no seam cells.
    """
    before = verify_level(ctx, ctx.grid, ctx.entities, spans=spans)
    base = Baseline()
    for err in before.errors:
        if err.code.startswith("ENTITY") and err.entity_iid:
            base.bad_entities.add(err.entity_iid)
        else:
            base.failing.add(err.code)
    if rect is not None:
        for sid, span in before.spans.rects.items():
            cell = _outside_cell(rect, span)
            if cell is None:
                continue
            if sid in before.reach:
                base.reach_cells.append(cell)
            if sid in before.coreach and before.start_span is not None:
                base.exit_cells.append(cell)
    return base, before


# Problems an edit cannot fix: they are not in the region (Start and Exit are fixed kinds).
_START_CODES = ("NO_START", "START_NO_GROUND", "START_BLOCKED", "NO_EXIT")


def preexisting_blocker(before: LevelCheck, ctx: EditContext, rect: Optional[Rect] = None
                        ) -> Optional[str]:
    """Why no edit can verify while the file is like this, or None.

    A Start without ground (often moved by hand) makes every platform unreachable,
    so each new entity would be reported as the problem.  The cells under the Start
    can still be fixed by a region that contains them.
    """
    start = next((e for e in ctx.entities if e.entity_type == "Start"), None)
    for err in before.errors:
        if err.code not in _START_CODES:
            continue
        if err.code == "START_NO_GROUND" and rect is not None and start is not None \
                and rect.contains(start.col, min(start.row + 1, ctx.height - 1)):
            continue
        return ("This level cannot be beaten as it is now, so no edit can be verified: "
                f"{err.message} Fix it in LDtk (or use ↶ Undo) and try again.")
    return None


__all__ = ["Baseline", "CheckError", "LevelCheck", "make_baseline", "preexisting_blocker",
           "verify_level"]
