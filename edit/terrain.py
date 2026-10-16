"""Section edits that change a section's structure (v3 Part 21).

"Make this section a labyrinth" carves the section's band again with the
labyrinth generator (``layout/terrain``), no LLM call:

* the **seams** with the neighbours are kept: in a horizontal level the floor
  row where the neighbour's floor meets the band border (the door), in a
  vertical level the 2-column holes in the band's bottom row and in the row
  above it (links); at the level's edge the section starts / ends freely;
* the band is filled with rock and carved (``open`` clears it to air and keeps
  only the seams' floors);
* the section's entities move to the new anchors (their ``place``, or the
  structure's default place, else along the floor), the Start and Exit to the
  section's ends;
* the level is verified like any edit; up to ``MAX_ATTEMPTS`` generator seeds
  are tried.  The section's ``structure`` is updated in ``gldtk_sections`` and
  in the spec.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from layout.ir import IREntity, TileValue
from layout.terrain import MAX_ATTEMPTS, GENERATORS, generate_section
from layout.terrain.base import JUMP_AIR, Port, TerrainResult
from layout.terrain.place import SectionPlacer, default_place
from spec.level_spec import STRUCTURE_ALIASES, TERRAIN_STRUCTURES, LevelSpec

from .check import make_baseline, verify_level
from .context import Band, EditContext, Rect
from .outcome import EditOutcome, base_spec, failure, finish

_WORDS = "|".join(sorted(set(GENERATORS) | set(STRUCTURE_ALIASES), key=len, reverse=True))
_PATTERN = re.compile(
    rf"\b(?:make|turn|change|convert|rebuild|redo|rework|transform)\b[^.]*?\b({_WORDS})\b",
    re.IGNORECASE,
)


def normal_structure(word: Optional[str]) -> Optional[str]:
    if not word:
        return None
    w = word.strip().lower()
    w = STRUCTURE_ALIASES.get(w, w)
    return w if w in GENERATORS else None


def structure_from_prompt(prompt: str) -> Optional[str]:
    """The structure a "make this a …" prompt asks for, or None."""
    m = _PATTERN.search(prompt or "")
    return normal_structure(m.group(1)) if m else None


def _standable(grid: List[List[Any]], c: int, r: int, ph: int) -> bool:
    if r - ph < 0 or int(grid[r][c]) != TileValue.SOLID:
        return False
    return all(int(grid[r - k][c]) == TileValue.EMPTY for k in range(1, ph + 1))


def _row_limits(height: int) -> Tuple[int, int]:
    hi = height - 3
    return min(1 + JUMP_AIR + 1, hi), hi


def _side_port(ctx: EditContext, band: Band, side: str) -> Port:
    grid, ph = ctx.grid, ctx.player_h
    lo, hi = _row_limits(ctx.height)
    out = band.col_start - 1 if side == "left" else band.col_end + 1
    inner = band.col_start if side == "left" else band.col_end
    if out < 0 or out >= ctx.width:
        rows = [r for r in range(lo, hi + 1) if _standable(grid, inner, r, ph)]
        return Port("end", inner, rows[-1] if rows else hi)
    rows = [r for r in range(ph, ctx.height) if _standable(grid, out, r, ph)]
    doors = [r for r in rows if int(grid[r - 1][inner]) == TileValue.EMPTY]
    pick = (doors or rows or [hi])[-1]
    neighbour = ctx.band_at(out, pick)
    kind = "door" if neighbour.is_terrain else "open"
    return Port(kind, band.col_start if side == "left" else band.col_end + 1,
                min(max(pick, lo), hi))


def _hole(grid: List[List[Any]], row: int, c0: int, c1: int) -> Optional[int]:
    for c in range(max(1, c0), min(c1, len(grid[0]) - 2)):
        if int(grid[row][c]) == TileValue.EMPTY and int(grid[row][c + 1]) == TileValue.EMPTY:
            return c
    return None


def _vertical_ports(ctx: EditContext, band: Band) -> Tuple[Port, Port]:
    grid = ctx.grid
    mid = (band.col_start + band.col_end) // 2
    if band.row_end >= ctx.height - 1:
        start = next((e for e in ctx.entities if e.entity_type == "Start"), None)
        entry = Port("end", start.col if start and band.contains(start.col, start.row) else mid,
                     ctx.height - 2)
    else:
        below = ctx.band_at(mid, band.row_end + 1)
        col = _hole(grid, band.row_end, band.col_start, band.col_end)
        entry = Port("hole" if below.is_terrain and col is not None else "open",
                     col if col is not None else mid, band.row_end)
    if band.row_start <= 0:
        entry_x = Port("end", mid, band.row_start + 6)
        return entry, entry_x
    above = ctx.band_at(mid, band.row_start - 1)
    col = _hole(grid, band.row_start - 1, band.col_start, band.col_end)
    if above.is_terrain and col is not None:
        return entry, Port("hole", col, band.row_start - 1)
    return entry, Port("open", col if col is not None else mid, band.row_start + 4)


def _ports(ctx: EditContext, band: Band) -> Tuple[Port, Port]:
    if band.orientation == "vertical":
        return _vertical_ports(ctx, band)
    return _side_port(ctx, band, "left"), _side_port(ctx, band, "right")


def _open_band(ctx: EditContext, band: Band, grid: List[List[Any]], entry: Port, exit_: Port,
               ) -> TerrainResult:
    """``open``: clear the band to air; short platforms lead from seam to seam."""
    res = TerrainResult()
    for r in range(band.row_start, band.row_end + 1):
        for c in range(band.col_start, band.col_end + 1):
            grid[r][c] = TileValue.EMPTY
    # a row of 4-tile platforms 2 tiles apart from one seam's floor to the other's
    cells = []
    c0, c1 = band.col_start, band.col_end
    r_in = min(max(entry.row, 2), ctx.height - 1)
    r_out = min(max(exit_.row, 2), ctx.height - 1)
    xs = list(range(c0, c1 - 3, 6)) or [c0]
    for k, x in enumerate(xs):
        t = k / max(1, len(xs) - 1)
        row = round(r_in + (r_out - r_in) * t)
        seg = [(c, row) for c in range(x, min(x + 4, c1 + 1))]
        cells += seg
    cells += [(c, r_out) for c in range(max(c0, c1 - 3), c1 + 1)]
    for c, r in cells:
        grid[r][c] = TileValue.SOLID
    res.path = cells
    res.add("floor", cells)
    res.add("entry", cells[:3])
    res.add("exit", cells[-3:][::-1])
    return res


def restructure_section(
    ctx: EditContext, section_id: str, structure: str, prompt: str = "", seed: int = 0,
    complexity: Optional[float] = None, stamp_finder: Any = None,
) -> EditOutcome:
    """Carve section *section_id* again as *structure* (see the module docstring).
    *stamp_finder* (v4 Part 26b) may give patterns to stamp first."""
    mode = "section"
    want = normal_structure(structure)
    if want is None:
        return failure(mode, f"Unknown structure '{structure}' (use one of {', '.join(GENERATORS)}).")
    band = next((b for b in ctx.bands if b.id == section_id), None)
    if band is None:
        return failure(mode, f"No section '{section_id}' in this level.")
    rect = band.rect
    spec0 = base_spec(ctx)
    sec = next((s for s in spec0.sections if s.id == section_id), None)
    if sec is None:
        return failure(mode, f"The level's spec has no section '{section_id}'.", rect)
    sec = sec.model_copy(update={"structure": want})
    if complexity is not None:  # v4 Part 24: the section's own target and the terrain params it implies
        from pipeline.complexity import _params_for, clamp01, targets
        from spec.level_spec import StructureParams

        c = clamp01(complexity)
        have = sec.params.model_dump() if sec.params else {}
        have.update(_params_for(sec, targets(c)))
        sec = sec.model_copy(update={"complexity": c, "params": StructureParams(**have)})
    spec = spec0.model_copy(update={"sections": [sec if s.id == section_id else s
                                                 for s in spec0.sections]})
    baseline, _before = make_baseline(ctx, rect)
    entry, exit_ = _ports(ctx, band)
    shim = SimpleNamespace(width=ctx.width, height=ctx.height, orientation=band.orientation,
                           player_tiles=(1, ctx.player_h))
    bounds = (band.col_start, band.row_start, band.col_end, band.row_end)
    inside = [e for e in ctx.entities if band.contains(e.col, e.row)]
    outside = [e for e in ctx.entities if not band.contains(e.col, e.row)]
    last_errors: List[str] = []
    old_raw = dict(band.raw)
    # verification sees the new structure (stairs are walkable in carved sections)
    if want == "open":
        band.raw.pop("structure", None)
        band.raw.pop("params", None)
    else:
        band.raw["structure"] = want
    stamps = (stamp_finder(spec).get(section_id, []) if stamp_finder is not None
              and want in TERRAIN_STRUCTURES and band.orientation != "vertical" else [])
    band.raw.pop("pattern", None)
    for attempt in range(MAX_ATTEMPTS + len(stamps)):
        grid = [list(r) for r in ctx.grid]
        if want in TERRAIN_STRUCTURES:
            result = generate_section(shim, spec, sec, bounds, entry, exit_, seed, attempt, grid,
                                      stamps)
        else:
            result = _open_band(ctx, band, grid, entry, exit_)
        placer = SectionPlacer(shim, spec, sec, result, bounds, grid)
        entities = list(outside) + _move_entities(placer, spec, want, inside)
        check = verify_level(ctx, grid, entities, baseline)
        if check.ok:
            if result.stats.get("pattern"):
                band.raw["pattern"] = result.stats["pattern"]
            break
        last_errors = check.messages()
    else:
        band.raw.clear()
        band.raw.update(old_raw)
        return failure(mode, f"The section could not be rebuilt as {want}: "
                             + " ".join(last_errors[:3]), rect)
    return finish(
        ctx, mode=mode, label_prompt=prompt or f"make {section_id} a {want}", grid=grid,
        entities=entities, spec=spec, check=check, rect=rect,
        modified=[str(e.metadata["iid"]) for e in inside if e.metadata.get("iid")],
        warnings=[f"section '{section_id}' rebuilt as {want}"
                  + (f" from pattern {result.stats['pattern']}" if result.stats.get("pattern")
                     else f" (generator seed attempt {attempt})" if attempt else "")],
        stats={"llm_attempts": 0, "terrain_attempts": attempt + 1},
    )


def _move_entities(placer: SectionPlacer, spec: LevelSpec, structure: str,
                   inside: List[IREntity]) -> List[IREntity]:
    by_id = {e.id: e for e in spec.entities}
    out: List[IREntity] = []
    floors = [e for e in inside if e.entity_type not in ("Start", "Exit")]
    counters: Dict[str, int] = {}
    taken = set()
    for ent in inside:
        meta = dict(ent.metadata)
        if ent.entity_type in ("Start", "Exit"):
            cells = placer.cells("entry" if ent.entity_type == "Start" else "exit")
            place = None
        else:
            se = by_id.get(str(meta.get("spec_id", "")))
            place = (se.place if se else None) or (default_place(structure, se) if se else None) \
                or "floor"
            cells = placer.cells(place)
        if not cells:
            out.append(ent)
            continue
        k = counters.get(place or ent.entity_type, 0)
        counters[place or ent.entity_type] = k + 1
        if place == "floor":
            idx = round((floors.index(ent) + 1) / (len(floors) + 1) * (len(cells) - 1)) \
                if ent in floors else 0
            cell = cells[idx]
        else:
            cell = cells[k % len(cells)]
        col, row = cell[0], cell[1] - 1
        while (col, row) in taken and col + 1 < len(placer.grid[0]):
            col += 1
        taken.add((col, row))
        out.append(IREntity(ent.entity_type, col, row, meta))
    return out


__all__ = ["restructure_section", "structure_from_prompt", "normal_structure", "Rect"]
