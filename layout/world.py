"""Multi-level worlds in one ``.ldtk`` (v3 Part 17).

A world is an LDtk project with several levels in a ``LinearHorizontal`` world
layout.  Each level is exported on its own by ``to_ldtk_dict`` (so it keeps its
own ``gldtk_*`` fields) and *grafted* into the world document:

* the layer defs are shared by every level, so the Tiles layers become
  positional *slots* (``Tiles``, ``Tiles_2``, …); a level fills slot *i* with
  its own *i*-th Tiles layer (its own theme sheet, through LDtk's per-level
  ``overrideTilesetUid``) and gets an empty instance for the slots it does not
  use.  Tileset defs are the union of every level's;
* the level gets a free uid, its refs point at its final iid and the world
  iid, and its world coordinates are written the way LDtk writes linear
  layouts (``worldX``/``worldY`` -1, no entity ``__worldX``/``__worldY``).

:func:`link_world` then sets the cross-level links: every Exit of level *k*
gets ``leads_to`` = the Start of level *k+1* (an entity ref across levels),
and the Exits of the last level get ``final: true``.  It only rewrites a field
whose value is wrong, so relinking an unchanged world changes nothing (edits
rely on that: the levels they do not touch stay byte-identical).

The outline is stored in the first level's ``gldtk_world`` field.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Sequence

from spec.catalog import KINDS

from .ldtk_adapter import (
    _EXTRA_LAYER_UIDS,
    _RefTarget,
    _field_instance,
    _layer_instance_base,
    _ref_instance,
)
from .ldtk_reader import all_levels

WORLD_LAYOUT = "LinearHorizontal"
LINEAR_LAYOUTS = ("LinearHorizontal", "LinearVertical")


def is_world(doc: Dict[str, Any]) -> bool:
    """More than one level."""
    return len(all_levels(doc)) > 1


def level_identifier(title: str, index: int, taken: Sequence[str] = ()) -> str:
    """A unique LDtk identifier for level *index* (0-based): ``Forest_1``, ``Cave_2`` …"""
    words = re.findall(r"[A-Za-z0-9]+", title or "")
    base = "_".join(w[:1].upper() + w[1:] for w in words[:3]) or "Level"
    if base[0].isdigit():
        base = "L" + base
    name = f"{base}_{index + 1}"
    n = 2
    taken_set = set(taken)
    while name in taken_set:
        name = f"{base}_{index + 1}_{n}"
        n += 1
    return name


# ─────────────────────────────────────────────────────────────────────────────
# Graft one exported level into a world document
# ─────────────────────────────────────────────────────────────────────────────


def _tiles_defs(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [d for d in doc["defs"]["layers"] if d.get("__type") == "Tiles"]


def _used_uids(doc: Dict[str, Any]) -> set:
    uids = {int(d["uid"]) for d in doc["defs"].get("layers") or []}
    uids |= {int(t["uid"]) for t in doc["defs"].get("tilesets") or []}
    uids |= {int(e["uid"]) for e in doc["defs"].get("entities") or []}
    uids |= {int(lv.get("uid") or 0) for lv in all_levels(doc)}
    return uids


def _empty_tiles_instance(slot: Dict[str, Any], lvl: Dict[str, Any], tilesets: Dict[int, Dict[str, Any]],
                          grid_size: int) -> Dict[str, Any]:
    ts_uid = slot.get("tilesetDefUid")
    ts = tilesets.get(int(ts_uid)) if ts_uid is not None else None
    li = _layer_instance_base(
        slot["identifier"], "Tiles", int(lvl["pxWid"]) // grid_size, int(lvl["pxHei"]) // grid_size,
        grid_size, int(slot["uid"]), int(lvl.get("uid") or 0),
        tileset_def_uid=ts_uid, tileset_rel_path=ts.get("relPath") if ts else None,
    )
    li["overrideTilesetUid"] = ts_uid
    return li


def _new_slot(doc: Dict[str, Any], template: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Add Tiles slot *index* to ``defs.layers`` (before the IntGrid) and an empty
    instance of it to every level already in the world."""
    used = _used_uids(doc)
    if index == 0 and 3 not in used:
        uid = 3
    else:
        free = [u for u in _EXTRA_LAYER_UIDS if u not in used]
        uid = free[0] if free else max(int(doc.get("nextUid") or 0), max(used) + 1)
    slot = copy.deepcopy(template)
    slot["uid"] = uid
    slot["identifier"] = "Tiles" if index == 0 else f"Tiles_{index + 1}"
    layers = doc["defs"]["layers"]
    pos = max((i for i, d in enumerate(layers) if d.get("__type") in ("Tiles", "Entities")),
              default=-1) + 1
    layers.insert(pos, slot)
    doc["nextUid"] = max(int(doc.get("nextUid") or 0), uid + 1)
    tilesets = {int(t["uid"]): t for t in doc["defs"].get("tilesets") or []}
    grid = int(doc.get("defaultGridSize") or 16)
    for lvl in all_levels(doc):
        lis = lvl.get("layerInstances") or []
        at = max((i for i, li in enumerate(lis) if li.get("__type") in ("Tiles", "Entities")),
                 default=-1) + 1
        lis.insert(at, _empty_tiles_instance(slot, lvl, tilesets, grid))
    return slot


def _retarget_level(lvl: Dict[str, Any], old_iid: str, new_iid: str, world_iid: str) -> None:
    for li in lvl.get("layerInstances") or []:
        for ei in li.get("entityInstances") or []:
            for fi in ei.get("fieldInstances") or []:
                vals = fi.get("__value")
                for v in vals if isinstance(vals, list) else [vals]:
                    if isinstance(v, dict) and "entityIid" in v:
                        if v.get("levelIid") == old_iid:
                            v["levelIid"] = new_iid
                        v["worldIid"] = world_iid


def graft_level(doc: Dict[str, Any], fresh: Dict[str, Any], index: Optional[int] = None) -> Dict[str, Any]:
    """Put the single level of the export *fresh* into the world *doc* (in place).

    *index* None appends it; otherwise it replaces level *index*, keeping that
    level's iid, uid and identifier.  Returns the grafted level dict.
    """
    lvl = copy.deepcopy(fresh["levels"][0])
    defs, fdefs = doc["defs"], fresh["defs"]
    levels = doc["levels"]
    world_iid = doc.get("dummyWorldIid") or ""

    # ── tilesets and level-field defs: the union ──
    have = {int(t["uid"]) for t in defs.get("tilesets") or []}
    for t in fdefs.get("tilesets") or []:
        if int(t["uid"]) not in have:
            defs.setdefault("tilesets", []).append(copy.deepcopy(t))
            have.add(int(t["uid"]))
    have_fields = {int(d["uid"]) for d in defs.get("levelFields") or []}
    for d in fdefs.get("levelFields") or []:
        if int(d["uid"]) not in have_fields:
            defs.setdefault("levelFields", []).append(copy.deepcopy(d))

    # ── uid / iid / identifier ──
    if index is None:
        uid = max(int(doc.get("nextUid") or 0), max(_used_uids(doc)) + 1)
        doc["nextUid"] = uid + 1
        taken = [str(lv.get("identifier")) for lv in levels]
        if lvl["identifier"] in taken:
            lvl["identifier"] = level_identifier(lvl["identifier"], len(levels), taken)
    else:
        old = levels[index]
        uid = int(old.get("uid") or 0)
        _retarget_level(lvl, lvl["iid"], old["iid"], world_iid)
        lvl["iid"] = old["iid"]
        lvl["identifier"] = old["identifier"]
    if index is None:
        _retarget_level(lvl, lvl["iid"], lvl["iid"], world_iid)
    lvl["uid"] = uid

    # ── layers: Entities, the Tiles slots, the IntGrid (defs order) ──
    fresh_tiles = [li for li in lvl["layerInstances"] if li.get("__type") == "Tiles"]
    fresh_tile_defs = {int(d["uid"]): d for d in _tiles_defs(fresh)}
    slots = _tiles_defs(doc)
    for i in range(len(slots), len(fresh_tiles)):
        template = fresh_tile_defs.get(int(fresh_tiles[i]["layerDefUid"])) or _tiles_defs(fresh)[i]
        _new_slot(doc, template, i)
    slots = _tiles_defs(doc)
    tilesets = {int(t["uid"]): t for t in defs.get("tilesets") or []}
    grid = int(doc.get("defaultGridSize") or 16)
    # IntGrids are told apart by identifier (Collisions / the optional Background)
    by_type = {(li.get("__identifier") if li.get("__type") == "IntGrid" else li.get("__type")): li
               for li in lvl["layerInstances"] if li.get("__type") != "Tiles"}
    out: List[Dict[str, Any]] = []
    for d in defs["layers"]:
        t = d.get("__type")
        if t == "Tiles":
            i = slots.index(d)
            if i < len(fresh_tiles):
                li = fresh_tiles[i]
                li["__identifier"] = d["identifier"]
                li["layerDefUid"] = int(d["uid"])
            else:
                li = _empty_tiles_instance(d, lvl, tilesets, grid)
        else:
            li = by_type.get(d["identifier"] if t == "IntGrid" else t)
            if li is None and t == "IntGrid" and d["identifier"] == "Background":
                # the world has the decorative layer but this level does not: an empty one
                w, h = int(lvl["pxWid"]) // grid, int(lvl["pxHei"]) // grid
                li = _layer_instance_base("Background", "IntGrid", w, h, grid, int(d["uid"]),
                                          uid)
                li["intGridCsv"] = [0] * (w * h)
            if li is None:
                continue
            li["layerDefUid"] = int(d["uid"])
            li["__identifier"] = d["identifier"]
        out.append(li)
    for li in out:
        li["levelId"] = uid
    lvl["layerInstances"] = out

    # ── world coordinates (linear layouts) ──
    if doc.get("worldLayout") in LINEAR_LAYOUTS:
        lvl["worldX"] = lvl["worldY"] = -1
        lvl["__neighbours"] = []
        for li in out:
            for ei in li.get("entityInstances") or []:
                ei["__worldX"] = ei["__worldY"] = None

    doc["nextUid"] = max(int(doc.get("nextUid") or 0), int(fresh.get("nextUid") or 0))
    if index is None:
        levels.append(lvl)
    else:
        levels[index] = lvl
    return lvl


def new_world_doc(first: Dict[str, Any]) -> Dict[str, Any]:
    """An empty ``LinearHorizontal`` world built around the project root of the export
    *first* (its defs without Tiles layers or tilesets; graft the levels next)."""
    doc = copy.deepcopy(first)
    doc["levels"] = []
    doc["worldLayout"] = WORLD_LAYOUT
    doc["defs"]["layers"] = [d for d in doc["defs"]["layers"] if d.get("__type") != "Tiles"]
    doc["defs"]["tilesets"] = []
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# Cross-level links
# ─────────────────────────────────────────────────────────────────────────────


def _instances(lvl: Dict[str, Any], kind: str):
    for li in lvl.get("layerInstances") or []:
        for ei in li.get("entityInstances") or []:
            if ei.get("__identifier") == kind:
                yield li, ei


def link_world(doc: Dict[str, Any]) -> int:
    """Set every Exit's ``leads_to`` / ``final`` (see the module docstring).

    Returns the number of fields changed (0 when the links were already right).
    """
    levels = all_levels(doc)
    exit_kind = KINDS["Exit"]
    lead_fs, final_fs = exit_kind.field("leads_to"), exit_kind.field("final")
    lead_uid, final_uid = exit_kind.field_uid("leads_to"), exit_kind.field_uid("final")
    world_iid = doc.get("dummyWorldIid") or ""
    # a file whose Exit def predates the world fields gets no new instances of them
    exit_def = next((d for d in doc.get("defs", {}).get("entities") or []
                     if d.get("identifier") == "Exit"), {})
    def_uids = {int(f["uid"]) for f in exit_def.get("fieldDefs") or []}
    can_add = lead_uid in def_uids and final_uid in def_uids
    changed = 0
    for k, lvl in enumerate(levels):
        target: Optional[_RefTarget] = None
        if k + 1 < len(levels):
            nxt = levels[k + 1]
            start = next(_instances(nxt, "Start"), None)
            if start is not None:
                target = _RefTarget(start[1]["iid"], start[0]["iid"], nxt["iid"], world_iid)
        final = k == len(levels) - 1
        for _, ei in _instances(lvl, "Exit"):
            fis = ei.setdefault("fieldInstances", [])
            names = [fi.get("__identifier") for fi in fis]
            want = target.as_json() if target else None
            if "leads_to" in names:
                i = names.index("leads_to")
                if fis[i].get("__value") != want:
                    fis[i] = _ref_instance(lead_fs, lead_uid, target)
                    changed += 1
            elif want is not None and can_add:
                fis.append(_ref_instance(lead_fs, lead_uid, target))
                changed += 1
            if "final" in names:
                i = names.index("final")
                if bool(fis[i].get("__value")) != final:
                    fis[i] = _field_instance(final_fs, final_uid, True if final else None)
                    changed += 1
            elif final and can_add:
                fis.append(_field_instance(final_fs, final_uid, True))
                changed += 1
    return changed


def world_levels(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``[{index, id, identifier, iid, title}]`` for every level (title from the outline
    in ``gldtk_world`` when there is one, else the identifier; ``id`` is the outline
    level id or None)."""
    from .ldtk_reader import _level_fields, _loads_lenient

    levels = all_levels(doc)
    outline = None
    if levels:
        outline = _loads_lenient(_level_fields(levels[0]).get("gldtk_world"))
    entries: Dict[int, Dict[str, Any]] = {}
    if isinstance(outline, dict):
        for i, ol in enumerate((outline.get("outline") or outline).get("levels") or []):
            if isinstance(ol, dict):
                entries[i] = ol
    return [
        {"index": i, "id": entries.get(i, {}).get("id"), "identifier": str(lv.get("identifier")),
         "iid": lv.get("iid"),
         "title": str(entries.get(i, {}).get("title") or lv.get("identifier"))}
        for i, lv in enumerate(levels)
    ]


__all__ = [
    "LINEAR_LAYOUTS",
    "WORLD_LAYOUT",
    "graft_level",
    "is_world",
    "level_identifier",
    "link_world",
    "new_world_doc",
    "world_levels",
]
