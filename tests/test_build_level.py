"""build_level end to end with a FakeProvider (offline)."""

from __future__ import annotations

import json
import random
import time
from collections import Counter

import networkx as nx
import pytest

from game import GameProfile
from layout import verify_grid
from llm import FakeProvider, LLMController, validate_spec
from pipeline import LevelBuildFailed, build_level
from spec import LevelSpec
from spec_builders import chain_spec
from symbolic import extract_graph


def _controller(responses, max_retries=3):
    fake = FakeProvider(responses)
    return LLMController(provider=fake, max_retries=max_retries), fake


def _valid_fixtures(fixtures_dir):
    files = sorted(fixtures_dir.glob("valid_*.json"))
    files += [fixtures_dir / "patchable_jump_down_no_start_entity.json"]
    return files


def test_every_valid_fixture_builds_verified(fixtures_dir):
    files = _valid_fixtures(fixtures_dir)
    assert len(files) >= 3
    for path in files:
        ctrl, _ = _controller([path.read_text(encoding="utf-8")])
        built = build_level("x", ctrl, GameProfile())
        assert built.report.ok, (path.name, built.report.errors)
        assert built.stats["layout_retries"] == 0
        spec_kinds = Counter(e.kind for e in built.spec.entities)
        ir_kinds = Counter(e.entity_type for e in built.spec_layout.ir.entities)
        assert ir_kinds == spec_kinds, path.name


def test_unrepairable_grid_reprompts_with_the_failing_hop(load_fixture):
    ctrl, fake = _controller(
        [load_fixture("grid_unrepairable_tunnel.json"), load_fixture("valid_minimal.json")]
    )
    built = build_level("a tunnel", ctrl, GameProfile())
    assert built.report.ok
    assert len(fake.calls) == 2
    assert built.stats["layout_retries"] == 1
    assert built.stats["llm_attempts"] == 2
    msgs = fake.calls[1].messages
    assert msgs[1]["role"] == "assistant" and '"Tunnel"' in msgs[1]["content"]
    repair = msgs[-1]["content"]
    assert "GRID VERIFICATION FAILED" in repair
    assert "'start'→'exit'" in repair


def test_unrepairable_grid_raises_after_max_layout_retries(load_fixture):
    bad = load_fixture("grid_unrepairable_tunnel.json")
    ctrl, fake = _controller([bad, bad, bad])
    with pytest.raises(LevelBuildFailed) as info:
        build_level("a tunnel", ctrl, GameProfile(), max_layout_retries=2)
    assert len(fake.calls) == 3
    assert info.value.report is not None and not info.value.report.ok
    assert info.value.stats["layout_retries"] == 2
    assert "NO_GRID_PATH" in str(info.value)


def _independent_path_exists(layout) -> bool:
    """BFS on a fresh collision-aware extraction, from the Start to the Exit entity."""
    ir = layout.ir
    graph = extract_graph(ir.grid, profile=GameProfile(), collision=True,
                          gravity_scale_at=layout.gravity_scale_at)
    def span_under(ent):
        for n in graph.nodes():
            m = n.metadata
            nl = layout.node_layouts[ent.metadata["anchor"]]
            if m["row"] == nl.row and m["col_start"] <= ent.col <= m["col_end"]:
                return n.id
        return None
    start = next(e for e in ir.entities if e.entity_type == "Start")
    exits = [e for e in ir.entities if e.entity_type == "Exit"]
    s = span_under(start)
    return s is not None and any(
        (t := span_under(x)) is not None and nx.has_path(graph.nx_graph, s, t) for x in exits
    )


def _random_valid_spec(rng):
    while True:
        d = chain_spec(rng, rng.randint(30, 60), rng.randint(15, 20), rng.randint(1, 5),
                       n_sections=rng.randint(1, 2), extras=rng.randint(0, 2))
        spec = LevelSpec.model_validate(d)
        if validate_spec(spec).valid:
            return d


def test_property_random_specs_are_ok_or_explicit_failure():
    outcomes = Counter()
    for seed in range(50):
        rng = random.Random(seed)
        raw = json.dumps(_random_valid_spec(rng))
        ctrl, _ = _controller([raw] * 3)
        try:
            built = build_level("random", ctrl, GameProfile(), seed=seed)
        except LevelBuildFailed as exc:
            assert exc.report is not None and not exc.report.ok
            outcomes["failed"] += 1
            continue
        assert built.report.ok
        assert _independent_path_exists(built.spec_layout), seed
        outcomes["ok"] += 1
    assert outcomes["ok"] + outcomes["failed"] == 50
    assert outcomes["ok"] >= 40, outcomes  # the pipeline should mostly succeed


def _large_spec():
    rng = random.Random(1234)
    for _ in range(50):
        d = chain_spec(rng, 300, 100, 45, n_sections=4, extras=40,
                       step=(4, 5), dy_range=(-2, 2))
        spec = LevelSpec.model_validate(d)
        if validate_spec(spec).valid and len(spec.nodes) >= 75:
            return d
    raise AssertionError("could not generate a large valid spec")


def test_large_level_is_fast():
    d = _large_spec()
    ctrl, _ = _controller([json.dumps(d)] * 3)
    t0 = time.perf_counter()
    try:
        built = build_level("big", ctrl, GameProfile())
        ok = built.report.ok
    except LevelBuildFailed:
        ok = False
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0, f"build_level took {elapsed:.2f}s"
    assert ok
    assert built.spec_layout.ir.width_tiles == 300 and built.spec_layout.ir.height_tiles == 100
    # still verified when re-checked from scratch
    assert verify_grid(built.spec_layout, GameProfile(), built.spec).ok
