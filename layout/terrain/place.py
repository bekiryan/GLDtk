"""Put a terrain section's spec nodes and entities on the carved rock.

* **Spans.**  A standable run is a row of rock tiles inside the band with the
  player's height of air above each tile (the extractor's rule).
* **Nodes.**  The LLM's nodes in a terrain section keep their ids but not their
  coordinates: the Start goes to the section's entry end (its exit end when the
  section is the last one), the Exit to the exit end, every other platform to
  the main-path cell at its relative position along the section (by x in a
  horizontal level, by height in a vertical one), at most 4 tiles wide and
  never overlapping another node.  Hazard nodes go onto the generator's spike
  runs.  The rest of every run becomes a synthetic Platform node
  ``<section>.t<k>`` so each standable tile belongs to a node.  All of these are
  **fixed**: repair never nudges them and placement never widens them.
* **Entities.**  An entity with ``place`` (or a default place: a boss in an
  arena → ``arena_center``; pickups, keys and chests in a labyrinth →
  ``dead_end``) is anchored on the node under the resolved cell, at that
  column.  Others stay on their (snapped) anchor node.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set, Tuple

from spec.level_spec import LevelSpec, Section

from ..ir import TileValue
from ..sugiyama import NodeLayout
from .base import Cell, TerrainResult

Run = Tuple[int, int, int]  # (row, c0, c1)
MAX_NODE_WIDTH = 4
PLACE_FALLBACKS = {
    "dead_end": ("dead_end", "room", "floor"),
    "room": ("room", "dead_end", "floor"),
    "arena_center": ("arena_center", "room", "floor"),
    "ledge": ("ledge", "floor"),
    "entry": ("entry", "floor"),
    "exit": ("exit", "floor"),
    "floor": ("floor",),
}


def standable_runs(grid: Sequence[Sequence[int]], rect: Tuple[int, int, int, int], ph: int) -> List[Run]:
    c0, r0, c1, r1 = rect
    runs: List[Run] = []
    for r in range(max(r0, ph), r1 + 1):
        c = c0
        while c <= c1:
            if _standable(grid, c, r, ph):
                s = c
                while c + 1 <= c1 and _standable(grid, c + 1, r, ph):
                    c += 1
                runs.append((r, s, c))
            c += 1
    return runs


def _standable(grid: Sequence[Sequence[int]], c: int, r: int, ph: int) -> bool:
    if grid[r][c] != TileValue.SOLID or r - ph < 0:
        return False
    return all(grid[r - k][c] == TileValue.EMPTY for k in range(1, ph + 1))


class SectionPlacer:
    def __init__(self, layout, spec: LevelSpec, sec: Section, result: TerrainResult,  # type: ignore[no-untyped-def]
                 rect: Tuple[int, int, int, int], grid: Sequence[Sequence[int]]) -> None:
        self.layout = layout
        self.spec = spec
        self.sec = sec
        self.result = result
        self.grid = grid
        ph = layout.player_tiles[1]
        self.ph = ph
        self.runs = standable_runs(grid, rect, ph)
        self.run_at: Dict[Cell, int] = {}
        for i, (r, a, b) in enumerate(self.runs):
            for c in range(a, b + 1):
                self.run_at[(c, r)] = i
        self.taken: Dict[int, List[Tuple[int, int]]] = {}
        self.path = [c for c in result.path if c in self.run_at] or [
            (self.runs[i][1], self.runs[i][0]) for i in range(len(self.runs))
        ]
        self.warnings: List[str] = []
        self.fixed: Set[str] = set()

    # ── anchors ───────────────────────────────────────────────────────

    def cells(self, place: str) -> List[Cell]:
        for name in PLACE_FALLBACKS.get(place, (place, "floor")):
            got = [c for c in self.result.anchors.get(name, []) if c in self.run_at]
            if got:
                return got
        return list(self.path)

    def _fits(self, i: int, a: int, b: int) -> bool:
        r, c0, c1 = self.runs[i]
        if a < c0 or b > c1:
            return False
        return all(b < x or a > y for x, y in self.taken.get(i, []))

    def _claim(self, cell: Cell, width: int) -> Optional[Tuple[int, int, int]]:
        """(row, col, width) of a free interval of *width* on the run under *cell*."""
        i = self.run_at.get(cell)
        if i is None:
            return None
        r, c0, c1 = self.runs[i]
        width = max(1, min(width, c1 - c0 + 1))
        for w in range(width, 0, -1):
            centre = cell[0] - w // 2
            for d in sorted(range(-(c1 - c0), c1 - c0 + 1), key=abs):
                a = centre + d
                if self._fits(i, a, a + w - 1):
                    self.taken.setdefault(i, []).append((a, a + w - 1))
                    return r, a, w
        return None

    def _claim_near(self, cells: List[Cell], index: int, width: int) -> Optional[Tuple[int, int, int]]:
        order = sorted(range(len(cells)), key=lambda k: (abs(k - index), k))
        for k in order:
            got = self._claim(cells[k], width)
            if got:
                return got
        return None

    # ── nodes ─────────────────────────────────────────────────────────

    def snap_nodes(self, first: bool, last: bool) -> None:
        lay = self.layout
        horizontal = lay.orientation != "vertical"
        mine = [n for n in self.spec.nodes if n.section == self.sec.id]
        entry_cells = self.cells("entry")
        exit_cells = self.cells("exit")
        start_cells = exit_cells if (last and not first) else entry_cells
        exit_end = entry_cells if (first and not last) else exit_cells
        order = [n for n in mine if n.type == "Start"] + [n for n in mine if n.type == "Exit"]
        others = sorted((n for n in mine if n.type == "Platform"),
                        key=lambda n: (n.x if horizontal else n.y, n.id))
        hazards = [n for n in mine if n.type == "Hazard"]
        for n in order:
            cells = start_cells if n.type == "Start" else exit_end
            got = self._claim_near(cells, 0, 2) or self._claim_near(self.path, 0, 2)
            self._set(n.id, got)
        count = len(others)
        for k, n in enumerate(others):
            frac = (k + 1) / (count + 1)
            idx = round(frac * (len(self.path) - 1))
            width = min(MAX_NODE_WIDTH, max(1, round(n.w / lay.tile_size)))
            self._set(n.id, self._claim_near(self.path, idx, width))
        spikes = self.result.hazards
        for k, n in enumerate(hazards):
            if not spikes:
                self._drop(n.id, "no spikes in this section")
                continue
            r, a, b = spikes[k % len(spikes)]
            lay.node_layouts[n.id] = NodeLayout(node_id=n.id, col=a, row=r, width=b - a + 1, height=1)
            self.fixed.add(n.id)
        # every other standable tile: synthetic nodes
        k = 0
        for i, (r, c0, c1) in enumerate(self.runs):
            taken = sorted(self.taken.get(i, []))
            pos = c0
            pieces = []
            for a, b in taken:
                if a > pos:
                    pieces.append((pos, a - 1))
                pos = max(pos, b + 1)
            if pos <= c1:
                pieces.append((pos, c1))
            for a, b in pieces:
                nid = f"{self.sec.id}.t{k}"
                k += 1
                lay.node_layouts[nid] = NodeLayout(node_id=nid, col=a, row=r, width=b - a + 1, height=1)
                lay.node_kinds[nid] = "Platform"
                lay.node_sections[nid] = self.sec.id
                self.fixed.add(nid)

    def _set(self, nid: str, got: Optional[Tuple[int, int, int]]) -> None:
        if got is None:
            self._drop(nid, "no free floor left")
            return
        r, a, w = got
        self.layout.node_layouts[nid] = NodeLayout(node_id=nid, col=a, row=r, width=w, height=1)
        self.fixed.add(nid)

    def _drop(self, nid: str, why: str) -> None:
        lay = self.layout
        lay.node_layouts.pop(nid, None)
        if nid in lay.golden_path:
            lay.golden_path = [x for x in lay.golden_path if x != nid]
        self.warnings.append(f"terrain: node '{nid}' of section '{self.sec.id}' was left out ({why})")

    # ── entities ──────────────────────────────────────────────────────

    def node_at(self, cell: Cell) -> Optional[str]:
        c, r = cell
        for nid, nl in self.layout.node_layouts.items():
            if nl.row == r and nl.col <= c < nl.col + nl.width and self.layout.node_kinds.get(nid) != "Hazard":
                return nid
        return None

    def place_entities(self) -> None:
        lay = self.layout
        ents = [e for e in self.spec.entities if e.section == self.sec.id and e.kind not in ("Start", "Exit")]
        groups: Dict[str, List] = {}
        for e in ents:
            place = e.place or default_place(self.sec.structure, e)
            if place is None and e.anchor not in lay.node_layouts:
                place = "floor"
            if place is None:
                continue
            groups.setdefault(place, []).append(e)
        for place, group in groups.items():
            cells = self.cells(place)
            if not cells:
                continue
            for k, e in enumerate(group):
                if place == "floor":
                    pos = e.position if e.position is not None else (k + 1) / (len(group) + 1)
                    cell = cells[round(pos * (len(cells) - 1))]
                else:
                    cell = cells[k % len(cells)]
                nid = self.node_at(cell)
                if nid is None:
                    continue
                lay.entity_anchors[e.id] = nid
                lay.entity_cols[e.id] = cell[0]


def default_place(structure: Optional[str], ent) -> Optional[str]:  # type: ignore[no-untyped-def]
    if structure == "arena" and ent.kind == "Enemy" and ent.archetype == "boss":
        return "arena_center"
    if structure == "labyrinth" and (
        ent.kind in ("Pickup", "Key", "Weapon")
        or (ent.kind == "Interactive" and ent.interactive_kind == "chest")
    ):
        return "dead_end"
    if structure == "hub" and ent.kind in ("Key",) or (
        structure == "hub" and ent.kind == "Interactive" and ent.interactive_kind == "chest"
    ):
        return "room"
    if structure in ("parkour", "shaft") and ent.kind == "Pickup":
        return "ledge"
    return None
