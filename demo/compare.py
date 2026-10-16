"""How closely a manually built .ldtk matches the target.

    python demo/compare.py TARGET.ldtk BUILT.ldtk [--region x0,x1]

Prints JSON: matching collision cells, tile cells and entities (identifier,
grid cell and the hand-set fields), within columns x0..x1 when given.
"""
from __future__ import annotations

import json
import sys

SKIP = {"spec_id", "section", "anchor", "gldtk_meta", "sprite_tile", "sprite", "description"}


def load(path):
    lvl = json.load(open(path))["levels"][0]
    return lvl, {li["__identifier"]: li for li in lvl["layerInstances"]}


def main(argv):
    target, built = argv[0], argv[1]
    region = None
    if "--region" in argv:
        region = tuple(int(v) for v in argv[argv.index("--region") + 1].split(","))
    inside = (lambda x: region[0] <= x <= region[1]) if region else (lambda x: True)
    tl, tlayers = load(target)
    bl, blayers = load(built)
    out = {"size_match": (tl["pxWid"], tl["pxHei"]) == (bl["pxWid"], bl["pxHei"])}

    tc, bc = tlayers["Collisions"], blayers["Collisions"]
    w = tc["__cWid"]
    bw = bc["__cWid"]
    total = same = 0
    for i, v in enumerate(tc["intGridCsv"]):
        x, y = i % w, i // w
        if not inside(x):
            continue
        b = bc["intGridCsv"][y * bw + x] if x < bw and y < bc["__cHei"] else None
        total += 1
        same += b == v
    out["collision_cells"] = [same, total]

    total = same = 0
    for name, li in tlayers.items():
        if li["__type"] != "Tiles":
            continue
        g = li["__gridSize"]
        want = {(t["px"][0] // g, t["px"][1] // g): (t["t"], t["f"]) for t in li["gridTiles"]}
        got = {(t["px"][0] // g, t["px"][1] // g): (t["t"], t["f"]) for t in blayers.get(name, {}).get("gridTiles", [])}
        for k in set(want) | set(got):
            if inside(k[0]):
                total += 1
                same += want.get(k) == got.get(k)
    out["tile_cells"] = [same, total]

    def ents(layers):
        res = []
        for li in layers.values():
            for e in li["entityInstances"]:
                fields = {f["__identifier"]: f["__value"] for f in e["fieldInstances"]
                          if f["__identifier"] not in SKIP and not isinstance(f["__value"], (list, dict))}
                res.append((e["__identifier"], tuple(e["__grid"]), fields))
        return res

    want = [e for e in ents(tlayers) if inside(e[1][0])]
    got = ents(blayers)
    matched = 0
    for e in want:
        hit = next((g for g in got if g[0] == e[0] and g[1] == e[1]), None)
        if hit and all(hit[2].get(k) == v for k, v in e[2].items() if v is not None):
            matched += 1
            got.remove(hit)
    out["entities"] = [matched, len(want)]
    out["extra_entities"] = len([g for g in got if inside(g[1][0])])
    print(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv[1:])
