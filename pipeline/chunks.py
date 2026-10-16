"""Endless chunks — fixed-size, seeded levels appended to a world file (v4 Part 25a).

A chunk is a ``CHUNK_W × CHUNK_H`` level at chunk coordinate *k*, horizontal
(entry on the left border, exit on the right) or vertical (entry on top, exit
at the bottom).  Chunk *k* is a pure function of ``(seed, k, orientation,
theme, profile)``: building it twice, alone or in a run, gives the same level,
byte for byte (its LDtk iids are seeded too).

**Seams.**  The opening on the border between chunks *k* and *k+1* is
:func:`seam` ``(seed, k + 1)``: a floor row (horizontal; the opening is the
rows above it) or a column band (vertical), from a hash of the seed and the
border index.  ``seam_of(seed, k, "exit") == seam_of(seed, k + 1, "entry")``,
so neighbours agree with no shared state.

**Contents.**  A generated :class:`~spec.LevelSpec` (no LLM): a Start platform
on the entry seam, a chain of short hops to an Exit platform on the exit seam
(the carved path), coins on every other hop and walkers whose number grows
with ``|k|`` (a seeded rule table), one section themed by *theme* (the theme of
a chunk group: every chunk of one request).  It goes through the normal
``build_level`` (layout → verify → repair → comfort hops → densify) with the
noise fill at high density, sampled at the chunk's world offset so the rock
field runs on across borders; the path, its arcs, the headroom and the seam
openings are protected.  ``build_level`` verifies entry → exit; the Part 22
soft-lock checks plug in through :data:`CHUNK_CHECKS`.

:func:`append_chunks` grafts the chunks into a world file (Part 17 export):
Exit ``leads_to`` the next chunk's Start.
"""

from __future__ import annotations

import json
import random
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from game.profile import GameProfile, load_profile, profile_path_for
from layout.fill import FillParams
from layout.noise import hash_ints
from spec.level_spec import LevelSpec

from layout.densify import densify

from .level import BuiltLevel, _with_added_nodes, build_level

CHUNK_W, CHUNK_H = 64, 32  # horizontal chunk; a vertical chunk is CHUNK_H × CHUNK_W
CHUNK_FILL = 0.8  # noise fill density
SEAM_W = 4  # start / exit platform width (tiles)
PREFIX = "Chunk_"

# Extra checks on a built chunk (Part 22 soft-locks plug in here): each returns
# problems as strings; any problem fails the chunk.
CHUNK_CHECKS: List[Callable[[BuiltLevel, GameProfile], List[str]]] = []


@dataclass(frozen=True)
class Seam:
    """An opening on a chunk border: ``row`` = its floor row (horizontal; the opening
    is the rows just above) or ``col`` = its first column (vertical, ``SEAM_W`` wide)."""

    boundary: int
    orientation: str
    row: Optional[int] = None
    col: Optional[int] = None


def seam(seed: int, boundary: int, orientation: str = "horizontal") -> Seam:
    """The opening on border *boundary* (between chunks ``boundary - 1`` and ``boundary``)."""
    h = hash_ints(seed, boundary, 0x5EA3, 1 if orientation == "vertical" else 0)
    if orientation == "vertical":
        lo, hi = 4, CHUNK_H - SEAM_W - 4
        return Seam(boundary, orientation, col=lo + h % (hi - lo + 1))
    lo, hi = CHUNK_H // 3 + 2, CHUNK_H - 3
    return Seam(boundary, orientation, row=lo + h % (hi - lo + 1))


def seam_of(seed: int, k: int, side: str, orientation: str = "horizontal") -> Seam:
    """Chunk *k*'s ``"entry"`` or ``"exit"`` seam."""
    return seam(seed, k if side == "entry" else k + 1, orientation)


def chunk_size(orientation: str) -> Tuple[int, int]:
    return (CHUNK_W, CHUNK_H) if orientation == "horizontal" else (CHUNK_H, CHUNK_W)


def chunk_origin(k: int, orientation: str = "horizontal") -> Tuple[int, int]:
    """World cell offset of chunk *k* (shared noise fields line up at the seams)."""
    width, height = chunk_size(orientation)
    return (k * width, 0) if orientation == "horizontal" else (0, k * height)


def fill_style(theme: str) -> str:
    t = theme.lower()
    if any(w in t for w in ("sky", "cloud", "air", "float", "space")):
        return "sky"
    if any(w in t for w in ("ruin", "castle", "temple", "brick", "tomb")):
        return "ruins"
    return "cave"


# ─────────────────────────────────────────────────────────────────────────────
# The chunk spec
# ─────────────────────────────────────────────────────────────────────────────


def _node(nid: str, kind: str, col: int, row: int, w: int, height: int) -> Dict[str, Any]:
    return {"id": nid, "type": kind, "x": col * 16, "y": (height - 1 - row) * 16,
            "w": w * 16, "h": 16, "section": "chunk"}


def _path_horizontal(rng: random.Random, r_in: int, r_out: int) -> List[Tuple[int, int, int]]:
    """``(col, row, width)`` of the start, the hops and the exit, left to right."""
    width, height = CHUNK_W, CHUNK_H
    out = [(0, r_in, SEAM_W)]
    col, row = SEAM_W, r_in
    end = width - SEAM_W
    plats: List[Tuple[int, int, int]] = []
    while True:
        gap = rng.randint(2, 3)
        w = rng.randint(3, 5)
        if col + gap + w > end - 2:
            break
        plats.append((col + gap, 0, w))
        col += gap + w
    n = len(plats)
    for i, (c, _, w) in enumerate(plats):
        left = n - i  # hops still to make, the exit included
        target = r_in + (r_out - r_in) * (i + 1) / (n + 1) + rng.randint(-3, 3)
        lo = max(row - 3, r_out - 3 * left, height // 4)  # climb ≤ 3 per hop
        hi = min(row + 4, r_out + 4 * left, height - 3)  # drop ≤ 4 per hop
        row = int(min(max(round(target), lo), hi)) if lo <= hi else row
        plats[i] = (c, row, w)
    return out + plats + [(end, r_out, SEAM_W)]


def _path_vertical(rng: random.Random, c_in: int, c_out: int) -> List[Tuple[int, int, int]]:
    """``(col, row, width)`` of the start, the hops and the exit, top to bottom."""
    width, height = CHUNK_H, CHUNK_W
    out = [(c_in, 3, SEAM_W)]
    rows = list(range(7, height - 4, 4))
    col = c_in
    for i, row in enumerate(rows):
        left = len(rows) - i
        target = c_in + (c_out - c_in) * (i + 1) / (len(rows) + 1)
        w = rng.randint(3, 5)
        # step sideways by 2-5 columns (never straight below the last platform)
        step = rng.choice((-1, 1)) * rng.randint(2, 5)
        nxt = int(round(0.5 * (col + step) + 0.5 * target))
        if abs(nxt - col) < 2:
            nxt = col + (2 if nxt >= col else -2)
        col = min(max(nxt, 1), width - w - 1)
        out.append((col, row, w))
        del left
    return out + [(c_out, height - 1, SEAM_W)]


def chunk_spec(seed: int, k: int, orientation: str = "horizontal", theme: str = "cave"
               ) -> LevelSpec:
    """The LevelSpec of chunk *k* (see the module doc)."""
    rng = random.Random(hash_ints(seed, k, 0xC4C))
    width, height = chunk_size(orientation)
    entry, exit_ = seam_of(seed, k, "entry", orientation), seam_of(seed, k, "exit", orientation)
    if orientation == "vertical":
        path = _path_vertical(rng, int(entry.col), int(exit_.col))
    else:
        path = _path_horizontal(rng, int(entry.row), int(exit_.row))
    ids = ["start"] + [f"p{i}" for i in range(1, len(path) - 1)] + ["exit"]
    kinds = ["Start"] + ["Platform"] * (len(path) - 2) + ["Exit"]
    nodes = [_node(i, kind, c, r, w, height) for i, kind, (c, r, w) in zip(ids, kinds, path)]
    edges = []
    for a, b in zip(nodes, nodes[1:]):
        t = "Jump" if b["y"] > a["y"] else "Fall" if b["y"] < a["y"] else "Jump"
        edges.append({"from": a["id"], "to": b["id"], "type": t, "vx": 0, "vy": 0, "ability": None})

    ents: List[Dict[str, Any]] = [
        {"id": "player", "kind": "Start", "anchor": "start", "section": "chunk",
         "facing": "right" if orientation == "horizontal" else "left"},
        {"id": "door", "kind": "Exit", "anchor": "exit", "section": "chunk", "requires_key": False},
    ]
    hops = ids[1:-1]
    for i, nid in enumerate(hops):
        if i % 2 == 0:
            ents.append({"id": f"coin_{i}", "kind": "Pickup", "anchor": nid, "section": "chunk",
                         "pickup_kind": "coin", "value": 1})
    n_enemies = min(len(hops) // 3, 1 + abs(k) // 3, 4)  # more the farther out
    for j, nid in enumerate(sorted(hops[1:], key=lambda n: hash_ints(seed, k, int(n[1:])))[:n_enemies]):
        ents.append({"id": f"enemy_{j}", "kind": "Enemy", "anchor": nid, "section": "chunk",
                     "archetype": "walker", "attack": "melee", "health": 1 + abs(k) // 5,
                     "damage": 1, "patrol_range": 2})
    difficulty = "easy" if abs(k) < 4 else "medium" if abs(k) < 10 else "hard"
    return LevelSpec.model_validate({
        "title": f"Chunk {k}",
        "size": {"width_tiles": width, "height_tiles": height},
        "orientation": orientation,
        "sections": [{"id": "chunk", "name": f"Chunk {k}", "theme": theme,
                      "difficulty": difficulty, "gravity_scale": 1.0,
                      "description": f"endless chunk {k}", "order": 0}],
        "nodes": nodes,
        "edges": edges,
        "entities": ents,
        "requirements": [{"id": "R1", "text": f"Endless chunk {k}: entry seam to exit seam",
                          "category": "layout"}],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Building a chunk
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _Stats:
    attempts: int = 0
    patches_applied: List[str] = field(default_factory=list)


class _FixedController:
    """A controller that returns a fixed spec (no LLM)."""

    def __init__(self, spec: LevelSpec) -> None:
        self.spec = spec
        self.stats = _Stats()

    def generate_spec(self, *_: Any, **__: Any) -> LevelSpec:
        return self.spec

    def repair_spec(self, _d: str, spec: LevelSpec, *_: Any, **__: Any) -> LevelSpec:
        return spec


@dataclass
class Chunk:
    k: int
    orientation: str
    entry: Seam
    exit: Seam
    built: BuiltLevel
    problems: List[str] = field(default_factory=list)  # CHUNK_CHECKS findings

    @property
    def ok(self) -> bool:
        return self.built.report.ok and not self.problems


def seam_cells(s: Seam, side: str, player_h: int) -> List[Tuple[int, int]]:
    """The border cells of an opening (kept clear by the fill)."""
    width, height = chunk_size(s.orientation)
    if s.orientation == "vertical":
        row = 0 if side == "entry" else height - 1
        rows = range(0, 3) if side == "entry" else range(height - player_h - 2, height - 1)
        return [(c, r) for c in range(int(s.col), int(s.col) + SEAM_W) for r in rows] + \
               [(c, row) for c in range(int(s.col), int(s.col) + SEAM_W) if side == "entry"]
    col = 0 if side == "entry" else width - 1
    return [(col, r) for r in range(int(s.row) - player_h - 2, int(s.row))]


def build_chunk(seed: int, k: int, *, orientation: str = "horizontal", theme: str = "cave",
                profile: Optional[GameProfile] = None) -> Chunk:
    """Build and verify chunk *k* (deterministic; raises ``LevelBuildFailed``)."""
    profile = profile or GameProfile()
    spec = chunk_spec(seed, k, orientation, theme)
    entry, exit_ = seam_of(seed, k, "entry", orientation), seam_of(seed, k, "exit", orientation)
    width, height = chunk_size(orientation)
    ph = profile.player_tiles()[1]
    origin = (k * width, 0) if orientation == "horizontal" else (0, k * height)
    params = FillParams(density=CHUNK_FILL, style=fill_style(theme), origin=origin,
                        min_distance_from_path=1,
                        keep_clear=tuple(seam_cells(entry, "entry", ph)
                                         + seam_cells(exit_, "exit", ph)))
    s = hash_ints(seed, k) & 0xFFFF
    # terrain first, then densify around it (the rock is kept as rock_N layout nodes)
    built = build_level(spec.title, _FixedController(spec), profile, seed=s, densify=False,
                        fill=params)
    dense = densify(built.spec_layout, built.report, profile, spec, seed=s)
    built.report = dense.report or built.report
    built.stats["densify_added"] = len(dense.added)
    built.warnings += dense.notes
    built.spec = _with_added_nodes(spec, built.spec_layout)
    built.spec_layout.ir.identifier = f"{PREFIX}{k}"
    chunk = Chunk(k, orientation, entry, exit_, built)
    for check in CHUNK_CHECKS:
        chunk.problems += check(built, profile)
    return chunk


# ─────────────────────────────────────────────────────────────────────────────
# Export: a chunk as an LDtk level, appended to a world file
# ─────────────────────────────────────────────────────────────────────────────


@contextmanager
def seeded_iids(seed: int) -> Iterator[None]:
    """LDtk iids drawn from a seeded RNG (this thread only) while the block runs."""
    from layout import ldtk_adapter

    state = ldtk_adapter._IID_STATE
    old = getattr(state, "rng", None)
    state.rng = random.Random(seed)
    try:
        yield
    finally:
        state.rng = old


def dress_chunk(chunk: Chunk, theme: str, seed: int, background: bool = False,
                orientation: str = "horizontal") -> Any:
    """The aesthetic layer (tiles, entity sprites) of a built chunk."""
    from aesthetic import build_aesthetic_layer
    from aesthetic.themes import detect_theme

    built = chunk.built
    tile_theme = detect_theme(theme)
    for band in built.spec_layout.ir.metadata.get("sections", []):
        band["mapped_theme"] = tile_theme.identifier
    return build_aesthetic_layer(
        description=theme, graph=built.spec.to_graph(), node_layouts=built.spec_layout.node_layouts,
        ir_level=built.spec_layout.ir, theme_override=tile_theme.identifier, density=0.5,
        seed=seed, place_random_entities=False, background=background,
        origin=chunk_origin(chunk.k, orientation),
    )


def export_chunk(chunk: Chunk, theme: str, seed: int, out: Path, profile_path: Path,
                 profile: GameProfile, background: bool = False,
                 orientation: str = "horizontal") -> Tuple[Dict[str, Any], Any, List[str]]:
    """``(fresh LDtk doc of the chunk alone, aesthetic result, export warnings)``."""
    import os

    from layout import LDtkConfig, LevelMeta, to_ldtk_dict_with_warnings

    result = dress_chunk(chunk, theme, seed, background, orientation)
    tw, th = profile.player_tiles()
    meta = LevelMeta(
        prompt=f"endless chunk {chunk.k} ({theme})",
        requirements=list(chunk.built.spec.requirements),
        game_profile_relpath=os.path.relpath(Path(profile_path).resolve(), out.parent.resolve()),
        spec_json=chunk.built.spec,
    )
    with seeded_iids(hash_ints(seed, chunk.k, 0x11D)):
        fresh, warnings = to_ldtk_dict_with_warnings(
            chunk.built.spec_layout.ir, aesthetic=result.aesthetic,
            cfg=LDtkConfig(player_px=(tw * profile.tile_size, th * profile.tile_size)),
            level_meta=meta)
    return fresh, result, warnings


def next_chunk_k(doc: Dict[str, Any]) -> int:
    """One past the largest ``Chunk_<k>`` level of *doc* (0 when it has none)."""
    ks = [int(m.group(1)) for lv in doc.get("levels") or []
          if (m := re.fullmatch(rf"{PREFIX}(-?\d+)", str(lv.get("identifier"))))]
    return max(ks) + 1 if ks else 0


@dataclass
class ChunkRun:
    path: Path
    chunks: List[Chunk]
    iids: List[str]  # level iid of each chunk in the file
    identifiers: List[str]
    warnings: List[str] = field(default_factory=list)


def append_chunks(path: str | Path, count: int, seed: int, start_k: Optional[int] = None,
                  theme: Optional[str] = None, orientation: str = "horizontal",
                  background: bool = False) -> ChunkRun:
    """Build chunks ``start_k .. start_k + count - 1`` and append them to the world at
    *path* (created when missing; a one-level file becomes a world), linked Exit →
    next Start.  *start_k* None continues after the file's last chunk.  Raises
    ``LevelBuildFailed`` (nothing is written) when a chunk fails."""
    from aesthetic import copy_aesthetic_assets
    from edit import history as edit_history
    from layout.world import graft_level, is_world, link_world, new_world_doc

    out = Path(path)
    theme = theme or "cave"
    profile_path = profile_path_for(out)
    profile = load_profile(profile_path)
    doc: Optional[Dict[str, Any]] = None
    if out.is_file():
        doc = json.loads(out.read_text(encoding="utf-8"))
        if not is_world(doc):
            world = new_world_doc(doc)
            graft_level(world, doc)
            doc = world
    k0 = start_k if start_k is not None else (next_chunk_k(doc) if doc else 0)

    from api import jobs

    chunks = []
    for i, k in enumerate(range(k0, k0 + count), 1):
        jobs.report("chunk", f"Chunk {i}/{count}: building chunk {k}")
        chunks.append(build_chunk(seed, k, orientation=orientation, theme=theme, profile=profile))
    jobs.checkpoint()  # a cancel here has written nothing
    from .level import LevelBuildFailed
    for c in chunks:
        if c.problems:
            raise LevelBuildFailed(f"Chunk {c.k} failed its checks: {c.problems}")

    run = ChunkRun(out, chunks, [], [])
    out.parent.mkdir(parents=True, exist_ok=True)
    edit_history.snapshot(out, edit_history.make_label("chunks", f"{count} chunk(s) from {k0}"))
    for i, c in enumerate(chunks, 1):
        jobs.report("chunk", f"Chunk {i}/{count}: writing chunk {c.k}")
        fresh, result, warnings = export_chunk(c, theme, seed, out, profile_path, profile,
                                              background, orientation)
        copy_aesthetic_assets(result, out.parent)
        if doc is None:
            doc = new_world_doc(fresh)
        lvl = graft_level(doc, fresh)
        run.iids.append(lvl["iid"])
        run.identifiers.append(lvl["identifier"])
        run.warnings += [f"{lvl['identifier']}: {w}" for w in c.built.warnings + warnings]
    assert doc is not None
    link_world(doc)
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return run


def chunks_response(req: Any) -> Any:
    """``POST /chunks``: a :class:`~api.models.ChunksRequest` → ``ChunksResponse``."""
    from api.models import ChunkInfo, ChunksResponse

    try:
        run = append_chunks(req.path, req.count, req.seed, req.start_k, req.theme,
                            req.orientation, req.background)
    except Exception as exc:  # LevelBuildFailed, unreadable file, ...
        return ChunksResponse(success=False, error=f"Chunks: {exc}")
    return ChunksResponse(
        success=True,
        output_path=str(run.path.resolve()),
        warnings=run.warnings,
        chunks=[ChunkInfo(
            k=c.k, identifier=ident, iid=iid, verified=c.ok,
            fill_added=c.built.stats.get("fill_added", 0),
            densify_added=c.built.stats.get("densify_added", 0),
            entry={"row": c.entry.row, "col": c.entry.col},
            exit={"row": c.exit.row, "col": c.exit.col},
        ) for c, ident, iid in zip(run.chunks, run.identifiers, run.iids)],
    )


__all__ = [
    "CHUNK_CHECKS",
    "chunks_response",
    "CHUNK_H",
    "CHUNK_W",
    "Chunk",
    "ChunkRun",
    "Seam",
    "append_chunks",
    "build_chunk",
    "chunk_spec",
    "export_chunk",
    "next_chunk_k",
    "seam",
    "seam_of",
]
