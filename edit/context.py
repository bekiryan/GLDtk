"""Edit context: everything an edit needs, read from the current ``.ldtk``.

The file is the source of truth (design Q9): the grid comes from the IntGrid,
the entities from the entity layer (``layout.ldtk_reader``), and the sections,
prompt, requirements, LevelSpec and Game Profile path from the ``gldtk_*``
level fields.  A v1 or hand-made file without those fields reads as one
section covering the whole level, with the default Game Profile (or the
``gldtk.game.json`` next to the file).

Seams
-----
For a region edit the player's paths that cross the region boundary are
*locked*.  The current grid's platform graph is extracted (collision-aware,
``symbolic.extractor``) and every walkable span is classified as outside,
inside or straddling the rect:

* a span that **straddles** the boundary is a walk seam: the (up to 3) inside
  cells next to the boundary stay solid;
* an **edge** (jump / fall / walk) between an outside span and a span in the
  region is a jump seam: the (up to 3) cells of the inside span nearest the
  outside span stay solid.

Each seam is a short *stub* (row, col_start..col_end) inside the rect.  Its
cells stay solid and the player's headroom above it stays empty; the new
content must connect to it.  The Start and Exit entities inside a region are
kept, with their footing and headroom locked the same way, and the footing of
an entity outside the region that stands on a cell inside it stays solid.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple, Union

from game.profile import GameProfile, load_profile, profile_path_for
from layout.ir import IREntity, IRLevel, TileValue
from layout.ldtk_reader import ReadLevel, all_levels, read_level
from spec.level_spec import LevelSpec
from symbolic.extractor import extract_graph
from symbolic.schema import AbstractLevelGraph, NodeType

Cell = Tuple[int, int]  # (col, row)
SpanRect = Tuple[int, int, int]  # (row, col_start, col_end), ends inclusive

STUB_WIDTH = 3
FIXED_KINDS = ("Start", "Exit")  # kept (with their footing) when a region is regenerated


class EditError(RuntimeError):
    """An edit that cannot be done; the message is shown to the user."""


# ─────────────────────────────────────────────────────────────────────────────
# Rect
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Rect:
    """A tile rectangle; row 0 is the top of the level."""

    col: int
    row: int
    w: int
    h: int

    @property
    def right(self) -> int:  # inclusive
        return self.col + self.w - 1

    @property
    def bottom(self) -> int:  # inclusive
        return self.row + self.h - 1

    def contains(self, col: int, row: int) -> bool:
        return self.col <= col <= self.right and self.row <= row <= self.bottom

    def cells(self) -> Iterable[Cell]:
        for r in range(self.row, self.bottom + 1):
            for c in range(self.col, self.right + 1):
                yield (c, r)

    def expanded(self, margin: int, width: int, height: int) -> "Rect":
        c0, r0 = max(0, self.col - margin), max(0, self.row - margin)
        c1, r1 = min(width - 1, self.right + margin), min(height - 1, self.bottom + margin)
        return Rect(c0, r0, c1 - c0 + 1, r1 - r0 + 1)

    def clipped(self, width: int, height: int) -> Optional["Rect"]:
        c0, r0 = max(0, self.col), max(0, self.row)
        c1, r1 = min(width - 1, self.right), min(height - 1, self.bottom)
        if c0 > c1 or r0 > r1:
            return None
        return Rect(c0, r0, c1 - c0 + 1, r1 - r0 + 1)

    def as_dict(self) -> Dict[str, int]:
        return {"col": self.col, "row": self.row, "w": self.w, "h": self.h}


# ─────────────────────────────────────────────────────────────────────────────
# Sections
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Band:
    """One section's cell rectangle (from ``gldtk_sections``)."""

    id: str
    name: str
    col_start: int
    col_end: int
    row_start: int
    row_end: int
    gravity_scale: float = 1.0
    theme: str = ""
    difficulty: str = "medium"
    orientation: str = "horizontal"
    mapped_theme: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def contains(self, col: int, row: int) -> bool:
        return self.col_start <= col <= self.col_end and self.row_start <= row <= self.row_end

    @property
    def structure(self) -> Optional[str]:
        """The section's structure (v3 Part 21); None for open air."""
        v = self.raw.get("structure")
        return str(v) if v else None

    @property
    def is_terrain(self) -> bool:
        return self.structure not in (None, "open")

    @property
    def rect(self) -> Rect:
        return Rect(self.col_start, self.row_start,
                    self.col_end - self.col_start + 1, self.row_end - self.row_start + 1)


def _bands(sections: List[Dict[str, Any]], width: int, height: int) -> List[Band]:
    bands: List[Band] = []
    for i, s in enumerate(sections):
        if not isinstance(s, dict):
            continue
        try:
            bands.append(Band(
                id=str(s.get("id") or f"section{i}"),
                name=str(s.get("name") or s.get("id") or f"Section {i + 1}"),
                col_start=int(s.get("col_start", 0)),
                col_end=int(s.get("col_end", width - 1)),
                row_start=int(s.get("row_start", 0)),
                row_end=int(s.get("row_end", height - 1)),
                gravity_scale=float(s.get("gravity_scale", 1.0) or 1.0),
                theme=str(s.get("theme") or ""),
                difficulty=str(s.get("difficulty") or "medium"),
                orientation=str(s.get("orientation") or "horizontal"),
                mapped_theme=s.get("mapped_theme"),
                raw=dict(s),
            ))
        except (TypeError, ValueError):
            continue
    if not bands:
        bands = [Band("main", "Level", 0, width - 1, 0, height - 1)]
    return bands


# ─────────────────────────────────────────────────────────────────────────────
# Spans
# ─────────────────────────────────────────────────────────────────────────────


class Spans:
    """The standable spans of an extracted graph, indexed by row."""

    def __init__(self, graph: AbstractLevelGraph) -> None:
        self.graph = graph
        self.rects: Dict[str, SpanRect] = {}
        self.by_row: Dict[int, List[Tuple[int, int, str]]] = {}
        for n in graph.nodes():
            if n.node_type == NodeType.HAZARD:
                continue
            rect = (int(n.metadata["row"]), int(n.metadata["col_start"]), int(n.metadata["col_end"]))
            self.rects[n.id] = rect
            self.by_row.setdefault(rect[0], []).append((rect[1], rect[2], n.id))

    def at(self, col: int, row: int) -> Optional[str]:
        for c0, c1, sid in self.by_row.get(row, []):
            if c0 <= col <= c1:
                return sid
        return None

    def under(self, ent: IREntity, depth: int = 4) -> Optional[str]:
        """The span an entity stands (or hovers, up to *depth* rows) above."""
        for d in range(1, depth + 1):
            sid = self.at(ent.col, ent.row + d)
            if sid is not None:
                return sid
        return None

    def reach(self, source: Optional[str], stop: Iterable[str] = ()) -> Set[str]:
        """Spans reachable from *source* (spans in *stop* are reached, not expanded)."""
        if source is None:
            return set()
        stop = set(stop)
        g = self.graph.nx_graph
        seen, todo = {source}, [source]
        while todo:
            cur = todo.pop()
            if cur in stop and cur != source:
                continue
            for nxt in g.successors(cur):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return seen

    def coreach(self, targets: Iterable[str]) -> Set[str]:
        """Spans from which some span in *targets* is reachable."""
        g = self.graph.nx_graph
        seen = {t for t in targets if t in g}
        todo = list(seen)
        while todo:
            cur = todo.pop()
            for prev in g.predecessors(cur):
                if prev not in seen:
                    seen.add(prev)
                    todo.append(prev)
        return seen


# ─────────────────────────────────────────────────────────────────────────────
# Context
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class EditContext:
    path: Path
    raw: bytes  # the file as read (for the snapshot / no-op checks)
    doc: Dict[str, Any]  # the parsed project JSON
    level: ReadLevel
    profile: GameProfile
    profile_path: Path
    bands: List[Band]
    spec: Optional[LevelSpec]
    warnings: List[str] = field(default_factory=list)

    @property
    def width(self) -> int:
        return self.level.width_tiles

    @property
    def height(self) -> int:
        return self.level.height_tiles

    @property
    def tile_size(self) -> int:
        return self.level.tile_size

    @property
    def grid(self) -> List[List[Any]]:
        return self.level.grid

    @property
    def entities(self) -> List[IREntity]:
        return self.level.entities

    @property
    def player_h(self) -> int:
        return self.profile.player_tiles()[1]

    def band_at(self, col: int, row: int) -> Band:
        for b in self.bands:
            if b.contains(col, row):
                return b
        # Out of every band (only out of bounds, or hand-edited sections): nearest.
        return min(self.bands, key=lambda b: (
            max(0, b.col_start - col, col - b.col_end) + max(0, b.row_start - row, row - b.row_end)))

    def gravity_scale_at(self, col: int, row: int) -> float:
        return self.band_at(col, row).gravity_scale

    @property
    def has_terrain(self) -> bool:
        return any(b.is_terrain for b in self.bands)

    def step_at(self) -> Optional[Callable[[int, int], bool]]:
        """Where 1-tile steps are walkable (terrain sections), or None."""
        if not self.has_terrain:
            return None
        return lambda col, row: self.band_at(col, row).is_terrain

    def bands_in(self, rect: Rect) -> List[Band]:
        return [b for b in self.bands
                if b.col_start <= rect.right and rect.col <= b.col_end
                and b.row_start <= rect.bottom and rect.row <= b.row_end]

    def extract(self, grid: List[List[Any]]) -> Spans:
        """The collision-aware platform graph of *grid*.

        Solid cells without the player's headroom above them (inside a wall,
        under a low ceiling) are masked as non-standable obstacles first: the
        extractor treats a span as walkable end to end, so a seam merged into
        a wall would otherwise let the player walk through the wall.
        """
        g = extract_graph(_mask_buried(grid, self.player_h), profile=self.profile,
                          gravity_scale_at_cell=self.gravity_scale_at, collision=True,
                          step_at=self.step_at())
        return Spans(g)

    def to_ir(self, grid: Optional[List[List[Any]]] = None,
              entities: Optional[List[IREntity]] = None) -> IRLevel:
        ir = self.level.to_ir()
        if grid is not None:
            ir.grid = [list(r) for r in grid]
        if entities is not None:
            ir.entities = entities
        ir.metadata["sections"] = [dict(b.raw) for b in self.bands if b.raw] or None
        return ir


BURIED = 9  # an obstacle value no span is built from (any non-zero cell blocks arcs)


def _mask_buried(grid: List[List[Any]], player_h: int) -> List[List[int]]:
    out = [[int(v) for v in row] for row in grid]
    for r, row in enumerate(grid):
        for c, v in enumerate(row):
            if int(v) != TileValue.SOLID:
                continue
            if any(rr >= 0 and int(grid[rr][c]) != TileValue.EMPTY for rr in range(r - player_h, r)):
                out[r][c] = BURIED
    return out


def load_context(path: Union[str, Path], level: Union[int, str, None] = None) -> EditContext:
    """Read one level of an ``.ldtk`` for editing: *level* is an iid, an identifier or an
    index (None: the first level).  Raises :class:`EditError`."""
    p = Path(path)
    if p.suffix.lower() != ".ldtk":
        raise EditError(f"{p} is not an .ldtk file.")
    if not p.is_file():
        raise EditError(f"{p} does not exist.")
    raw = p.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise EditError(f"{p.name} is not valid LDtk JSON: {exc}") from exc
    try:
        read = read_level(doc, level)
    except (ValueError, KeyError, IndexError) as exc:
        msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        raise EditError(f"Cannot read {p.name}: {msg}") from exc
    level = read
    lvl = _level_json(doc, level.index)
    layers = lvl.get("layerInstances")
    if not layers:
        raise EditError(f"{p.name}: the level has no layer instances (external level files are "
                        "not supported).")
    if not any(li.get("__type") == "IntGrid" for li in layers):
        raise EditError(f"{p.name}: the level has no IntGrid layer, so there is no collision grid "
                        "to edit.")

    if level.game_profile_relpath:
        profile_path = (p.parent / level.game_profile_relpath).resolve()
    else:
        profile_path = profile_path_for(p)
    try:
        profile = load_profile(profile_path)
    except ValueError as exc:
        raise EditError(str(exc)) from exc
    if profile.tile_size != level.tile_size:
        raise EditError(f"{p.name} uses {level.tile_size} px tiles; GLDtk edits need "
                        f"{profile.tile_size} px tiles.")

    return EditContext(
        path=p.resolve(),
        raw=raw,
        doc=doc,
        level=level,
        profile=profile,
        profile_path=profile_path,
        bands=_bands(level.sections, level.width_tiles, level.height_tiles),
        spec=level.spec,
        warnings=list(level.warnings),
    )


def _level_json(doc: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
    return all_levels(doc)[index]


def level_json(ctx: EditContext) -> Dict[str, Any]:
    """The edited level's dict in ``ctx.doc``."""
    return _level_json(ctx.doc, ctx.level.index)


# ─────────────────────────────────────────────────────────────────────────────
# Seams and locked cells
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Seam:
    """A locked stub inside the region that a path from outside uses."""

    id: str  # "seam1", … (the LLM may anchor entities on it and use it in edges)
    row: int
    col_start: int
    col_end: int  # inclusive
    side: str  # left | right | top | bottom | inside (a kept Start / Exit)
    kind: str  # walk | jump | fall | start | exit
    direction: str  # in | out | both

    @property
    def cells(self) -> List[Cell]:
        return [(c, self.row) for c in range(self.col_start, self.col_end + 1)]


@dataclass
class Locks:
    solid: Set[Cell] = field(default_factory=set)  # stays solid
    clear: Set[Cell] = field(default_factory=set)  # stays empty (headroom)


def _side_of(rect: Rect, span: SpanRect) -> str:
    row, c0, c1 = span
    dists = {
        "left": rect.col - c1,
        "right": c0 - rect.right,
        "top": rect.row - row,
        "bottom": row - rect.bottom,
    }
    return max(dists, key=lambda k: dists[k])


def _stub_toward(span: SpanRect, rect: Rect, toward_col: float) -> Optional[SpanRect]:
    """The (up to 3) inside cells of *span* nearest the column *toward_col*."""
    row, c0, c1 = span
    lo, hi = max(c0, rect.col), min(c1, rect.right)
    if lo > hi or not rect.row <= row <= rect.bottom:
        return None
    if toward_col <= lo:
        return (row, lo, min(hi, lo + STUB_WIDTH - 1))
    if toward_col >= hi:
        return (row, max(lo, hi - STUB_WIDTH + 1), hi)
    a = int(round(toward_col)) - STUB_WIDTH // 2
    a = min(max(a, lo), max(lo, hi - STUB_WIDTH + 1))
    return (row, a, min(hi, a + STUB_WIDTH - 1))


def find_seams(ctx: EditContext, rect: Rect, spans: Spans) -> List[Seam]:
    """The locked stubs of a region edit (see the module docstring)."""

    def where(sid: str) -> str:
        row, c0, c1 = spans.rects[sid]
        inside = sum(1 for c in range(c0, c1 + 1) if rect.contains(c, row))
        if inside == 0:
            return "out"
        return "in" if inside == c1 - c0 + 1 else "straddle"

    found: Dict[SpanRect, Seam] = {}

    def add(stub: Optional[SpanRect], side: str, kind: str, direction: str) -> None:
        if stub is None:
            return
        old = found.get(stub)
        if old is None:
            found[stub] = Seam("", stub[0], stub[1], stub[2], side, kind, direction)
        elif old.direction != direction:
            old.direction = "both"

    for sid, (row, c0, c1) in spans.rects.items():
        if where(sid) != "straddle":
            continue
        if c0 < rect.col:
            add((row, rect.col, min(c1, rect.col + STUB_WIDTH - 1)), "left", "walk", "both")
        if c1 > rect.right:
            add((row, max(c0, rect.right - STUB_WIDTH + 1), rect.right), "right", "walk", "both")

    for e in spans.graph.edges():
        a, b = e.source_id, e.target_id
        if a not in spans.rects or b not in spans.rects:
            continue
        wa, wb = where(a), where(b)
        kind = e.edge_type.value
        if wa == "out" and wb != "out":
            ra = spans.rects[a]
            add(_stub_toward(spans.rects[b], rect, (ra[1] + ra[2]) / 2.0), _side_of(rect, ra), kind, "in")
        elif wb == "out" and wa != "out":
            rb = spans.rects[b]
            add(_stub_toward(spans.rects[a], rect, (rb[1] + rb[2]) / 2.0), _side_of(rect, rb), kind, "out")

    # Stubs on one row that overlap become one seam.
    merged: List[Seam] = []
    for stub in sorted(found, key=lambda s: (s[0], s[1])):
        s = found[stub]
        last = merged[-1] if merged else None
        if last and last.row == s.row and s.col_start <= last.col_end + 1:
            last.col_end = max(last.col_end, s.col_end)
            if last.direction != s.direction:
                last.direction = "both"
            continue
        merged.append(Seam("", s.row, s.col_start, s.col_end, s.side, s.kind, s.direction))

    # Kept Start / Exit entities inside the region: their footing is locked too.
    for ent in ctx.entities:
        if ent.entity_type not in FIXED_KINDS or not rect.contains(ent.col, ent.row):
            continue
        sid = spans.under(ent, depth=1)
        if sid is None:
            continue
        stub = _stub_toward(spans.rects[sid], rect, float(ent.col))
        if stub is None or any(m.row == stub[0] and m.col_start <= ent.col <= m.col_end for m in merged):
            continue
        merged.append(Seam("", stub[0], stub[1], stub[2], "inside", ent.entity_type.lower(), "both"))

    merged.sort(key=lambda s: (s.row, s.col_start))
    for i, s in enumerate(merged, 1):
        s.id = f"seam{i}"
    return merged


def locked_cells(ctx: EditContext, rect: Rect, seams: List[Seam],
                 entities: Optional[List[IREntity]] = None) -> Locks:
    """Cells inside *rect* the regenerated content may not change."""
    locks = Locks()
    ph = ctx.player_h
    for s in seams:
        for c, r in s.cells:
            locks.solid.add((c, r))
            for d in range(1, ph + 1):
                if rect.contains(c, r - d):
                    locks.clear.add((c, r - d))
    for ent in entities if entities is not None else ctx.entities:
        inside = rect.contains(ent.col, ent.row)
        if inside and ent.entity_type in FIXED_KINDS:
            height = ph if ent.entity_type == "Start" else 1
            for d in range(height):
                if rect.contains(ent.col, ent.row - d):
                    locks.clear.add((ent.col, ent.row - d))
            if rect.contains(ent.col, ent.row + 1) and ctx.grid[ent.row + 1][ent.col] != TileValue.EMPTY:
                locks.solid.add((ent.col, ent.row + 1))
        elif not inside and rect.contains(ent.col, ent.row + 1):
            if ctx.grid[ent.row + 1][ent.col] != TileValue.EMPTY:
                locks.solid.add((ent.col, ent.row + 1))  # footing of an entity outside
    locks.clear -= locks.solid
    return locks


# ─────────────────────────────────────────────────────────────────────────────
# ASCII views (for the LLM)
# ─────────────────────────────────────────────────────────────────────────────

ENTITY_CHARS = {
    "Start": "S", "Exit": "X", "Enemy": "E", "Pickup": "P", "Weapon": "W",
    "Interactive": "I", "NPC": "N", "Checkpoint": "C", "Key": "K",
}
_INSIDE = {TileValue.EMPTY: ".", TileValue.SOLID: "#", TileValue.HAZARD: "^"}
_OUTSIDE = {TileValue.EMPTY: " ", TileValue.SOLID: "%", TileValue.HAZARD: "~"}


def region_view(ctx: EditContext, rect: Rect, seams: List[Seam], context: int = 6) -> str:
    """The region (local coordinates) and *context* tiles of its surroundings.

    Inside the region: ``.`` empty, ``#`` solid, ``^`` hazard; outside it:
    space, ``%`` solid, ``~`` hazard.  Seam cells are digits (seam number mod
    10), entities their kind letter.  Rulers give region-local columns / rows
    (negative = outside, left / above).
    """
    win = rect.expanded(context, ctx.width, ctx.height)
    ents = {(e.col, e.row): ENTITY_CHARS.get(e.entity_type, "?") for e in ctx.entities}
    seam_at = {c: str(i % 10) for i, s in enumerate(seams, 1) for c in s.cells}
    lines = []
    cols = range(win.col, win.right + 1)
    lines.append("       " + "".join(str(abs(c - rect.col) // 10 % 10) if c >= rect.col else "-" for c in cols))
    lines.append("       " + "".join(str(abs(c - rect.col) % 10) for c in cols))
    for r in range(win.row, win.bottom + 1):
        row_chars = []
        for c in cols:
            inside = rect.contains(c, r)
            ch = ents.get((c, r))
            if ch is None and inside:
                ch = seam_at.get((c, r))
            if ch is None:
                v = int(ctx.grid[r][c])
                table = _INSIDE if inside else _OUTSIDE
                ch = table[TileValue(v)] if v in (0, 1, 2) else "?"
            row_chars.append(ch)
        lines.append(f"{r - rect.row:>5}  " + "".join(row_chars))
    return "\n".join(lines)


def level_view(ctx: EditContext, max_cells: int = 9000) -> Tuple[str, int]:
    """The whole level as ASCII (``#`` solid, ``^`` hazard, entity letters).

    Levels larger than *max_cells* are downsampled by an integer factor
    (each character covers ``factor × factor`` tiles; solid wins over hazard
    over empty).  Returns ``(text, factor)``.
    """
    factor = 1
    while (ctx.width // factor) * (ctx.height // factor) > max_cells:
        factor += 1
    ents = {}
    for e in ctx.entities:
        ents.setdefault((e.col // factor, e.row // factor), ENTITY_CHARS.get(e.entity_type, "?"))
    lines = []
    for R in range((ctx.height + factor - 1) // factor):
        out = []
        for C in range((ctx.width + factor - 1) // factor):
            ch = ents.get((C, R))
            if ch is None:
                vals = {int(ctx.grid[r][c])
                        for r in range(R * factor, min(ctx.height, (R + 1) * factor))
                        for c in range(C * factor, min(ctx.width, (C + 1) * factor))}
                ch = "#" if 1 in vals else "^" if 2 in vals else "."
            out.append(ch)
        lines.append(f"{R * factor:>4} " + "".join(out))
    return "\n".join(lines), factor


__all__ = [
    "Band",
    "EditContext",
    "EditError",
    "FIXED_KINDS",
    "Locks",
    "Rect",
    "Seam",
    "Spans",
    "find_seams",
    "level_json",
    "level_view",
    "load_context",
    "locked_cells",
    "region_view",
]
