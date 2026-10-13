"""LDtk Adapter — serialises an IRLevel to a valid LDtk JSON v1.5.3 document.

Spec reference: ``ldtk-src/docs/JSON_SCHEMA.json`` / ``JSON_DOC.md``; the
value encodings mirror the editor's own ``toJson`` code
(``ldtk-src/src/electron.renderer/data``).  A full export validates against
the JSON schema (see ``tests/test_ldtk_export.py``).

What is written
---------------
* ``defs.entities``: one def per kind in ``spec.catalog`` with typed
  ``fieldDefs``; ``defs.enums``: the catalog enums; ``defs.levelFields``: the
  ``gldtk_*`` level fields (prompt, sections, requirements, Game Profile path,
  version, full LevelSpec).
* Every entity instance has a ``fieldInstance`` for every field def of its
  kind — the IR value when present, otherwise the default (stored as a
  ``null`` editor value, exactly as LDtk stores "not set").
* ``linked_to`` / ``opens`` hold spec ids in the IR; they are resolved to the
  target's iid and written as ``EntityReferenceInfos``.

Aesthetic integration
---------------------
Pass an ``AestheticData`` instance to activate a ``Tiles`` visual layer, the
theme background colour and the tileset reference.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from spec.catalog import (
    ENUMS,
    F_BOOL,
    F_ENTITY_REF,
    F_ENUM,
    F_INT,
    F_STRING,
    F_TEXT,
    GLDTK_VERSION,
    KINDS,
    LEVEL_FIELDS,
    EnumSpec,
    FieldSpec,
    KindSpec,
    all_uids,
    enum_value_id,
    resolve_kind,
)

from .ir import IREntity, IRLevel, TileValue

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Aesthetic carrier types  (defined here to avoid circular imports with the
# aesthetic package, which imports from layout)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TileEntry:
    """One visual tile to be placed in the LDtk Tiles layer."""

    col: int
    row: int
    src_x: int  # pixel X in the tileset image
    src_y: int  # pixel Y in the tileset image
    flip_x: bool = False
    flip_y: bool = False


@dataclass
class AestheticData:
    """Aesthetic layer payload consumed by ``to_ldtk_dict``."""

    tile_entries: List[TileEntry] = field(default_factory=list)
    bg_color: str = "#1a1a2e"
    tileset_uid: int = 100
    tileset_rel_path: str = "tilesets/dungeon.png"
    tileset_px_width: int = 256
    tileset_px_height: int = 256


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class LDtkConfig:
    """Static UID and appearance settings.

    Entity def uids, colours and sizes live in ``spec.catalog``; only the
    player hitbox (the Start entity's size) can be overridden here.
    """

    # Layer def UIDs
    collisions_layer_uid: int = 1
    entities_layer_uid: int = 2
    tiles_layer_uid: int = 3  # visual Tiles layer (aesthetic)

    # IntGrid value codes (must match TileValue enum)
    solid_value: int = int(TileValue.SOLID)
    hazard_value: int = int(TileValue.HAZARD)
    solid_color: str = "#8b8b8b"
    hazard_color: str = "#ff4422"

    # Start entity size in px (the player hitbox, from the Game Profile)
    player_px: Tuple[int, int] = (16, 32)

    # LDtk project metadata
    json_version: str = "1.5.3"
    background_color: str = "#1a1a2e"


@dataclass
class LevelMeta:
    """GLDtk state stored in the level's custom fields (the ``.ldtk`` is the
    single source of truth).

    ``sections`` defaults to ``ir.metadata["sections"]``; ``requirements``
    are plain strings (``Requirement`` objects are reduced to their text);
    ``spec_json`` may be a JSON string or a ``LevelSpec``.
    """

    prompt: Optional[str] = None
    sections: Optional[List[Dict[str, Any]]] = None
    requirements: List[Any] = field(default_factory=list)
    game_profile_relpath: Optional[str] = None
    spec_json: Optional[Any] = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _new_iid() -> str:
    return str(uuid.uuid4())


def escape_string(s: Optional[str]) -> Optional[str]:
    """LDtk ``JsonTools.escapeString``: backslashes and newlines."""
    if s is None:
        return None
    return s.replace("\\", "\\\\").replace("\n", "\\n")


def unescape_string(s: Optional[str]) -> Optional[str]:
    """LDtk ``JsonTools.unescapeString`` (inverse of :func:`escape_string`)."""
    if s is None:
        return None
    return s.replace("\\\\", "\\").replace("\\n", "\n")


def _json_text(value: Any) -> str:
    # ensure_ascii=False keeps non-ASCII text free of backslash escapes, which
    # LDtk's string escaping would otherwise mangle on re-save.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


_JSON_TYPE = {F_INT: "Int", F_STRING: "String", F_TEXT: "String", F_BOOL: "Bool",
              F_ENTITY_REF: "EntityRef"}
_WRAPPER = {F_INT: "V_Int", F_BOOL: "V_Bool", F_STRING: "V_String", F_TEXT: "V_String",
            F_ENUM: "V_String", F_ENTITY_REF: "V_String"}


def field_json_type(fs: FieldSpec) -> str:
    """Instance / def ``__type``: ``Int``, ``LocalEnum.Facing``, ``Array<String>`` …"""
    base = f"LocalEnum.{fs.enum}" if fs.ftype == F_ENUM else _JSON_TYPE[fs.ftype]
    return f"Array<{base}>" if fs.is_array else base


def field_type_enum(fs: FieldSpec) -> str:
    """FieldDef ``type``: ``F_Int``, ``F_Enum(501)`` …"""
    if fs.ftype == F_ENUM:
        return f"F_Enum({ENUMS[fs.enum].uid})"
    return fs.ftype


def _editor_value(fs: FieldSpec, raw: Any) -> Optional[Dict[str, Any]]:
    """One ``realEditorValues`` entry (``JsonTools.writeEnum(ValueWrapper)``)."""
    if raw is None:
        return None
    if fs.ftype == F_TEXT:
        raw = escape_string(raw)  # LDtk unescapes F_Text once on load
    return {"id": _WRAPPER[fs.ftype], "params": [raw]}


# ─────────────────────────────────────────────────────────────────────────────
# Definitions
# ─────────────────────────────────────────────────────────────────────────────


def _layer_def_base(identifier: str, layer_type: str, uid: int) -> Dict[str, Any]:
    return {
        "__type": layer_type,
        "identifier": identifier,
        "type": layer_type,
        "uid": uid,
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
        "autoTilesKilledByOtherLayerUid": None,
        "uiFilterTags": [],
        "useAsyncRender": False,
        "intGridValues": [],
        "intGridValuesGroups": [],
        "autoRuleGroups": [],
        "autoSourceLayerDefUid": None,
        "autoTilesetDefUid": None,
        "tilesetDefUid": None,
        "tilePivotX": 0.0,
        "tilePivotY": 0.0,
        "biomeFieldUid": None,
    }


def _make_layer_def_intgrid(cfg: LDtkConfig, grid_size: int) -> Dict[str, Any]:
    d = _layer_def_base("Collisions", "IntGrid", cfg.collisions_layer_uid)
    d["gridSize"] = grid_size
    d["intGridValues"] = [
        {"value": cfg.solid_value, "identifier": "Solid", "color": cfg.solid_color,
         "tile": None, "groupUid": 0},
        {"value": cfg.hazard_value, "identifier": "Hazard", "color": cfg.hazard_color,
         "tile": None, "groupUid": 0},
    ]
    return d


def _make_layer_def_entities(cfg: LDtkConfig, grid_size: int) -> Dict[str, Any]:
    d = _layer_def_base("Entities", "Entities", cfg.entities_layer_uid)
    d["gridSize"] = grid_size
    return d


def _make_layer_def_tiles(cfg: LDtkConfig, tileset_uid: int, grid_size: int) -> Dict[str, Any]:
    d = _layer_def_base("Tiles", "Tiles", cfg.tiles_layer_uid)
    d["gridSize"] = grid_size
    d["tilesetDefUid"] = tileset_uid
    return d


def _make_field_def(fs: FieldSpec, uid: int) -> Dict[str, Any]:
    default = fs.default
    if fs.ftype == F_ENUM and default is not None:
        default = enum_value_id(default)
    default_override = (
        None if fs.is_array or default is None or fs.ftype == F_ENTITY_REF
        else {"id": _WRAPPER[fs.ftype], "params": [default]}
    )
    is_ref = fs.ftype == F_ENTITY_REF
    return {
        "identifier": fs.name,
        "doc": fs.doc,
        "__type": field_json_type(fs),
        "uid": uid,
        "type": field_type_enum(fs),
        "isArray": fs.is_array,
        "canBeNull": default is None or fs.is_array,
        "arrayMinLength": None,
        "arrayMaxLength": None,
        "editorDisplayMode": fs.display,
        "editorDisplayScale": 1.0,
        "editorDisplayPos": "Above",
        "editorLinkStyle": "CurvedArrow" if is_ref else "StraightArrow",
        "editorDisplayColor": None,
        "editorAlwaysShow": False,
        "editorShowInWorld": True,
        "editorCutLongValues": True,
        "editorTextSuffix": None,
        "editorTextPrefix": None,
        "useForSmartColor": False,
        "exportToToc": False,
        "searchable": False,
        "min": 0.0 if fs.ftype == F_INT else None,
        "max": None,
        "regex": None,
        "acceptFileTypes": None,
        "defaultOverride": default_override,
        "textLanguageMode": "LangJson" if fs.name in ("gldtk_meta", "gldtk_sections", "gldtk_spec") else None,
        "symmetricalRef": False,
        "autoChainRef": True,
        "allowOutOfLevelRef": True,
        "allowedRefs": "Any",
        "allowedRefsEntityUid": None,
        "allowedRefTags": [],
        "tilesetUid": None,
    }


def _kind_size(kind: KindSpec, cfg: LDtkConfig) -> Tuple[int, int]:
    return tuple(cfg.player_px) if kind.kind == "Start" else (kind.width, kind.height)  # type: ignore[return-value]


def _make_entity_def(kind: KindSpec, cfg: LDtkConfig) -> Dict[str, Any]:
    width, height = _kind_size(kind, cfg)
    return {
        "identifier": kind.identifier,
        "uid": kind.uid,
        "tags": list(kind.tags),
        "doc": None,
        "exportToToc": False,
        "allowOutOfBounds": False,
        "color": kind.color,
        "renderMode": "Rectangle",
        "showName": True,
        "tilesetId": None,
        "tileRenderMode": "FitInside",
        "tileRect": None,
        "uiTileRect": None,
        "hollow": False,
        "fillOpacity": 0.4,
        "lineOpacity": 1.0,
        "tileOpacity": 1.0,
        "fieldDefs": [_make_field_def(f, kind.field_uid(f.name)) for f in kind.all_fields],
        "maxCount": kind.max_count,
        "limitScope": "PerLevel",
        "limitBehavior": "MoveLastOne",
        "width": width,
        "height": height,
        "resizableX": kind.resizable,
        "resizableY": kind.resizable,
        "minWidth": None,
        "maxWidth": None,
        "minHeight": None,
        "maxHeight": None,
        "keepAspectRatio": kind.resizable,
        "pivotX": 0.5,
        "pivotY": 1.0,
        "nineSliceBorders": [],
    }


def _make_enum_def(e: EnumSpec) -> Dict[str, Any]:
    return {
        "identifier": e.identifier,
        "uid": e.uid,
        "values": [{"id": enum_value_id(v), "tileRect": None, "color": 0} for v in e.values],
        "iconTilesetUid": None,
        "externalRelPath": None,
        "externalFileChecksum": None,
        "tags": ["gldtk"],
    }


def _make_tileset_def(aesthetic: AestheticData, tile_size: int) -> Dict[str, Any]:
    return {
        "__cWid": aesthetic.tileset_px_width // tile_size,
        "__cHei": aesthetic.tileset_px_height // tile_size,
        "identifier": "Tileset",
        "uid": aesthetic.tileset_uid,
        "relPath": aesthetic.tileset_rel_path,
        "embedAtlas": None,
        "pxWid": aesthetic.tileset_px_width,
        "pxHei": aesthetic.tileset_px_height,
        "tileGridSize": tile_size,
        "spacing": 0,
        "padding": 0,
        "tags": [],
        "tagsSourceEnumUid": None,
        "enumTags": [],
        "customData": [],
        "savedSelections": [],
        "cachedPixelData": None,
    }


def _make_defs(cfg: LDtkConfig, aesthetic: Optional[AestheticData], tile_size: int) -> Dict[str, Any]:
    layers = [_make_layer_def_entities(cfg, tile_size), _make_layer_def_intgrid(cfg, tile_size)]
    tilesets: List[Dict[str, Any]] = []
    if aesthetic is not None:
        layers.append(_make_layer_def_tiles(cfg, aesthetic.tileset_uid, tile_size))
        tilesets.append(_make_tileset_def(aesthetic, tile_size))
    return {
        "layers": layers,
        "entities": [_make_entity_def(k, cfg) for k in KINDS.values()],
        "tilesets": tilesets,
        "enums": [_make_enum_def(e) for e in ENUMS.values()],
        "externalEnums": [],
        "levelFields": [_make_field_def(fs, uid) for fs, uid in LEVEL_FIELDS],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Field instances
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _RefTarget:
    entity_iid: str
    layer_iid: str
    level_iid: str
    world_iid: str

    def as_json(self) -> Dict[str, str]:
        return {
            "entityIid": self.entity_iid,
            "layerIid": self.layer_iid,
            "levelIid": self.level_iid,
            "worldIid": self.world_iid,
        }


def _coerce(fs: FieldSpec, value: Any, where: str, warnings: List[str]) -> Any:
    """Coerce one scalar to the field's type; None (→ default) when impossible."""
    if value is None:
        return None
    try:
        if fs.ftype == F_INT:
            if isinstance(value, bool):
                return int(value)
            f = float(value)
            if f != int(f):
                warnings.append(f"{where}: rounded {fs.name}={value!r} to an integer")
            return int(round(f))
        if fs.ftype == F_BOOL:
            if isinstance(value, str):
                return value.strip().lower() in ("true", "yes", "1")
            return bool(value)
        if fs.ftype == F_ENUM:
            v = ENUMS[fs.enum].to_ldtk(value)
            if v is None:
                warnings.append(f"{where}: {fs.name}={value!r} is not one of "
                                f"{list(ENUMS[fs.enum].values)}; using the default")
            return v
        if isinstance(value, (list, dict)):
            return _json_text(value)
        return str(value)
    except (TypeError, ValueError):
        warnings.append(f"{where}: cannot use {fs.name}={value!r}; using the default")
        return None


def _field_instance(fs: FieldSpec, uid: int, value: Any) -> Dict[str, Any]:
    """Field instance for a non-ref field.  *value* is already coerced (or None)."""
    if fs.is_array:
        items = list(value or [])
        return {
            "__identifier": fs.name,
            "__type": field_json_type(fs),
            "__value": [escape_string(v) if isinstance(v, str) else v for v in items],
            "__tile": None,
            "defUid": uid,
            "realEditorValues": [_editor_value(fs, v) for v in items],
        }
    shown = value
    if shown is None:
        shown = fs.default
        if fs.ftype == F_ENUM and shown is not None:
            shown = enum_value_id(shown)
    if fs.ftype in (F_STRING, F_TEXT):
        shown = escape_string(shown)
    return {
        "__identifier": fs.name,
        "__type": field_json_type(fs),
        "__value": shown,
        "__tile": None,
        "defUid": uid,
        "realEditorValues": [_editor_value(fs, value)],
    }


def _ref_instance(fs: FieldSpec, uid: int, target: Optional[_RefTarget]) -> Dict[str, Any]:
    return {
        "__identifier": fs.name,
        "__type": field_json_type(fs),
        "__value": target.as_json() if target else None,
        "__tile": None,
        "defUid": uid,
        "realEditorValues": [
            {"id": "V_String", "params": [target.entity_iid]} if target else None
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Entity instances
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _Prepared:
    entity: IREntity
    kind: KindSpec
    values: Dict[str, Any]  # field name → coerced value (refs: raw spec id)
    iid: str


def _prepare_entity(entity: IREntity, warnings: List[str]) -> _Prepared:
    res = resolve_kind(entity.entity_type)
    kind = res.kind
    meta = dict(entity.metadata)
    where = f"entity {meta.get('spec_id') or entity.entity_type}@({entity.col},{entity.row})"
    iid = meta.pop("iid", None)
    if not isinstance(iid, str) or not iid:
        iid = _new_iid()

    for k, v in res.implied.items():
        meta.setdefault(k, v)

    extra: Dict[str, Any] = {}
    if not res.exact:
        original = str(entity.entity_type)
        desc = str(meta.get("description") or "")
        meta["description"] = original if not desc else f"{original}: {desc}"
        extra["original_type"] = original
        warnings.append(f"{where}: unknown entity type {original!r} exported as {kind.kind}")

    values: Dict[str, Any] = {}
    for key, val in meta.items():
        fs = kind.field(key)
        if fs is None or fs.name == "gldtk_meta":
            extra[key] = val
            continue
        if fs.ftype == F_ENTITY_REF:
            values[key] = None if val is None else str(val)
        elif fs.is_array:
            items = val if isinstance(val, (list, tuple)) else [val]
            values[key] = [c for c in (_coerce(fs, v, where, warnings) for v in items) if c is not None]
        else:
            values[key] = _coerce(fs, val, where, warnings)

    if extra:
        values["gldtk_meta"] = _json_text(_jsonable(extra))
    return _Prepared(entity, kind, values, iid)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(v) for v in value]
        return str(value)


def _make_entity_instance(
    p: _Prepared,
    tile_size: int,
    world_x: int,
    world_y: int,
    resolve_ref: Any,
    cfg: LDtkConfig,
) -> Dict[str, Any]:
    kind = p.kind
    e = p.entity
    if kind.kind == "Start":
        width, height = cfg.player_px
    else:
        width, height = kind.size_for(e.metadata)
    px_x = e.col * tile_size + tile_size // 2
    px_y = e.row * tile_size + tile_size

    field_instances = []
    for fs in kind.all_fields:
        uid = kind.field_uid(fs.name)
        if fs.ftype == F_ENTITY_REF:
            field_instances.append(_ref_instance(fs, uid, resolve_ref(p, fs.name)))
        else:
            field_instances.append(_field_instance(fs, uid, p.values.get(fs.name)))

    return {
        "__identifier": kind.identifier,
        "__grid": [e.col, e.row],
        "__pivot": [0.5, 1.0],
        "__tags": list(kind.tags),
        "__tile": None,
        "__worldX": px_x + world_x,
        "__worldY": px_y + world_y,
        "__smartColor": kind.color,
        "iid": p.iid,
        "width": width,
        "height": height,
        "defUid": kind.uid,
        "px": [px_x, px_y],
        "fieldInstances": field_instances,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Layer instances
# ─────────────────────────────────────────────────────────────────────────────


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


def _make_layer_instance_intgrid(ir: IRLevel, level_uid: int, cfg: LDtkConfig) -> Dict[str, Any]:
    base = _layer_instance_base(
        "Collisions", "IntGrid", ir.width_tiles, ir.height_tiles, ir.tile_size,
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
        tile_id = (te.src_y // ir.tile_size) * tiles_per_row + (te.src_x // ir.tile_size)
        grid_tiles.append(
            {
                "px": [te.col * ir.tile_size, te.row * ir.tile_size],
                "src": [te.src_x, te.src_y],
                "f": (1 if te.flip_x else 0) | (2 if te.flip_y else 0),
                "t": tile_id,
                "a": 1.0,
                "d": [te.row * ir.width_tiles + te.col],
            }
        )
    base = _layer_instance_base(
        "Tiles", "Tiles", ir.width_tiles, ir.height_tiles, ir.tile_size,
        cfg.tiles_layer_uid, level_uid,
        tileset_def_uid=aesthetic.tileset_uid,
        tileset_rel_path=aesthetic.tileset_rel_path,
    )
    base["gridTiles"] = grid_tiles
    base["overrideTilesetUid"] = aesthetic.tileset_uid
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Level fields
# ─────────────────────────────────────────────────────────────────────────────


def _spec_text(spec: Any) -> Optional[str]:
    if spec is None:
        return None
    if isinstance(spec, str):
        return spec
    if hasattr(spec, "model_dump"):
        return _json_text(spec.model_dump(mode="json", by_alias=True))
    return _json_text(spec)


def _requirement_text(r: Any) -> str:
    if isinstance(r, str):
        return r
    if isinstance(r, dict):
        return str(r.get("text", r))
    return str(getattr(r, "text", r))


def _level_field_values(ir: IRLevel, meta: Optional[LevelMeta]) -> Dict[str, Any]:
    sections = meta.sections if meta and meta.sections is not None else ir.metadata.get("sections")
    return {
        "gldtk_prompt": meta.prompt if meta else None,
        "gldtk_sections": _json_text(_jsonable(sections)) if sections is not None else None,
        "gldtk_requirements": [_requirement_text(r) for r in (meta.requirements if meta else [])],
        "gldtk_game_profile": meta.game_profile_relpath if meta else None,
        "gldtk_version": GLDTK_VERSION,
        "gldtk_spec": _spec_text(meta.spec_json) if meta else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Level builder
# ─────────────────────────────────────────────────────────────────────────────


def _make_level(
    ir: IRLevel,
    level_uid: int,
    world_x: int,
    world_y: int,
    world_iid: str,
    cfg: LDtkConfig,
    aesthetic: Optional[AestheticData],
    level_meta: Optional[LevelMeta],
    warnings: List[str],
) -> Dict[str, Any]:
    bg_color = aesthetic.bg_color if aesthetic else cfg.background_color
    level_iid = _new_iid()

    entities_layer = _layer_instance_base(
        "Entities", "Entities", ir.width_tiles, ir.height_tiles, ir.tile_size,
        cfg.entities_layer_uid, level_uid,
    )

    # Pass 1: assign every iid, index spec ids.
    prepared = [_prepare_entity(e, warnings) for e in ir.entities]
    by_spec_id: Dict[str, _Prepared] = {}
    by_iid: Dict[str, _Prepared] = {}
    for p in prepared:
        if p.iid in by_iid:
            warnings.append(f"duplicate entity iid {p.iid!r}; generated a new one")
            p.iid = _new_iid()
        by_iid[p.iid] = p
        sid = p.values.get("spec_id")
        if sid:
            if sid in by_spec_id:
                warnings.append(f"duplicate entity spec id {sid!r}; references use the first one")
            else:
                by_spec_id[sid] = p

    # Pass 2: resolve refs (spec id first, then a raw iid) and build instances.
    def resolve_ref(p: _Prepared, name: str) -> Optional[_RefTarget]:
        ref = p.values.get(name)
        if ref is None:
            return None
        target = by_spec_id.get(ref) or by_iid.get(ref)
        if target is None:
            who = p.values.get("spec_id") or p.kind.kind
            warnings.append(f"entity {who!r}: {name} refers to missing entity {ref!r}; written as null")
            return None
        return _RefTarget(target.iid, entities_layer["iid"], level_iid, world_iid)

    entities_layer["entityInstances"] = [
        _make_entity_instance(p, ir.tile_size, world_x, world_y, resolve_ref, cfg)
        for p in prepared
    ]

    # Layer stack (top-most listed first = rendered on top)
    layer_instances: List[Dict[str, Any]] = [entities_layer]
    if aesthetic and aesthetic.tile_entries:
        layer_instances.append(
            _make_layer_instance_tiles(aesthetic.tile_entries, ir, level_uid, aesthetic, cfg)
        )
    layer_instances.append(_make_layer_instance_intgrid(ir, level_uid, cfg))

    values = _level_field_values(ir, level_meta)
    field_instances = []
    for fs, uid in LEVEL_FIELDS:
        v = values.get(fs.name)
        field_instances.append(_field_instance(fs, uid, v))

    return {
        "identifier": ir.identifier,
        "iid": level_iid,
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
        "bgPivotX": 0.5,
        "bgPivotY": 0.5,
        "useAutoIdentifier": False,
        "externalRelPath": None,
        "fieldInstances": field_instances,
        "layerInstances": layer_instances,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────


def to_ldtk_dict_with_warnings(
    ir_level: IRLevel,
    aesthetic: Union[AestheticData, LDtkConfig, None] = None,
    cfg: Union[LDtkConfig, AestheticData, None] = None,
    level_meta: Optional[LevelMeta] = None,
    level_uid: int = 0,
    world_x: int = 0,
    world_y: int = 0,
) -> Tuple[Dict[str, Any], List[str]]:
    """Like :func:`to_ldtk_dict`, also returning the export warnings
    (unresolved entity refs, unknown entity types, invalid field values …)."""
    # v1 signature was (ir, cfg, aesthetic): accept either positional order.
    if isinstance(aesthetic, LDtkConfig) or isinstance(cfg, AestheticData):
        aesthetic, cfg = cfg, aesthetic  # type: ignore[assignment]
    cfg = cfg or LDtkConfig()
    warnings: List[str] = []
    world_iid = _new_iid()

    uids: List[int] = [cfg.collisions_layer_uid, cfg.entities_layer_uid,
                       cfg.tiles_layer_uid, level_uid, *all_uids()]
    if aesthetic:
        uids.append(aesthetic.tileset_uid)
    next_uid = max(uids) + 1

    bg = aesthetic.bg_color if aesthetic else cfg.background_color
    level = _make_level(ir_level, level_uid, world_x, world_y, world_iid, cfg,
                        aesthetic, level_meta, warnings)

    doc = {
        "__header__": {
            "fileType": "LDtk Project JSON",
            "app": "LDtk",
            "doc": "https://ldtk.io/json",
            "schema": "https://ldtk.io/files/JSON_SCHEMA.json",
            "appAuthor": "Sebastien 'deepnight' Benard",
            "appVersion": cfg.json_version,
            "url": "https://ldtk.io",
        },
        "iid": _new_iid(),
        "jsonVersion": cfg.json_version,
        "appBuildId": 0,
        "nextUid": next_uid,
        "identifierStyle": "Capitalize",
        "toc": [],
        "worldLayout": "Free",
        "worldGridWidth": 256,
        "worldGridHeight": 256,
        "defaultLevelWidth": 256,
        "defaultLevelHeight": 256,
        "defaultPivotX": 0.0,
        "defaultPivotY": 1.0,
        "defaultGridSize": ir_level.tile_size,
        "defaultEntityWidth": 16,
        "defaultEntityHeight": 16,
        "bgColor": bg,
        "defaultLevelBgColor": bg,
        "minifyJson": False,
        "externalLevels": False,
        "exportTiled": False,
        "simplifiedExport": False,
        "imageExportMode": "None",
        "exportLevelBg": True,
        "pngFilePattern": None,
        "backupOnSave": False,
        "backupLimit": 10,
        "backupRelPath": None,
        "levelNamePattern": "Level_%idx",
        "tutorialDesc": None,
        "customCommands": [],
        "flags": [],
        "defs": _make_defs(cfg, aesthetic, ir_level.tile_size),
        "levels": [level],
        "worlds": [],
        "dummyWorldIid": world_iid,
    }
    for w in warnings:
        log.debug("ldtk export: %s", w)
    return doc, warnings


def to_ldtk_dict(
    ir_level: IRLevel,
    aesthetic: Union[AestheticData, LDtkConfig, None] = None,
    cfg: Union[LDtkConfig, AestheticData, None] = None,
    level_meta: Optional[LevelMeta] = None,
    level_uid: int = 0,
    world_x: int = 0,
    world_y: int = 0,
) -> Dict[str, Any]:
    """Serialise *ir_level* to a fully-valid LDtk JSON v1.5.3 project dict.

    Parameters
    ----------
    ir_level   : source IRLevel to serialise.
    aesthetic  : optional aesthetic payload (Tiles layer, theme background).
    cfg        : layer uid / appearance config; defaults to ``LDtkConfig()``.
    level_meta : GLDtk state for the level custom fields (prompt, sections,
                 requirements, Game Profile path, LevelSpec JSON).
    level_uid, world_x, world_y : level placement metadata.

    Use :func:`to_ldtk_dict_with_warnings` to also get the export warnings.
    """
    doc, _ = to_ldtk_dict_with_warnings(
        ir_level, aesthetic, cfg, level_meta, level_uid, world_x, world_y
    )
    return doc
