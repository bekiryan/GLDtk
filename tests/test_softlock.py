"""Soft-lock detection (v4 Part 22): ``layout/softlock.py`` and its hooks.

Offline: hand-made grids (default Game Profile: a jump rises ~11 tiles, so a
pit deeper than that cannot be climbed), a small spec laid out by
``layout_spec``, and a region edit answered by a FakeProvider.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional, Tuple

from edit import history
from edit.context import Rect, Spans
from edit.region import edit_region
from game import GameProfile
from layout import IREntity, IRLevel, TileValue, verify_grid
from layout.ldtk_adapter import to_ldtk_dict
from layout.repair import repair_grid
from layout.route import level_route
from layout.softlock import find_softlocks, items_from_entities
from layout.spec_entities import place_spec_entities
from layout.spec_layout import layout_spec
from llm import FakeProvider
from spec import LevelSpec
from symbolic.extractor import extract_graph

W, H = 40, 30


def blank() -> List[List[int]]:
    return [[TileValue.EMPTY] * W for _ in range(H)]


def solid(grid, row: int, c0: int, c1: int) -> None:
    for c in range(c0, c1 + 1):
        grid[row][c] = TileValue.SOLID


def check(grid, entities: List[IREntity]):
    profile = GameProfile()
    graph = extract_graph(grid, profile=profile, collision=True)
    spans = Spans(graph)

    def span_of(e: IREntity) -> Optional[str]:
        return spans.under(e, depth=1 if e.entity_type in ("Start", "Exit") else 4)

    start = next(e for e in entities if e.entity_type == "Start")
    exits = {span_of(e) for e in entities if e.entity_type == "Exit"} - {None}
    rep = find_softlocks(graph, span_of(start), exits, items_from_entities(entities, span_of))
    return rep, spans


def ent(kind: str, col: int, row: int, **meta) -> IREntity:
    return IREntity(kind, col, row, metadata=dict(meta))


# ─────────────────────────────────────────────────────────────────────────────
# Movement graph (SCC) traps
# ─────────────────────────────────────────────────────────────────────────────


def test_level_without_traps_reports_none():
    grid = blank()
    solid(grid, 20, 0, W - 1)
    rep, _ = check(grid, [ent("Start", 1, 19), ent("Exit", 38, 19)])
    assert rep.ok and rep.traps == [] and not rep.unbeatable


def test_one_way_drop_into_a_sealed_room_is_detected():
    grid = blank()
    solid(grid, 10, 0, 17)  # the upper floor, with a hole at columns 18..20
    solid(grid, 10, 21, W - 1)
    solid(grid, 26, 10, 30)  # the room's floor, 16 rows down: no way back up
    for r in range(11, 26):
        grid[r][10] = grid[r][30] = TileValue.SOLID  # its walls
    rep, spans = check(grid, [ent("Start", 1, 9), ent("Exit", 38, 9)])
    assert not rep.ok and not rep.unbeatable
    assert len(rep.traps) == 1
    trap = rep.traps[0]
    room = spans.at(20, 26)
    assert trap.spans == [room] and trap.kind == "pit"
    assert trap.entry is not None and trap.entry[1] == room
    assert "traps the player (no way back up)" in trap.message
    assert "row 26" in trap.message


def test_route_reports_softlock_spans():
    grid = blank()
    solid(grid, 10, 0, 17)
    solid(grid, 10, 21, W - 1)
    solid(grid, 26, 10, 30)
    route = level_route(grid, [ent("Start", 1, 9), ent("Exit", 38, 9)], GameProfile())
    assert route.ok
    assert route.softlock_spans == [(10, 26, 21)]
    assert route.as_dict()["softlock_spans"] == [{"col": 10, "row": 26, "w": 21}]


# ─────────────────────────────────────────────────────────────────────────────
# Item-state traps
# ─────────────────────────────────────────────────────────────────────────────


def _drop_past_the_key(key_at: Tuple[int, int]):
    grid = blank()
    solid(grid, 10, 0, 9)  # A: the Start ledge
    solid(grid, 5, 0, 3)  # K: a ledge above it (the key's, in the first case)
    solid(grid, 26, 0, W - 1)  # B: the floor 16 rows down, with the locked Exit
    ents = [ent("Start", 1, 9), ent("Exit", 38, 25, requires_key=True),
            ent("Key", key_at[0], key_at[1], spec_id="k1")]
    return check(grid, ents)


def test_dropping_past_the_key_before_a_locked_exit_is_detected():
    rep, spans = _drop_past_the_key((1, 4))  # on K: the player may drop to B without it
    assert [t.kind for t in rep.traps] == ["item"]
    trap = rep.traps[0]
    assert spans.at(20, 26) in trap.spans
    assert trap.missing == ["key:k1"]
    assert "without Key 'k1' traps the player" in trap.message


def test_key_moved_before_the_drop_passes():
    rep, _ = _drop_past_the_key((6, 9))  # on A: picked up before any drop
    assert rep.ok and not rep.unbeatable


def test_key_behind_its_own_door_is_detected():
    grid = blank()
    solid(grid, 25, 0, 30)  # A: the floor with the Start
    solid(grid, 25, 33, W - 1)  # E: the (unlocked) Exit's ledge
    solid(grid, 19, 20, 22)  # D: the door ledge
    solid(grid, 13, 24, 27)  # C: the key ledge, only reachable from D
    ents = [ent("Start", 1, 24), ent("Exit", 38, 24),
            ent("Interactive", 21, 18, spec_id="gate", interactive_kind="door", linked_to="k1"),
            ent("Key", 25, 12, spec_id="k1", opens="gate")]
    rep, spans = check(grid, ents)
    kinds = [t.kind for t in rep.traps]
    assert "key_behind_door" in kinds
    trap = rep.traps[kinds.index("key_behind_door")]
    assert trap.spans == [spans.at(25, 13)]
    assert "behind its own door" in trap.message
    # Without the door the same key is fine.
    rep2, _ = check(grid, [e for e in ents if e.entity_type != "Interactive"])
    assert rep2.ok


def test_too_many_items_is_reported_not_checked():
    grid = blank()
    solid(grid, 20, 0, W - 1)
    ents = [ent("Start", 1, 19), ent("Exit", 38, 19, requires_key=True)]
    ents += [ent("Key", 3 + 4 * i, 19, spec_id=f"k{i}") for i in range(8)]
    rep, _ = check(grid, ents)
    assert rep.too_many_items and rep.ok


# ─────────────────────────────────────────────────────────────────────────────
# Generation: verify + repair
# ─────────────────────────────────────────────────────────────────────────────


S = "s1"
PIT_SPEC = {
    "title": "Pit", "size": {"width_tiles": 40, "height_tiles": 40},
    "sections": [{"id": S, "name": "F", "theme": "forest", "difficulty": "easy",
                  "gravity_scale": 1.0, "description": "", "order": 0}],
    "nodes": [
        {"id": "start", "type": "Start", "x": 32, "y": 400, "w": 96, "h": 16, "section": S},
        {"id": "pit", "type": "Platform", "x": 192, "y": 32, "w": 64, "h": 16, "section": S},
        {"id": "exit", "type": "Exit", "x": 192, "y": 400, "w": 96, "h": 16, "section": S},
    ],
    "edges": [{"from": "start", "to": "exit", "type": "Jump", "vx": 200, "vy": 300},
              {"from": "start", "to": "pit", "type": "Fall", "vx": 100, "vy": 0}],
    "entities": [
        {"id": "player", "kind": "Start", "anchor": "start", "section": S, "description": "",
         "sprite": ""},
        {"id": "door", "kind": "Exit", "anchor": "exit", "section": S, "description": "",
         "sprite": ""},
    ],
    "requirements": [],
}


def test_pit_trap_is_detected_then_repaired():
    spec = LevelSpec.model_validate(PIT_SPEC)
    profile = GameProfile()
    lay = layout_spec(spec, profile, seed=0)
    place_spec_entities(spec, lay)
    rep = verify_grid(lay, profile, spec)
    assert [e.code for e in rep.errors] == ["SOFTLOCK"]
    err = rep.errors[0]
    assert "pit" in err.node_ids and err.cells
    assert err.message.startswith("Falling into the pit at column")
    lay, applied = repair_grid(lay, rep, profile, spec)
    assert any("stepping stone" in a and "soft-lock" in a for a in applied)
    final = verify_grid(lay, profile, spec)
    assert final.ok, final.error_messages()
    assert final.softlock is not None and final.softlock.ok


KEY_SPEC = {
    **PIT_SPEC,
    "nodes": [
        {"id": "start", "type": "Start", "x": 32, "y": 400, "w": 128, "h": 16, "section": S},
        {"id": "high", "type": "Platform", "x": 32, "y": 480, "w": 48, "h": 16, "section": S},
        {"id": "exit", "type": "Exit", "x": 32, "y": 32, "w": 560, "h": 16, "section": S},
    ],
    "edges": [{"from": "start", "to": "high", "type": "Jump", "vx": 100, "vy": 300},
              {"from": "high", "to": "start", "type": "Fall", "vx": 100, "vy": 0},
              {"from": "start", "to": "exit", "type": "Fall", "vx": 100, "vy": 0}],
    "entities": PIT_SPEC["entities"][:1] + [
        {"id": "door", "kind": "Exit", "anchor": "exit", "section": S, "description": "",
         "sprite": "", "requires_key": True},
        {"id": "k1", "kind": "Key", "anchor": "high", "section": S, "description": "",
         "sprite": ""},
    ],
}


def test_item_trap_is_repaired_by_moving_the_key():
    spec = LevelSpec.model_validate(KEY_SPEC)
    profile = GameProfile()
    lay = layout_spec(spec, profile, seed=0)
    place_spec_entities(spec, lay)
    rep = verify_grid(lay, profile, spec)
    assert [e.code for e in rep.errors] == ["SOFTLOCK"], rep.error_messages()
    assert rep.errors[0].entity_ids == ["k1"]
    assert "without Key 'k1'" in rep.errors[0].message
    lay, applied = repair_grid(lay, rep, profile, spec)
    assert any(a.startswith("moved k1 from high to start") for a in applied), applied
    final = verify_grid(lay, profile, spec)
    assert final.ok, final.error_messages()


def test_softlock_check_is_cheap_on_a_large_level():
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from test_build_level import _large_spec

    spec = LevelSpec.model_validate(_large_spec())
    profile = GameProfile()
    lay = layout_spec(spec, profile, seed=0)
    place_spec_entities(spec, lay)
    rep = verify_grid(lay, profile, spec)
    t0 = time.perf_counter()
    for _ in range(5):
        find_softlocks(rep.graph, rep.start_span, rep.exit_spans)
    per = (time.perf_counter() - t0) / 5
    assert per < 0.1 * rep.stats["verify_seconds"] + 0.005, (per, rep.stats)


# ─────────────────────────────────────────────────────────────────────────────
# Edits
# ─────────────────────────────────────────────────────────────────────────────


def _floor_file(path: Path) -> None:
    ir = IRLevel.empty("Level_0", 40, 32)
    ir.fill_rect(0, 14, 40, 1, TileValue.SOLID)
    ir.entities = [IREntity("Start", 1, 13), IREntity("Exit", 38, 13)]
    path.write_text(json.dumps(to_ldtk_dict(ir)), encoding="utf-8")


def test_edit_that_creates_a_trap_is_not_written(tmp_path):
    p = tmp_path / "floor.ldtk"
    _floor_file(p)
    raw = p.read_bytes()
    # The region opens a 6-tile hole in the floor (jumpable) over a ledge 16 rows down.
    answer = json.dumps({"nodes": [{"id": "deep", "type": "Platform", "col": 4, "row": 24,
                                    "w": 4}],
                         "edges": [], "entities": [], "requirements": []})
    fake = FakeProvider([answer, answer])
    out = edit_region(str(p), Rect(14, 6, 12, 26), "a deep pit", fake, densify=False)
    assert not out.success
    assert "traps the player" in (out.error or "")
    assert p.read_bytes() == raw
    assert history.list_snapshots(p) == []
