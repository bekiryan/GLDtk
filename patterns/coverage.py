"""Stamping coverage of the pattern index: ``python -m patterns.coverage`` (v4 Part 29g).

Two tables, per structure type × section height (``HEIGHTS``):

* **windows** (static, no embedder): how many accepted windows of the type
  (:func:`patterns.classify.structure_matches`) can be stamped into a section of that
  height, with the real fit rules of ``layout/terrain/stamp.py`` (alone, padded, or stacked
  under another window of the type, :func:`stamp_section`);
* **levels** (``--real``, needs the index's embedder): a 2-section level of the type per
  height and seed, built with the real :class:`pipeline.stamps.StampFinder` and verified;
  the count is how many sections were stamped (``2 × seeds`` at most).

The section is half of a 120-column level, as in the Part 26e/26f reports.
"""

from __future__ import annotations

import argparse
import random
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence

TYPES = ("corridor", "parkour", "labyrinth", "arena", "shaft", "hub", "open")
HEIGHTS = (20, 26, 32, 34, 40)
LEVEL_WIDTH = 120
STACK_PARTNERS = 3  # other windows of the type tried as the top of a stack


def section_ctx(structure: str, height: int, width: int = LEVEL_WIDTH // 2) -> Any:
    """A horizontal terrain section like the first half of a ``LEVEL_WIDTH``-wide level."""
    from layout.terrain.base import Port, TerrainContext

    return TerrainContext(section_id="cov", structure=structure, rect=(0, 0, width - 1, height - 1),
                          level_size=(LEVEL_WIDTH, height), orientation="horizontal",
                          entry=Port("end", 1, height - 3), exit=Port("door", width, height - 3),
                          rng=random.Random(0))


def typed_windows(store: Any, structure: str) -> List[Dict[str, Any]]:
    """Accepted ``kind="pattern"`` windows of *structure* with a grid (as the finder hands them)."""
    from layout.terrain.stamp import pattern_grid
    from patterns.classify import structure_matches
    from patterns.retrieve import accept

    out = []
    for rec in store:
        if rec.get("kind", "pattern") != "pattern" or not accept(rec):
            continue
        if not structure_matches(rec, structure):
            continue
        g = pattern_grid(rec)
        if g is not None:
            out.append({"id": rec["id"], "grid": g})
    return out


def stampable(win: Dict[str, Any], others: Sequence[Dict[str, Any]], structure: str,
              height: int) -> bool:
    """Can *win* be stamped (alone, padded or stacked under one of *others*) at *height*?"""
    from layout.terrain.base import Canvas
    from layout.terrain.stamp import stamp_section

    ctx = section_ctx(structure, height)
    res, _n = stamp_section(ctx, Canvas(ctx), [win, *others], 0)
    return res is not None and res.stats.get("pattern") == win["id"]


def window_coverage(store: Any, types: Iterable[str] = TYPES,
                    heights: Iterable[int] = HEIGHTS) -> Dict[str, Dict[int, int]]:
    out: Dict[str, Dict[int, int]] = {}
    for t in types:
        wins = typed_windows(store, t)
        rng = random.Random(f"coverage:{t}")
        row: Dict[int, int] = {}
        for h in heights:
            n = 0
            for w in wins:
                others = [o for o in rng.sample(wins, min(len(wins), STACK_PARTNERS + 1))
                          if o["id"] != w["id"]][:STACK_PARTNERS]
                n += stampable(w, others, t, h)
            row[h] = n
        out[t] = row
        print(f"  windows {t}: {row}", file=sys.stderr, flush=True)
    return out


def level_coverage(store: Any, embedder: Any, types: Iterable[str] = TYPES,
                   heights: Iterable[int] = HEIGHTS, seeds: Sequence[int] = (0, 1)
                   ) -> Dict[str, Dict[int, str]]:
    """``"stamped/total"`` sections per type × height (``"x"`` marks a level that failed)."""
    from game.profile import GameProfile
    from pipeline.level import _layout_and_verify
    from pipeline.stamps import StampFinder
    from spec.level_spec import LevelSpec

    profile = GameProfile()
    out: Dict[str, Dict[int, str]] = {}
    for t in types:
        row: Dict[int, str] = {}
        for h in heights:
            stamped = total = 0
            bad = False
            for seed in seeds:
                spec = LevelSpec.model_validate(_two_section_spec(t, h))
                finder = StampFinder(f"a {t} level", store=store, embedder=embedder)
                sl, rep, _a = _layout_and_verify(spec, profile, seed, finder)
                bad |= not rep.ok
                total += len(sl.sections)
                stamped += sum(1 for b in sl.sections if b.pattern)
            row[h] = f"{stamped}/{total}" + ("x" if bad else "")
        out[t] = row
        print(f"  levels {t}: {row}", file=sys.stderr, flush=True)
    return out


def _two_section_spec(structure: str, height: int) -> Dict[str, Any]:
    """The spec of ``tests/test_terrain.py::terrain_spec([t, t], size=(120, h))``."""
    w = LEVEL_WIDTH
    secs, nodes = [], []
    for i in range(2):
        secs.append({"id": f"s{i}", "name": f"S{i}", "theme": "dungeon", "difficulty": "medium",
                     "gravity_scale": 1.0, "description": structure, "order": i,
                     "structure": structure})
        nodes.append({"id": f"n{i}", "type": "Start" if i == 0 else "Platform",
                      "x": int((i + 0.5) * w * 16 / 2), "y": 48, "w": 48, "h": 16, "section": f"s{i}"})
    nodes.append({"id": "exit", "type": "Exit", "x": nodes[-1]["x"] + 64, "y": 48, "w": 48, "h": 16,
                  "section": "s1"})
    return {
        "title": "T", "size": {"width_tiles": w, "height_tiles": height},
        "sections": secs, "nodes": nodes,
        "edges": [{"from": a["id"], "to": b["id"], "type": "Walk", "vx": 200, "vy": 0}
                  for a, b in zip(nodes, nodes[1:])],
        "entities": [
            {"id": "player", "kind": "Start", "anchor": "n0", "section": "s0", "description": "",
             "sprite": ""},
            {"id": "door", "kind": "Exit", "anchor": "exit", "section": "s1", "description": "",
             "sprite": ""},
        ],
        "requirements": [{"id": "R1", "text": "t", "category": "other"}],
    }


def table(rows: Dict[str, Dict[int, Any]], heights: Iterable[int] = HEIGHTS) -> str:
    hs = list(heights)
    lines = ["| type | " + " | ".join(str(h) for h in hs) + " |",
             "|---|" + "---|" * len(hs)]
    lines += [f"| {t} | " + " | ".join(str(r.get(h, "")) for h in hs) + " |" for t, r in rows.items()]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m patterns.coverage", description=__doc__.split("\n")[0])
    ap.add_argument("--real", action="store_true", help="also build 2-section levels with the finder")
    ap.add_argument("--no-windows", action="store_true", help="skip the (slow) window table")
    ap.add_argument("--embedder", default=None, help="query embedder (default: the index's)")
    ap.add_argument("--types", nargs="*", default=list(TYPES))
    ap.add_argument("--heights", nargs="*", type=int, default=list(HEIGHTS))
    args = ap.parse_args(argv)

    from patterns.store import PatternStore

    store = PatternStore()
    if not args.no_windows:
        print("stampable windows per type × section height")
        print(table(window_coverage(store, args.types, args.heights), args.heights))
    if args.real:
        from patterns.embed import make_embedder

        name = args.embedder or (store.embedder or {}).get("name") or "hash"
        print("\nstamped sections, real index (2 seeds × 2 sections; x: a level failed)")
        print(table(level_coverage(store, make_embedder(name), args.types, args.heights),
                    args.heights))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
