"""Evaluation harness CLI: run a prompt suite through ``pipeline.build_level``.

    python -m eval.run --provider gemini [--model M] [--tags t1,t2] [--limit N] [--seed S]
        [--profile path] [--out eval/results/<timestamp>] [--max-calls N] [--sleep SEC]
        [--replay DIR | --record DIR] [--export]
    python -m eval.run --compare RESULTS_A RESULTS_B

See ``eval/README.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.metrics import compare_folders, load_records, render_report, summarize  # noqa: E402
from eval.providers import (  # noqa: E402
    BudgetExceeded,
    CallBudget,
    CountingProvider,
    RecordingProvider,
    ReplayMissing,
    ReplayProvider,
    is_quota_error,
    read_meta,
    write_meta,
)
from game.profile import GameProfile, load_profile  # noqa: E402
from llm.validator import LLMController, MaxRetriesExceeded  # noqa: E402
from pipeline import LevelBuildFailed, build_level  # noqa: E402

PROMPTS_FILE = Path(__file__).resolve().parent / "prompts.json"

# Failure types that are retried when a run is resumed (the cause was external).
RETRY_ON_RESUME = ("quota", "budget")


# ── prompts ─────────────────────────────────────────────────────────────────


def load_prompts(
    path: Path = PROMPTS_FILE,
    tags: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Prompts from the JSON suite; *tags* keeps prompts having any of them."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    prompts = data["prompts"] if isinstance(data, dict) else data
    ids = [p["id"] for p in prompts]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate prompt ids in the suite")
    if tags:
        want = set(tags)
        prompts = [p for p in prompts if want & set(p.get("tags", []))]
    return prompts[:limit] if limit else prompts


# ── one prompt ──────────────────────────────────────────────────────────────


def classify_failure(exc: BaseException) -> str:
    if isinstance(exc, BudgetExceeded):
        return "budget"
    if isinstance(exc, MaxRetriesExceeded):
        return "MaxRetriesExceeded"
    if isinstance(exc, LevelBuildFailed):
        return "LevelBuildFailed"
    if isinstance(exc, ReplayMissing):
        return "replay_missing"
    if is_quota_error(exc):
        return "quota"
    return type(exc).__name__


def check_expectations(expect: Dict[str, Any], built: Any) -> List[str]:
    """Extra per-prompt assertions (see eval/README.md); returns the failures."""
    out: List[str] = []
    spec = built.spec
    if "min_sections" in expect and len(spec.sections) < expect["min_sections"]:
        out.append(f"sections {len(spec.sections)} < {expect['min_sections']}")
    if "min_width_tiles" in expect and spec.size.width_tiles < expect["min_width_tiles"]:
        out.append(f"width {spec.size.width_tiles} < {expect['min_width_tiles']}")
    if "min_height_tiles" in expect and spec.size.height_tiles < expect["min_height_tiles"]:
        out.append(f"height {spec.size.height_tiles} < {expect['min_height_tiles']}")
    kinds = {e.kind for e in spec.entities}
    for k in expect.get("entity_kinds", []):
        if k not in kinds:
            out.append(f"no {k} entity")
    if "min_impossible" in expect:
        n = built.fidelity.counts()["impossible"] if built.fidelity else 0
        if n < expect["min_impossible"]:
            out.append(f"impossible items {n} < {expect['min_impossible']}")
    return out


def run_prompt(
    entry: Dict[str, Any],
    provider: Any,
    profile: GameProfile,
    *,
    seed: int = 0,
    max_retries: int = 3,
    budget_slept: Callable[[], float] = lambda: 0.0,
    export_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build one level and return its result record (never raises, except BudgetExceeded)."""
    counted: CountingProvider = provider
    controller = LLMController(profile=profile, provider=counted, max_retries=max_retries)
    rec: Dict[str, Any] = {
        "id": entry["id"],
        "tags": entry.get("tags", []),
        "prompt": entry["prompt"],
        "seed": seed,
        "verified": False,
        "failure": None,
        "fidelity_score": None,
        "fidelity_counts": None,
        "fidelity_items": [],
        "expect_failures": [],
    }
    slept0 = budget_slept()
    t0 = time.perf_counter()
    stats: Dict[str, Any] = {}
    try:
        built = build_level(entry["prompt"], controller, profile, seed=seed)
    except BudgetExceeded:
        raise
    except Exception as exc:  # a failed prompt is a result, not a crash
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
        rec["warnings"] = len(built.warnings)
        rec["size_tiles"] = [built.spec.size.width_tiles, built.spec.size.height_tiles]
        rec["sections"] = len(built.spec.sections)
        if export_dir is not None:
            try:
                from layout.ldtk_adapter import to_ldtk_dict

                export_dir.mkdir(parents=True, exist_ok=True)
                doc = to_ldtk_dict(built.spec_layout.ir)
                (export_dir / f"{entry['id']}.ldtk").write_text(json.dumps(doc), encoding="utf-8")
            except Exception as exc:
                rec["export_error"] = f"{type(exc).__name__}: {exc}"
    rec["wall_time_s"] = round(time.perf_counter() - t0 - (budget_slept() - slept0), 4)
    rec["llm_calls"] = counted.calls
    rec["llm_attempts"] = stats.get("llm_attempts", 0)
    rec["patches"] = len(stats.get("patches_applied", []))
    rec["patches_applied"] = list(stats.get("patches_applied", []))
    for k in ("completeness_reprompts", "entities_filled", "layout_retries", "grid_repairs"):
        rec[k] = stats.get(k, 0)
    return rec


# ── a whole run ─────────────────────────────────────────────────────────────


def run_suite(
    prompts: List[Dict[str, Any]],
    base_provider_for: Callable[[str], Any],
    out_dir: Path,
    *,
    provider_name: str,
    model: Optional[str],
    profile: Optional[GameProfile] = None,
    seed: int = 0,
    max_retries: int = 3,
    max_calls: Optional[int] = None,
    sleep: float = 0.0,
    record_dir: Optional[Path] = None,
    export: bool = False,
    keep_going: bool = False,
    log: Callable[[str], None] = print,
) -> List[Dict[str, Any]]:
    """Run *prompts*, appending one line per prompt to ``out_dir/results.jsonl``.

    Resumes: prompts that already have a result there (other than a ``quota``
    or ``budget`` failure) are skipped.  Stops early when ``max_calls`` is
    reached (the unfinished prompt is not recorded) or, unless *keep_going*,
    after the first ``quota`` failure.  Returns all records of the folder.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = out_dir / "results.jsonl"
    profile = profile or GameProfile()
    existing = load_records(out_dir)
    done = {r["id"] for r in existing if (r.get("failure") or {}).get("type") not in RETRY_ON_RESUME}
    if len(done) != len(existing):  # drop retryable records; they are re-run below
        keep = [r for r in existing if r["id"] in done]
        results.write_text("".join(json.dumps(r) + "\n" for r in keep), encoding="utf-8")
    if record_dir:
        write_meta(record_dir, provider_name, model)
    budget = CallBudget(max_calls, sleep)

    for entry in prompts:
        if entry["id"] in done:
            log(f"skip  {entry['id']} (already in {results.name})")
            continue
        if budget.exhausted:
            log(f"stop  --max-calls {max_calls} reached before {entry['id']}")
            break
        inner = base_provider_for(entry["id"])
        if record_dir:
            inner = RecordingProvider(inner, record_dir, entry["id"])
        counted = CountingProvider(inner, budget)
        try:
            rec = run_prompt(
                entry,
                counted,
                profile,
                seed=seed,
                max_retries=max_retries,
                budget_slept=lambda: budget.slept,
                export_dir=(out_dir / "ldtk") if export else None,
            )
        except BudgetExceeded:
            budget.blocked = True
        if budget.blocked:  # also when the pipeline swallowed the refusal (re-prompt)
            log(f"stop  --max-calls {max_calls} reached during {entry['id']} (not recorded)")
            break
        rec["provider"] = provider_name
        # the requested model, else the one(s) that answered (Gemini falls back on 429/503)
        rec["model"] = model or ",".join(counted.models_used) or None
        with results.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        state = "ok" if rec["verified"] else (rec["failure"] or {}).get("type", "failed")
        log(f"{state:<18} {entry['id']}  fidelity={rec['fidelity_score']}  calls={rec['llm_calls']}")
        if (rec.get("failure") or {}).get("type") == "quota" and not keep_going:
            log("stop  quota exhausted (use --keep-going to continue); resume later with the same --out")
            break
    return write_outputs(out_dir)


def write_outputs(out_dir: Path) -> List[Dict[str, Any]]:
    """Rebuild summary.json and report.md from results.jsonl."""
    records = load_records(out_dir)
    summary = summarize(records)
    (Path(out_dir) / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (Path(out_dir) / "report.md").write_text(render_report(records, summary), encoding="utf-8")
    return records


# ── CLI ─────────────────────────────────────────────────────────────────────


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m eval.run", description=__doc__.split("\n")[0])
    ap.add_argument("--provider", help="gemini | anthropic | ollama | openai | deepseek")
    ap.add_argument("--model")
    ap.add_argument("--tags", help="comma-separated; keeps prompts with any of these tags")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--profile", help="path to a gldtk.game.json (default: the default profile)")
    ap.add_argument("--out", help="result folder (default eval/results/<timestamp>); reuse to resume")
    ap.add_argument("--max-calls", type=int, help="stop before more LLM calls than this are spent")
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds to pause between LLM calls")
    ap.add_argument("--retries", type=int, default=3, help="controller max_retries")
    ap.add_argument("--prompts", help="alternative prompt-suite JSON file")
    ap.add_argument("--export", action="store_true", help="also write <out>/ldtk/<id>.ldtk")
    ap.add_argument("--keep-going", action="store_true", help="do not stop at the first 429")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--record", metavar="DIR", help="save every raw LLM response under DIR")
    g.add_argument("--replay", metavar="DIR", help="answer from responses recorded under DIR (no LLM)")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"), help="diff two result folders")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.compare:
        print(compare_folders(Path(args.compare[0]), Path(args.compare[1])))
        return 0

    provider_name, model = args.provider, args.model
    if args.replay:
        meta = read_meta(Path(args.replay))
        provider_name = provider_name or meta.get("provider") or "replay"
        model = model or meta.get("model")
        base = lambda pid: ReplayProvider(Path(args.replay), pid, name=provider_name)  # noqa: E731
    else:
        if not provider_name:
            print("error: --provider is required (or use --replay / --compare)", file=sys.stderr)
            return 2
        from llm.providers import make_provider

        _load_dotenv()
        shared = make_provider(provider_name, model=model)
        base = lambda pid: shared  # noqa: E731

    out = Path(args.out) if args.out else ROOT / "eval" / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    prompts = load_prompts(
        Path(args.prompts) if args.prompts else PROMPTS_FILE,
        tags=[t for t in (args.tags or "").split(",") if t],
        limit=args.limit,
    )
    profile = load_profile(args.profile) if args.profile else GameProfile()
    print(f"{len(prompts)} prompt(s) -> {out}")
    records = run_suite(
        prompts,
        base,
        out,
        provider_name=provider_name,
        model=model,
        profile=profile,
        seed=args.seed,
        max_retries=args.retries,
        max_calls=args.max_calls,
        sleep=0.0 if args.replay else args.sleep,
        record_dir=Path(args.record) if args.record else None,
        export=args.export,
        keep_going=args.keep_going,
    )
    print(f"{len(records)} result(s); report: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
