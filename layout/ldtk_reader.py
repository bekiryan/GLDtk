"""Read an LDtk project back into GLDtk terms (inverse of ``ldtk_adapter``).

``read_level(path_or_dict)`` returns the IntGrid as ``grid[row][col]``, the
entities as :class:`IREntity` objects (kind and metadata rebuilt from their
``fieldInstances``) and the parsed ``gldtk_*`` level fields.

Tolerant by design (Part 8 reads files the user has edited in LDtk): extra
entity kinds, missing GLDtk fields and v1 files without any GLDtk fields all
read fine — whatever is absent comes back as None / empty.

Entity metadata
---------------
* kind fields are included when they were explicitly set (a non-null editor
  value); fields left at their default are omitted, mirroring the IR contract
  where only non-null spec fields are present.  Array fields are always
  included (``[]`` when empty).  Pass ``include_defaults=True`` to get every
  field with its effective value instead.
* ``description`` and ``sprite`` are always included.
* entity refs (``linked_to``, ``opens``) are mapped back to the target's
  ``spec_id``; a target without one is referred to by its iid.
* ``gldtk_meta`` (JSON) is merged back into the metadata.
* ``metadata["iid"]`` holds the entity's LDtk iid; the adapter reuses it on
  re-export, so iids survive a read → edit → write cycle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from spec.catalog import (
    ENUMS,
    F_ENTITY_REF,
    F_ENUM,
    F_TEXT,
    INTERNAL_FIELDS,
    KIND_BY_UID,
    KINDS,
    FieldSpec,
    KindSpec,
)

from .ir import IREntity, IRLevel, TileValue
from .ldtk_adapter import unescape_string

_TILE_VALUES = {int(v) for v in TileValue}


@dataclass
class ReadLevel:
    identifier: str
    width_tiles: int
    height_tiles: int
    tile_size: int
    grid: List[List[Any]]  # [row][col] TileValue (raw int for unknown values)
    entities: List[IREntity] = field(default_factory=list)
    level_iid: Optional[str] = None
    prompt: Optional[str] = None
    sections: List[Dict[str, Any]] = field(default_factory=list)
    requirements: List[str] = field(default_factory=list)
    spec: Optional[Any] = None  # LevelSpec
    game_profile_relpath: Optional[str] = None
    version: Optional[str] = None  # "2" for GLDtk v2 exports, None otherwise
    warnings: List[str] = field(default_factory=list)

    def to_ir(self) -> IRLevel:
        """An :class:`IRLevel` with this grid, entities and sections."""
        ir = IRLevel(
            identifier=self.identifier,
            width_tiles=self.width_tiles,
            height_tiles=self.height_tiles,
            tile_size=self.tile_size,
            grid=[list(r) for r in self.grid],
            entities=[IREntity(e.entity_type, e.col, e.row, dict(e.metadata)) for e in self.entities],
        )
        if self.sections:
            ir.metadata["sections"] = [dict(s) for s in self.sections]
        return ir


# ─────────────────────────────────────────────────────────────────────────────
# Value decoding
# ─────────────────────────────────────────────────────────────────────────────


def _editor_value(raw: Any) -> Any:
    """Decode one ``realEditorValues`` entry (object or ``V_Int(3)`` string form)."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        params = raw.get("params") or []
        return params[0] if params else None
    if isinstance(raw, str) and "(" in raw and raw.endswith(")"):
        name, _, rest = raw.partition("(")
        body = rest[:-1]
        if name == "V_Int":
            try:
                return int(body)
            except ValueError:
                return None
        if name == "V_Float":
            try:
                return float(body)
            except ValueError:
                return None
        if name == "V_Bool":
            return body == "true"
        return body
    return None


def _is_text(fi: Dict[str, Any], fs: Optional[FieldSpec]) -> bool:
    return fs is not None and fs.ftype == F_TEXT


def _raw_values(fi: Dict[str, Any], fs: Optional[FieldSpec]) -> Optional[List[Any]]:
    """Explicitly set values (list), or None when the field is at its default.

    Reads ``realEditorValues`` (what LDtk itself loads); falls back to
    ``__value`` when it is absent.
    """
    if "realEditorValues" in fi and isinstance(fi["realEditorValues"], list):
        vals = [_editor_value(v) for v in fi["realEditorValues"]]
        if _is_text(fi, fs):
            vals = [unescape_string(v) if isinstance(v, str) else v for v in vals]
        is_array = fs.is_array if fs else str(fi.get("__type", "")).startswith("Array<")
        if is_array:
            return [v for v in vals if v is not None]
        return None if not vals or vals[0] is None else [vals[0]]
    value = fi.get("__value")
    if value is None:
        return None
    vals = value if isinstance(value, list) else [value]
    return [unescape_string(v) if isinstance(v, str) else v for v in vals]


def _shown_value(fi: Dict[str, Any], fs: Optional[FieldSpec]) -> Any:
    """Effective value from ``__value`` (default included)."""
    v = fi.get("__value")
    if isinstance(v, str):
        return unescape_string(v)
    if isinstance(v, list):
        return [unescape_string(x) if isinstance(x, str) else x for x in v]
    return v


def _ref_iid(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        v = value.get("entityIid")
        return str(v) if v else None
    if isinstance(value, str) and value:
        return value
    return None


def _decode_scalar(fs: Optional[FieldSpec], v: Any) -> Any:
    if fs is not None and fs.ftype == F_ENUM:
        decoded = ENUMS[fs.enum].from_ldtk(v)
        return decoded if decoded is not None else v
    return v


def _loads_lenient(text: Any) -> Any:
    """``json.loads`` that undoes up to two rounds of LDtk backslash escaping.

    LDtk's (un)escaping is not a perfect inverse: a JSON ``\\n`` escape can come
    back as a raw newline and a re-save doubles backslashes.  ``strict=False``
    accepts raw control characters inside strings (same meaning), and each
    retry halves doubled backslashes.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    cand = text
    for _ in range(3):
        try:
            return json.loads(cand, strict=False)
        except ValueError:
            cand = cand.replace("\\\\", "\\")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Entities
# ─────────────────────────────────────────────────────────────────────────────


def _field_spec(kind: Optional[KindSpec], fi: Dict[str, Any]) -> Optional[FieldSpec]:
    if kind is None:
        return None
    return kind.field(str(fi.get("__identifier", "")))


def _read_entity(
    ei: Dict[str, Any], grid_size: int, include_defaults: bool, warnings: List[str]
):
    identifier = str(ei.get("__identifier") or "")
    kind = KINDS.get(identifier)
    if kind is None and ei.get("defUid") in KIND_BY_UID and not identifier:
        kind = KIND_BY_UID[ei["defUid"]]
    entity_type = kind.kind if kind else (identifier or f"def{ei.get('defUid')}")

    grid = ei.get("__grid")
    if isinstance(grid, list) and len(grid) == 2:
        col, row = int(grid[0]), int(grid[1])
    else:
        px = ei.get("px") or [0, 0]
        col = int(px[0]) // grid_size
        row = (int(px[1]) - 1) // grid_size

    meta: Dict[str, Any] = {}
    refs: Dict[str, str] = {}
    extra: Dict[str, Any] = {}
    for fi in ei.get("fieldInstances") or []:
        name = str(fi.get("__identifier", ""))
        fs = _field_spec(kind, fi)
        is_ref = (fs.ftype == F_ENTITY_REF) if fs else "EntityRef" in str(fi.get("__type", ""))
        if is_ref:
            raw = _raw_values(fi, fs)
            iid = _ref_iid(raw[0]) if raw else _ref_iid(fi.get("__value"))
            if iid:
                refs[name] = iid
            elif include_defaults:
                meta[name] = None
            continue
        if name == "gldtk_meta":
            raw = _raw_values(fi, fs)
            parsed = _loads_lenient(raw[0] if raw else None)
            if isinstance(parsed, dict):
                extra = parsed
            elif raw:
                warnings.append(f"entity {ei.get('iid')}: unreadable gldtk_meta ignored")
            continue

        is_array = fs.is_array if fs else str(fi.get("__type", "")).startswith("Array<")
        always = name in ("description", "sprite") or is_array
        if include_defaults or always or fs is None:
            value = _shown_value(fi, fs)
            if is_array:
                value = [_decode_scalar(fs, v) for v in (value or [])]
            else:
                value = _decode_scalar(fs, value)
            if name in ("description", "sprite") and value is None:
                value = ""
            if fs is None or value is not None or include_defaults or always:
                meta[name] = value
            continue
        raw = _raw_values(fi, fs)
        if raw:
            meta[name] = _decode_scalar(fs, raw[0])

    if kind is not None:
        for name in ("description", "sprite"):
            meta.setdefault(name, "")
    for name in INTERNAL_FIELDS:
        if name in meta and meta[name] in (None, ""):
            del meta[name]
    for k, v in extra.items():
        meta.setdefault(k, v)
    if ei.get("iid"):
        meta["iid"] = str(ei["iid"])
    return IREntity(entity_type=entity_type, col=col, row=row, metadata=meta), refs


# ─────────────────────────────────────────────────────────────────────────────
# Level fields
# ─────────────────────────────────────────────────────────────────────────────


def _level_fields(level: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for fi in level.get("fieldInstances") or []:
        name = str(fi.get("__identifier", ""))
        is_text = name in ("gldtk_prompt", "gldtk_sections", "gldtk_spec")
        fs = FieldSpec(name, F_TEXT) if is_text else None
        raw = _raw_values(fi, fs)
        if name == "gldtk_requirements":
            out[name] = [str(v) for v in (raw or []) if v is not None]
        else:
            out[name] = raw[0] if raw else None
    return out


def _parse_spec(text: Any, warnings: List[str]) -> Optional[Any]:
    data = _loads_lenient(text)
    if data is None:
        if text:
            warnings.append("gldtk_spec is not valid JSON; spec ignored")
        return None
    try:
        from spec.level_spec import LevelSpec

        return LevelSpec.model_validate(data)
    except Exception as exc:  # pydantic ValidationError and friends
        warnings.append(f"gldtk_spec does not match LevelSpec ({exc.__class__.__name__}); spec ignored")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────


def _find_layer(layers: List[Dict[str, Any]], layer_type: str, preferred: str) -> Optional[Dict[str, Any]]:
    typed = [li for li in layers if li.get("__type") == layer_type]
    for li in typed:
        if li.get("__identifier") == preferred:
            return li
    return typed[0] if typed else None


def read_level(
    source: Union[str, Path, Dict[str, Any]],
    level: Union[int, str] = 0,
    include_defaults: bool = False,
) -> ReadLevel:
    """Read one level (index or identifier; default the first) of an LDtk project."""
    if isinstance(source, dict):
        project = source
    else:
        project = json.loads(Path(source).read_text(encoding="utf-8"))

    levels = list(project.get("levels") or [])
    for w in project.get("worlds") or []:
        levels += list(w.get("levels") or [])
    if not levels:
        raise ValueError("LDtk project has no levels")
    if isinstance(level, int):
        lvl = levels[level]
    else:
        matches = [lv for lv in levels if lv.get("identifier") == level]
        if not matches:
            raise KeyError(f"no level named {level!r}")
        lvl = matches[0]

    warnings: List[str] = []
    grid_size = int(project.get("defaultGridSize") or 16)
    layers = lvl.get("layerInstances")
    if layers is None:
        warnings.append("level has no layerInstances (external level files are not supported)")
        layers = []

    intgrid = _find_layer(layers, "IntGrid", "Collisions")
    if intgrid is not None:
        grid_size = int(intgrid.get("__gridSize") or grid_size)
    width = int(intgrid["__cWid"]) if intgrid else int(lvl.get("pxWid", 0)) // grid_size
    height = int(intgrid["__cHei"]) if intgrid else int(lvl.get("pxHei", 0)) // grid_size

    grid: List[List[Any]] = [[TileValue.EMPTY] * width for _ in range(height)]
    if intgrid is not None:
        csv = list(intgrid.get("intGridCsv") or [])
        for i, v in enumerate(csv[: width * height]):
            v = int(v)
            grid[i // width][i % width] = TileValue(v) if v in _TILE_VALUES else v
    else:
        warnings.append("level has no IntGrid layer; grid is empty")

    entities: List[IREntity] = []
    ref_maps: List[Dict[str, str]] = []
    for li in layers:
        if li.get("__type") != "Entities":
            continue
        g = int(li.get("__gridSize") or grid_size)
        for ei in li.get("entityInstances") or []:
            ent, refs = _read_entity(ei, g, include_defaults, warnings)
            entities.append(ent)
            ref_maps.append(refs)

    # Entity refs → target spec id (or iid when the target has none).
    spec_id_of = {
        e.metadata.get("iid"): e.metadata.get("spec_id") for e in entities if e.metadata.get("iid")
    }
    for ent, refs in zip(entities, ref_maps):
        for name, iid in refs.items():
            if iid not in spec_id_of:
                warnings.append(f"entity {ent.metadata.get('spec_id') or ent.entity_type}: "
                                f"{name} points at missing entity {iid}")
            ent.metadata[name] = spec_id_of.get(iid) or iid

    fields = _level_fields(lvl)
    sections = _loads_lenient(fields.get("gldtk_sections"))
    if fields.get("gldtk_sections") and not isinstance(sections, list):
        warnings.append("gldtk_sections is not a JSON list; ignored")
    return ReadLevel(
        identifier=str(lvl.get("identifier") or "Level_0"),
        width_tiles=width,
        height_tiles=height,
        tile_size=grid_size,
        grid=grid,
        entities=entities,
        level_iid=lvl.get("iid"),
        prompt=fields.get("gldtk_prompt"),
        sections=sections if isinstance(sections, list) else [],
        requirements=fields.get("gldtk_requirements") or [],
        spec=_parse_spec(fields.get("gldtk_spec"), warnings),
        game_profile_relpath=fields.get("gldtk_game_profile"),
        version=fields.get("gldtk_version"),
        warnings=warnings,
    )


__all__ = ["ReadLevel", "read_level"]
