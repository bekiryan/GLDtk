"""Noise rock fill inside a region / section edit's rect (v4 Part 25a).

Protected, left empty (then widened by the fill's margin): the locked seams and
their headroom, every move arc of the current level and its golden route, the
headroom of every span and of every entity (``layout.fill.fill_grid``).  Rock
blobs that break verification are dropped, so this never fails an edit.
"""

from __future__ import annotations

from typing import Any, List, Set, Tuple

from layout.fill import fill_grid
from layout.ir import IREntity

from .check import LevelCheck, verify_level

Cell = Tuple[int, int]


def fill_region(ctx: Any, rect: Any, grid, entities: List[IREntity], locks: Any, baseline: Any,
                check: LevelCheck, name, seed: int, fill: Any) -> Tuple[Any, LevelCheck, int, str]:
    """Returns ``(grid, check, rock tiles added, note)``."""
    from .region import _section_bands

    protected: Set[Cell] = set(locks.solid) | set(locks.clear)
    res = fill_grid(
        grid, entities, (rect.col, rect.row, rect.right, rect.bottom), ctx.profile,
        sections=_section_bands(ctx), graph=check.spans.graph, start_span=check.start_span,
        exit_spans=set(check.exit_spans), protected=protected,
        verify=lambda g: verify_level(ctx, g, entities, baseline, name).ok,
        params=fill, seed=seed, tile_size=ctx.tile_size,
    )
    if not res.added:
        return grid, check, 0, ""
    note = (f"fill: added {len(res.added)} rock tile(s) in {len(res.blobs)} mass(es)"
            + (f", dropped {res.dropped} that broke verification" if res.dropped else ""))
    return res.grid, verify_level(ctx, res.grid, entities, baseline, name), len(res.added), note
