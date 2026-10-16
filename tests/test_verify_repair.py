"""verify_grid error codes and repair_grid on hand-built cases."""

from __future__ import annotations

from game import GameProfile
from layout import TileValue, layout_spec, place_spec_entities, repair_grid, verify_grid
from spec_builders import entity, make_spec, node


def _run(spec, profile=None):
    profile = profile or GameProfile()
    lay = layout_spec(spec, profile)
    place_spec_entities(spec, lay)
    return lay, verify_grid(lay, profile, spec), profile


def _codes(report):
    return [e.code for e in report.errors]


def test_ok_case():
    spec = make_spec([node("start", "Start", 0, 2), node("exit", "Exit", 6, 3)],
                     [("start", "exit", "Jump")])
    _lay, rep, _ = _run(spec)
    assert rep.ok, rep.errors
    assert rep.golden_path == ["start", "exit"]


def test_no_grid_path_names_the_failing_hop():
    spec = make_spec(
        [node("start", "Start", 0, 2), node("p", "Platform", 5, 2), node("exit", "Exit", 26, 2, 3)],
        [("start", "p", "Jump"), ("p", "exit", "Jump")])
    _lay, rep, _ = _run(spec)
    assert _codes(rep) == ["NO_GRID_PATH"]
    err = rep.errors[0]
    assert err.node_ids == ["p", "exit"]
    assert "'p'→'exit'" in err.message and "out of the player's reach" in err.message


def test_wall_missing():
    prof = GameProfile()
    prof.abilities.wall_jump.enabled = True
    # 112 px up: beyond a plain jump (69 px), inside the wall-jump envelope (133 px).
    spec = make_spec([node("start", "Start", 0, 0), node("exit", "Exit", 6, 7)],
                     [("start", "exit", "Jump")], size=(30, 20))
    spec.edges[0].ability = "wall_jump"
    _lay, rep, _ = _run(spec, prof)
    assert _codes(rep) == ["WALL_MISSING"]
    assert rep.errors[0].node_ids == ["start", "exit"]


def test_entity_unreachable_and_decorative_warning():
    nodes = [node("start", "Start", 0, 2), node("exit", "Exit", 6, 2),
             node("island", "Platform", 24, 10)]
    ents = [entity("player", "Start", "start"), entity("door", "Exit", "exit", requires_key=False),
            entity("gem", "Pickup", "island", pickup_kind="gem"),
            entity("sign", "Interactive", "island", interactive_kind="sign"),
            entity("bob", "NPC", "island", name="Bob")]
    spec = make_spec(nodes, [("start", "exit", "Jump")], ents)
    _lay, rep, _ = _run(spec)
    assert _codes(rep) == ["ENTITY_UNREACHABLE"]
    assert rep.errors[0].entity_ids == ["gem"]
    assert len(rep.warnings) == 2  # sign + NPC only warn


def test_key_lock_order():
    nodes = [node("start", "Start", 0, 2), node("exit", "Exit", 6, 2)]
    ents = [entity("player", "Start", "start"), entity("door", "Exit", "exit", requires_key=True)]
    spec = make_spec(nodes, [("start", "exit", "Jump")], ents)
    _lay, rep, _ = _run(spec)
    assert _codes(rep) == ["KEY_LOCK_ORDER"]
    # a key on the start platform fixes it
    ents.append(entity("key", "Key", "start", opens="door"))
    _lay, rep, _ = _run(make_spec(nodes, [("start", "exit", "Jump")], ents))
    assert rep.ok, rep.errors


def test_key_behind_the_exit_is_flagged_and_moved():
    nodes = [node("start", "Start", 0, 2), node("p", "Platform", 6, 3),
             node("exit", "Exit", 12, 3), node("vault", "Platform", 19, 3)]  # a hop past the exit
    ents = [entity("player", "Start", "start"), entity("door", "Exit", "exit", requires_key=True),
            entity("key", "Key", "vault", opens="door")]
    spec = make_spec(nodes, [("start", "p", "Jump"), ("p", "exit", "Jump")], ents)
    lay, rep, prof = _run(spec)
    assert _codes(rep) == ["KEY_LOCK_ORDER"]
    lay, applied = repair_grid(lay, rep, prof, spec)
    assert verify_grid(lay, prof, spec).ok
    assert any(a.startswith("moved key from vault to") for a in applied)


def test_start_blocked():
    spec = make_spec([node("start", "Start", 0, 2), node("exit", "Exit", 6, 2)],
                     [("start", "exit", "Jump")])
    lay, _rep, prof = _run(spec)
    start = next(e for e in lay.ir.entities if e.entity_type == "Start")
    lay.ir.grid[start.row - 1][start.col] = TileValue.SOLID
    rep = verify_grid(lay, prof, spec)
    assert "START_BLOCKED" in _codes(rep)


def test_repair_inserts_stepping_stone_for_a_wide_gap():
    spec = make_spec([node("start", "Start", 0, 2), node("exit", "Exit", 22, 2)],
                     [("start", "exit", "Jump")])
    lay, rep, prof = _run(spec)
    assert _codes(rep) == ["NO_GRID_PATH"]
    lay, applied = repair_grid(lay, rep, prof, spec)
    final = verify_grid(lay, prof, spec)
    assert final.ok, final.errors
    assert any("stepping stone" in a for a in applied)
    stones = lay.added_nodes
    assert stones and all(lay.node_sections[s] == "s1" for s in stones)
    assert stones[0] in final.golden_path
    assert lay.golden_path[0] == "start" and lay.golden_path[-1] == "exit"


def test_repair_nudges_a_ceiling():
    # 'roof' (off the golden path; a hazard so it is not a platform to use
    # instead) hangs right in every jump arc start → exit.
    spec = make_spec(
        [node("start", "Start", 0, 2), node("exit", "Exit", 6, 4), node("roof", "Hazard", 0, 6, 6)],
        [("start", "exit", "Jump")])
    lay, rep, prof = _run(spec)
    assert _codes(rep) == ["NO_GRID_PATH"]
    assert "blocked by tiles" in rep.errors[0].message
    before = lay.node_layouts["roof"].row
    lay, applied = repair_grid(lay, rep, prof, spec)
    assert verify_grid(lay, prof, spec).ok
    assert any(a.startswith("nudged 'roof'") for a in applied)
    assert lay.node_layouts["roof"].row != before
    assert (lay.node_layouts["start"].row, lay.node_layouts["exit"].row) == (12, 10)


def test_repair_moves_unreachable_pickup_within_section():
    nodes = [node("start", "Start", 0, 2), node("p", "Platform", 6, 3), node("exit", "Exit", 12, 3),
             node("island", "Platform", 26, 12)]
    ents = [entity("player", "Start", "start"), entity("door", "Exit", "exit", requires_key=False),
            entity("gem", "Pickup", "island", pickup_kind="gem")]
    spec = make_spec(nodes, [("start", "p", "Jump"), ("p", "exit", "Jump")], ents)
    lay, rep, prof = _run(spec)
    assert _codes(rep) == ["ENTITY_UNREACHABLE"]
    lay, applied = repair_grid(lay, rep, prof, spec)
    assert verify_grid(lay, prof, spec).ok
    assert applied == ["moved gem from island to p"]
