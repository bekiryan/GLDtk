"""Aggregation, markdown report and diff of result folders (pure, no pipeline imports)."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

STATUSES = ("satisfied", "approximated", "impossible", "missing", "unchecked")
MEAN_FIELDS = (
    "llm_attempts",
    "llm_calls",
    "patches",
    "completeness_reprompts",
    "entities_filled",
    "layout_retries",
    "grid_repairs",
)


def _mean(xs: Iterable[float]) -> Optional[float]:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def p95(xs: Iterable[float]) -> Optional[float]:
    """Nearest-rank 95th percentile."""
    xs = sorted(xs)
    if not xs:
        return None
    return xs[max(0, math.ceil(0.95 * len(xs)) - 1)]


def load_records(folder: Path) -> List[Dict[str, Any]]:
    path = Path(folder) / "results.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Metrics for one provider/model group (see docs/v2/parts/10-evaluation.md)."""
    n = len(records)
    verified = [r for r in records if r.get("verified")]
    scores = [r["fidelity_score"] for r in records if r.get("fidelity_score") is not None]
    statuses = Counter()
    for r in records:
        for k, v in (r.get("fidelity_counts") or {}).items():
            statuses[k] += v
    walls = [r["wall_time_s"] for r in records if r.get("wall_time_s") is not None]
    out: Dict[str, Any] = {
        "prompts": n,
        "fidelity_mean": _mean(scores),
        "fidelity_scored": len(scores),
        "status_counts": {s: statuses.get(s, 0) for s in STATUSES},
        "verified": len(verified),
        "playable_share": (len(verified) / n) if n else None,
        "failures": dict(Counter(r["failure"]["type"] for r in records if r.get("failure"))),
        "wall_mean_s": _mean(walls),
        "wall_p95_s": p95(walls),
        "llm_calls_total": sum(r.get("llm_calls", 0) for r in records),
        "expect_failed": sum(1 for r in records if r.get("expect_failures")),
        "difficulty_by_label": difficulty_by_label(records),
    }
    for f in MEAN_FIELDS:
        out[f + "_mean"] = _mean(r[f] for r in records if r.get(f) is not None)
    return out


def difficulty_by_label(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Section difficulty scores (v3 Part 15a) grouped by the section's label: n, mean, min, max."""
    scores: Dict[str, List[float]] = defaultdict(list)
    for r in records:
        for s in (r.get("difficulty") or {}).get("sections") or []:
            if s.get("score") is not None:
                scores[s.get("label", "?")].append(float(s["score"]))
    order = {"easy": 0, "medium": 1, "hard": 2}
    return {
        label: {"n": len(xs), "mean": _mean(xs), "min": min(xs), "max": max(xs)}
        for label, xs in sorted(scores.items(), key=lambda kv: order.get(kv[0], 9))
    }


def group_key(r: Dict[str, Any]) -> str:
    return f"{r.get('provider', '?')}/{r.get('model') or 'default'}"


def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        groups[group_key(r)].append(r)
    return {k: aggregate(v) for k, v in sorted(groups.items())}


def worst_requirements(records: List[Dict[str, Any]], top: int = 10) -> List[Dict[str, Any]]:
    """Requirements most often missing / approximated / impossible, by (category, text)."""
    bad: Counter = Counter()
    total: Counter = Counter()
    for r in records:
        for it in r.get("fidelity_items") or []:
            key = (it["category"], " ".join(it["text"].lower().split()))
            total[key] += 1
            if it["status"] in ("missing", "approximated", "impossible"):
                bad[(key, it["status"])] += 1
    rows = []
    for key in total:
        counts = {s: bad.get((key, s), 0) for s in ("missing", "approximated", "impossible")}
        n_bad = sum(counts.values())
        if n_bad:
            rows.append({"category": key[0], "text": key[1], "seen": total[key], "bad": n_bad, **counts})
    rows.sort(key=lambda x: (-x["bad"], -x["missing"], x["text"]))
    return rows[:top]


# ── formatting ──────────────────────────────────────────────────────────────


def _f(x: Any, nd: int = 2) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def render_report(records: List[Dict[str, Any]], summary: Optional[Dict[str, Any]] = None) -> str:
    summary = summary if summary is not None else summarize(records)
    lines = ["# GLDtk evaluation report", ""]
    lines += [
        "## Summary",
        "",
        "| provider/model | prompts | fidelity | playable | LLM calls | attempts | patches | re-prompts | filled | layout retries | repairs | wall mean | wall p95 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for key, s in summary.items():
        lines.append(
            f"| {key} | {s['prompts']} | {_f(s['fidelity_mean'])} | {_pct(s['playable_share'])} "
            f"({s['verified']}/{s['prompts']}) | {s['llm_calls_total']} | {_f(s['llm_attempts_mean'])} "
            f"| {_f(s['patches_mean'])} | {_f(s['completeness_reprompts_mean'])} | {_f(s['entities_filled_mean'])} "
            f"| {_f(s['layout_retries_mean'])} | {_f(s['grid_repairs_mean'])} | {_f(s['wall_mean_s'])}s "
            f"| {_f(s['wall_p95_s'])}s |"
        )
    lines.append("")
    for key, s in summary.items():
        sc = s["status_counts"]
        lines.append(
            f"- **{key}** fidelity items: "
            + ", ".join(f"{k} {v}" for k, v in sc.items())
            + "; failures: "
            + (", ".join(f"{k} x{v}" for k, v in s["failures"].items()) or "none")
            + f"; expectation failures: {s['expect_failed']}"
        )
    diff_rows = [(key, label, d) for key, s in summary.items()
                 for label, d in (s.get("difficulty_by_label") or {}).items()]
    if diff_rows:
        lines += ["", "## Difficulty by section label", "",
                  "| provider/model | label | sections | mean score | min | max |", "|---|---|---|---|---|---|"]
        for key, label, d in diff_rows:
            lines.append(f"| {key} | {label} | {d['n']} | {_f(d['mean'], 1)} | {_f(d['min'], 1)} | {_f(d['max'], 1)} |")
    lines += ["", "## Per prompt", "", "| prompt | tags | status | fidelity | calls | wall | notes |", "|---|---|---|---|---|---|---|"]
    for r in records:
        status = "ok" if r.get("verified") else (r.get("failure") or {}).get("type", "failed")
        notes = "; ".join(r.get("expect_failures") or [])
        lines.append(
            f"| {r['id']} | {', '.join(r.get('tags', []))} | {status} | {_f(r.get('fidelity_score'))} "
            f"| {r.get('llm_calls', 0)} | {_f(r.get('wall_time_s'), 1)}s | {notes} |"
        )
    worst = worst_requirements(records)
    lines += ["", "## Worst requirements", ""]
    if worst:
        lines += ["| category | requirement | seen | missing | approximated | impossible |", "|---|---|---|---|---|---|"]
        for w in worst:
            lines.append(
                f"| {w['category']} | {w['text']} | {w['seen']} | {w['missing']} | {w['approximated']} | {w['impossible']} |"
            )
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


# ── compare ─────────────────────────────────────────────────────────────────

_COMPARE_ROWS: List[Tuple[str, str]] = [
    ("fidelity_mean", "fidelity"),
    ("playable_share", "playable share"),
    ("llm_calls_total", "LLM calls"),
    ("llm_attempts_mean", "attempts"),
    ("patches_mean", "patches"),
    ("completeness_reprompts_mean", "re-prompts"),
    ("entities_filled_mean", "entities filled"),
    ("layout_retries_mean", "layout retries"),
    ("grid_repairs_mean", "grid repairs"),
    ("wall_mean_s", "wall mean s"),
    ("wall_p95_s", "wall p95 s"),
]


def _delta(a: Any, b: Any) -> str:
    if a is None or b is None:
        return "-"
    d = b - a
    return f"{d:+.3f}" if isinstance(d, float) else f"{d:+d}"


def compare_folders(a: Path, b: Path) -> str:
    """Markdown diff of two result folders: metric deltas and per-prompt changes."""
    ra, rb = load_records(a), load_records(b)
    sa, sb = aggregate(ra), aggregate(rb)
    lines = [f"# Compare {a} (A) vs {b} (B)", "", "| metric | A | B | B - A |", "|---|---|---|---|"]
    for key, label in _COMPARE_ROWS:
        lines.append(f"| {label} | {_f(sa[key], 3)} | {_f(sb[key], 3)} | {_delta(sa[key], sb[key])} |")
    lines += ["", "## Per prompt", "", "| prompt | A | B | change |", "|---|---|---|---|"]
    ia = {r["id"]: r for r in ra}
    ib = {r["id"]: r for r in rb}

    def cell(r: Optional[Dict[str, Any]]) -> str:
        if r is None:
            return "(absent)"
        if not r.get("verified"):
            return (r.get("failure") or {}).get("type", "failed")
        return _f(r.get("fidelity_score"))

    for pid in list(ia) + [i for i in ib if i not in ia]:
        x, y = ia.get(pid), ib.get(pid)
        sx = x.get("fidelity_score") if x and x.get("verified") else None
        sy = y.get("fidelity_score") if y and y.get("verified") else None
        if x and y and (bool(x.get("verified")) != bool(y.get("verified"))):
            change = "now playable" if y.get("verified") else "REGRESSED (no longer playable)"
        elif sx is not None and sy is not None:
            change = _delta(sx, sy) if abs(sy - sx) > 1e-9 else "same"
        else:
            change = "same" if cell(x) == cell(y) else "changed"
        lines.append(f"| {pid} | {cell(x)} | {cell(y)} | {change} |")
    return "\n".join(lines) + "\n"
