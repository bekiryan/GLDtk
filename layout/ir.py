"""IRLevel — version-independent Internal Representation of a level.

Acts as the bridge between the Abstract Level Graph (symbolic domain) and any
concrete file format (LDtk, Tiled, …).  No format-specific keys live here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional


class TileValue(IntEnum):
    """Canonical integer codes written into the grid."""
    EMPTY  = 0
    SOLID  = 1
    HAZARD = 2


@dataclass
class IREntity:
    """A non-tile game object placed on the entity layer."""
    entity_type: str          # "Start" | "Exit"
    col: int                  # tile column of the entity's anchor cell
    row: int                  # tile row  of the entity's anchor cell
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass
class IRLevel:
    """A fully-specified level ready for serialization.

    grid[row][col] stores a TileValue.  Row 0 is the visual top of the level.
    """

    identifier: str
    width_tiles: int
    height_tiles: int
    tile_size: int                              # pixels per tile edge
    grid: List[List[TileValue]]                 # [row][col]
    entities: List[IREntity] = field(default_factory=list)
    metadata: Dict[str, object] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def empty(
        cls,
        identifier: str,
        width_tiles: int,
        height_tiles: int,
        tile_size: int = 16,
        **metadata: object,
    ) -> IRLevel:
        grid = [[TileValue.EMPTY] * width_tiles for _ in range(height_tiles)]
        return cls(
            identifier=identifier,
            width_tiles=width_tiles,
            height_tiles=height_tiles,
            tile_size=tile_size,
            grid=grid,
            metadata=dict(metadata),
        )

    # ------------------------------------------------------------------
    # Grid accessors
    # ------------------------------------------------------------------

    def in_bounds(self, col: int, row: int) -> bool:
        return 0 <= col < self.width_tiles and 0 <= row < self.height_tiles

    def set_tile(self, col: int, row: int, value: TileValue) -> None:
        if not self.in_bounds(col, row):
            return
        self.grid[row][col] = value

    def get_tile(self, col: int, row: int) -> TileValue:
        if not self.in_bounds(col, row):
            return TileValue.EMPTY
        return self.grid[row][col]

    def fill_rect(
        self,
        col: int,
        row: int,
        width: int,
        height: int,
        value: TileValue,
    ) -> None:
        for r in range(row, row + height):
            for c in range(col, col + width):
                self.set_tile(c, r, value)

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def to_int_grid_csv(self) -> List[int]:
        """Flat row-major list of int values (top-to-bottom, left-to-right)."""
        return [int(cell) for row in self.grid for cell in row]

    @property
    def px_width(self) -> int:
        return self.width_tiles * self.tile_size

    @property
    def px_height(self) -> int:
        return self.height_tiles * self.tile_size

    def __repr__(self) -> str:
        return (
            f"IRLevel('{self.identifier}', "
            f"{self.width_tiles}×{self.height_tiles} tiles, "
            f"{len(self.entities)} entities)"
        )

    # ------------------------------------------------------------------
    # Debug / introspection
    # ------------------------------------------------------------------

    def ascii_render(self, entity_chars: Optional[Dict[str, str]] = None) -> str:
        """Return a compact ASCII art view of the grid (useful in tests/REPL)."""
        _chars = entity_chars or {}
        _tile_chars = {TileValue.EMPTY: ".", TileValue.SOLID: "#", TileValue.HAZARD: "^"}
        entity_positions = {(e.col, e.row): _chars.get(e.entity_type, e.entity_type[0]) for e in self.entities}
        lines = []
        for r, row in enumerate(self.grid):
            line = ""
            for c, cell in enumerate(row):
                line += entity_positions.get((c, r), _tile_chars[cell])
            lines.append(line)
        return "\n".join(lines)
