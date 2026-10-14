"""Aesthetic integration pipeline for GLDtk.

Builds all Part 4 outputs in one deterministic pass:
1) Theme selection from description keywords (plus one theme per section:
   ``ir.metadata["sections"][i]["mapped_theme"]``, set by the server)
2) Auto-tiling from the IR tile grid, each section's band in its own theme
   (columns in a horizontal level, rows in a vertical one)
3) Golden-path entity placement
4) The entity sprite atlas
5) AestheticData payload for LDtk serialization

``copy_aesthetic_assets`` writes every sheet the level uses (and the atlas)
next to the ``.ldtk``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from layout.ir import IREntity, IRLevel
from layout.ldtk_adapter import AestheticData, TilesetInfo
from layout.sugiyama import NodeLayout
from symbolic.schema import AbstractLevelGraph

from .autotile import autotile_level
from .entities import place_entities
from .sprites import SpriteAtlas, build_sprite_atlas, write_sprite_atlas
from .themes import THEMES, Theme, copy_theme_assets, detect_theme


@dataclass
class AestheticBuildResult:
    """All outputs produced by the aesthetic pass."""

    theme: Theme  # primary theme (the first section's)
    aesthetic: AestheticData
    placed_entities: List[IREntity]
    themes: List[Theme] = field(default_factory=list)  # every theme sheet in use, primary first
    sprite_atlas: Optional[SpriteAtlas] = None


def _tileset_identifier(theme: Theme) -> str:
    return re.sub(r"\W", "_", theme.identifier).capitalize()


SectionRect = Tuple[int, int, int, int, Theme]  # col_start, col_end, row_start, row_end, theme


def _section_spans(ir_level: IRLevel) -> List[SectionRect]:
    """The cell rectangle and theme of each section band with a ``mapped_theme``.

    Bands without ``row_start`` / ``row_end`` (files from before vertical
    sections) cover every row.
    """
    spans = []
    last_row = ir_level.height_tiles - 1
    for band in ir_level.metadata.get("sections") or []:
        mapped = band.get("mapped_theme") if isinstance(band, dict) else None
        if mapped in THEMES:
            spans.append((
                int(band["col_start"]),
                int(band["col_end"]),
                int(band.get("row_start", 0)),
                int(band.get("row_end", last_row)),
                THEMES[mapped],
            ))
    return spans


def _first_section(spans: List[SectionRect]) -> Theme:
    """The theme of the first section: leftmost, or bottom-most in a vertical level."""
    return min(spans, key=lambda sp: (sp[0], -sp[3]))[4]


def copy_aesthetic_assets(
    result: "AestheticBuildResult", ldtk_dir: Union[str, Path], all_themes: bool = True
) -> List[str]:
    """Copy every theme sheet in use and write the sprite atlas next to the
    ``.ldtk``; returns the relative paths of those files.

    With *all_themes* every other library sheet is copied too (about 1 KB
    each), so the first generate in a project writes them all and later
    generates create no new files: LDtk's file watcher would otherwise reload
    a tileset that appears after the level is loaded and mark it modified.
    Unchanged files are never rewritten (``copy_theme_assets``).
    """
    used = result.themes or [result.theme]
    paths = [copy_theme_assets(t, ldtk_dir) for t in used]
    if all_themes:
        for theme in list(THEMES.values()):
            if theme in used or theme.sheet_path is None or not theme.sheet_path.exists():
                continue
            copy_theme_assets(theme, ldtk_dir)
    if result.sprite_atlas is not None:
        paths.append(write_sprite_atlas(result.sprite_atlas, ldtk_dir))
    return paths


def build_aesthetic_layer(
    description: str,
    graph: AbstractLevelGraph,
    node_layouts: Dict[str, NodeLayout],
    ir_level: IRLevel,
    theme_override: Optional[str] = None,
    density: float = 1.0,
    seed: int = 42,
    attach_entities: bool = True,
    custom_enemy_types: Optional[List[str]] = None,
    place_random_entities: bool = True,
    entity_sprites: bool = True,
) -> AestheticBuildResult:
    """Build and optionally attach all Part 4 outputs to an IR level.

    Parameters
    ----------
    description        : free-text level description for keyword theme detection.
    graph              : abstract graph used for golden-path driven placements.
    node_layouts       : mapping from ``sugiyama_layout``.
    ir_level           : rasterized level to auto-tile and enrich with entities.
    theme_override     : explicit theme identifier (e.g. "dungeon", "forest").
    density            : global multiplier for collectible/enemy density.
    seed               : deterministic RNG seed for placement decisions.
    attach_entities    : append generated entities to ``ir_level.entities``.
    custom_enemy_types : if provided, enemies are drawn from this list instead
                         of the theme's default enemy archetype.
    place_random_entities : False skips the random coin / enemy placement, so
                         the level holds only the entities the spec asked for.
    entity_sprites     : build the sprite atlas so entities show their library
                         sprite in LDtk (needs Pillow; skipped without it).

    Returns
    -------
    AestheticBuildResult containing theme, serialized aesthetic payload,
    and the list of entities generated by the placement pass.
    """
    spans = _section_spans(ir_level)
    if spans and not theme_override:
        theme = _first_section(spans)  # the first section's theme is the primary one
    else:
        theme = detect_theme(description, override=theme_override)

    def theme_for_cell(col: int, row: int) -> Theme:
        for c0, c1, r0, r1, th in spans:
            if c0 <= col <= c1 and r0 <= row <= r1:
                return th
        return theme

    tile_entries = autotile_level(ir_level, theme, theme_for_cell=theme_for_cell)
    # Every sheet the tiles use, the primary theme's first.
    themes: List[Theme] = [theme]
    for te in tile_entries:
        if all(t.tileset_uid != te.tileset_uid for t in themes):
            themes.append(next(t for t in THEMES.values() if t.tileset_uid == te.tileset_uid))
    entities: List[IREntity] = []
    if place_random_entities:
        entities = place_entities(
            graph=graph,
            node_layouts=node_layouts,
            theme=theme,
            density=density,
            seed=seed,
            custom_enemy_types=custom_enemy_types,
        )

    if attach_entities:
        ir_level.entities.extend(entities)

    atlas = build_sprite_atlas() if entity_sprites else None
    aesthetic = AestheticData(
        tile_entries=tile_entries,
        bg_color=theme.background_color,
        tileset_uid=theme.tileset_uid,
        tileset_rel_path=theme.tileset_rel_path,
        tileset_px_width=theme.tileset_px_width,
        tileset_px_height=theme.tileset_px_height,
        tileset_identifier=_tileset_identifier(theme),
        extra_tilesets=[
            TilesetInfo(t.tileset_uid, t.tileset_rel_path, t.tileset_px_width,
                        t.tileset_px_height, _tileset_identifier(t))
            for t in themes[1:]
        ],
        entity_atlas=atlas.to_entity_atlas() if atlas else None,
    )

    return AestheticBuildResult(
        theme=theme,
        aesthetic=aesthetic,
        placed_entities=entities,
        themes=themes,
        sprite_atlas=atlas,
    )
