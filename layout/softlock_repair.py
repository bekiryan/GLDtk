"""Deterministic soft-lock repair (v4 Part 22), called by ``layout.repair``.

* **Pit / sealed area:** a stepping-stone chain (``find_stone``, comfortable
  hops first, then any hop the oracle accepts) from a trapped span out to the
  nearest span that still leads to an Exit.  A stone that only gets closer is
  kept and the chain goes on from it (up to ``MAX_CHAIN`` stones per trap).
* **Item trap** (dropped past the key, key behind its own door): move the Key
  or ability pickup to the nearest platform from which the trap is avoidable,
  checked with the item-state graph before moving; failing that, add a way
  back (a stone chain from the trap to the item's platform).

The caller re-verifies after every round and undoes a round that makes the
level worse (``repair_grid``), so a repair attempt never makes a level fail.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from game.profile import GameProfile
from spec.level_spec import LevelSpec

from .repair import STONE_WIDTH, find_stone
from .route import is_comfortable
from .softlock import ItemModel, SoftlockReport, Trap, find_softlocks
from .spec_layout import Rect, SpecLayout
from .sugiyama import NodeLayout
from .verify import GridReport, SpanIndex

MAX_CHAIN = 4
MAX_PAIRS = 6
MAX_ITEM_CANDIDATES = 40


def _centre(rect: Rect) -> Tuple[float, float]:
    return ((rect[1] + rect[2] + 1) / 2.0, float(rect[0]))


def _add_stone(layout: SpecLayout, stone: Rect, why: str, applied: List[str]) -> str:
    row, col, end = stone
    k = 1
    while f"stone{k}" in layout.node_layouts:
        k += 1
    sid = f"stone{k}"
    layout.node_layouts[sid] = NodeLayout(sid, col, row, end - col + 1, 1)
    layout.node_kinds[sid] = "Platform"
    section = layout.section_at(col + (end - col) // 2, row).id
    layout.node_sections[sid] = section
    layout.added_nodes.append(sid)
    applied.append(f"inserted stepping stone '{sid}' at column {col}, row {row} "
                   f"(section '{section}') {why}")
    return sid


def _stone(layout: SpecLayout, profile: GameProfile, ra: Rect, rb: Rect
           ) -> Optional[Tuple[Rect, bool]]:
    """A comfortable stone first, then any stone the oracle accepts."""
    found = find_stone(
        layout, profile, ra, rb, widths=(STONE_WIDTH,),
        accept=lambda st: is_comfortable(ra, st, profile),
        links=lambda st: is_comfortable(st, rb, profile),
    )
    if found is not None and found[1]:
        return found
    return find_stone(layout, profile, ra, rb) or found


def _chain_out(
    layout: SpecLayout,
    profile: GameProfile,
    sources: List[Rect],
    targets: List[Rect],
    why: str,
    applied: List[str],
) -> bool:
    """Stones from one of *sources* towards one of *targets* (nearest pairs first).

    A stone that only gets closer joins the sources and the search goes on (up
    to ``MAX_CHAIN`` stones); True when at least one stone was added.
    """
    sources = list(sources)
    added = 0
    while added < MAX_CHAIN:
        pairs = sorted(
            ((math.dist(_centre(a), _centre(b)), a, b) for a in sources for b in targets),
            key=lambda p: (p[0], p[1], p[2]),
        )[:MAX_PAIRS]
        found = None
        for _d, ra, rb in pairs:
            found = _stone(layout, profile, ra, rb)
            if found is not None:
                break
        if found is None:
            break
        stone, full = found
        _add_stone(layout, stone, why, applied)
        added += 1
        layout.rasterise()  # the next stone of the chain starts from this one
        if full:
            break
        sources.append(stone)
    return added > 0


def _moved(model: ItemModel, item: str, span: str) -> ItemModel:
    m = copy.copy(model)
    m.pickups = {s: set(its) - {item} for s, its in model.pickups.items()}
    m.pickups.setdefault(span, set()).add(item)
    return m


def _item_traps(rep: SoftlockReport) -> int:
    return sum(1 for t in rep.traps if t.kind in ("item", "key_behind_door"))


def _move_item(
    layout: SpecLayout,
    report: GridReport,
    spec: LevelSpec,
    index: SpanIndex,
    trap: Trap,
    applied: List[str],
) -> bool:
    sl = report.softlock
    if sl is None or sl.items is None or report.graph is None:
        return False
    model = sl.items
    base = _item_traps(sl)
    for item in trap.missing:
        eid = model.item_entities.get(item)
        ent = next((e for e in spec.entities if e.id == eid), None)
        if ent is None:
            continue
        current = layout.entity_anchors.get(ent.id, ent.anchor)
        if current not in layout.node_layouts:
            continue
        here = _centre(layout.rect(current))
        cands = sorted(
            (n for n, spans in index.node_spans.items()
             if n != current and spans and layout.standable(n)
             and layout.node_kinds.get(n) not in ("Exit", "Hazard")
             and any(s in sl.reach for s in spans)),
            key=lambda n: (math.dist(_centre(layout.rect(n)), here), n),
        )
        for n in cands[:MAX_ITEM_CANDIDATES]:
            span = next(s for s in index.node_spans[n] if s in sl.reach)
            rep = find_softlocks(report.graph, sl.start_span, sl.exit_spans,
                                 _moved(model, item, span))
            if _item_traps(rep) < base and not rep.unbeatable:
                layout.entity_anchors[ent.id] = n
                applied.append(f"moved {ent.id} from {current} to {n} so the player "
                               "cannot get past it without it (soft-lock)")
                return True
    return False


def repair_softlocks(
    layout: SpecLayout, report: GridReport, profile: GameProfile, spec: LevelSpec
) -> List[str]:
    """One round of soft-lock fixes (see the module docstring)."""
    sl = report.softlock
    graph = report.graph
    if sl is None or graph is None or not sl.traps:
        return []
    from .route import span_rect

    applied: List[str] = []
    index = SpanIndex(graph, layout)
    live = [span_rect(graph, s) for s in sorted(sl.reach & sl.live)]
    for trap in sl.traps:
        sources = [span_rect(graph, s) for s in trap.spans]
        if trap.kind in ("pit", "sealed"):
            _chain_out(layout, profile, sources, live, "to climb out of a soft-lock", applied)
            continue
        if _move_item(layout, report, spec, index, trap, applied):
            break  # the item model changed: check again before the next item fix
        items = sl.items or ItemModel()
        back = [span_rect(graph, s) for item in trap.missing
                for s, its in items.pickups.items() if item in its]
        if trap.kind == "item" and back:
            _chain_out(layout, profile, sources, back, "as a way back to the key", applied)
    return applied


def snapshot(layout: SpecLayout) -> Dict[str, Any]:
    return {k: copy.deepcopy(getattr(layout, k)) for k in
            ("node_layouts", "node_kinds", "node_sections", "added_nodes", "golden_path",
             "entity_anchors")}


def restore(layout: SpecLayout, snap: Dict[str, Any]) -> None:
    for k, v in snap.items():
        setattr(layout, k, v)


def error_score(report: GridReport) -> Tuple[int, int]:
    """(other errors, soft-locks): a soft-lock repair round must not raise it."""
    soft = sum(1 for e in report.errors if e.code == "SOFTLOCK")
    return (len(report.errors) - soft, soft)


__all__ = ["error_score", "repair_softlocks", "restore", "snapshot"]
