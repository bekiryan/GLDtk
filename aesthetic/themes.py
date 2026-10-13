"""Theme Mapping for GLDtk.

Maps keywords in a level description to a complete visual identity: tileset,
background colour, tile addresses and enemy archetype. Themes are loaded from
the asset library manifest (``assets/manifest.json``, see ``aesthetic.library``);
adding a theme means adding a sheet PNG and a manifest entry, with no code
changes.

Theme sheet layout (each theme has its own sheet, ``base_x = 0``)
-----------------------------------------------------------------
The sheet is 5 tiles wide; rows 0-2 hold the 14 tile roles:

  Row 0:  CORNER_TL  EDGE_T  CORNER_TR  CAP_LEFT   CAP_RIGHT
  Row 1:  EDGE_L     CENTER  EDGE_R     FILL        SINGLE
  Row 2:  CORNER_BL  EDGE_B  CORNER_BR  HAZARD_TILE  --

Extra rows below may hold variants (declared in the manifest per role); the
auto-tiler picks one deterministically from the cell position.

The .ldtk file is usually written elsewhere, so ``copy_theme_assets`` copies a
theme's sheet next to the output and returns the relative path that the .ldtk
references (``Theme.tileset_rel_path``).
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Union

from .library import Library, clear_cache, load_library, png_size


# ─────────────────────────────────────────────────────────────────────────────
# Tile roles
# ─────────────────────────────────────────────────────────────────────────────


class TileRole(str, Enum):
    """Semantic role of a tile within a platform or hazard region."""

    # Thin horizontal platform (height = 1 tile)
    SINGLE = "single"  # isolated, no neighbours
    CAP_LEFT = "cap_left"  # left end of platform (has east neighbour)
    CAP_RIGHT = "cap_right"  # right end            (has west neighbour)
    FILL = "fill"  # interior             (has east + west)
    # Rectangular platform corners (height ≥ 2 tiles)
    CORNER_TL = "corner_tl"
    CORNER_TR = "corner_tr"
    CORNER_BL = "corner_bl"
    CORNER_BR = "corner_br"
    # Rectangular platform edges
    EDGE_T = "edge_t"
    EDGE_B = "edge_b"
    EDGE_L = "edge_l"
    EDGE_R = "edge_r"
    CENTER = "center"
    # Hazard overlay
    HAZARD_TILE = "hazard_tile"


# ─────────────────────────────────────────────────────────────────────────────
# Theme data
# ─────────────────────────────────────────────────────────────────────────────

# Folder (relative to the .ldtk file) that copy_theme_assets writes into.
ASSET_DIR = "gldtk_assets"


@dataclass
class TileAddress:
    """Pixel-level source position inside the tileset image."""

    src_x: int
    src_y: int


def _region(base_x: int = 0, tile_size: int = 16) -> Dict[TileRole, TileAddress]:
    """Build a tile_map for the 5x3 role region starting at *base_x* in a sheet."""
    b = base_x
    s = tile_size
    return {
        TileRole.CORNER_TL: TileAddress(b + 0 * s, 0 * s),
        TileRole.EDGE_T: TileAddress(b + 1 * s, 0 * s),
        TileRole.CORNER_TR: TileAddress(b + 2 * s, 0 * s),
        TileRole.CAP_LEFT: TileAddress(b + 3 * s, 0 * s),
        TileRole.CAP_RIGHT: TileAddress(b + 4 * s, 0 * s),
        TileRole.EDGE_L: TileAddress(b + 0 * s, 1 * s),
        TileRole.CENTER: TileAddress(b + 1 * s, 1 * s),
        TileRole.EDGE_R: TileAddress(b + 2 * s, 1 * s),
        TileRole.FILL: TileAddress(b + 3 * s, 1 * s),
        TileRole.SINGLE: TileAddress(b + 4 * s, 1 * s),
        TileRole.CORNER_BL: TileAddress(b + 0 * s, 2 * s),
        TileRole.EDGE_B: TileAddress(b + 1 * s, 2 * s),
        TileRole.CORNER_BR: TileAddress(b + 2 * s, 2 * s),
        TileRole.HAZARD_TILE: TileAddress(b + 3 * s, 2 * s),
    }


@dataclass
class Theme:
    """Complete visual identity for a level."""

    identifier: str
    display_name: str
    keywords: List[str]
    background_color: str
    tileset_uid: int
    tileset_rel_path: str  # path the .ldtk file references (see copy_theme_assets)
    tileset_px_width: int
    tileset_px_height: int
    tile_map: Dict[TileRole, TileAddress]
    enemy_type: str  # enemy archetype label stored in entity metadata
    enemy_rate: float  # enemies per golden-path node (0-1 scale factor)
    variants: Dict[TileRole, List[TileAddress]] = field(default_factory=dict)
    sheet_path: Optional[Path] = None  # absolute path of the sheet in the library

    def tile(self, role: TileRole) -> TileAddress:
        """Return the address for *role*, falling back to SINGLE if missing."""
        return self.tile_map.get(role, self.tile_map[TileRole.SINGLE])

    def tile_for(self, role: TileRole, col: int, row: int) -> TileAddress:
        """Like ``tile`` but picks deterministically among the role's variants."""
        extra = self.variants.get(role)
        if not extra:
            return self.tile(role)
        choices = [self.tile(role), *extra]
        return choices[((col * 73856093) ^ (row * 19349663)) % len(choices)]


def _theme_from_entry(lib: Library, entry, index: int) -> Theme:
    ts = lib.manifest.tile_size
    sheet = lib.theme_sheet_path(entry)
    w, h = png_size(sheet.read_bytes()[:32])
    variants: Dict[TileRole, List[TileAddress]] = {
        TileRole(role): [TileAddress(c * ts, r * ts) for c, r in cells]
        for role, cells in entry.variants.items()
    }
    return Theme(
        identifier=entry.id,
        display_name=entry.display_name,
        keywords=list(entry.keywords),
        background_color=entry.background_color,
        tileset_uid=entry.uid if entry.uid is not None else 100 + index,
        tileset_rel_path=f"{ASSET_DIR}/{entry.sheet}",
        tileset_px_width=w,
        tileset_px_height=h,
        tile_map=_region(0, ts),
        enemy_type=entry.enemy_type,
        enemy_rate=entry.enemy_rate,
        variants=variants,
        sheet_path=sheet,
    )


def build_themes(library: Optional[Library] = None) -> Dict[str, Theme]:
    """Build the theme registry (including ``"default"``) from a library."""
    lib = library or load_library()
    themes: Dict[str, Theme] = {
        e.id: _theme_from_entry(lib, e, i) for i, e in enumerate(lib.themes)
    }
    first = next(iter(themes.values()))
    # "default": used when no keywords match -- first theme's art, neutral enemy.
    themes["default"] = Theme(
        **{
            **first.__dict__,
            "identifier": "default",
            "display_name": "Default",
            "keywords": [],
            "background_color": "#1a1a2e",
            "enemy_type": "Enemy",
            "enemy_rate": 0.5,
        }
    )
    return themes


# ─────────────────────────────────────────────────────────────────────────────
# Theme registry
# ─────────────────────────────────────────────────────────────────────────────

THEMES: Dict[str, Theme] = build_themes()


def reload_themes(library: Optional[Library] = None) -> Dict[str, Theme]:
    """Rebuild ``THEMES`` in place (call after ``add_theme``)."""
    if library is None:
        clear_cache()
    new = build_themes(library)
    THEMES.clear()
    THEMES.update(new)
    return THEMES


def copy_theme_assets(theme: Theme, ldtk_dir: Union[str, Path]) -> str:
    """Copy *theme*'s sheet next to the output .ldtk and return the relative path.

    The file lands at ``<ldtk_dir>/<theme.tileset_rel_path>`` (by default
    ``gldtk_assets/themes/<id>.png``); the returned path is relative to
    *ldtk_dir*, i.e. exactly what the .ldtk file must reference.
    """
    if theme.sheet_path is None or not theme.sheet_path.exists():
        raise FileNotFoundError(f"theme {theme.identifier!r} has no sheet on disk")
    dest = Path(ldtk_dir) / theme.tileset_rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(theme.sheet_path, dest)
    return theme.tileset_rel_path


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
    Ties go to the theme listed first in the manifest.
    """
    if override and override.lower() in THEMES:
        return THEMES[override.lower()]

    tokens = set(re.findall(r"[a-z]+", description.lower()))

    scores: Dict[str, int] = {
        tid: sum(1 for kw in theme.keywords if kw in tokens)
        for tid, theme in THEMES.items()
        if tid != "default"
    }
    if not scores:
        return THEMES["default"]
    best_id, best_score = max(scores.items(), key=lambda x: x[1])
    return THEMES[best_id] if best_score > 0 else THEMES["default"]
