"""Reference-pattern examples for the LLM *user* messages (never the cacheable system prompt)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from patterns.retrieve import Filters, Hit, retrieve
from patterns.store import PatternStore

MAX_W, MAX_H = 30, 16
MAX_PATTERNS = 6
MAX_NOTES = 2
NOTES_HEADER = ("DESIGN GUIDANCE (techniques of well-known games, in words; apply the idea where it "
                "fits the request, never recreate their levels):")
HEADER = ("REFERENCE PATTERNS (real level fragments: use them as inspiration for rhythm and "
          "spacing; do not copy them). Legend: # solid, ^ hazard, S start, X exit, E enemy, "
          "P pickup, . empty.")


def crop(ascii_text: str, max_w: int = MAX_W, max_h: int = MAX_H) -> str:
    """Up to *max_w* x *max_h*: the bottom rows (the floor) and the left columns."""
    lines = ascii_text.splitlines()[-max_h:]
    return "\n".join(line[:max_w] for line in lines)


def describe(hit: Hit) -> str:
    p = hit.pattern
    lab = p.get("label") or {}
    f = p["features"]
    bits = [lab.get("structure_type") or f.get("structure", "")]
    d = lab.get("difficulty")
    if d is not None:
        bits.append(f"difficulty {d}/10")
    if lab.get("mechanics"):
        bits.append("needs " + ", ".join(lab["mechanics"]))
    bits.append(f"{f['hazards']} hazard tiles, {f['enemies']} enemies, max gap {f['max_gap']}")
    purpose = f" {lab['purpose']}" if lab.get("purpose") else ""
    return f"\"{hit.title}\" ({'; '.join(b for b in bits if b)}).{purpose}"


def examples_block(
    queries: Sequence[str],
    k: int,
    *,
    store: Optional[PatternStore] = None,
    filters: Optional[Filters] = None,
) -> Tuple[str, List[Dict[str, str]]]:
    """``(text, used)``: the examples block for *queries* (the prompt first, then section
    descriptions) and the ``[{"id", "title"}]`` of the patterns in it.  ``("", [])`` when nothing
    is indexed.  *k* patterns per query, deduplicated, at most ``MAX_PATTERNS`` in total."""
    store = store or PatternStore()
    hits: List[Hit] = []
    seen: set = set()
    for q in dict.fromkeys(q.strip() for q in queries if q and q.strip()):
        for h in retrieve(q, k, filters, store=store, exclude=seen):
            seen.add(h.id)
            hits.append(h)
    hits = hits[:MAX_PATTERNS]
    first = next((q.strip() for q in queries if q and q.strip()), "")
    lv = retrieve(first, 1, filters, store=store, kind="level") if first else []
    notes = []
    if first and any(r.get("kind") == "note" for r in store.patterns.values()):
        from patterns.notes import excluded_notes

        notes = retrieve(" ".join(q for q in queries if q and q.strip()), MAX_NOTES, store=store,
                         kind="note", exclude=excluded_notes())
    if not hits and not lv and not notes:
        return "", []
    parts = [HEADER] if hits else []
    for i, h in enumerate(hits, 1):
        parts.append(f"Pattern {i}: {describe(h)}\n{crop(h.pattern['ascii'])}")
    used = [{"id": h.id, "title": h.title} for h in hits]
    for h in lv:  # one short summary of a similar approved level (no ASCII)
        f = h.pattern["features"]
        parts.append(f"Similar approved level \"{h.title}\": {f['width']}x{f['height']} tiles, "
                     f"{f['platforms']} platforms, {f['structure']}, {f['hazards']} hazard tiles, "
                     f"{f['enemies']} enemies, max gap {f['max_gap']}.")
        used.append({"id": h.id, "title": h.title})
    if notes:  # design notes of famous games (Part 26d): technique only, never a layout
        from patterns.notes import guidance_line

        parts.append(NOTES_HEADER + "\n" + "\n".join(guidance_line(h.pattern) for h in notes))
        used += [{"id": h.id, "title": h.title} for h in notes]
    return "\n\n".join(parts), used


__all__ = ["HEADER", "crop", "describe", "examples_block"]
