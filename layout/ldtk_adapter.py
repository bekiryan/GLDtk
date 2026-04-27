"""LDtk Adapter — serialises an IRLevel to a valid LDtk JSON v1.5.3 document.

Spec reference: https://ldtk.io/json/
All IIDs are v4 UUIDs.  Layer/entity UIDs are stable integers derived from
the LDtkConfig so they round-trip correctly when the file is reopened in the
LDtk editor.

Aesthetic integration
---------------------
Pass an ``AestheticData`` instance to ``to_ldtk_dict`` to activate:
  • A ``Tiles`` visual layer (auto-tiled platformer graphics)
  • Theme-driven background colour and tileset reference
  • Collectible / enemy entity defs (Coin, Key, Enemy, Checkpoint)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ir import IREntity, IRLevel, TileValue


# ─────────────────────────────────────────────────────────────────────────────
# Aesthetic carrier types  (defined here to avoid circular imports with the
# aesthetic package, which imports from layout)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TileEntry:
    """One visual tile to be placed in the LDtk Tiles layer."""
    col:   int
    row:   int
    src_x: int       # pixel X in the tileset image
    src_y: int       # pixel Y in the tileset image
    flip_x: bool = False
    flip_y: bool = False


@dataclass
class AestheticData:
    """Aesthetic layer payload consumed by ``to_ldtk_dict``."""
    tile_entries:       List[TileEntry] = field(default_factory=list)
    bg_color:           str  = "#1a1a2e"
    tileset_uid:        int  = 100
    tileset_rel_path:   str  = "tilesets/dungeon.png"
    tileset_px_width:   int  = 256
    tileset_px_height:  int  = 256


# ─────────────────────────────────────────────────────────────────────────────
# Configuration (UID mapping)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LDtkConfig:
    """Static UID and appearance settings.

    Keep these stable across calls so the same project file can be updated
    incrementally without breaking entity/layer cross-references.
    """

    # Layer def UIDs
    collisions_layer_uid:  int = 1
    entities_layer_uid:    int = 2
    tiles_layer_uid:       int = 3    # visual Tiles layer (aesthetic)

    # Entity def UIDs — structural
    start_entity_uid: int = 10
    exit_entity_uid:  int = 11

    # Entity def UIDs — aesthetic
    coin_entity_uid:       int = 20
    key_entity_uid:        int = 21
    enemy_entity_uid:      int = 22
    checkpoint_entity_uid: int = 23

    # IntGrid value codes (must match TileValue enum)
    solid_value:  int = int(TileValue.SOLID)
    hazard_value: int = int(TileValue.HAZARD)

    # Appearance — structural entities
    solid_color:  str = "#8b8b8b"
    hazard_color: str = "#ff4422"
    start_color:  str = "#00cc44"
    exit_color:   str = "#ffcc00"

    # Appearance — aesthetic entities
    coin_color:       str = "#ffd700"
    key_color:        str = "#cc44ff"
    enemy_color:      str = "#ff2244"
    checkpoint_color: str = "#44aaff"

    # LDtk project metadata
    json_version:     str = "1.5.3"
    background_color: str = "#1a1a2e"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _new_iid() -> str:
    return str(uuid.uuid4())


# ─────────────────────────────────────────────────────────────────────────────
# Layer definition builders
# ─────────────────────────────────────────────────────────────────────────────

def _make_layer_def_intgrid(cfg: LDtkConfig) -> Dict[str, Any]:
    return {
        "__type": "IntGrid",
        "identifier": "Collisions",
        "type": "IntGrid",
        "uid": cfg.collisions_layer_uid,
        "doc": None,
        "uiColor": None,
        "gridSize": 16,
        "guideGridWid": 0,
        "guideGridHei": 0,
        "displayOpacity": 1.0,
        "inactiveOpacity": 0.6,
        "hideInList": False,
        "hideFieldsWhenInactive": True,
        "canSelectWhenInactive": True,
        "renderInWorldView": True,
        "pxOffsetX": 0,
        "pxOffsetY": 0,
        "parallaxFactorX": 0.0,
        "parallaxFactorY": 0.0,
        "parallaxScaling": True,
        "requiredTags": [],
        "excludedTags": [],
        "intGridValues": [
            {"value": cfg.solid_value,  "identifier": "Solid",  "color": cfg.solid_color,  "tile": None, "groupUid": 0},
            {"value": cfg.hazard_value, "identifier": "Hazard", "color": cfg.hazard_color, "tile": None, "groupUid": 0},
        ],
        "intGridValuesGroups": [],
        "autoTilesetDefUid": None,
        "autoSourceLayerDefUid": None,
        "autoRuleGroups": [],
        "overrideTilesetUid": None,
        "tilesetDefUid": None,
        "tilePivotX": 0,
        "tilePivotY": 0,
    }


def _make_layer_def_entities(cfg: LDtkConfig) -> Dict[str, Any]:
    return {
        "__type": "Entities",
        "identifier": "Entities",
        "type": "Entities",
        "uid": cfg.entities_layer_uid,
        "doc": None,
        "uiColor": None,
        "gridSize": 16,
        "guideGridWid": 0,
        "guideGridHei": 0,
        "displayOpacity": 1.0,
        "inactiveOpacity": 0.6,
        "hideInList": False,
        "hideFieldsWhenInactive": True,
        "canSelectWhenInactive": True,
        "renderInWorldView": True,
        "pxOffsetX": 0,
        "pxOffsetY": 0,
        "parallaxFactorX": 0.0,
        "parallaxFactorY": 0.0,
        "parallaxScaling": True,
        "requiredTags": [],
        "excludedTags": [],
        "tilesetDefUid": None,
        "autoSourceLayerDefUid": None,
    }


def _make_layer_def_tiles(cfg: LDtkConfig, tileset_uid: int) -> Dict[str, Any]:
    return {
        "__type": "Tiles",
        "identifier": "Tiles",
        "type": "Tiles",
        "uid": cfg.tiles_layer_uid,
        "doc": None,
        "uiColor": None,
        "gridSize": 16,
        "guideGridWid": 0,
        "guideGridHei": 0,
        "displayOpacity": 1.0,
        "inactiveOpacity": 0.6,
        "hideInList": False,
        "hideFieldsWhenInactive": True,
        "canSelectWhenInactive": True,
        "renderInWorldView": True,
        "pxOffsetX": 0,
        "pxOffsetY": 0,
        "parallaxFactorX": 0.0,
        "parallaxFactorY": 0.0,
        "parallaxScaling": True,
        "requiredTags": [],
        "excludedTags": [],
        "tilesetDefUid": tileset_uid,
        "autoSourceLayerDefUid": None,
        "tilePivotX": 0,
        "tilePivotY": 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Entity definition builder
# ─────────────────────────────────────────────────────────────────────────────

def _make_entity_def(
    identifier: str,
    uid: int,
    color: str,
    width: int = 16,
    height: int = 16,
    max_count: int = 1,
) -> Dict[str, Any]:
    unlimited = max_count == 0
    return {
        "identifier": identifier,
        "uid": uid,
        "tags": [],
        "doc": None,
        "exportToToc": False,
        "allowOutOfBounds": False,
        "color": color,
        "renderMode": "Cross",
        "showName": True,
        "tilesetId": None,
        "tileRenderMode": "FitInside",
        "tilePivotX": 0,
        "tilePivotY": 0,
        "hollow": False,
        "fieldDefs": [],
        "maxCount": max_count,
        "limitScope": "PerLevel",
        "limitBehavior": "MoveLastOne",
        "width": width,
        "height": height,
        "resizableX": False,
        "resizableY": False,
        "keepAspectRatio": False,
        "pivotX": 0.5,
        "pivotY": 1.0,
        "nineSliceBorders": [0, 0, 0, 0],
        "uiTileRect": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tileset definition builder
# ─────────────────────────────────────────────────────────────────────────────

def _make_tileset_def(
    identifier: str,
    uid: int,
    rel_path: str,
    px_wid: int = 256,
    px_hei: int = 256,
    tile_size: int = 16,
) -> Dict[str, Any]:
    return {
        "identifier": identifier,
        "uid": uid,
        "relPath": rel_path,
        "pxWid": px_wid,
        "pxHei": px_hei,
        "tileGridSize": tile_size,
        "spacing": 0,
        "padding": 0,
        "cachedPixelData": None,
        "customData": [],
        "enumTags": [],
        "tagsSourceEnumUid": None,
        "savedSelections": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Defs section builder
# ─────────────────────────────────────────────────────────────────────────────

def _make_defs(
    cfg: LDtkConfig,
    aesthetic: Optional[AestheticData] = None,
) -> Dict[str, Any]:
    layers = [_make_layer_def_entities(cfg), _make_layer_def_intgrid(cfg)]
    entities = [
        _make_entity_def("Start", cfg.start_entity_uid, cfg.start_color),
        _make_entity_def("Exit",  cfg.exit_entity_uid,  cfg.exit_color),
    ]
    tilesets: List[Dict[str, Any]] = []

    if aesthetic is not None:
        layers.append(_make_layer_def_tiles(cfg, aesthetic.tileset_uid))
        tilesets.append(_make_tileset_def(
            identifier="Tileset",
            uid=aesthetic.tileset_uid,
            rel_path=aesthetic.tileset_rel_path,
            px_wid=aesthetic.tileset_px_width,
            px_hei=aesthetic.tileset_px_height,
        ))
        entities += [
            _make_entity_def("Coin",       cfg.coin_entity_uid,       cfg.coin_color,       max_count=0),
            _make_entity_def("Key",        cfg.key_entity_uid,        cfg.key_color,        max_count=1),
            _make_entity_def("Enemy",      cfg.enemy_entity_uid,      cfg.enemy_color,      max_count=0),
            _make_entity_def("Checkpoint", cfg.checkpoint_entity_uid, cfg.checkpoint_color, max_count=1),
        ]

    return {
        "layers": layers,
        "entities": entities,
        "tilesets": tilesets,
        "enums": [],
        "externalEnums": [],
        "levelFields": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Layer instance builders
# ─────────────────────────────────────────────────────────────────────────────

_ENTITY_UID_MAP: Dict[str, str] = {
    "start":      "start_entity_uid",
    "exit":       "exit_entity_uid",
    "coin":       "coin_entity_uid",
    "key":        "key_entity_uid",
    "enemy":      "enemy_entity_uid",
    "skeleton":   "enemy_entity_uid",
    "slime":      "enemy_entity_uid",
    "harpy":      "enemy_entity_uid",
    "checkpoint": "checkpoint_entity_uid",
}

_ENTITY_COLOR_MAP: Dict[str, str] = {
    "start":      "start_color",
    "exit":       "exit_color",
    "coin":       "coin_color",
    "key":        "key_color",
    "enemy":      "enemy_color",
    "skeleton":   "enemy_color",
    "slime":      "enemy_color",
    "harpy":      "enemy_color",
    "checkpoint": "checkpoint_color",
}

_ENTITY_IDENTIFIER_MAP: Dict[str, str] = {
    "start":      "Start",
    "exit":       "Exit",
    "coin":       "Coin",
    "key":        "Key",
    "enemy":      "Enemy",
    "skeleton":   "Enemy",
    "slime":      "Enemy",
    "harpy":      "Enemy",
    "checkpoint": "Checkpoint",
}


def _make_entity_instance(
    entity: IREntity,
    tile_size: int,
    cfg: LDtkConfig,
) -> Dict[str, Any]:
    key = entity.entity_type.lower()
    uid_attr   = _ENTITY_UID_MAP.get(key,   "enemy_entity_uid")
    color_attr = _ENTITY_COLOR_MAP.get(key, "enemy_color")
    identifier = _ENTITY_IDENTIFIER_MAP.get(key, "Enemy")
    def_uid    = getattr(cfg, uid_attr)
    color      = getattr(cfg, color_attr)

    px_x = entity.col * tile_size + tile_size // 2
    px_y = entity.row * tile_size + tile_size

    return {
        "__identifier": identifier,
        "__grid": [entity.col, entity.row],
        "__pivot": [0.5, 1.0],
        "__tags": [],
        "__tile": None,
        "__worldX": px_x,
        "__worldY": px_y,
        "__worldZ": 0,
        "__smartColor": color,
        "iid": _new_iid(),
        "width": tile_size,
        "height": tile_size,
        "defUid": def_uid,
        "px": [px_x, px_y],
        "fieldInstances": [],
    }


def _layer_instance_base(
    identifier: str,
    layer_type: str,
    c_wid: int,
    c_hei: int,
    grid_size: int,
    layer_def_uid: int,
    level_uid: int,
    *,
    tileset_def_uid: Optional[int] = None,
    tileset_rel_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Common fields shared by all layer instance types."""
    return {
        "__identifier": identifier,
        "__type": layer_type,
        "__cWid": c_wid,
        "__cHei": c_hei,
        "__gridSize": grid_size,
        "__opacity": 1,
        "__pxTotalOffsetX": 0,
        "__pxTotalOffsetY": 0,
        "__tilesetDefUid": tileset_def_uid,
        "__tilesetRelPath": tileset_rel_path,
        "iid": _new_iid(),
        "layerDefUid": layer_def_uid,
        "levelId": level_uid,
        "pxOffsetX": 0,
        "pxOffsetY": 0,
        "visible": True,
        "optionalRules": [],
        "seed": 0,
        "overrideTilesetUid": None,
        "autoLayerTiles": [],
        "intGridCsv": [],
        "gridTiles": [],
        "entityInstances": [],
    }


def _make_layer_instance_entities(
    ir: IRLevel,
    level_uid: int,
    cfg: LDtkConfig,
) -> Dict[str, Any]:
    base = _layer_instance_base(
        "Entities", "Entities",
        ir.width_tiles, ir.height_tiles, ir.tile_size,
        cfg.entities_layer_uid, level_uid,
    )
    base["entityInstances"] = [_make_entity_instance(e, ir.tile_size, cfg) for e in ir.entities]
    return base


def _make_layer_instance_intgrid(
    ir: IRLevel,
    level_uid: int,
    cfg: LDtkConfig,
) -> Dict[str, Any]:
    base = _layer_instance_base(
        "Collisions", "IntGrid",
        ir.width_tiles, ir.height_tiles, ir.tile_size,
        cfg.collisions_layer_uid, level_uid,
    )
    base["intGridCsv"] = ir.to_int_grid_csv()
    return base


def _make_layer_instance_tiles(
    tile_entries: List[TileEntry],
    ir: IRLevel,
    level_uid: int,
    aesthetic: AestheticData,
    cfg: LDtkConfig,
) -> Dict[str, Any]:
    tiles_per_row = aesthetic.tileset_px_width // ir.tile_size
    grid_tiles = []
    for te in tile_entries:
        px_x    = te.col * ir.tile_size
        px_y    = te.row * ir.tile_size
        tile_id = (te.src_y // ir.tile_size) * tiles_per_row + (te.src_x // ir.tile_size)
        flip    = (1 if te.flip_x else 0) | (2 if te.flip_y else 0)
        d_idx   = te.row * ir.width_tiles + te.col
        grid_tiles.append({
            "px":  [px_x, px_y],
            "src": [te.src_x, te.src_y],
            "f":   flip,
            "t":   tile_id,
            "a":   1.0,
            "d":   [d_idx],
        })

    base = _layer_instance_base(
        "Tiles", "Tiles",
        ir.width_tiles, ir.height_tiles, ir.tile_size,
        cfg.tiles_layer_uid, level_uid,
        tileset_def_uid=aesthetic.tileset_uid,
        tileset_rel_path=aesthetic.tileset_rel_path,
    )
    base["gridTiles"] = grid_tiles
    base["overrideTilesetUid"] = aesthetic.tileset_uid
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Level builder
# ─────────────────────────────────────────────────────────────────────────────

def _make_level(
    ir: IRLevel,
    level_uid: int,
    world_x: int,
    world_y: int,
    cfg: LDtkConfig,
    aesthetic: Optional[AestheticData] = None,
) -> Dict[str, Any]:
    bg_color = aesthetic.bg_color if aesthetic else cfg.background_color

    # Layer stack (top-most listed first = rendered on top)
    layer_instances: List[Dict[str, Any]] = [
        _make_layer_instance_entities(ir, level_uid, cfg),
    ]
    if aesthetic and aesthetic.tile_entries:
        layer_instances.append(
            _make_layer_instance_tiles(aesthetic.tile_entries, ir, level_uid, aesthetic, cfg)
        )
    layer_instances.append(_make_layer_instance_intgrid(ir, level_uid, cfg))

    return {
        "identifier": ir.identifier,
        "iid": _new_iid(),
        "uid": level_uid,
        "worldX": world_x,
        "worldY": world_y,
        "worldDepth": 0,
        "pxWid": ir.px_width,
        "pxHei": ir.px_height,
        "__neighbours": [],
        "__bgColor": bg_color,
        "__smartColor": bg_color,
        "__bgPos": None,
        "bgRelPath": None,
        "bgPos": None,
        "bgColor": bg_color,
        "bgPivotX": 0,
        "bgPivotY": 0,
        "useAutoIdentifier": True,
        "externalRelPath": None,
        "fieldInstances": [],
        "layerInstances": layer_instances,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def to_ldtk_dict(
    ir_level: IRLevel,
    cfg: Optional[LDtkConfig] = None,
    aesthetic: Optional[AestheticData] = None,
    level_uid: int = 0,
    world_x: int = 0,
    world_y: int = 0,
) -> Dict[str, Any]:
    """Serialise *ir_level* to a fully-valid LDtk JSON v1.5.3 project dict.

    Parameters
    ----------
    ir_level  : source IRLevel to serialise.
    cfg       : UID / appearance config; defaults to ``LDtkConfig()``.
    aesthetic : optional aesthetic payload that activates the Tiles layer,
                theme background, and collectible/enemy entity defs.
    level_uid, world_x, world_y : level placement metadata.
    """
    cfg = cfg or LDtkConfig()

    all_uids = [
        cfg.collisions_layer_uid, cfg.entities_layer_uid, cfg.tiles_layer_uid,
        cfg.start_entity_uid, cfg.exit_entity_uid,
        cfg.coin_entity_uid, cfg.key_entity_uid,
        cfg.enemy_entity_uid, cfg.checkpoint_entity_uid,
        level_uid,
    ]
    if aesthetic:
        all_uids.append(aesthetic.tileset_uid)
    next_uid = max(all_uids) + 1

    bg = aesthetic.bg_color if aesthetic else cfg.background_color

    return {
        "__header__": {
            "fileType": "LDtk Project JSON",
            "app": "LDtk",
            "schema": "https://ldtk.io/files/JSON_SCHEMA.json",
            "appAuthor": "Deepnight Games",
            "appVersion": cfg.json_version,
            "url": "https://ldtk.io",
        },
        "iid": _new_iid(),
        "jsonVersion": cfg.json_version,
        "appBuildId": 0,
        "nextUid": next_uid,
        "bgColor": bg,
        "defaultLevelBgColor": bg,
        "defaultGridSize": 16,
        "defaultEntityWidth": 16,
        "defaultEntityHeight": 16,
        "defaultPivotX": 0.0,
        "defaultPivotY": 1.0,
        "defaultLevelWidth": 256,
        "defaultLevelHeight": 256,
        "worldLayout": "Free",
        "worldGridWidth": 256,
        "worldGridHeight": 256,
        "dummyWorldIid": _new_iid(),
        "flags": [],
        "toc": [],
        "minifyJson": False,
        "exportTiled": False,
        "simplifiedExport": False,
        "imageExportMode": "None",
        "exportLevelBg": True,
        "pngFilePattern": None,
        "backupOnSave": False,
        "backupLimit": 10,
        "backupRelPath": None,
        "levelNamePattern": None,
        "identifierStyle": "Capitalize",
        "tutorialDesc": None,
        "customCommands": [],
        "externalLevels": False,
        "defs": _make_defs(cfg, aesthetic),
        "levels": [_make_level(ir_level, level_uid, world_x, world_y, cfg, aesthetic)],
        "worlds": [],
    }
