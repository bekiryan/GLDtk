"""Hand-edit detection: the file GLDtk last wrote versus the file as the user saved it.

A copy of every GLDtk output is kept as ``last_output.ldtk.bak`` in the file's history folder
(the ``*.ldtk`` glob of ``edit.history`` does not see it).  :func:`scan` diffs it against the
file lazily, when the next GLDtk request for that file arrives or on ``POST /memory/scan``.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from edit import history
from memory import store

LAST_OUTPUT = "last_output.ldtk.bak"
MAX_LIST = 40
IGNORED_FIELDS = {"iid", "description", "sprite", "sprite_tile"}
GAP_ROW_REACH = 4  # a platform's "next platform" lies within this many rows


def last_output_path(ldtk_path: Any) -> Path:
    return history.history_dir(ldtk_path) / LAST_OUTPUT


def remember_output(ldtk_path: Any) -> None:
    """Keep a copy of the file GLDtk has just written."""
    if not store.enabled():
        return
    src = Path(ldtk_path)
    if src.is_file():
        dest = last_output_path(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)


def _platforms(spans) -> List[Tuple[int, int, int]]:
    return sorted(spans.rects.values())


def _gaps(plats: List[Tuple[int, int, int]]) -> List[int]:
    """Empty columns between each platform and the next one to its right (rows within reach)."""
    gaps = []
    for row, c0, c1 in plats:
        nxt = [p[1] for p in plats if p[1] > c1 and abs(p[0] - row) <= GAP_ROW_REACH]
        if nxt:
            gaps.append(min(nxt) - c1 - 1)
    return gaps


def _pits(grid: List[List[Any]]) -> List[Tuple[int, int]]:
    """(first column, width) of runs of empty bottom-row cells that solid cells bound."""
    if not grid:
        return []
    bottom = [1 if v else 0 for v in grid[-1]]
    out, c, n = [], 0, len(bottom)
    while c < n:
        if bottom[c] == 0:
            e = c
            while e + 1 < n and bottom[e + 1] == 0:
                e += 1
            if c > 0 and e < n - 1:
                out.append((c, e - c + 1))
            c = e + 1
        else:
            c += 1
    return out


def _multiset_diff(after: List[Any], before: List[Any]) -> List[Any]:
    return list((Counter(after) - Counter(before)).elements())


def _enemy_spacing(ents) -> Optional[float]:
    pts = [(e.col, e.row) for e in ents if e.entity_type == "Enemy"]
    if len(pts) < 2:
        return None
    near = [min(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for b in pts if b is not a) for a in pts]
    return round(statistics.median(near), 1)


def _entity_context(ctx, ent) -> str:
    try:
        return ctx.band_at(ent.col, ent.row).id
    except Exception:
        return ""


def _fields(ent) -> Dict[str, Any]:
    return {k: v for k, v in ent.metadata.items()
            if k not in IGNORED_FIELDS and isinstance(v, (int, float, str, bool))}


def diff_levels(old, new) -> Dict[str, Any]:
    """Compact diff of two EditContexts of the same level (*new* extracts both grids)."""
    oe = {e.metadata.get("iid"): e for e in old.entities if e.metadata.get("iid")}
    ne = {e.metadata.get("iid"): e for e in new.entities if e.metadata.get("iid")}
    moved, added, removed, changed = [], [], [], []
    for iid, e in ne.items():
        o = oe.get(iid)
        if o is None:
            added.append({"kind": e.entity_type, "context": _entity_context(new, e)})
            continue
        d = round(((e.col - o.col) ** 2 + (e.row - o.row) ** 2) ** 0.5, 1)
        if d > 0:
            moved.append({"kind": e.entity_type, "dist": d})
        fo, fn = _fields(o), _fields(e)
        for k in sorted(set(fo) | set(fn)):
            if fo.get(k) != fn.get(k):
                changed.append({"kind": e.entity_type, "field": k, "from": fo.get(k), "to": fn.get(k)})
    for iid, o in oe.items():
        if iid not in ne:
            removed.append({"kind": o.entity_type, "context": _entity_context(old, o)})

    og, ng = old.grid, new.grid
    painted = erased = 0
    for r in range(min(len(og), len(ng))):
        for c in range(min(len(og[r]), len(ng[r]))):
            a, b = bool(og[r][c]), bool(ng[r][c])
            painted += (not a) and b
            erased += a and not b
    tiles: Dict[str, Any] = {"painted": painted, "erased": erased}
    if painted or erased:
        po, pn = _platforms(old.extract(og)), _platforms(new.extract(ng))
        go, gn = _gaps(po), _gaps(pn)
        wo = [p[2] - p[1] + 1 for p in po]
        wn = [p[2] - p[1] + 1 for p in pn]
        tiles.update(
            platforms_before=len(po), platforms_after=len(pn),
            platforms_added=sorted(_multiset_diff(wn, wo))[:MAX_LIST],
            platforms_removed=sorted(_multiset_diff(wo, wn))[:MAX_LIST],
            gaps_before=sorted(go)[:MAX_LIST], gaps_after=sorted(gn)[:MAX_LIST],
            gaps_created=sorted(_multiset_diff(gn, go))[:MAX_LIST],
            gaps_closed=sorted(_multiset_diff(go, gn))[:MAX_LIST],
            pits_created=sorted(w for _, w in _multiset_diff(_pits(ng), _pits(og)))[:MAX_LIST],
            pits_filled=sorted(w for _, w in _multiset_diff(_pits(og), _pits(ng)))[:MAX_LIST],
        )
    out: Dict[str, Any] = {"tiles": tiles}
    for key, val in (("moved", moved), ("added", added), ("removed", removed), ("changed", changed)):
        out[key] = val[:MAX_LIST]
    sp = _enemy_spacing(new.entities)
    if sp is not None:
        out["enemy_spacing"] = sp
    return out


def _is_empty(d: Dict[str, Any]) -> bool:
    t = d["tiles"]
    return not (d["moved"] or d["added"] or d["removed"] or d["changed"] or t["painted"] or t["erased"])


def scan(path: Any) -> Dict[str, Any]:
    """Diff the file against GLDtk's last output; record a ``hand_edit`` event per changed level.

    Returns ``{"edited": bool, "levels": n}``.  Always refreshes the stored copy, so the same
    edit is never recorded twice.  Never raises.
    """
    res: Dict[str, Any] = {"edited": False, "levels": 0}
    if not store.enabled():
        return res
    p = Path(path)
    last = last_output_path(p)
    try:
        if not p.is_file():
            return res
        if not last.is_file():
            remember_output(p)  # first sight: this is the baseline
            return res
        if last.read_bytes() == p.read_bytes():
            return res
        from edit.context import load_context
        from layout.ldtk_reader import read_level

        old_doc = json.loads(last.read_text(encoding="utf-8"))
        new_doc = json.loads(p.read_text(encoding="utf-8"))
        old_iids = {lv.get("iid"): i for i, lv in enumerate(old_doc.get("levels", []))}
        for i, lv in enumerate(new_doc.get("levels", [])):
            iid = lv.get("iid")
            if iid not in old_iids:
                continue
            new = load_context(p, i)
            old = dataclasses.replace(new, level=read_level(old_doc, old_iids[iid]), doc=old_doc)
            d = diff_levels(old, new)
            if _is_empty(d):
                continue
            res["levels"] += 1
            store.record("hand_edit", file=p.name, level=lv.get("identifier"), **d)
        res["edited"] = res["levels"] > 0
    except Exception as exc:  # a broken file must never break a request
        res["error"] = str(exc)
    finally:
        try:
            remember_output(p)
        except OSError:
            pass
    return res
