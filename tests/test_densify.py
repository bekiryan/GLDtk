"""Densify: extra platforms in large empty regions, never at the cost of verification (M2 bug 4)."""

from __future__ import annotations

import json
import time

import networkx as nx

import layout.densify as densify_mod
from game import GameProfile
from layout import TileValue, verify_grid
from layout.densify import FILL_PREFIX, densify, protected_cells
from llm import FakeProvider, LLMController
from pipeline import build_level
from pipeline.level import _layout_and_verify
from spec import LevelSpec
from spec_builders import entity, make_spec, node, section

PROFILE = GameProfile()


def _sparse(size=(80, 30)) -> LevelSpec:
    """Seven small platforms along the bottom third of an 80×30, two sections."""
    nodes = [
        node("start", "Start", 2, 2, 3, "a"),
        node("p1", "Platform", 10, 4, 3, "a"),
        node("p2", "Platform", 18, 6, 3, "a"),
        node("p3", "Platform", 27, 4, 3, "a"),
        node("p4", "Platform", 44, 3, 3, "b"),
        node("p5", "Platform", 52, 5, 3, "b"),
        node("spikes", "Hazard", 36, 0, 4, "a"),
        node("exit", "Exit", 62, 4, 3, "b"),
    ]
    chain = ["start", "p1", "p2", "p3", "p4", "p5", "exit"]
    ents = [entity("player", "Start", "start", "a", facing="right"),
            entity("door", "Exit", "exit", "b", requires_key=False),
            entity("coin", "Pickup", "p2", "a", pickup_kind="coin", value=1),
            entity("key", "Key", "p4", "b", opens="door")]
    return make_spec(nodes, [(a, b, "Jump") for a, b in zip(chain, chain[1:])], ents,
                     [section("a", 0), section("b", 1, theme="castle")], size)


def _laid_out(spec, seed=7):
    lay, report, _ = _layout_and_verify(spec, PROFILE, seed)
    assert report.ok, report.error_messages()
    return lay, report


def _fill_cells(lay):
    return {(c, nl.row) for nid, nl in lay.node_layouts.items() if nid.startswith(FILL_PREFIX)
            for c in range(nl.col, nl.col + nl.width)}


def test_densify_adds_platforms_and_keeps_the_level_verified():
    spec = _sparse()
    lay, report = _laid_out(spec)
    before = {nid: (nl.col, nl.row, nl.width) for nid, nl in lay.node_layouts.items()}
    res = densify(lay, report, PROFILE, spec, seed=7)
    assert res.report is not None and res.report.ok
    assert verify_grid(lay, PROFILE, spec).ok  # re-checked from scratch
    assert len(res.added) >= 15 and not res.dropped
    # existing nodes untouched; fills are marked, 3..8 wide, in their own section band
    for nid, rect in before.items():
        nl = lay.node_layouts[nid]
        assert (nl.col, nl.row, nl.width) == rect
    for nid in res.added:
        nl = lay.node_layouts[nid]
        assert nid.startswith(FILL_PREFIX) and lay.node_kinds[nid] == "Platform"
        assert 3 <= nl.width <= 8
        band = lay.section_at(nl.col, nl.row)
        assert band.id == lay.node_sections[nid] and band.contains(nl.col + nl.width - 1, nl.row)
        assert lay.row_is_free(nid, nl.row)  # headroom for the player above and below
    assert lay.added_nodes[-len(res.added):] == res.added
    # ground segments under the long empty stretches of the bottom row
    assert any(lay.node_layouts[n].row == lay.height - 1 for n in res.added)
    # every entity is still where it was and reachable
    assert {e.metadata.get("spec_id") for e in lay.ir.entities} >= {"coin", "key", "player", "door"}


def test_density_target():
    spec = _sparse()
    lay, report = _laid_out(spec)
    densify(lay, report, PROFILE, spec, seed=7)
    standable = sum(1 for n in lay.node_layouts if lay.standable(n))
    empty = sum(row.count(TileValue.EMPTY) for row in lay.ir.grid)
    assert 35 <= empty / standable <= 90, empty / standable  # about one per 40–60 tiles


def test_nothing_lies_on_the_golden_path_arcs():
    spec = _sparse()
    lay, report = _laid_out(spec)
    graph = report.graph
    exit_span = next(iter(report.exit_spans))
    path = nx.shortest_path(graph.nx_graph, report.start_span, exit_span)
    arcs = set()
    for a, b in zip(path, path[1:]):
        arcs |= densify_mod._edge_cells(lay, PROFILE, graph.get_edge(a, b))
    assert arcs and arcs <= protected_cells(lay, report, PROFILE)
    densify(lay, report, PROFILE, spec, seed=7)
    assert not _fill_cells(lay) & arcs
    # the very same moves are still collision-free on the new grid
    from symbolic.extractor import move_between

    for a, b in zip(path, path[1:]):
        ra = (graph.get_node(a).metadata["row"], graph.get_node(a).metadata["col_start"],
              graph.get_node(a).metadata["col_end"])
        rb = (graph.get_node(b).metadata["row"], graph.get_node(b).metadata["col_start"],
              graph.get_node(b).metadata["col_end"])
        assert move_between(lay.ir.grid, ra, rb, PROFILE) is not None


def test_densify_is_deterministic_for_a_seed():
    spec = _sparse()

    def run(seed):
        lay, report = _laid_out(spec)
        densify(lay, report, PROFILE, spec, seed=seed)
        return lay.ir.ascii_render()

    assert run(3) == run(3)
    assert run(3) != run(4)


def test_densify_drops_additions_that_break_verification(monkeypatch):
    spec = _sparse()
    lay, report = _laid_out(spec)
    original = [row[:] for row in lay.ir.grid]
    real = densify_mod.verify_grid

    def picky(layout, profile, spec_):  # more than 4 fills "breaks" the level
        rep = real(layout, profile, spec_)
        if sum(n.startswith(FILL_PREFIX) for n in layout.node_layouts) > 4:
            rep.ok = False
        return rep

    monkeypatch.setattr(densify_mod, "verify_grid", picky)
    res = densify(lay, report, PROFILE, spec, seed=7)
    assert len(res.added) == 4 and res.dropped and res.report.ok
    assert sorted(n for n in lay.node_layouts if n.startswith(FILL_PREFIX)) == sorted(res.added)
    assert any("dropped" in n for n in res.notes)

    # nothing verifies: the input layout comes back unchanged
    monkeypatch.setattr(densify_mod, "verify_grid",
                        lambda l, p, s: _not_ok(real(l, p, s), l))
    lay2, report2 = _laid_out(spec)
    res2 = densify(lay2, report2, PROFILE, spec, seed=7)
    assert res2.added == [] and res2.report.ok
    assert lay2.ir.grid == original
    assert not any(n.startswith(FILL_PREFIX) for n in lay2.node_layouts)


def _not_ok(rep, layout):
    if any(n.startswith(FILL_PREFIX) for n in layout.node_layouts):
        rep.ok = False
    return rep


def test_vertical_levels_get_intermediate_ledges_and_no_ground():
    nodes = [
        node("start", "Start", 3, 1, 4, "low"),
        node("p1", "Platform", 12, 9, 4, "low"),
        node("p2", "Platform", 4, 17, 4, "low"),
        node("p3", "Platform", 14, 25, 4, "high"),
        node("exit", "Exit", 5, 33, 4, "high"),
    ]
    chain = [n["id"] for n in nodes]
    spec = make_spec(nodes, [(a, b, "Jump") for a, b in zip(chain, chain[1:])], None,
                     [section("low", 0), section("high", 1)], (30, 45))
    lay, report = _laid_out(spec)
    phases = densify_mod._Densifier(lay, report, PROFILE, 1)
    phases.ground()
    assert phases.added == []  # ground is for horizontal levels only
    lay, report = _laid_out(spec)
    res = densify(lay, report, PROFILE, spec, seed=1)
    assert res.report.ok and res.added
    # ledges between the 8-row climb steps
    rows = {lay.node_layouts[n].row for n in res.added}
    for a, b in zip(chain, chain[1:]):
        lo, hi = sorted((lay.node_layouts[a].row, lay.node_layouts[b].row))
        assert any(lo < r < hi for r in rows), (a, b)


def test_build_level_densify_flag():
    spec = _sparse()
    raw = json.dumps(spec.model_dump(by_alias=True))

    def build(**kw):
        ctrl = LLMController(provider=FakeProvider([raw]))
        return build_level("sparse", ctrl, PROFILE, seed=7, **kw)

    off = build(densify=False)
    assert off.stats["densify_added"] == 0
    assert not any(n.id.startswith(FILL_PREFIX) for n in off.spec.nodes)

    on = build()
    fills = [n for n in on.spec.nodes if n.id.startswith(FILL_PREFIX)]
    assert on.report.ok and len(fills) == on.stats["densify_added"] > 0
    assert {n.section for n in fills} == {"a", "b"}
    assert all(n.type == "Platform" for n in fills)
    assert not any(e.from_.startswith(FILL_PREFIX) or e.to.startswith(FILL_PREFIX)
                   for e in on.spec.edges)
    assert any(w.startswith("densify: added") for w in on.warnings)
    assert on.fidelity is not None


def test_large_level_densify_stays_bounded():
    from test_build_level import _large_spec

    d = _large_spec()
    ctrl = LLMController(provider=FakeProvider([json.dumps(d)] * 3))
    t0 = time.perf_counter()
    built = build_level("big", ctrl, PROFILE)
    elapsed = time.perf_counter() - t0
    assert built.report.ok
    assert 0 < built.stats["densify_added"] <= densify_mod.MAX_ADDED
    assert elapsed < 15.0, f"build_level with densify took {elapsed:.2f}s"
