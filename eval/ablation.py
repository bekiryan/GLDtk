"""Offline replay ablation benchmark suite for GLDtk (A1-A5).

Runs controlled ablations deterministically over recorded LLM responses under
``eval/recordings/`` without making any network or remote LLM API calls:

    python -m eval.ablation
    python -m eval.ablation --out eval/results/ablations
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.metrics import aggregate  # noqa: E402
from eval.providers import CallBudget, CountingProvider, ReplayProvider, read_meta  # noqa: E402
from eval.run import (  # noqa: E402
    PROMPTS_FILE,
    check_expectations,
    classify_failure,
    load_prompts,
    measure_record_difficulty,
)
from fidelity import FidelityReport, FillResult  # noqa: E402
from game.profile import GameProfile  # noqa: E402
from llm.patcher import PatchResult  # noqa: E402
from llm.validator import LLMController, MaxRetriesExceeded  # noqa: E402
from pipeline import build_level  # noqa: E402
from pipeline import complexity as cx  # noqa: E402
from pipeline import level as pipeline_level  # noqa: E402
from pipeline.completeness import CompletenessResult  # noqa: E402
from pipeline.stamps import StampFinder  # noqa: E402

DEFAULT_OUT_DIR = ROOT / "eval" / "results" / "ablations"
DEFAULT_RECORDINGS_ROOT = ROOT / "eval" / "recordings"


@dataclass(frozen=True)
class SuiteSlice:
    """One recording directory and the prompts to replay from it."""

    recording: str
    tags: Optional[Tuple[str, ...]] = None
    prompt_ids: Optional[Tuple[str, ...]] = None
    overrides: Optional[Dict[str, Dict[str, Any]]] = None


# ── ablation patch contexts ─────────────────────────────────────────────────


@contextmanager
def _no_op_context() -> Iterator[None]:
    yield


@contextmanager
def _no_symbolic_patcher() -> Iterator[None]:
    """Bypass LLMController._patch_spec and SymbolicPatcher.patch."""
    with ExitStack() as stack:
        stack.enter_context(patch.object(LLMController, "_patch_spec", lambda self, result: None))
        stack.enter_context(
            patch(
                "llm.patcher.SymbolicPatcher.patch",
                lambda self, graph, errors: PatchResult(success=False, graph=graph, applied=[]),
            )
        )
        yield


@contextmanager
def _no_completeness_fill() -> Iterator[None]:
    """Bypass ensure_complete and fill_missing without extra LLM re-prompts."""
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                pipeline_level,
                "ensure_complete",
                lambda spec, profile=None, reprompt=None, prompt=None: CompletenessResult(
                    spec=spec, report=FidelityReport()
                ),
            )
        )
        stack.enter_context(
            patch.object(
                pipeline_level,
                "fill_missing",
                lambda spec: FillResult(spec=spec),
            )
        )
        yield


@contextmanager
def _no_grid_and_softlock_repair() -> Iterator[None]:
    """Bypass deterministic grid/soft-lock repair and multi-seed terrain retries."""
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                pipeline_level,
                "repair_grid",
                lambda spec_layout, report, profile, spec: (spec_layout, []),
            )
        )
        stack.enter_context(patch.object(pipeline_level, "TERRAIN_ATTEMPTS", 1))
        yield


@contextmanager
def _no_closed_loop_complexity() -> Iterator[None]:
    """Bypass the closed-loop difficulty adjustment pass."""
    with patch.object(
        pipeline_level,
        "_close_loop",
        lambda spec, spec_layout, report, stage, profile, stats: (spec, spec_layout, report, []),
    ):
        yield


# ── prompt selection & offline replay runner ────────────────────────────────


def _select_prompts(
    slice_cfg: SuiteSlice,
    recordings_root: Path,
    prompts_by_id: Dict[str, Dict[str, Any]],
    ordered_prompts: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rec_dir = recordings_root / slice_cfg.recording
    available = {d.name for d in rec_dir.iterdir() if d.is_dir()} if rec_dir.is_dir() else set()
    selected: List[Dict[str, Any]] = []
    if slice_cfg.prompt_ids is not None:
        candidates = [prompts_by_id[pid] for pid in slice_cfg.prompt_ids if pid in prompts_by_id]
    else:
        want_tags = set(slice_cfg.tags) if slice_cfg.tags else None
        candidates = [
            p for p in ordered_prompts
            if p["id"] in available and (want_tags is None or bool(want_tags & set(p.get("tags", []))))
        ]
    for base in candidates:
        if base["id"] not in available:
            continue
        entry = dict(base)
        if slice_cfg.overrides and base["id"] in slice_cfg.overrides:
            entry.update(slice_cfg.overrides[base["id"]])
        selected.append(entry)
    return selected


def _run_single_prompt(
    entry: Dict[str, Any],
    recording_dir: Path,
    recording_name: str,
    profile: GameProfile,
    *,
    seed: int = 0,
    max_retries: int = 3,
    stamp_finder: Any = None,
) -> Dict[str, Any]:
    """Replay one prompt deterministically through ``build_level``."""
    meta = read_meta(recording_dir)
    provider_name = meta.get("provider") or "replay"
    model = meta.get("model")
    inner = ReplayProvider(recording_dir, entry["id"], name=provider_name)
    counted = CountingProvider(inner, CallBudget())
    controller = LLMController(profile=profile, provider=counted, max_retries=max_retries)

    rec: Dict[str, Any] = {
        "id": entry["id"],
        "recording": recording_name,
        "provider": provider_name,
        "model": model,
        "tags": list(entry.get("tags", [])),
        "prompt": entry["prompt"],
        "seed": seed,
        "verified": False,
        "failure": None,
        "fidelity_score": None,
        "fidelity_counts": None,
        "fidelity_items": [],
        "expect_failures": [],
        "complexity": entry.get("complexity"),
        "complexity_rounds": 0 if entry.get("complexity") is not None else None,
        "patterns_stamped": [],
        "stamped_sections": 0,
    }
    t0 = time.perf_counter()
    stats: Dict[str, Any] = {}
    try:
        built = build_level(
            entry["prompt"],
            controller,
            profile,
            seed=seed,
            complexity=entry.get("complexity"),
            stamp_finder=stamp_finder,
        )
    except Exception as exc:  # a failed replay is recorded, never raised
        if isinstance(exc, MaxRetriesExceeded):
            gs = exc.stats
            stats = {"llm_attempts": gs.attempts, "patches_applied": list(gs.patches_applied)} if gs else {}
        else:
            raw = getattr(exc, "stats", None)
            stats = dict(raw) if isinstance(raw, dict) else {}
        rec["failure"] = {"type": classify_failure(exc), "message": str(exc)[:500]}
    else:
        stats = built.stats
        rec["verified"] = bool(built.report.ok)
        if built.fidelity is not None:
            rec["fidelity_score"] = built.fidelity.score
            rec["fidelity_counts"] = built.fidelity.counts()
            rec["fidelity_items"] = built.fidelity.to_dict()["items"]
        rec["expect_failures"] = check_expectations(entry.get("expect", {}), built)
        rec["difficulty"] = measure_record_difficulty(built, profile, entry["prompt"])
        if entry.get("complexity") is not None:
            rec["complexity_rounds"] = int((built.stats.get("complexity") or {}).get("rounds") or 0)
        rec["warnings"] = len(built.warnings)
        rec["size_tiles"] = [built.spec.size.width_tiles, built.spec.size.height_tiles]
        rec["sections"] = len(built.spec.sections)
        rec["patterns_stamped"] = list(built.patterns_stamped)
        rec["stamped_sections"] = len(built.patterns_stamped)

    rec["wall_time_s"] = round(time.perf_counter() - t0, 4)
    rec["llm_calls"] = counted.calls
    rec["llm_attempts"] = stats.get("llm_attempts", 0)
    rec["patches"] = len(stats.get("patches_applied", []))
    rec["patches_applied"] = list(stats.get("patches_applied", []))
    for k in (
        "completeness_reprompts",
        "entities_filled",
        "layout_retries",
        "grid_repairs",
        "terrain_retries",
        "comfort_added",
        "densify_added",
    ):
        rec[k] = stats.get(k, 0)
    return rec


def _run_condition(
    slices: Sequence[SuiteSlice],
    recordings_root: Path,
    prompts_by_id: Dict[str, Dict[str, Any]],
    ordered_prompts: Sequence[Dict[str, Any]],
    profile: GameProfile,
    *,
    seed: int = 0,
    context_factory: Callable[[], Any] = _no_op_context,
    stamp_finder_factory: Optional[Callable[[str], Any]] = None,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with context_factory():
        for slice_cfg in slices:
            rec_dir = recordings_root / slice_cfg.recording
            entries = _select_prompts(slice_cfg, recordings_root, prompts_by_id, ordered_prompts)
            for entry in entries:
                finder = stamp_finder_factory(entry["prompt"]) if stamp_finder_factory else None
                records.append(
                    _run_single_prompt(
                        entry,
                        rec_dir,
                        slice_cfg.recording,
                        profile,
                        seed=seed,
                        stamp_finder=finder,
                    )
                )
    return records


# ── summary & delta helpers ─────────────────────────────────────────────────


def _complexity_metrics(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for r in records:
        c = r.get("complexity")
        if c is None:
            continue
        t = cx.targets(float(c))
        overall = (r.get("difficulty") or {}).get("overall")
        if overall is None:
            band_err = None
            within_1_5 = False
            in_strict = False
        else:
            overall_f = float(overall)
            if overall_f < t.score_min:
                band_err = round(t.score_min - overall_f, 4)
            elif overall_f > t.score_max:
                band_err = round(overall_f - t.score_max, 4)
            else:
                band_err = 0.0
            within_1_5 = (t.score_min - cx.TOLERANCE) <= overall_f <= (t.score_max + cx.TOLERANCE)
            in_strict = band_err == 0.0
        rows.append(
            {
                "id": r["id"],
                "recording": r["recording"],
                "complexity": float(c),
                "target_min": t.score_min,
                "target_max": t.score_max,
                "measured_overall": overall,
                "abs_band_error": band_err,
                "within_1_5": within_1_5,
                "in_strict_band": in_strict,
                "complexity_rounds": r.get("complexity_rounds", 0),
                "verified": bool(r.get("verified")),
            }
        )
    errs = [row["abs_band_error"] for row in rows if row["abs_band_error"] is not None]
    rounds = [row["complexity_rounds"] for row in rows if row["complexity_rounds"] is not None]
    return {
        "rows": rows,
        "mean_band_error": (sum(errs) / len(errs)) if errs else None,
        "in_range_share": (sum(1 for row in rows if row["in_strict_band"]) / len(rows)) if rows else None,
        "within_1_5": sum(1 for row in rows if row["within_1_5"]),
        "complexity_rounds_mean": (sum(rounds) / len(rounds)) if rounds else None,
    }


def _summarize_records(records: List[Dict[str, Any]], ablation_id: str) -> Dict[str, Any]:
    base = aggregate(records)
    terrain_retries = [r.get("terrain_retries", 0) for r in records]
    base["terrain_retries_mean"] = (
        sum(terrain_retries) / len(terrain_retries) if terrain_retries else None
    )
    if ablation_id == "A4":
        base["stamped_sections_total"] = sum(r.get("stamped_sections", 0) for r in records)
        base["stamped_prompts"] = sum(1 for r in records if r.get("stamped_sections", 0) > 0)
    if ablation_id == "A5":
        cx_info = _complexity_metrics(records)
        base["mean_band_error"] = cx_info["mean_band_error"]
        base["in_range_share"] = cx_info["in_range_share"]
        base["within_1_5"] = cx_info["within_1_5"]
        base["complexity_rounds_mean"] = cx_info["complexity_rounds_mean"]
        base["complexity_rows"] = cx_info["rows"]
    return base


def _compute_delta(baseline: Dict[str, Any], ablated: Dict[str, Any]) -> Dict[str, Any]:
    """Ablated minus Baseline for numeric fields, plus status count deltas."""
    delta: Dict[str, Any] = {}
    for k, b_val in baseline.items():
        if k not in ablated:
            continue
        a_val = ablated[k]
        if isinstance(b_val, (int, float)) and isinstance(a_val, (int, float)) and not isinstance(b_val, bool):
            d = a_val - b_val
            delta[k] = round(d, 6) if isinstance(d, float) else d
    b_sc = baseline.get("status_counts") or {}
    a_sc = ablated.get("status_counts") or {}
    if b_sc and a_sc:
        delta["status_counts"] = {k: a_sc.get(k, 0) - b_sc.get(k, 0) for k in b_sc}
    return delta


def _compact_prompt_view(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "verified": bool(r.get("verified")),
        "failure": (r.get("failure") or {}).get("type"),
        "fidelity_score": r.get("fidelity_score"),
        "status_counts": r.get("fidelity_counts"),
        "expect_failures": list(r.get("expect_failures") or []),
        "patches": r.get("patches", 0),
        "entities_filled": r.get("entities_filled", 0),
        "grid_repairs": r.get("grid_repairs", 0),
        "terrain_retries": r.get("terrain_retries", 0),
        "stamped_sections": r.get("stamped_sections", 0),
        "patterns_stamped": [p.get("id") for p in (r.get("patterns_stamped") or [])],
        "overall_difficulty": (r.get("difficulty") or {}).get("overall"),
        "complexity_rounds": r.get("complexity_rounds"),
        "wall_time_s": r.get("wall_time_s"),
    }


def _pair_prompts(
    baseline_recs: Sequence[Dict[str, Any]],
    ablated_recs: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    paired: List[Dict[str, Any]] = []
    by_key = {(r["recording"], r["id"]): r for r in ablated_recs}
    for b in baseline_recs:
        key = (b["recording"], b["id"])
        a = by_key.get(key)
        paired.append(
            {
                "id": b["id"],
                "recording": b["recording"],
                "baseline": _compact_prompt_view(b),
                "ablated": _compact_prompt_view(a) if a is not None else None,
            }
        )
    return paired


# ── ablation definitions ────────────────────────────────────────────────────


def _ablation_specs(
    stamp_finder_factory: Optional[Callable[[str], Any]],
) -> List[Dict[str, Any]]:
    a4_factory = stamp_finder_factory or (lambda prompt: StampFinder(prompt))
    return [
        {
            "id": "A1",
            "name": "no-SymbolicPatcher",
            "description": "Bypass LLMController._patch_spec and SymbolicPatcher.patch (no deterministic spec/graph first-aid).",
            "slices": [
                SuiteSlice("qwen-2026-10-16", tags=("structure",)),
                SuiteSlice("gemini-2026-10-19c", tags=("structure",)),
            ],
            "baseline_ctx": _no_op_context,
            "ablated_ctx": _no_symbolic_patcher,
            "baseline_stamp_factory": None,
            "ablated_stamp_factory": None,
            "focus_metrics": ["verified", "playable_share", "fidelity_mean", "patches_mean", "failures"],
        },
        {
            "id": "A2",
            "name": "no-CompletenessFill",
            "description": "Bypass pipeline.level.ensure_complete and fill_missing (no prompt-derived requirements, section retyping, or entity filling).",
            "slices": [
                SuiteSlice("gemini-2026-10-19c", tags=("structure",)),
                SuiteSlice("gemini-2026-10-15"),
            ],
            "baseline_ctx": _no_op_context,
            "ablated_ctx": _no_completeness_fill,
            "baseline_stamp_factory": None,
            "ablated_stamp_factory": None,
            "focus_metrics": [
                "verified",
                "playable_share",
                "fidelity_mean",
                "status_counts",
                "expect_failed",
                "entities_filled_mean",
            ],
        },
        {
            "id": "A3",
            "name": "no-GridAndSoftlockRepair",
            "description": "Bypass layout.repair.repair_grid (stepping-stone/ceiling/softlock repair) and set TERRAIN_ATTEMPTS = 1.",
            "slices": [
                SuiteSlice("gemini-2026-10-15"),
                SuiteSlice("gemini-2026-10-19c"),
                SuiteSlice("qwen-2026-10-16"),
            ],
            "baseline_ctx": _no_op_context,
            "ablated_ctx": _no_grid_and_softlock_repair,
            "baseline_stamp_factory": None,
            "ablated_stamp_factory": None,
            "focus_metrics": ["verified", "playable_share", "grid_repairs_mean", "failures"],
        },
        {
            "id": "A4",
            "name": "no-PatternStamping",
            "description": "Compare StampFinder retrieval stamping vs. procedural carvers only (stamp_finder=None).",
            "slices": [
                SuiteSlice("gemini-2026-10-19c", tags=("structure",)),
            ],
            "baseline_ctx": _no_op_context,
            "ablated_ctx": _no_op_context,
            "baseline_stamp_factory": a4_factory,
            "ablated_stamp_factory": None,
            "focus_metrics": [
                "stamped_sections_total",
                "stamped_prompts",
                "verified",
                "playable_share",
                "fidelity_mean",
            ],
        },
        {
            "id": "A5",
            "name": "no-ClosedLoopComplexity",
            "description": "Bypass pipeline.level._close_loop deterministic difficulty adjustment against complexity targets.",
            "slices": [
                SuiteSlice(
                    "gemini-2026-10-16",
                    prompt_ids=("v4-complexity-low", "v4-complexity-high"),
                    overrides={"v4-complexity-low": {"complexity": 0.3}, "v4-complexity-high": {"complexity": 0.8}},
                ),
                SuiteSlice(
                    "gemini-2026-10-19c",
                    prompt_ids=("structure-crypt",),
                    overrides={"structure-crypt": {"complexity": 0.75}},
                ),
            ],
            "baseline_ctx": _no_op_context,
            "ablated_ctx": _no_closed_loop_complexity,
            "baseline_stamp_factory": None,
            "ablated_stamp_factory": None,
            "focus_metrics": [
                "mean_band_error",
                "in_range_share",
                "within_1_5",
                "complexity_rounds_mean",
                "verified",
            ],
        },
    ]


def run_all_ablations(
    out_dir: Path | str = DEFAULT_OUT_DIR,
    *,
    recordings_root: Path = DEFAULT_RECORDINGS_ROOT,
    prompts_file: Path = PROMPTS_FILE,
    profile: Optional[GameProfile] = None,
    seed: int = 0,
    stamp_finder_factory: Optional[Callable[[str], Any]] = None,
    ablations: Optional[Sequence[str]] = None,
    log: Callable[[str], None] = print,
) -> Dict[str, Any]:
    """Run all (or selected) offline replay ablations and write ``summary.json`` + ``report.md``."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    ordered_prompts = load_prompts(prompts_file)
    prompts_by_id = {p["id"]: p for p in ordered_prompts}
    profile = profile or GameProfile()
    want = {a.upper() for a in ablations} if ablations else None

    specs = _ablation_specs(stamp_finder_factory)
    if want is not None:
        specs = [s for s in specs if s["id"] in want or s["name"].upper() in want]

    results: Dict[str, Any] = {}
    for spec in specs:
        aid = spec["id"]
        log(f"==> {aid}: {spec['name']}")
        base_recs = _run_condition(
            spec["slices"],
            recordings_root,
            prompts_by_id,
            ordered_prompts,
            profile,
            seed=seed,
            context_factory=spec["baseline_ctx"],
            stamp_finder_factory=spec["baseline_stamp_factory"],
        )
        abl_recs = _run_condition(
            spec["slices"],
            recordings_root,
            prompts_by_id,
            ordered_prompts,
            profile,
            seed=seed,
            context_factory=spec["ablated_ctx"],
            stamp_finder_factory=spec["ablated_stamp_factory"],
        )
        base_sum = _summarize_records(base_recs, aid)
        abl_sum = _summarize_records(abl_recs, aid)

        by_rec: Dict[str, Any] = {}
        seen_recs = list(dict.fromkeys(sl.recording for sl in spec["slices"]))
        for rec_name in seen_recs:
            br = [r for r in base_recs if r["recording"] == rec_name]
            ar = [r for r in abl_recs if r["recording"] == rec_name]
            bs = _summarize_records(br, aid)
            as_ = _summarize_records(ar, aid)
            by_rec[rec_name] = {
                "baseline": bs,
                "ablated": as_,
                "delta": _compute_delta(bs, as_),
            }

        results[aid] = {
            "id": aid,
            "name": spec["name"],
            "description": spec["description"],
            "recordings": seen_recs,
            "metrics": spec["focus_metrics"],
            "baseline": base_sum,
            "ablated": abl_sum,
            "delta": _compute_delta(base_sum, abl_sum),
            "by_recording": by_rec,
            "per_prompt": _pair_prompts(base_recs, abl_recs),
        }
        log(
            f"    baseline verified={base_sum['verified']}/{base_sum['prompts']}  "
            f"ablated verified={abl_sum['verified']}/{abl_sum['prompts']}"
        )

    summary = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": seed,
        "recordings_root": str(recordings_root),
        "ablations": results,
    }
    (out_path / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_path / "report.md").write_text(render_ablation_report(summary), encoding="utf-8")
    return summary


# ── markdown formatting ─────────────────────────────────────────────────────


def _fmt(val: Any, nd: int = 3) -> str:
    if val is None:
        return "-"
    if isinstance(val, bool):
        return "yes" if val else "no"
    if isinstance(val, float):
        return f"{val:.{nd}f}"
    return str(val)


def _fmt_pct(val: Optional[float]) -> str:
    return "-" if val is None else f"{100.0 * val:.1f}%"


def _fmt_delta(b: Any, a: Any, nd: int = 3, pct: bool = False) -> str:
    if b is None or a is None:
        return "-"
    d = a - b
    if pct:
        return f"{100.0 * d:+.1f} pp"
    if isinstance(d, float):
        return f"{d:+.{nd}f}"
    return f"{d:+d}"


def _fmt_failures(failures: Dict[str, int]) -> str:
    if not failures:
        return "none"
    return ", ".join(f"{k} x{v}" for k, v in sorted(failures.items()))


def _metric_row(label: str, b_val: Any, a_val: Any, *, pct: bool = False, nd: int = 3) -> str:
    if pct:
        return f"| {label} | {_fmt_pct(b_val)} | {_fmt_pct(a_val)} | {_fmt_delta(b_val, a_val, pct=True)} |"
    return f"| {label} | {_fmt(b_val, nd)} | {_fmt(a_val, nd)} | {_fmt_delta(b_val, a_val, nd=nd)} |"


def _render_a1_a2_a3_section(ab: Dict[str, Any]) -> List[str]:
    aid = ab["id"]
    b, a = ab["baseline"], ab["ablated"]
    lines = [
        "| Metric | Baseline | Ablated | Delta (Ablated - Baseline) |",
        "|---|---:|---:|---:|",
        _metric_row("Verified", b["verified"], a["verified"], nd=0),
        _metric_row("Playable share", b["playable_share"], a["playable_share"], pct=True),
        _metric_row("Fidelity mean", b["fidelity_mean"], a["fidelity_mean"], nd=3),
    ]
    if aid == "A1":
        lines.append(_metric_row("Patches mean", b.get("patches_mean"), a.get("patches_mean"), nd=2))
    elif aid == "A2":
        b_sc, a_sc = b["status_counts"], a["status_counts"]
        lines.append(_metric_row("Requirements satisfied", b_sc.get("satisfied", 0), a_sc.get("satisfied", 0), nd=0))
        lines.append(_metric_row("Requirements missing", b_sc.get("missing", 0), a_sc.get("missing", 0), nd=0))
        lines.append(_metric_row("Expectation failures", b["expect_failed"], a["expect_failed"], nd=0))
        lines.append(_metric_row("Entities filled mean", b.get("entities_filled_mean"), a.get("entities_filled_mean"), nd=2))
    elif aid == "A3":
        lines.append(_metric_row("Grid repairs mean", b.get("grid_repairs_mean"), a.get("grid_repairs_mean"), nd=2))
        lines.append(_metric_row("Terrain retries mean", b.get("terrain_retries_mean"), a.get("terrain_retries_mean"), nd=2))
    lines.append(f"| Failures | {_fmt_failures(b['failures'])} | {_fmt_failures(a['failures'])} | - |")

    if len(ab["by_recording"]) > 1:
        lines += [
            "",
            "#### By recording suite",
            "",
            "| Recording | N | Baseline verified | Ablated verified | Baseline fidelity | Ablated fidelity |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for rec_name, sub in ab["by_recording"].items():
            sb, sa = sub["baseline"], sub["ablated"]
            lines.append(
                f"| `{rec_name}` | {sb['prompts']} "
                f"| {sb['verified']}/{sb['prompts']} ({_fmt_pct(sb['playable_share'])}) "
                f"| {sa['verified']}/{sa['prompts']} ({_fmt_pct(sa['playable_share'])}) "
                f"| {_fmt(sb['fidelity_mean'], 3)} | {_fmt(sa['fidelity_mean'], 3)} |"
            )

    lines += [
        "",
        "#### Per prompt",
        "",
        "| Recording | Prompt | Baseline | Ablated | Notes |",
        "|---|---|---|---|---|",
    ]
    for p in ab["per_prompt"]:
        pb, pa = p["baseline"], p["ablated"] or {}
        b_str = f"ok (fid={_fmt(pb['fidelity_score'], 3)}, patches={pb['patches']})" if pb["verified"] else f"FAIL ({pb['failure']})"
        a_str = f"ok (fid={_fmt(pa.get('fidelity_score'), 3)}, patches={pa.get('patches', 0)})" if pa.get("verified") else f"FAIL ({pa.get('failure')})"
        notes_parts: List[str] = []
        if pb.get("expect_failures") != pa.get("expect_failures"):
            notes_parts.append(f"expect_fail: {pb.get('expect_failures')} -> {pa.get('expect_failures')}")
        if pb.get("grid_repairs") or pa.get("grid_repairs"):
            notes_parts.append(f"grid_repairs: {pb.get('grid_repairs')} -> {pa.get('grid_repairs')}")
        lines.append(f"| `{p['recording']}` | `{p['id']}` | {b_str} | {a_str} | {'; '.join(notes_parts) or '-'} |")
    return lines


def _render_a4_section(ab: Dict[str, Any]) -> List[str]:
    b, a = ab["baseline"], ab["ablated"]
    lines = [
        "| Metric | Baseline (`StampFinder`) | Ablated (`stamp_finder=None`) | Delta (Ablated - Baseline) |",
        "|---|---:|---:|---:|",
        _metric_row("Stamped sections total", b.get("stamped_sections_total"), a.get("stamped_sections_total"), nd=0),
        _metric_row("Stamped prompts", b.get("stamped_prompts"), a.get("stamped_prompts"), nd=0),
        _metric_row("Verified", b["verified"], a["verified"], nd=0),
        _metric_row("Playable share", b["playable_share"], a["playable_share"], pct=True),
        _metric_row("Fidelity mean", b["fidelity_mean"], a["fidelity_mean"], nd=3),
        "",
        "#### Per prompt",
        "",
        "| Prompt | Baseline verified | Baseline stamped | Patterns stamped | Ablated verified | Baseline fid | Ablated fid |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in ab["per_prompt"]:
        pb, pa = p["baseline"], p["ablated"] or {}
        pats = ", ".join(pb.get("patterns_stamped") or []) or "-"
        lines.append(
            f"| `{p['id']}` | {_fmt(pb['verified'])} | {pb['stamped_sections']} | `{pats}` "
            f"| {_fmt(pa.get('verified'))} | {_fmt(pb['fidelity_score'], 3)} | {_fmt(pa.get('fidelity_score'), 3)} |"
        )
    return lines


def _render_a5_section(ab: Dict[str, Any]) -> List[str]:
    b, a = ab["baseline"], ab["ablated"]
    lines = [
        "| Metric | Baseline (`_close_loop`) | Ablated (bypassed) | Delta (Ablated - Baseline) |",
        "|---|---:|---:|---:|",
        _metric_row("Mean band error", b.get("mean_band_error"), a.get("mean_band_error"), nd=3),
        _metric_row("In-range share (strict band)", b.get("in_range_share"), a.get("in_range_share"), pct=True),
        _metric_row("Within 1.5 count", b.get("within_1_5"), a.get("within_1_5"), nd=0),
        _metric_row("Complexity rounds mean", b.get("complexity_rounds_mean"), a.get("complexity_rounds_mean"), nd=2),
        _metric_row("Verified", b["verified"], a["verified"], nd=0),
        _metric_row("Playable share", b["playable_share"], a["playable_share"], pct=True),
        "",
        "#### Per prompt",
        "",
        "| Recording | Prompt | Complexity | Target band | Baseline overall | Baseline err | Baseline rounds | Ablated overall | Ablated err | Ablated rounds |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    b_rows = {(r["recording"], r["id"]): r for r in (b.get("complexity_rows") or [])}
    a_rows = {(r["recording"], r["id"]): r for r in (a.get("complexity_rows") or [])}
    for p in ab["per_prompt"]:
        key = (p["recording"], p["id"])
        rb = b_rows.get(key, {})
        ra = a_rows.get(key, {})
        band = f"[{_fmt(rb.get('target_min'), 1)}, {_fmt(rb.get('target_max'), 1)}]"
        lines.append(
            f"| `{p['recording']}` | `{p['id']}` | {_fmt(rb.get('complexity'), 2)} | {band} "
            f"| {_fmt(rb.get('measured_overall'), 1)} | {_fmt(rb.get('abs_band_error'), 2)} | {rb.get('complexity_rounds', 0)} "
            f"| {_fmt(ra.get('measured_overall'), 1)} | {_fmt(ra.get('abs_band_error'), 2)} | {ra.get('complexity_rounds', 0)} |"
        )
    return lines


def _overview_row(ab: Dict[str, Any]) -> str:
    aid = ab["id"]
    b, a = ab["baseline"], ab["ablated"]
    n = b["prompts"]
    if aid in ("A1", "A2", "A3"):
        metric = "Playable share"
        b_txt = f"{b['verified']}/{n} ({_fmt_pct(b['playable_share'])})"
        a_txt = f"{a['verified']}/{n} ({_fmt_pct(a['playable_share'])})"
        d_txt = _fmt_delta(b["playable_share"], a["playable_share"], pct=True)
        if aid == "A2":
            metric = "Playable / expect_failed"
            b_txt = f"{b['verified']}/{n}, expect_fail={b['expect_failed']}"
            a_txt = f"{a['verified']}/{n}, expect_fail={a['expect_failed']}"
            d_txt = f"expect_fail {a['expect_failed'] - b['expect_failed']:+d}"
    elif aid == "A4":
        metric = "Stamped sections / prompts"
        b_txt = f"{b.get('stamped_sections_total', 0)} ({b.get('stamped_prompts', 0)}/{n})"
        a_txt = f"{a.get('stamped_sections_total', 0)} ({a.get('stamped_prompts', 0)}/{n})"
        d_txt = _fmt_delta(b.get("stamped_sections_total"), a.get("stamped_sections_total"), nd=0)
    else:
        metric = "Mean band error / strict in-range"
        b_txt = f"err={_fmt(b.get('mean_band_error'), 2)} ({_fmt_pct(b.get('in_range_share'))})"
        a_txt = f"err={_fmt(a.get('mean_band_error'), 2)} ({_fmt_pct(a.get('in_range_share'))})"
        d_txt = _fmt_delta(b.get("mean_band_error"), a.get("mean_band_error"), nd=2)
    return f"| {aid} | `{ab['name']}` | {n} | {metric} | {b_txt} | {a_txt} | {d_txt} |"


def render_ablation_report(summary: Dict[str, Any]) -> str:
    """Render a thesis-ready Markdown report from ``summary``."""
    lines = [
        "# GLDtk Offline Replay Ablation Report",
        "",
        f"- Generated at: `{summary.get('generated_at', '-')}`",
        f"- Seed: `{summary.get('seed', 0)}`",
        "",
        "## Overview",
        "",
        "| ID | Ablation | Prompts | Primary Metric | Baseline | Ablated | Delta |",
        "|---|---|---:|---|---|---|---|",
    ]
    ablations = summary.get("ablations") or {}
    for ab in ablations.values():
        lines.append(_overview_row(ab))

    for ab in ablations.values():
        lines += [
            "",
            f"## {ab['id']}: `{ab['name']}`",
            "",
            ab["description"],
            "",
        ]
        if ab["id"] == "A4":
            lines += _render_a4_section(ab)
        elif ab["id"] == "A5":
            lines += _render_a5_section(ab)
        else:
            lines += _render_a1_a2_a3_section(ab)

    return "\n".join(lines) + "\n"


# ── CLI ─────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m eval.ablation",
        description="Run deterministic offline replay ablations (A1-A5) on recorded LLM responses.",
    )
    ap.add_argument(
        "--out",
        default=str(DEFAULT_OUT_DIR),
        help="Output folder for summary.json and report.md (default: eval/results/ablations)",
    )
    ap.add_argument(
        "--only",
        help="Comma-separated ablation IDs or names to run (e.g. A1,A5)",
    )
    ap.add_argument("--seed", type=int, default=0, help="Deterministic level layout seed (default: 0)")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    only = [x.strip() for x in args.only.split(",") if x.strip()] if args.only else None
    summary = run_all_ablations(Path(args.out), seed=args.seed, ablations=only)
    out = Path(args.out)
    print(f"Wrote {len(summary['ablations'])} ablation(s) to {out / 'summary.json'} and {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
