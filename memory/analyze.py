"""Statistics and rules learned from the event log.

* :func:`compute_stats` - deterministic statistics over the newest events.
* :func:`stat_rules` - rules derived from the statistics (``source: "stat"``).
* :func:`summarize` - optional LLM summary into at most :data:`MAX_RULES` rules
  (``source: "llm"``), through the provider layer; ``maybe_summarize`` runs it once per
  :data:`store.SUMMARY_EVERY` new events.
* :func:`refresh` - recompute statistics and merge the stat rules into ``preferences.json``.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from typing import Any, Dict, List, Optional

from memory import store

MAX_RULES = 10
MIN_SAMPLES = 3  # a statistic needs this many observations before it becomes a rule or a nudge
MAX_RULE_CHARS = 160


def _median(xs: List[float]) -> Optional[float]:
    return round(float(statistics.median(xs)), 1) if xs else None


def _confidence(n: int) -> float:
    return round(min(0.95, 0.4 + 0.1 * n), 2)


def compute_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Statistics over *events* (see the keys below; absent data gives None / empty)."""
    hand = [e for e in events if e.get("type") == "hand_edit"]
    gaps_user, gaps_gldtk, spacing, ratios = [], [], [], []
    removed, added = Counter(), Counter()
    fields: Dict[str, Dict[str, Counter]] = {}
    painted = erased = 0
    for e in hand:
        t = e.get("tiles") or {}
        painted += t.get("painted", 0)
        erased += t.get("erased", 0)
        gaps_user += t.get("gaps_created", [])
        gaps_gldtk += t.get("gaps_before", [])
        if t.get("platforms_before") and t.get("platforms_after") is not None:
            ratios.append(t["platforms_after"] / t["platforms_before"])
        if e.get("enemy_spacing") is not None:
            spacing.append(e["enemy_spacing"])
        removed.update(x["kind"] for x in e.get("removed", []))
        added.update(x["kind"] for x in e.get("added", []))
        for ch in e.get("changed", []):
            if ch.get("to") is not None:
                key = json.dumps(ch["to"])
                fields.setdefault(ch["kind"], {}).setdefault(ch["field"], Counter())[key] += 1

    edits, undone = Counter(), Counter()
    for e in events:
        if e.get("type") == "edited":
            edits[e.get("mode", "?")] += 1
        elif e.get("type") == "undone":
            undone[e.get("mode", "?")] += 1
    regen = Counter()
    for e in events:
        if e.get("type") == "regenerated":
            for s in e.get("sections", []):
                regen[str(s.get("style") or s.get("theme") or s.get("name") or "?")] += 1

    return {
        "events": len(events),
        "by_type": dict(Counter(e.get("type", "?") for e in events)),
        "hand_edits": len(hand),
        "gap": {"user_median": _median(gaps_user), "user_n": len(gaps_user),
                "gldtk_median": _median(gaps_gldtk), "gldtk_n": len(gaps_gldtk)},
        "enemy_spacing_median": _median(spacing),
        "enemy_spacing_n": len(spacing),
        "platform_ratio_median": round(float(statistics.median(ratios)), 2) if ratios else None,
        "platform_ratio_n": len(ratios),
        "tiles_painted": painted,
        "tiles_erased": erased,
        "removed_kinds": dict(removed),
        "added_kinds": dict(added),
        "field_values": {k: {f: dict(c) for f, c in v.items()} for k, v in fields.items()},
        "edits_by_mode": dict(edits),
        "undone_by_mode": dict(undone),
        "undo_rate": {m: round(undone[m] / edits[m], 2) for m in edits if edits[m]},
        "regenerated_structures": dict(regen),
    }


def typical_field_value(stats: Dict[str, Any], kind: str, field: str) -> Any:
    """The value the user most often sets *field* of *kind* to (>= MIN_SAMPLES, a clear
    majority), else None."""
    counts = (stats.get("field_values") or {}).get(kind, {}).get(field)
    if not counts:
        return None
    key, n = max(counts.items(), key=lambda kv: kv[1])
    return json.loads(key) if n >= MIN_SAMPLES and n * 2 > sum(counts.values()) else None


def stat_rules(stats: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Rules the statistics support; ids are stable so edits and dismissals stick."""
    rules: List[Dict[str, Any]] = []

    def add(rid: str, text: str, n: int) -> None:
        rules.append({"id": rid, "text": text[:MAX_RULE_CHARS], "confidence": _confidence(n),
                      "source": "stat"})

    g = stats["gap"]
    if g["user_n"] >= MIN_SAMPLES and g["user_median"] is not None and g["gldtk_median"] is not None:
        if abs(g["user_median"] - g["gldtk_median"]) >= 1:
            word = "wider" if g["user_median"] > g["gldtk_median"] else "narrower"
            add("gap", f"Prefers {word} gaps between platforms: about {g['user_median']:g} tiles "
                       f"(generated levels use about {g['gldtk_median']:g}).", g["user_n"])
    if stats["enemy_spacing_n"] >= MIN_SAMPLES and stats["enemy_spacing_median"] is not None:
        add("enemy_spacing", f"Keeps enemies about {stats['enemy_spacing_median']:g} tiles apart.",
            stats["enemy_spacing_n"])
    for kind, n in stats["removed_kinds"].items():
        if n >= MIN_SAMPLES:
            add(f"removes_{kind}", f"Often removes {kind} entities by hand; use fewer.", n)
    for kind, n in stats["added_kinds"].items():
        if n >= MIN_SAMPLES:
            add(f"adds_{kind}", f"Often adds {kind} entities by hand; use more.", n)
    for kind in stats["field_values"]:
        for field in stats["field_values"][kind]:
            v = typical_field_value(stats, kind, field)
            if v is not None:
                n = max(stats["field_values"][kind][field].values())
                add(f"field_{kind}_{field}", f"Sets {kind} {field} to {v} by hand.", n)
    for mode, rate in stats["undo_rate"].items():
        n = stats["undone_by_mode"].get(mode, 0)
        if n >= MIN_SAMPLES and rate >= 0.4:
            add(f"undo_{mode}", f"Often undoes {mode} edits ({int(rate * 100)}%); make them more "
                               "conservative.", n)
    for style, n in stats["regenerated_structures"].items():
        if n >= MIN_SAMPLES:
            add(f"regen_{style}", f"Regenerates levels with '{style}' sections often; avoid "
                                  "that structure.", n)
    return rules


def refresh() -> Dict[str, Any]:
    """Recompute the statistics and merge the stat rules into ``preferences.json``."""
    with store._lock:
        prefs = store.load_prefs()
        stats = compute_stats(store.read_events())
        prefs["stats"] = stats
        dismissed = set(prefs.get("dismissed", []))
        rules = [r for r in prefs.get("rules", []) if isinstance(r, dict)]
        by_id = {r.get("id"): r for r in rules}
        live = set()
        for r in stat_rules(stats):
            live.add(r["id"])
            if r["id"] in dismissed:
                continue
            cur = by_id.get(r["id"])
            if cur is None:
                rules.append(r)
            elif cur.get("source") == "stat":
                cur.update(text=r["text"], confidence=r["confidence"])
        # a stat rule whose evidence is gone (the user changed their mind) is dropped
        rules = [r for r in rules if r.get("source") != "stat" or r.get("id") in live]
        prefs["rules"] = rules
        store.save_prefs(prefs)
        return prefs


SUMMARY_SYSTEM = (
    "You summarise a level designer's habits from compact event statistics. Output ONE JSON "
    'object {"rules": [{"text": str, "confidence": number}]} with at most 10 short rules '
    "(each under 140 characters, imperative, about level design only) and a confidence in 0..1 "
    "that reflects how consistent the evidence is. Output ONLY the JSON object."
)


def summarize(provider: Any) -> List[Dict[str, Any]]:
    """Ask *provider* for <= 10 rules and store them (replacing earlier ``llm`` rules)."""
    prefs = store.load_prefs()
    if not prefs.get("enabled", True):
        return []
    events = store.read_events(60)
    stats = prefs.get("stats") or compute_stats(events)
    payload = {"statistics": stats, "recent_events": events[-30:]}
    schema = {"type": "object", "properties": {"rules": {"type": "array", "items": {
        "type": "object", "properties": {"text": {"type": "string"}, "confidence": {"type": "number"}},
        "required": ["text", "confidence"]}}}, "required": ["rules"]}
    raw = provider.complete_json(SUMMARY_SYSTEM, [{"role": "user", "content": json.dumps(payload)}],
                                 schema)
    from llm.validator import _extract_json

    data = json.loads(_extract_json(raw))
    new = []
    for i, r in enumerate((data.get("rules") or [])[:MAX_RULES]):
        text = str(r.get("text", "")).strip()[:MAX_RULE_CHARS]
        if text:
            conf = max(0.0, min(1.0, float(r.get("confidence", 0.5))))
            new.append({"id": f"llm{i + 1}", "text": text, "confidence": round(conf, 2), "source": "llm"})
    with store._lock:
        prefs = store.load_prefs()
        keep = [r for r in prefs.get("rules", []) if r.get("source") != "llm"]
        prefs["rules"] = keep + new
        prefs["events_since_summary"] = 0
        store.save_prefs(prefs)
    return new


def maybe_summarize(provider: Any) -> bool:
    """Run :func:`summarize` once per ``SUMMARY_EVERY`` new events; never raises."""
    try:
        if provider is None or store.load_prefs().get("events_since_summary", 0) < store.SUMMARY_EVERY:
            return False
        summarize(provider)
        return True
    except Exception:
        prefs = store.load_prefs()
        prefs["events_since_summary"] = 0  # do not retry on every request
        store.save_prefs(prefs)
        return False
