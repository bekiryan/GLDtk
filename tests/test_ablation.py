"""Fast offline tests for the ablation benchmark suite (eval/ablation.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import ablation as ab
from game.profile import GameProfile
from llm.patcher import SymbolicPatcher
from llm.validator import LLMController, validate_spec
from patterns.store import PatternStore
from pipeline import level as pipeline_level
from pipeline.stamps import StampFinder
from spec.level_spec import LevelSpec


class _ConstEmbedder:
    name = "const"

    def embed(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


def _corridor_ascii(w: int = 30, h: int = 24, floor: int = 21) -> str:
    rows = []
    for r in range(h):
        if floor - 6 <= r < floor:
            rows.append("." * w)
        else:
            rows.append("#" * w)
    return "\n".join(rows)


def _write_recording(rec_root: Path, rec_name: str, prompt_id: str, responses: list[str]) -> None:
    pdir = rec_root / rec_name / prompt_id
    pdir.mkdir(parents=True, exist_ok=True)
    meta = rec_root / rec_name / "meta.json"
    if not meta.exists():
        meta.write_text(json.dumps({"provider": "fake", "model": "test-model"}), encoding="utf-8")
    for idx, raw in enumerate(responses):
        (pdir / f"{idx:02d}.txt").write_text(raw, encoding="utf-8")


def _write_prompts(path: Path, prompts: list[dict]) -> Path:
    path.write_text(json.dumps({"version": 1, "prompts": prompts}), encoding="utf-8")
    return path


def test_patch_contexts_restore_original_symbols():
    orig_patch_spec = LLMController._patch_spec
    orig_sym_patch = SymbolicPatcher.patch
    orig_ensure = pipeline_level.ensure_complete
    orig_fill = pipeline_level.fill_missing
    orig_repair = pipeline_level.repair_grid
    orig_attempts = pipeline_level.TERRAIN_ATTEMPTS
    orig_close = pipeline_level._close_loop

    with ab._no_symbolic_patcher():
        assert LLMController._patch_spec is not orig_patch_spec
        assert SymbolicPatcher.patch is not orig_sym_patch
    assert LLMController._patch_spec is orig_patch_spec
    assert SymbolicPatcher.patch is orig_sym_patch

    with ab._no_completeness_fill():
        assert pipeline_level.ensure_complete is not orig_ensure
        assert pipeline_level.fill_missing is not orig_fill
    assert pipeline_level.ensure_complete is orig_ensure
    assert pipeline_level.fill_missing is orig_fill

    with ab._no_grid_and_softlock_repair():
        assert pipeline_level.repair_grid is not orig_repair
        assert pipeline_level.TERRAIN_ATTEMPTS == 1
    assert pipeline_level.repair_grid is orig_repair
    assert pipeline_level.TERRAIN_ATTEMPTS == orig_attempts

    with ab._no_closed_loop_complexity():
        assert pipeline_level._close_loop is not orig_close
    assert pipeline_level._close_loop is orig_close


def test_a1_a2_a3_a4_a5_fast_fixture_ablations(tmp_path: Path, load_fixture, monkeypatch):
    rec_root = tmp_path / "recordings"
    out_dir = tmp_path / "out"

    # A1 fixture: Jump edge going down + missing Start entity -> SymbolicPatcher rescues it.
    patchable_raw = load_fixture("patchable_jump_down_no_start_entity.json")
    _write_recording(rec_root, "qwen-2026-10-16", "structure-crypt", [patchable_raw])
    _write_recording(rec_root, "gemini-2026-10-19c", "structure-crypt", [patchable_raw])

    # A2 fixture: spec missing 3 coins and 1 boss -> fill_missing adds 4 entities.
    comp_raw = load_fixture("completeness_fill.json")
    _write_recording(rec_root, "gemini-2026-10-15", "single-forest", [comp_raw])

    # A3 fixture: gem on an unreachable island platform -> spec validates, grid needs repair_grid.
    a3_spec = json.loads(load_fixture("valid_minimal.json"))
    a3_spec["size"] = {"width_tiles": 30, "height_tiles": 15}
    a3_spec["nodes"] = [
        {"id": "start", "type": "Start", "x": 0, "y": 32, "w": 48, "h": 16, "section": "s1"},
        {"id": "exit", "type": "Exit", "x": 96, "y": 32, "w": 48, "h": 16, "section": "s1"},
        {"id": "island", "type": "Platform", "x": 384, "y": 160, "w": 48, "h": 16, "section": "s1"},
    ]
    a3_spec["edges"] = [
        {"from": "start", "to": "exit", "type": "Jump", "vx": 150, "vy": 450},
    ]
    a3_spec["entities"].append(
        {"id": "gem1", "kind": "Pickup", "anchor": "island", "section": "s1", "description": "gem", "sprite": "", "pickup_kind": "gem"}
    )
    assert validate_spec(LevelSpec.model_validate(a3_spec), GameProfile()).valid
    _write_recording(rec_root, "gemini-2026-10-15", "two-sections", [json.dumps(a3_spec)])

    # A4 & A5 fixture: corridor terrain level (32x26 -> 30x24 inner band matches _corridor_ascii).
    corridor_spec = {
        "title": "Corridor",
        "size": {"width_tiles": 32, "height_tiles": 26},
        "sections": [
            {
                "id": "s1",
                "name": "Hall",
                "theme": "dungeon",
                "difficulty": "medium",
                "gravity_scale": 1.0,
                "description": "A stone corridor.",
                "structure": "corridor",
                "order": 0,
            }
        ],
        "nodes": [
            {"id": "start", "type": "Start", "x": 32, "y": 64, "w": 48, "h": 16, "section": "s1"},
            {"id": "exit", "type": "Exit", "x": 400, "y": 64, "w": 48, "h": 16, "section": "s1"},
        ],
        "edges": [{"from": "start", "to": "exit", "type": "Walk", "vx": 200, "vy": 0}],
        "entities": [
            {"id": "player", "kind": "Start", "anchor": "start", "section": "s1", "description": "player", "sprite": "", "facing": "right"},
            {"id": "door", "kind": "Exit", "anchor": "exit", "section": "s1", "description": "exit", "sprite": "", "requires_key": False},
        ],
        "requirements": [{"id": "R1", "text": "Corridor section.", "category": "section", "check": {"type": "structure", "subkind": "corridor", "section": "s1"}}],
    }
    _write_recording(rec_root, "gemini-2026-10-19c", "structure-labyrinth", [json.dumps(corridor_spec)])
    _write_recording(rec_root, "gemini-2026-10-16", "v4-complexity-low", [json.dumps(corridor_spec)])

    prompts_file = _write_prompts(
        tmp_path / "prompts.json",
        [
            {"id": "structure-crypt", "tags": ["structure"], "prompt": "A meadow and dungeon."},
            {"id": "structure-labyrinth", "tags": ["structure"], "prompt": "A stone corridor."},
            {"id": "single-forest", "tags": ["core"], "prompt": "A meadow with 3 coins and a boss.", "expect": {"entity_kinds": ["Pickup", "Enemy"]}},
            {"id": "two-sections", "tags": ["core"], "prompt": "Short jump over a platform."},
            {"id": "v4-complexity-low", "tags": ["v4"], "complexity": 0.3, "prompt": "A stone corridor."},
        ],
    )

    # Isolate PatternStore inside tmp_path so tests never touch user AppData.
    monkeypatch.setenv("GLDTK_PATTERNS_DIR", str(tmp_path / "patterns"))
    store = PatternStore(directory=tmp_path / "patterns")
    store.embedder = {"name": "const", "dim": 3}
    rec = {
        "id": "pat_corridor_1",
        "kind": "pattern",
        "source": "test",
        "license": "CC0",
        "origin_path": "/tmp/x.ldtk",
        "level": "L1",
        "ascii": _corridor_ascii(),
        "accepted": True,
        "label": {"title": "Stone corridor", "structure_type": "corridor"},
        "features": {"structure": "corridor", "route": {"ok": True}},
    }
    store.add(rec)
    store.vectors[rec["id"]] = {"text_hash": "h", "vec": [1.0, 0.0, 0.0]}
    store.save()

    summary = ab.run_all_ablations(
        out_dir,
        recordings_root=rec_root,
        prompts_file=prompts_file,
        stamp_finder_factory=lambda desc: StampFinder(desc, store=store, embedder=_ConstEmbedder()),
        log=lambda _msg: None,
    )

    assert (out_dir / "summary.json").is_file()
    assert (out_dir / "report.md").is_file()
    ablations = summary["ablations"]
    assert list(ablations.keys()) == ["A1", "A2", "A3", "A4", "A5"]

    # A1: SymbolicPatcher rescues structure-crypt in baseline; ablated fails.
    a1 = ablations["A1"]
    assert a1["baseline"]["patches_mean"] > 0
    assert a1["baseline"]["verified"] >= 2
    assert a1["ablated"]["patches_mean"] == 0
    assert a1["ablated"]["verified"] < a1["baseline"]["verified"]

    # A2: CompletenessFill fills 4 entities (3 coins + 1 boss) in baseline; 0 in ablated.
    a2 = ablations["A2"]
    assert a2["baseline"]["entities_filled_mean"] > 0
    assert a2["baseline"]["status_counts"]["missing"] == 0
    assert a2["ablated"]["entities_filled_mean"] == 0
    assert a2["ablated"]["status_counts"]["missing"] > 0
    assert a2["ablated"]["expect_failed"] > a2["baseline"]["expect_failed"]

    # A3: Grid repair runs on blocked path in baseline; ablated has 0 grid_repairs and fails it.
    a3 = ablations["A3"]
    assert a3["baseline"]["grid_repairs_mean"] > 0
    assert a3["ablated"]["grid_repairs_mean"] == 0
    assert a3["ablated"]["verified"] < a3["baseline"]["verified"]

    # A4: Pattern stamping stamps the corridor pattern in baseline vs 0 when disabled.
    a4 = ablations["A4"]
    assert a4["baseline"]["stamped_sections_total"] >= 1
    assert a4["baseline"]["stamped_prompts"] >= 1
    assert a4["ablated"]["stamped_sections_total"] == 0
    assert a4["ablated"]["stamped_prompts"] == 0

    # A5: Complexity metrics computed for both conditions.
    a5 = ablations["A5"]
    assert a5["baseline"]["mean_band_error"] is not None
    assert a5["ablated"]["complexity_rounds_mean"] == 0

    report = (out_dir / "report.md").read_text(encoding="utf-8")
    for heading in ("## Overview", "## A1:", "## A2:", "## A3:", "## A4:", "## A5:"):
        assert heading in report


def test_a4_empty_pattern_store_graceful_fallback(tmp_path: Path):
    empty_store = PatternStore(directory=tmp_path / "empty_patterns")
    finder = StampFinder("a corridor", store=empty_store, embedder=_ConstEmbedder())
    rec_root = tmp_path / "recordings"
    corridor_spec = {
        "title": "Minimal",
        "size": {"width_tiles": 30, "height_tiles": 15},
        "sections": [{"id": "s1", "name": "F", "theme": "forest", "difficulty": "easy", "gravity_scale": 1.0, "description": "walk", "order": 0}],
        "nodes": [
            {"id": "start", "type": "Start", "x": 0, "y": 32, "w": 48, "h": 16, "section": "s1"},
            {"id": "exit", "type": "Exit", "x": 64, "y": 32, "w": 48, "h": 16, "section": "s1"},
        ],
        "edges": [{"from": "start", "to": "exit", "type": "Walk", "vx": 200, "vy": 0}],
        "entities": [
            {"id": "p", "kind": "Start", "anchor": "start", "section": "s1", "description": "p", "sprite": "", "facing": "right"},
            {"id": "e", "kind": "Exit", "anchor": "exit", "section": "s1", "description": "e", "sprite": "", "requires_key": False},
        ],
        "requirements": [{"id": "R1", "text": "short", "category": "size"}],
    }
    _write_recording(rec_root, "gemini-2026-10-19c", "structure-labyrinth", [json.dumps(corridor_spec)])
    prompts_file = _write_prompts(tmp_path / "prompts.json", [{"id": "structure-labyrinth", "tags": ["structure"], "prompt": "short"}])

    summary = ab.run_all_ablations(
        tmp_path / "out",
        recordings_root=rec_root,
        prompts_file=prompts_file,
        stamp_finder_factory=lambda _d: finder,
        ablations=["A4"],
        log=lambda _m: None,
    )
    a4 = summary["ablations"]["A4"]
    assert a4["baseline"]["stamped_sections_total"] == 0
    assert a4["baseline"]["verified"] == 1


def test_complexity_band_error_calculation():
    records = [
        {"id": "in_band", "recording": "r", "complexity": 0.8, "difficulty": {"overall": 6.0}, "complexity_rounds": 1, "verified": True},
        {"id": "below", "recording": "r", "complexity": 0.8, "difficulty": {"overall": 4.5}, "complexity_rounds": 0, "verified": True},
        {"id": "above", "recording": "r", "complexity": 0.3, "difficulty": {"overall": 4.9}, "complexity_rounds": 0, "verified": True},
    ]
    # targets(0.8): [5.3, 7.3], targets(0.3): [2.3, 4.3]
    res = ab._complexity_metrics(records)
    errs = {row["id"]: row["abs_band_error"] for row in res["rows"]}
    assert errs["in_band"] == 0.0
    assert errs["below"] == pytest.approx(0.8)
    assert errs["above"] == pytest.approx(0.6)
    assert res["in_range_share"] == pytest.approx(1 / 3)
    assert res["within_1_5"] == 3
    assert res["mean_band_error"] == pytest.approx((0.0 + 0.8 + 0.6) / 3)


def test_cli_only_flag_writes_selected_ablation(tmp_path: Path, load_fixture):
    # Smoke-test CLI main() using --only A5 on real recordings into tmp_path
    out = tmp_path / "cli_out"
    code = ab.main(["--out", str(out), "--only", "A5"])
    assert code == 0
    data = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert list(data["ablations"].keys()) == ["A5"]
    assert (out / "report.md").is_file()
