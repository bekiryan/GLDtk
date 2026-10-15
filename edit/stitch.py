"""Stitch an edit into the ``.ldtk``: grid, entities, margin tiles, level fields.

Nothing outside the edit is touched.  For a GLDtk v2 file the edit is spliced
into the project JSON as read:

* the IntGrid cells of the region are replaced (cells outside it are equal);
* entity instances that were removed are dropped, modified and new ones come
  from a fresh ``to_ldtk_dict`` export of the edited level (the read entities
  keep their iids, so refs resolve), every other instance stays exactly as it
  was in the file, including fields the user edited by hand;
* the tiles of the region and of its 1-tile margin are re-autotiled from the
  new grid (each cell in its section's theme sheet), so the art joins up;
  every other tile, including hand-painted ones, stays;
* the ``gldtk_*`` level fields (requirements, spec, …) are refreshed.

A v1 or hand-made file (``gldtk_version`` not ``"2"``) has no GLDtk entity
catalog, so it is re-exported as a whole through ``to_ldtk_dict`` instead
(grid, entities with their iids and fields, sections); this upgrades it to
the v2 format on its first edit.

Refs from an untouched entity to a removed one are retargeted to a new entity
with the same spec id when there is one, otherwise cleared with a warning.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from aesthetic import THEMES, Theme, autotile_level, build_aesthetic_layer, detect_theme
from aesthetic.pipeline import copy_aesthetic_assets
from aesthetic.sprites import build_sprite_atlas
from layout.ir import IREntity, TileValue
from layout.ldtk_adapter import (
    ENTITY_ATLAS_UID,
    AestheticData,
    LDtkConfig,
    LevelMeta,
    TileEntry,
    grid_tile_json,
    to_ldtk_dict_with_warnings,
)
from spec.level_spec import LevelSpec
from symbolic.schema import AbstractLevelGraph

from . import history
from .context import EditContext, EditError, Locks, Rect, _level_json, level_json

GLDTK_FIELD_PREFIX = "gldtk_"


# ─────────────────────────────────────────────────────────────────────────────
# Grid
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RegionNode:
    """A platform or hazard strip in *level* coordinates."""

    id: str
    col: int
    row: int
    w: int
    type: str = "Platform"  # Platform | Hazard


def apply_region(grid: List[List[Any]], rect: Rect, nodes: Iterable[RegionNode], locks: Locks
                 ) -> List[List[Any]]:
    """A copy of *grid* with *rect* cleared, *nodes* stamped and *locks* enforced.

    Only cells inside *rect* change (node cells outside it are ignored).
    """
    out = [list(r) for r in grid]
    for c, r in rect.cells():
        out[r][c] = TileValue.EMPTY
    for n in nodes:
        value = TileValue.HAZARD if n.type == "Hazard" else TileValue.SOLID
        for c in range(n.col, n.col + n.w):
            if rect.contains(c, n.row):
                out[n.row][c] = value
    for c, r in locks.clear:
        out[r][c] = TileValue.EMPTY
    for c, r in locks.solid:
        out[r][c] = TileValue.SOLID
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Themes per cell
# ─────────────────────────────────────────────────────────────────────────────


def band_theme(ctx: EditContext, band) -> Theme:
    """The library theme a section uses (its ``mapped_theme``, else keywords)."""
    if band.mapped_theme in THEMES:
        return THEMES[band.mapped_theme]
    theme = detect_theme(band.theme)
    if theme.identifier == "default":
        theme = detect_theme(ctx.level.prompt or "")
    return theme


def _theme_by_uid() -> Dict[int, Theme]:
    return {t.tileset_uid: t for t in THEMES.values()}


# ─────────────────────────────────────────────────────────────────────────────
# Result of a stitch
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Stitched:
    doc: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)
    rebuilt: bool = False  # the whole file was re-exported (v1 / hand-made file)


def _level_meta(ctx: EditContext, spec: Optional[LevelSpec], requirements: List[Any]) -> LevelMeta:
    return LevelMeta(
        prompt=ctx.level.prompt,
        sections=[dict(b.raw) for b in ctx.bands if b.raw] or None,
        requirements=list(requirements),
        game_profile_relpath=ctx.level.game_profile_relpath,
        spec_json=spec,
    )


def _cfg(ctx: EditContext) -> LDtkConfig:
    tw, th = ctx.profile.player_tiles()
    return LDtkConfig(player_px=(tw * ctx.tile_size, th * ctx.tile_size))


# ─────────────────────────────────────────────────────────────────────────────
# Full re-export (v1 / hand-made files)
# ─────────────────────────────────────────────────────────────────────────────


def _rebuild(ctx: EditContext, grid, entities, spec, requirements) -> Stitched:
    ir = ctx.to_ir(grid, [IREntity(e.entity_type, e.col, e.row, dict(e.metadata)) for e in entities])
    sections = []
    for b in ctx.bands:
        raw = dict(b.raw) if b.raw else {
            "id": b.id, "name": b.name, "orientation": b.orientation,
            "col_start": b.col_start, "col_end": b.col_end,
            "row_start": b.row_start, "row_end": b.row_end,
            "gravity_scale": b.gravity_scale, "theme": b.theme, "difficulty": b.difficulty,
        }
        raw["mapped_theme"] = band_theme(ctx, b).identifier
        sections.append(raw)
    ir.metadata["sections"] = sections
    result = build_aesthetic_layer(
        description=ctx.level.prompt or "",
        graph=AbstractLevelGraph(),
        node_layouts={},
        ir_level=ir,
        place_random_entities=False,
    )
    copy_aesthetic_assets(result, ctx.path.parent)
    meta = _level_meta(ctx, spec, requirements)
    meta.sections = sections
    lvl = level_json(ctx)
    doc, warnings = to_ldtk_dict_with_warnings(
        ir, aesthetic=result.aesthetic, cfg=_cfg(ctx), level_meta=meta,
        level_uid=int(lvl.get("uid") or 0),
        world_x=int(lvl.get("worldX") or 0), world_y=int(lvl.get("worldY") or 0),
    )
    return Stitched(doc, [f"export: {w}" for w in warnings], rebuilt=True)


# ─────────────────────────────────────────────────────────────────────────────
# Splice (GLDtk v2 files)
# ─────────────────────────────────────────────────────────────────────────────


def _entity_layers(lvl: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [li for li in lvl.get("layerInstances") or [] if li.get("__type") == "Entities"]


def _is_ref(fi: Dict[str, Any]) -> bool:
    return "EntityRef" in str(fi.get("__type", ""))


def _ref_targets(fi: Dict[str, Any]) -> List[str]:
    vals = fi.get("__value")
    vals = vals if isinstance(vals, list) else [vals]
    return [v.get("entityIid") for v in vals if isinstance(v, dict) and v.get("entityIid")]


def _set_ref(fi: Dict[str, Any], target: Optional[Dict[str, str]]) -> None:
    """Point a (non-array) ref field at *target* (``__value`` form) or clear it."""
    fi["__value"] = dict(target) if target else None
    fi["realEditorValues"] = [{"id": "V_String", "params": [target["entityIid"]]} if target else None]


def _retarget_refs(ei: Dict[str, Any], iid_layer: Dict[str, str], level_iid: str, world_iid: str) -> None:
    """Make a fresh instance's refs use the file's level / layer / world iids."""
    for fi in ei.get("fieldInstances") or []:
        if not _is_ref(fi):
            continue
        vals = fi.get("__value")
        for v in vals if isinstance(vals, list) else [vals]:
            if isinstance(v, dict) and v.get("entityIid"):
                v["layerIid"] = iid_layer.get(v["entityIid"], v.get("layerIid"))
                v["levelIid"] = level_iid
                v["worldIid"] = world_iid


def _patch_margin_tiles(
    ctx: EditContext, lvl: Dict[str, Any], doc: Dict[str, Any], grid, margin: Rect,
    warnings: List[str],
) -> None:
    tile_layers = [li for li in lvl.get("layerInstances") or [] if li.get("__type") == "Tiles"]
    if not tile_layers:
        return
    ts = ctx.tile_size
    tileset_w = {int(t["uid"]): int(t.get("pxWid") or 0) for t in doc["defs"].get("tilesets") or []}
    by_uid = _theme_by_uid()

    def layer_uid(li: Dict[str, Any]) -> Optional[int]:
        uid = li.get("overrideTilesetUid") or li.get("__tilesetDefUid")
        return int(uid) if uid is not None else None

    layer_for_uid = {}
    for li in tile_layers:
        uid = layer_uid(li)
        if uid is not None:
            layer_for_uid.setdefault(uid, li)
    if not layer_for_uid:
        return

    # Themes: the section's theme when the file has a Tiles layer for its sheet;
    # otherwise the sheet the file already uses there (theme sheets share one layout).
    fallback_layer = next(iter(layer_for_uid.values()))
    fallback_theme = by_uid.get(layer_uid(fallback_layer) or -1)
    themes: Dict[str, Optional[Theme]] = {}
    for b in ctx.bands:
        t = band_theme(ctx, b)
        themes[b.id] = t if t.tileset_uid in layer_for_uid else (fallback_theme or t)

    def theme_for_cell(col: int, row: int) -> Theme:
        return themes[ctx.band_at(col, row).id] or THEMES["default"]

    ir = ctx.to_ir(grid, [])
    default = theme_for_cell(margin.col, margin.row)
    entries: List[TileEntry] = [
        te for te in autotile_level(ir, default, theme_for_cell=theme_for_cell)
        if margin.contains(te.col, te.row)
    ]

    for li in tile_layers:
        li["gridTiles"] = [
            t for t in li.get("gridTiles") or []
            if not margin.contains(int(t["px"][0]) // ts, int(t["px"][1]) // ts)
        ]
    for te in entries:
        li = layer_for_uid.get(te.tileset_uid) or fallback_layer
        uid = layer_uid(li)
        width_px = tileset_w.get(uid, 80) if uid is not None else 80
        li["gridTiles"].append(grid_tile_json(te, ts, ctx.width, width_px))


def splice(
    ctx: EditContext,
    grid: List[List[Any]],
    entities: List[IREntity],
    *,
    spec: Optional[LevelSpec],
    requirements: List[Any],
    rect: Optional[Rect] = None,
    removed: Iterable[str] = (),
    modified: Iterable[str] = (),
    added: Iterable[str] = (),
) -> Stitched:
    """Build the edited project JSON.

    *entities* is the final entity list (read entities keep ``metadata["iid"]``);
    *removed*, *modified* and *added* name iids.  *rect* (None for entity-only
    edits) is the region whose grid cells and margin tiles change.
    """
    removed, modified, added = set(removed), set(modified), set(added)
    if ctx.level.version != "2":
        return _rebuild(ctx, grid, entities, spec, requirements)

    doc = copy.deepcopy(ctx.doc)
    lvl = _level_json(doc)
    warnings: List[str] = []
    layers = _entity_layers(lvl)
    if not layers:
        return _rebuild(ctx, grid, entities, spec, requirements)

    # ── fresh export of the edited level (new / modified instances, level fields) ──
    defs_tilesets = {int(t["uid"]) for t in doc.get("defs", {}).get("tilesets") or []}
    atlas = None
    if (modified or added) and ENTITY_ATLAS_UID in defs_tilesets:
        sa = build_sprite_atlas()
        atlas = sa.to_entity_atlas() if sa else None
    world_iid = doc.get("dummyWorldIid") or ""
    for w in doc.get("worlds") or []:
        if any(lv is lvl for lv in w.get("levels") or []):
            world_iid = w.get("iid", world_iid)
    fresh, fresh_warnings = to_ldtk_dict_with_warnings(
        ctx.to_ir(grid, [IREntity(e.entity_type, e.col, e.row, dict(e.metadata)) for e in entities]),
        aesthetic=AestheticData(entity_atlas=atlas) if atlas else None,
        cfg=_cfg(ctx),
        level_meta=_level_meta(ctx, spec, requirements),
        level_uid=int(lvl.get("uid") or 0),
        world_x=int(lvl.get("worldX") or 0),
        world_y=int(lvl.get("worldY") or 0),
    )
    fresh_lvl = fresh["levels"][0]
    changed_ids = {str(e.metadata.get("spec_id")) for e in entities
                   if e.metadata.get("iid") in modified | added and e.metadata.get("spec_id")}
    warnings += [f"export: {w}" for w in fresh_warnings
                 if any(f"'{sid}'" in w or f" {sid}@" in w for sid in changed_ids)]
    known_defs = {int(d["uid"]) for d in doc["defs"].get("entities") or []}
    fresh_by_iid = {
        ei["iid"]: ei for li in _entity_layers(fresh_lvl) for ei in li.get("entityInstances") or []
    }
    for iid in modified | added:
        ei = fresh_by_iid.get(iid)
        if ei is None or int(ei.get("defUid", -1)) not in known_defs:
            # The file lacks the GLDtk catalog def for this kind: re-export it whole.
            return _rebuild(ctx, grid, entities, spec, requirements)

    # ── entity instances ──
    main = next((li for li in layers if li.get("__identifier") == "Entities"), layers[0])
    iid_layer = {ei["iid"]: li["iid"] for li in layers for ei in li.get("entityInstances") or []}
    for iid in added:
        iid_layer[iid] = main["iid"]
    spec_id_of_removed = {}
    for li in layers:
        for ei in li.get("entityInstances") or []:
            if ei["iid"] in removed:
                for fi in ei.get("fieldInstances") or []:
                    if fi.get("__identifier") == "spec_id" and fi.get("__value"):
                        spec_id_of_removed[ei["iid"]] = fi["__value"]
    new_by_spec_id = {str(e.metadata.get("spec_id")): e.metadata["iid"] for e in entities
                      if e.metadata.get("iid") in added and e.metadata.get("spec_id")}

    for li in layers:
        out = []
        for ei in li.get("entityInstances") or []:
            iid = ei.get("iid")
            if iid in removed:
                continue
            if iid in modified:
                ei = copy.deepcopy(fresh_by_iid[iid])
                _retarget_refs(ei, iid_layer, lvl["iid"], world_iid)
            out.append(ei)
        if li is main:
            for e in entities:
                iid = e.metadata.get("iid")
                if iid in added:
                    ei = copy.deepcopy(fresh_by_iid[iid])
                    _retarget_refs(ei, iid_layer, lvl["iid"], world_iid)
                    out.append(ei)
        li["entityInstances"] = out

    # Refs from kept instances to removed entities.
    for li in layers:
        for ei in li["entityInstances"]:
            if ei["iid"] in modified or ei["iid"] in added:
                continue
            for fi in ei.get("fieldInstances") or []:
                if not _is_ref(fi):
                    continue
                for target in _ref_targets(fi):
                    if target not in removed:
                        continue
                    who = next((f.get("__value") for f in ei.get("fieldInstances") or []
                                if f.get("__identifier") == "spec_id" and f.get("__value")),
                               ei.get("__identifier"))
                    sid = spec_id_of_removed.get(target)
                    new_iid = new_by_spec_id.get(str(sid)) if sid else None
                    name = fi.get("__identifier")
                    if new_iid and not isinstance(fi.get("__value"), list):
                        _set_ref(fi, {"entityIid": new_iid, "layerIid": main["iid"],
                                      "levelIid": lvl["iid"], "worldIid": world_iid})
                        warnings.append(f"entity '{who}': {name} now points at the new '{sid}'")
                    else:
                        if isinstance(fi.get("__value"), list):
                            keep = [v for v in fi["__value"]
                                    if not (isinstance(v, dict) and v.get("entityIid") == target)]
                            fi["__value"] = keep
                            fi["realEditorValues"] = [{"id": "V_String", "params": [v["entityIid"]]}
                                                      for v in keep]
                        else:
                            _set_ref(fi, None)
                        warnings.append(
                            f"entity '{who}': {name} pointed at '{sid or target}', which the edit "
                            "removed; the link was cleared")

    # ── IntGrid + margin tiles ──
    if rect is not None:
        intgrid = next(li for li in lvl["layerInstances"] if li.get("__type") == "IntGrid")
        csv = list(intgrid.get("intGridCsv") or [])
        need = ctx.width * ctx.height
        if len(csv) < need:
            csv += [0] * (need - len(csv))
        for c, r in rect.cells():
            csv[r * ctx.width + c] = int(grid[r][c])
        intgrid["intGridCsv"] = csv
        _patch_margin_tiles(ctx, lvl, doc, grid, rect.expanded(1, ctx.width, ctx.height), warnings)

    # ── level fields ──
    fresh_fields = {fi["__identifier"]: fi for fi in fresh_lvl.get("fieldInstances") or []}
    level_fields = list(lvl.get("fieldInstances") or [])
    names = {fi.get("__identifier") for fi in level_fields}
    lvl["fieldInstances"] = [
        fresh_fields.get(fi.get("__identifier"), fi) if str(fi.get("__identifier", "")).startswith(GLDTK_FIELD_PREFIX)
        else fi for fi in level_fields
    ] + [fi for name, fi in fresh_fields.items() if name not in names]
    have_defs = {int(d["uid"]) for d in doc["defs"].get("levelFields") or []}
    for d in fresh["defs"].get("levelFields") or []:
        if int(d["uid"]) not in have_defs:
            doc["defs"].setdefault("levelFields", []).append(d)
    doc["nextUid"] = max(int(doc.get("nextUid") or 0), int(fresh.get("nextUid") or 0))
    return Stitched(doc, warnings)


# ─────────────────────────────────────────────────────────────────────────────
# Write
# ─────────────────────────────────────────────────────────────────────────────


def write(ctx: EditContext, stitched: Stitched, label: str) -> Optional[Path]:
    """Snapshot the current file, then write the edited one.  Returns the snapshot.

    The file is only overwritten when it still holds what the edit read, so a
    save from LDtk in the meantime is never lost.
    """
    if ctx.path.read_bytes() != ctx.raw:
        raise EditError(f"{ctx.path.name} changed on disk while the edit ran; nothing was "
                        "written. Save in LDtk and try again.")
    snap = history.snapshot(ctx.path, label)
    tmp = ctx.path.with_name(ctx.path.name + ".gldtk_tmp")
    tmp.write_text(json.dumps(stitched.doc, indent=2), encoding="utf-8")
    tmp.replace(ctx.path)
    return snap


__all__ = ["RegionNode", "Stitched", "apply_region", "band_theme", "splice", "write"]
