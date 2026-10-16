"""Start projects for the demo recorders.

    python demo/make_projects.py empty  OUT.ldtk            # AI side: GLDtk layers, empty level
    python demo/make_projects.py blank  TARGET.ldtk OUT.ldtk # manual side: target's defs, no content
    python demo/make_projects.py plan   TARGET.ldtk OUT.json # what the manual run must rebuild

``blank`` keeps the target's definitions (layers, IntGrid values, tilesets,
entity defs), which a human would have set up once, and resets the level to
LDtk's default 256x256 px with no tiles, entities or GLDtk fields.

``plan`` turns the target into the manual script: greedy rectangles per
IntGrid value, tile rectangles per tile (same tile id, flips), and the
entities with their field values.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def make_empty(out: Path) -> None:
    from layout.ir import IRLevel
    from layout.ldtk_adapter import to_ldtk_dict

    ir = IRLevel.empty("Level_0", 16, 16)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(to_ldtk_dict(ir), indent=1))


def make_blank(target: Path, out: Path) -> None:
    d = json.loads(target.read_text())
    lvl = d["levels"][0]
    d["levels"] = [lvl]
    lvl["pxWid"] = lvl["pxHei"] = 256
    lvl["fieldInstances"] = []
    lvl["__neighbours"] = []
    for li in lvl["layerInstances"]:
        grid = li["__gridSize"]
        li["__cWid"], li["__cHei"] = 256 // grid, 256 // grid
        li["intGridCsv"] = [0] * (li["__cWid"] * li["__cHei"]) if li["__type"] == "IntGrid" else []
        li["gridTiles"], li["autoLayerTiles"], li["entityInstances"] = [], [], []
    d["defs"]["levelFields"] = []
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d, indent=1))


def greedy_rects(cells: dict[tuple[int, int], object]) -> list[dict]:
    """Covers equal-keyed cells with rectangles: take the top-left free cell,
    grow right while the key matches, then down while the whole row matches."""
    left = dict(cells)
    rects = []
    for (y, x) in sorted(left):
        if (y, x) not in left:
            continue
        key = left[(y, x)]
        w = 1
        while left.get((y, x + w)) == key:
            w += 1
        h = 1
        while all(left.get((y + h, x + i)) == key for i in range(w)):
            h += 1
        for j in range(h):
            for i in range(w):
                del left[(y + j, x + i)]
        rects.append({"x": x, "y": y, "w": w, "h": h, "key": key})
    return rects


def make_plan(target: Path, out: Path) -> None:
    d = json.loads(target.read_text())
    lvl = d["levels"][0]
    layers = {li["__identifier"]: li for li in lvl["layerInstances"]}
    col = layers["Collisions"]
    cw, ch = col["__cWid"], col["__cHei"]
    cells = {(i // cw, i % cw): v for i, v in enumerate(col["intGridCsv"]) if v}
    collisions = greedy_rects(cells)

    tiles = []
    for li in lvl["layerInstances"]:
        if li["__type"] != "Tiles" or not li["gridTiles"]:
            continue
        g = li["__gridSize"]
        cells = {(t["px"][1] // g, t["px"][0] // g): (t["t"], t["f"]) for t in li["gridTiles"]}
        rects = greedy_rects(cells)
        tiles.append({"layer": li["__identifier"], "tilesetUid": li["__tilesetDefUid"], "rects": rects})

    entities = []
    for li in lvl["layerInstances"]:
        for e in li["entityInstances"]:
            fields = [{"id": f["__identifier"], "type": f["__type"], "value": f["__value"]}
                      for f in e["fieldInstances"]]
            entities.append({"id": e["__identifier"], "defUid": e["defUid"], "cx": e["__grid"][0],
                             "cy": e["__grid"][1], "w": e["width"], "h": e["height"], "fields": fields})

    plan = {
        "level": {"pxWid": lvl["pxWid"], "pxHei": lvl["pxHei"], "cWid": cw, "cHei": ch},
        "intGridValues": {str(v["value"]): v["identifier"] for v in
                          next(l for l in d["defs"]["layers"] if l["identifier"] == "Collisions")["intGridValues"]},
        "collisions": collisions,
        "tiles": tiles,
        "entities": entities,
        "sections": json.loads(next((f["__value"] for f in lvl["fieldInstances"]
                                     if f["__identifier"] == "gldtk_sections"), "[]") or "[]"),
    }
    out.write_text(json.dumps(plan, indent=1))
    print(f"{cw}x{ch}: {len(collisions)} collision rects, "
          f"{sum(len(t['rects']) for t in tiles)} tile rects, {len(entities)} entities")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "empty":
        make_empty(Path(args[0]))
    elif cmd == "blank":
        make_blank(Path(args[0]), Path(args[1]))
    elif cmd == "plan":
        make_plan(Path(args[0]), Path(args[1]))
    else:
        sys.exit(__doc__)
