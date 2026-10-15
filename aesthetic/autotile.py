"""Auto-tiling Rules for GLDtk.

Converts a rasterised IRLevel tile grid into a list of visual TileEntry
objects for the LDtk Tiles layer.

Algorithm: 4-bit neighbourhood bitmask (NSEW)
----------------------------------------------
For every non-empty cell (col, row) in the grid we inspect the four cardinal
neighbours and produce a 4-bit mask:

    bit 3 (8): North neighbour is the SAME tile type  (row − 1)
    bit 2 (4): South neighbour is the SAME tile type  (row + 1)
    bit 1 (2): East  neighbour is the SAME tile type  (col + 1)
    bit 0 (1): West  neighbour is the SAME tile type  (col − 1)

The 16 possible masks map cleanly to TileRole values, covering:
  • Thin horizontal platforms (no N/S neighbours)
  • Full rectangular block interiors and all 8 border variants
  • Isolated single tiles

Hazard tiles always receive TileRole.HAZARD_TILE regardless of neighbours.

Per-section themes: the mask only looks at the grid, never at the theme, so a
platform that crosses a section seam keeps the roles of one continuous
platform (no cap at the seam); each cell just takes its tile from the sheet of
the theme owning it (``theme_for_cell``: the section band holding the cell — a
column band in horizontal levels, a row band in vertical ones).

Terrain levels (v3 Part 21): when a section is carved from rock (its section
dict has a ``structure`` other than open), the level border counts as solid
rock, so the frame and the rock mass around carved rooms read as one body —
interior cells get the theme's CENTER variants, only the faces toward the air
get edges and corners.  Open-air levels are tiled exactly as before.

The resulting TileEntry list is consumed by ``layout.ldtk_adapter.to_ldtk_dict``
when an AestheticData payload is provided.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from layout.ir import IRLevel, TileValue
from layout.ldtk_adapter import TileEntry
from .themes import Theme, TileRole


# ─────────────────────────────────────────────────────────────────────────────
# Bitmask → TileRole lookup  (N=8  S=4  E=2  W=1)
# ─────────────────────────────────────────────────────────────────────────────

_BITMASK_TO_ROLE: dict[int, TileRole] = {
    0b0000: TileRole.SINGLE,  # isolated
    0b0001: TileRole.CAP_RIGHT,  # W only    → right end of platform
    0b0010: TileRole.CAP_LEFT,  # E only    → left  end of platform
    0b0011: TileRole.FILL,  # E + W     → interior of thin platform
    0b0100: TileRole.SINGLE,  # S only    → top of single-column pillar
    0b0101: TileRole.CORNER_TR,  # S + W     → top-right corner of block
    0b0110: TileRole.CORNER_TL,  # S + E     → top-left  corner of block
    0b0111: TileRole.EDGE_T,  # S + E + W → top edge of wide block
    0b1000: TileRole.SINGLE,  # N only    → bottom of single-column pillar
    0b1001: TileRole.CORNER_BR,  # N + W     → bottom-right corner
    0b1010: TileRole.CORNER_BL,  # N + E     → bottom-left  corner
    0b1011: TileRole.EDGE_B,  # N + E + W → bottom edge of wide block
    0b1100: TileRole.FILL,  # N + S     → column middle (no EW)
    0b1101: TileRole.EDGE_R,  # N + S + W → right edge of tall block
    0b1110: TileRole.EDGE_L,  # N + S + E → left  edge of tall block
    0b1111: TileRole.CENTER,  # all four  → interior of filled block
}


# ─────────────────────────────────────────────────────────────────────────────
# Core helpers
# ─────────────────────────────────────────────────────────────────────────────


TERRAIN_STRUCTURES = ("corridor", "parkour", "labyrinth", "arena", "shaft", "hub")


def has_terrain(ir: IRLevel) -> bool:
    """True when a section of *ir* is carved from rock (``ir.metadata["sections"]``)."""
    return any(
        isinstance(s, dict) and s.get("structure") in TERRAIN_STRUCTURES
        for s in (ir.metadata.get("sections") or [])
    )


def _neighbour_mask(
    ir: IRLevel, col: int, row: int, tile_val: TileValue, frame: bool = False
) -> int:
    """Return the 4-bit NSEW mask for cell (col, row) checking same *tile_val*.

    With *frame*, cells outside the level count as solid rock."""

    def same(c: int, r: int) -> bool:
        if not ir.in_bounds(c, r):
            return frame and tile_val == TileValue.SOLID
        return ir.get_tile(c, r) == tile_val

    n = same(col, row - 1)
    s = same(col, row + 1)
    e = same(col + 1, row)
    w = same(col - 1, row)
    return (n << 3) | (s << 2) | (e << 1) | w


def _role_to_entry(col: int, row: int, role: TileRole, theme: Theme) -> TileEntry:
    addr = theme.tile_for(role, col, row)  # variants chosen deterministically
    return TileEntry(col=col, row=row, src_x=addr.src_x, src_y=addr.src_y,
                     tileset_uid=theme.tileset_uid)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def autotile_level(
    ir: IRLevel,
    theme: Theme,
    theme_for_col: Optional[Callable[[int], Theme]] = None,
    theme_for_cell: Optional[Callable[[int, int], Theme]] = None,
) -> List[TileEntry]:
    """Generate a TileEntry list for every non-empty cell in *ir*.

    Each cell receives the tile that visually matches its neighbourhood:
    corners, edges, caps, fills, and hazard spikes.

    Parameters
    ----------
    ir    : rasterised level grid.
    theme : active theme supplying the tile address lookup.
    theme_for_col : optional per-column theme (sections); defaults to *theme*.
    theme_for_cell : optional per-cell ``(col, row)`` theme; wins over
                    *theme_for_col*.
                    Every entry records its theme's ``tileset_uid``.

    Returns
    -------
    List[TileEntry] ready to be stored in ``AestheticData.tile_entries``.
    """
    entries: List[TileEntry] = []
    base_theme = theme
    frame = has_terrain(ir)

    for row in range(ir.height_tiles):
        for col in range(ir.width_tiles):
            cell = ir.get_tile(col, row)

            if cell == TileValue.EMPTY:
                continue
            if theme_for_cell:
                theme = theme_for_cell(col, row)
            else:
                theme = theme_for_col(col) if theme_for_col else base_theme

            if cell == TileValue.HAZARD:
                entries.append(_role_to_entry(col, row, TileRole.HAZARD_TILE, theme))
                continue

            # SOLID tile — derive role from 4-bit neighbourhood mask
            mask = _neighbour_mask(ir, col, row, TileValue.SOLID, frame)
            role = _BITMASK_TO_ROLE[mask]
            entries.append(_role_to_entry(col, row, role, theme))

    return entries
