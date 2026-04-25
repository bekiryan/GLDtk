"""Theme Mapping for GLDtk.

A dictionary-based system that maps keywords in a level description to a
complete visual identity: tileset, background colour, tile addresses, and
enemy archetype.

Tileset layout convention
--------------------------
Each theme occupies a 5×3 tile region inside a shared 256×256 tileset image.
The region starts at (base_x, 0) and encodes all 14 tile roles:

  Row 0:  CORNER_TL  EDGE_T  CORNER_TR  CAP_LEFT   CAP_RIGHT
  Row 1:  EDGE_L     CENTER  EDGE_R     FILL        SINGLE
  Row 2:  CORNER_BL  EDGE_B  CORNER_BR  HAZARD_TILE  —

  base_x offsets:  Dungeon=0  Forest=80  Sky=160  (each region is 80 px wide)

Artists replace the placeholder PNG with real tiles at those offsets and the
code needs no changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Tile roles
# ─────────────────────────────────────────────────────────────────────────────

class TileRole(str, Enum):
    """Semantic role of a tile within a platform or hazard region."""
    # Thin horizontal platform (height = 1 tile)
    SINGLE     = "single"      # isolated, no neighbours
    CAP_LEFT   = "cap_left"    # left end of platform (has east neighbour)
    CAP_RIGHT  = "cap_right"   # right end            (has west neighbour)
    FILL       = "fill"        # interior             (has east + west)
    # Rectangular platform corners (height ≥ 2 tiles)
    CORNER_TL  = "corner_tl"
    CORNER_TR  = "corner_tr"
    CORNER_BL  = "corner_bl"
    CORNER_BR  = "corner_br"
    # Rectangular platform edges
    EDGE_T     = "edge_t"
    EDGE_B     = "edge_b"
    EDGE_L     = "edge_l"
    EDGE_R     = "edge_r"
    CENTER     = "center"
    # Hazard overlay
    HAZARD_TILE = "hazard_tile"


# ─────────────────────────────────────────────────────────────────────────────
# Theme data
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TileAddress:
    """Pixel-level source position inside the tileset image."""
    src_x: int
    src_y: int


def _region(base_x: int, tile_size: int = 16) -> Dict[TileRole, TileAddress]:
    """Build a tile_map for a theme region starting at *base_x* in the tileset."""
    b = base_x
    s = tile_size
    return {
        TileRole.CORNER_TL:  TileAddress(b + 0*s, 0*s),
        TileRole.EDGE_T:     TileAddress(b + 1*s, 0*s),
        TileRole.CORNER_TR:  TileAddress(b + 2*s, 0*s),
        TileRole.CAP_LEFT:   TileAddress(b + 3*s, 0*s),
        TileRole.CAP_RIGHT:  TileAddress(b + 4*s, 0*s),
        TileRole.EDGE_L:     TileAddress(b + 0*s, 1*s),
        TileRole.CENTER:     TileAddress(b + 1*s, 1*s),
        TileRole.EDGE_R:     TileAddress(b + 2*s, 1*s),
        TileRole.FILL:       TileAddress(b + 3*s, 1*s),
        TileRole.SINGLE:     TileAddress(b + 4*s, 1*s),
        TileRole.CORNER_BL:  TileAddress(b + 0*s, 2*s),
        TileRole.EDGE_B:     TileAddress(b + 1*s, 2*s),
        TileRole.CORNER_BR:  TileAddress(b + 2*s, 2*s),
        TileRole.HAZARD_TILE: TileAddress(b + 3*s, 2*s),
    }


@dataclass
class Theme:
    """Complete visual identity for a level."""
    identifier:      str
    display_name:    str
    keywords:        List[str]
    background_color: str
    tileset_uid:     int
    tileset_rel_path: str
    tileset_px_width:  int
    tileset_px_height: int
    tile_map:        Dict[TileRole, TileAddress]
    enemy_type:      str   # enemy archetype label stored in entity metadata
    enemy_rate:      float # enemies per golden-path node (0–1 scale factor)

    def tile(self, role: TileRole) -> TileAddress:
        """Return the address for *role*, falling back to SINGLE if missing."""
        return self.tile_map.get(role, self.tile_map[TileRole.SINGLE])


# ─────────────────────────────────────────────────────────────────────────────
# Theme registry
# ─────────────────────────────────────────────────────────────────────────────

THEMES: Dict[str, Theme] = {
    "dungeon": Theme(
        identifier="dungeon",
        display_name="Dungeon",
        keywords=[
            "dungeon", "cave", "underground", "dark", "stone", "castle",
            "crypt", "tomb", "skeleton", "bone", "cursed", "ancient",
        ],
        background_color="#0d0d1a",
        tileset_uid=100,
        tileset_rel_path="tilesets/dungeon.png",
        tileset_px_width=256,
        tileset_px_height=256,
        tile_map=_region(base_x=0),
        enemy_type="Skeleton",
        enemy_rate=0.6,
    ),

    "forest": Theme(
        identifier="forest",
        display_name="Forest",
        keywords=[
            "forest", "jungle", "nature", "grass", "tree", "wood", "green",
            "moss", "vine", "leaf", "garden", "grove", "outdoor", "wildlife",
        ],
        background_color="#0a1f0a",
        tileset_uid=101,
        tileset_rel_path="tilesets/forest.png",
        tileset_px_width=256,
        tileset_px_height=256,
        tile_map=_region(base_x=80),
        enemy_type="Slime",
        enemy_rate=0.4,
    ),

    "sky": Theme(
        identifier="sky",
        display_name="Sky",
        keywords=[
            "sky", "cloud", "aerial", "floating", "heaven", "wind", "air",
            "blue", "high", "above", "celestial", "aurora", "storm",
        ],
        background_color="#1a2e4a",
        tileset_uid=102,
        tileset_rel_path="tilesets/sky.png",
        tileset_px_width=256,
        tileset_px_height=256,
        tile_map=_region(base_x=160),
        enemy_type="Harpy",
        enemy_rate=0.5,
    ),
}

# Alias: default theme used when no keywords match
THEMES["default"] = Theme(
    identifier="default",
    display_name="Default",
    keywords=[],
    background_color="#1a1a2e",
    tileset_uid=100,
    tileset_rel_path="tilesets/dungeon.png",
    tileset_px_width=256,
    tileset_px_height=256,
    tile_map=_region(base_x=0),
    enemy_type="Enemy",
    enemy_rate=0.5,
)


# ─────────────────────────────────────────────────────────────────────────────
# Keyword-based theme detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_theme(description: str, override: Optional[str] = None) -> Theme:
    """Return the best-matching theme for *description*.

    Parameters
    ----------
    description : the level description (free text).
    override    : explicit theme identifier that bypasses keyword matching.

    Returns
    -------
    Theme — never None; falls back to "default" when no keywords match.
    """
    if override and override.lower() in THEMES:
        return THEMES[override.lower()]

    tokens = set(re.findall(r"[a-z]+", description.lower()))

    scores: Dict[str, int] = {tid: 0 for tid in THEMES if tid != "default"}
    for tid, theme in THEMES.items():
        if tid == "default":
            continue
        scores[tid] = sum(1 for kw in theme.keywords if kw in tokens)

    best_id, best_score = max(scores.items(), key=lambda x: x[1])
    return THEMES[best_id] if best_score > 0 else THEMES["default"]
