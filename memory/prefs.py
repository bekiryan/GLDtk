"""Using what was learned: the prompt block and the bounded deterministic nudges.

Everything here is a no-op while memory is off.  Nudges are logged through
:func:`memory.hooks.note` (they end up in the response ``warnings``).
"""

from __future__ import annotations

from typing import Any, List, Tuple

from memory import analyze, store

BLOCK_HEADER = "DESIGNER PREFERENCES (learned from this designer's past changes; the request wins):"
BLOCK_MAX_CHARS = 600
MIN_RULE_CONFIDENCE = 0.7

COMFORT_MAX_SHIFT = 2  # comfort_hop_tiles moves at most this far from the profile's value
DENSITY_RANGE = (0.8, 1.25)  # multiplier on the number of fill platforms
FIELD_MAX = 99  # a nudged integer entity default stays within 0..FIELD_MAX


def preferences_block() -> str:
    """The "Designer preferences (learned)" prompt block, or "" (off / nothing confident)."""
    prefs = store.load_prefs()
    if not prefs.get("enabled", True):
        return ""
    rules = sorted((r for r in prefs.get("rules", [])
                    if isinstance(r, dict) and r.get("text") and r.get("enabled", True)
                    and float(r.get("confidence", 0)) >= MIN_RULE_CONFIDENCE),
                   key=lambda r: -float(r["confidence"]))
    text = BLOCK_HEADER
    for r in rules:
        line = f"\n- {r['text']}"
        if len(text) + len(line) > BLOCK_MAX_CHARS:
            break
        text += line
    return text if text != BLOCK_HEADER else ""


def with_block(content: str) -> str:
    """*content* followed by the preferences block (unchanged when there is none)."""
    block = preferences_block()
    return f"{content}\n\n{block}" if block else content


def _stats() -> dict:
    prefs = store.load_prefs()
    return prefs.get("stats") or {} if prefs.get("enabled", True) else {}


def _note(msg: str) -> None:
    from memory import hooks

    hooks.note(msg)


def nudge_profile(profile: Any) -> Any:
    """A copy of *profile* whose comfortable hop moves towards the user's median gap (at most
    ``COMFORT_MAX_SHIFT`` tiles, never below 1); the original is never mutated or written."""
    g = _stats().get("gap") or {}
    if g.get("user_n", 0) < analyze.MIN_SAMPLES or g.get("user_median") is None:
        return profile
    base = profile.design.comfort_hop_tiles
    target = max(base - COMFORT_MAX_SHIFT, min(base + COMFORT_MAX_SHIFT, round(g["user_median"])))
    target = max(1, int(target))
    if target == base:
        return profile
    out = profile.model_copy(deep=True)
    out.design.comfort_hop_tiles = target
    _note(f"Design memory: comfortable hop {base} -> {target} tiles (your median gap is "
          f"{g['user_median']:g}).")
    return out


def density_factor() -> float:
    """Multiplier on the densify platform budget, from how the user changed platform counts."""
    s = _stats()
    r = s.get("platform_ratio_median")
    if r is None or s.get("platform_ratio_n", 0) < analyze.MIN_SAMPLES:
        return 1.0
    f = max(DENSITY_RANGE[0], min(DENSITY_RANGE[1], float(r)))
    if abs(f - 1.0) < 0.05:
        return 1.0
    _note(f"Design memory: platform density x{f:.2f} (you usually change the platform count "
          f"by x{r:g}).")
    return round(f, 2)


def apply_entity_defaults(spec: Any) -> List[str]:
    """Fill unset int fields of the spec's entities with the user's typical values."""
    stats = _stats()
    done: List[str] = []
    for kind, flds in (stats.get("field_values") or {}).items():
        for field in flds:
            v = analyze.typical_field_value(stats, kind, field)
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= FIELD_MAX:
                continue
            n = 0
            for ent in spec.entities:
                if ent.kind == kind and field in type(ent).model_fields and getattr(ent, field) is None:
                    setattr(ent, field, v)
                    n += 1
            if n:
                done.append(f"{kind}.{field}={v} on {n}")
    if done:
        _note("Design memory: entity defaults " + ", ".join(done) + ".")
    return done
