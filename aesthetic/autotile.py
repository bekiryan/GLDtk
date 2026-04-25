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

The resulting TileEntry list is consumed by ``layout.ldtk_adapter.to_ldtk_dict``
when an AestheticData payload is provided.
"""

from __future__ import annotations

from typing import List

from layout.ir import IRLevel, TileValue
from layout.ldtk_adapter import TileEntry
from .themes import Theme, TileRole


# ─────────────────────────────────────────────────────────────────────────────
# Bitmask → TileRole lookup  (N=8  S=4  E=2  W=1)
# ─────────────────────────────────────────────────────────────────────────────

_BITMASK_TO_ROLE: dict[int, TileRole] = {
    0b0000: TileRole.SINGLE,      # isolated
    0b0001: TileRole.CAP_RIGHT,   # W only    → right end of platform
    0b0010: TileRole.CAP_LEFT,    # E only    → left  end of platform
    0b0011: TileRole.FILL,        # E + W     → interior of thin platform
    0b0100: TileRole.SINGLE,      # S only    → top of single-column pillar
    0b0101: TileRole.CORNER_TR,   # S + W     → top-right corner of block
    0b0110: TileRole.CORNER_TL,   # S + E     → top-left  corner of block
    0b0111: TileRole.EDGE_T,      # S + E + W → top edge of wide block
    0b1000: TileRole.SINGLE,      # N only    → bottom of single-column pillar
    0b1001: TileRole.CORNER_BR,   # N + W     → bottom-right corner
    0b1010: TileRole.CORNER_BL,   # N + E     → bottom-left  corner
    0b1011: TileRole.EDGE_B,      # N + E + W → bottom edge of wide block
    0b1100: TileRole.FILL,        # N + S     → column middle (no EW)
    0b1101: TileRole.EDGE_R,      # N + S + W → right edge of tall block
    0b1110: TileRole.EDGE_L,      # N + S + E → left  edge of tall block
    0b1111: TileRole.CENTER,      # all four  → interior of filled block
}


# ─────────────────────────────────────────────────────────────────────────────
# Core helpers
# ─────────────────────────────────────────────────────────────────────────────

def _neighbour_mask(ir: IRLevel, col: int, row: int, tile_val: TileValue) -> int:
    """Return the 4-bit NSEW mask for cell (col, row) checking same *tile_val*."""
    def same(c: int, r: int) -> bool:
        return ir.in_bounds(c, r) and ir.get_tile(c, r) == tile_val

    n = same(col,     row - 1)
    s = same(col,     row + 1)
    e = same(col + 1, row    )
    w = same(col - 1, row    )
    return (n << 3) | (s << 2) | (e << 1) | w


def _role_to_entry(col: int, row: int, role: TileRole, theme: Theme) -> TileEntry:
    addr = theme.tile(role)
    return TileEntry(col=col, row=row, src_x=addr.src_x, src_y=addr.src_y)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def autotile_level(ir: IRLevel, theme: Theme) -> List[TileEntry]:
    """Generate a TileEntry list for every non-empty cell in *ir*.

    Each cell receives the tile that visually matches its neighbourhood:
    corners, edges, caps, fills, and hazard spikes.

    Parameters
    ----------
    ir    : rasterised level grid.
    theme : active theme supplying the tile address lookup.

    Returns
    -------
    List[TileEntry] ready to be stored in ``AestheticData.tile_entries``.
    """
    entries: List[TileEntry] = []

    for row in range(ir.height_tiles):
        for col in range(ir.width_tiles):
            cell = ir.get_tile(col, row)

            if cell == TileValue.EMPTY:
                continue

            if cell == TileValue.HAZARD:
                entries.append(_role_to_entry(col, row, TileRole.HAZARD_TILE, theme))
                continue

            # SOLID tile — derive role from 4-bit neighbourhood mask
            mask = _neighbour_mask(ir, col, row, TileValue.SOLID)
            role = _BITMASK_TO_ROLE[mask]
            entries.append(_role_to_entry(col, row, role, theme))

    return entries
