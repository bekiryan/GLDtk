"""Design notes (v4 Part 26d): short LLM-written texts on *how* famous levels are designed.

Commercial levels cannot be copied, but their design techniques can be described: teaching
through layout, pacing, set-pieces, rest and challenge, loops and shortcuts, arena composition.
Each note is one structured LLM call on a curated topic (``TOPICS``), cached in the pattern index
as an entry of ``kind`` "note" (text only, at most about 120 words, never coordinates or
layouts).  ``retrieve(kind="note")`` finds them, and the generate / edit prompts get one or two as
"design guidance" lines when patterns are on.

The user reviews notes: ``reviewed`` on the record, and ``notes_exclude`` in the pattern prefs
(``prefs.json`` in the index folder) for the rejected ones (never retrieved).

    python -m patterns.notes --provider ollama --model qwen2.5:14b --embedder ollama
    python -m patterns.notes --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from patterns.label import StructureType
from patterns.store import PatternStore, patterns_dir

MAX_WORDS = 120
PREFS_FILE = "prefs.json"
NOTE_SOURCE = "design-notes"
NOTE_LICENSE = "LLM-written description (no copied content)"

# (game, level or section, what the note is about)
TOPICS: List[Dict[str, str]] = [{"game": g, "level_or_section": l, "focus": f} for g, l, f in [
    ("Super Mario Bros.", "World 1-1", "teaching the jump, enemies and power-ups through layout without text"),
    ("Super Mario Bros.", "World 1-2 and warp zones", "underground variation and hidden shortcuts as rewards"),
    ("Super Mario Bros. 3", "World 1 levels", "introducing one new idea per level and remixing it"),
    ("Super Mario World", "Yoshi's Island 2", "safe experimentation spaces and optional secret exits"),
    ("Super Mario Bros.", "castle levels", "fire bars, lava pits and the boss bridge as a climax"),
    ("Spelunky", "room templates", "room templates and a guaranteed critical path through a random grid"),
    ("Spelunky", "the Mines", "risk and reward: optional treasure off the critical path"),
    ("Celeste", "Chapter 1 Forsaken City", "screen-sized challenges and chapter pacing"),
    ("Celeste", "strawberries and B-sides", "optional hard routes beside an easier main path"),
    ("Celeste", "Chapter 3 and 7", "introducing, developing and twisting one mechanic per chapter"),
    ("Hollow Knight", "Forgotten Crossroads", "shortcuts that open back to earlier areas and backtracking"),
    ("Hollow Knight", "City of Tears", "landmarks and vertical hubs for orientation"),
    ("Hollow Knight", "boss arenas", "flat arenas with clear edges for readable boss fights"),
    ("Blasphemous", "boss arenas", "arena composition, hazards at the edges and telegraphed attacks"),
    ("Blasphemous", "area transitions", "gates and ability locks that pace exploration"),
    ("Sonic the Hedgehog", "Green Hill Zone", "loops, slopes and speed sections"),
    ("Sonic the Hedgehog 2", "Chemical Plant Zone", "multiple height routes: fast upper path, harder lower path"),
    ("Sonic the Hedgehog", "Marble Zone", "slowing the pace with puzzles and hazards after speed"),
    ("Mega Man 2", "stage design", "telegraphing a new hazard safely before it becomes dangerous"),
    ("Mega Man", "boss gates and corridors", "rest corridors before boss rooms"),
    ("Castlevania", "Stage 1", "stair rhythm and enemy placement on stairs"),
    ("Castlevania: Symphony of the Night", "the castle map", "a non-linear map with ability-gated areas"),
    ("Super Metroid", "Crateria and Brinstar", "ability gates, sequence breaks and the return trip"),
    ("Super Meat Boy", "level design", "short levels with instant retries and one clear route"),
    ("Super Meat Boy", "dark world", "remixing a level into a harder variant"),
    ("Dead Cells", "biome transitions", "biome transitions and branching paths between biomes"),
    ("Dead Cells", "hand-made tiles", "procedural assembly of hand-made rooms"),
    ("Shovel Knight", "Plains of Passage", "checkpoints, enemy introductions and optional treasure rooms"),
    ("Donkey Kong Country", "Jungle Hijinxs", "rhythm of rolling, jumping and barrel cannons"),
    ("Donkey Kong Country Returns", "mine cart levels", "auto-scrolling set pieces with learnable patterns"),
    ("Ori and the Blind Forest", "escape sequences", "chase sequences that test everything learned"),
    ("Rayman Legends", "music levels", "rhythm-based level structure synced to music"),
    ("Kirby's Dream Land", "level 1", "very low difficulty for beginners and generous recovery"),
    ("Cave Story", "First Cave", "a compact first area that teaches movement and shooting"),
    ("Mega Man X", "Intro stage", "a tutorial stage disguised as action"),
    ("Metroid Fusion", "sector design", "linear guidance inside a metroidvania structure"),
    ("Hollow Knight", "Path of Pain", "an optional extreme gauntlet built from known hazards"),
    ("Super Mario Maker", "community kaizo levels", "precision gauntlets and why fairness matters"),
    ("Limbo", "level flow", "environmental puzzles that teach by failure"),
    ("Inside", "pacing", "alternating tension and calm with set-pieces"),
    ("Spelunky 2", "labyrinth areas", "labyrinths with dead ends and reward caches"),
    ("Celeste", "Farewell", "very long hard rooms and the use of rests between them"),
]]

NOTES_SYSTEM_PROMPT: str = f"""\
You write short design notes for a 2-D platformer level-design library. Given a famous game, a
level or section and a focus, explain HOW its designers built that part: the technique, why it
works for the player, and how a designer could apply the same idea in a new level. Write in your
own words. Never give coordinates, tile positions, measurements or a reproduction of the level's
layout; describe ideas only. Output ONE JSON object and nothing else:
  "title": 3-7 words naming the technique,
  "game": the game,
  "level_or_section": the level or section,
  "technique": one short phrase (the design technique),
  "description": at most {MAX_WORDS} words,
  "structure_type": corridor | parkour | labyrinth | arena | shaft | hub | open (the closest),
  "difficulty": integer 1 (gentle) .. 10 (extreme) for the kind of content described,
  "tags": 3-6 lowercase words for retrieval.\
"""


class DesignNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    game: str
    level_or_section: str
    technique: str
    description: str
    structure_type: StructureType
    difficulty: int = Field(ge=1, le=10)
    tags: List[str] = Field(default_factory=list)


_COORDS = re.compile(r"\(\s*\d+\s*,\s*\d+\s*\)|\b[xy]\s*=\s*\d+|\b(?:tile|row|column|col)\s+\d+\b", re.I)


def check_note(note: DesignNote) -> List[str]:
    errs = []
    n = len(note.description.split())
    if n > MAX_WORDS + 10:
        errs.append(f"description has {n} words; at most {MAX_WORDS}")
    if _COORDS.search(note.description):
        errs.append("description contains coordinates or tile positions; describe ideas only")
    if not note.description.strip():
        errs.append("description is empty")
    return errs


def topic_id(topic: Dict[str, str]) -> str:
    key = f"{topic['game']}|{topic['level_or_section']}|{topic['focus']}"
    return "note-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def note_messages(topic: Dict[str, str]) -> List[dict]:
    return [{"role": "user", "content": (
        f"GAME: {topic['game']}\nLEVEL OR SECTION: {topic['level_or_section']}\n"
        f"FOCUS: {topic['focus']}")}]


# ── prefs (review) ──────────────────────────────────────────────────────────


def load_prefs() -> Dict[str, Any]:
    try:
        data = json.loads((patterns_dir() / PREFS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    out = {"notes_exclude": []}
    if isinstance(data, dict):
        out.update(data)
    return out


def save_prefs(prefs: Dict[str, Any]) -> None:
    d = patterns_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / PREFS_FILE).write_text(json.dumps(prefs, indent=2), encoding="utf-8")


def excluded_notes() -> set:
    return set(load_prefs().get("notes_exclude") or [])


def review_note(store: PatternStore, note_id: str, *, reviewed: Optional[bool] = None,
                exclude: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    """Set a note's ``reviewed`` flag and/or put it on (or take it off) the exclude list."""
    rec = store.get(note_id)
    if rec is None or rec.get("kind") != "note":
        return None
    if reviewed is not None:
        rec["reviewed"] = bool(reviewed)
        store.save()
    if exclude is not None:
        prefs = load_prefs()
        ex = [x for x in prefs["notes_exclude"] if x != note_id]
        if exclude:
            ex.append(note_id)
        prefs["notes_exclude"] = ex
        save_prefs(prefs)
    return rec


# ── generation ──────────────────────────────────────────────────────────────


@dataclass
class NotesReport:
    generated: int = 0
    cached: int = 0
    pending: int = 0
    calls: int = 0
    stopped: bool = False
    dry_run: bool = False
    errors: List[str] = field(default_factory=list)


def note_record(topic: Dict[str, str], note: DesignNote, provider_name: str = "") -> Dict[str, Any]:
    tags = [t.strip().lower() for t in note.tags if t.strip()][:6]
    return {
        "id": topic_id(topic), "kind": "note", "source": NOTE_SOURCE, "license": NOTE_LICENSE,
        "origin_path": f"notes/{topic['game']} - {topic['level_or_section']}",
        "level": topic["level_or_section"], "title": note.title,
        "note": {**note.model_dump(), "game": topic["game"], "tags": tags},
        "topic": topic, "provider": provider_name,
        # what the retrieval filters read
        "label": {"title": note.title, "structure_type": note.structure_type,
                  "difficulty": note.difficulty, "tags": tags},
        "features": {"structure": note.structure_type},
        "accepted": True, "reviewed": False, "ascii": "",
    }


def generate_notes(store: PatternStore, provider: Any, *, topics: Optional[List[Dict[str, str]]] = None,
                   max_calls: Optional[int] = None, dry_run: bool = False, force: bool = False,
                   max_retries: int = 2) -> NotesReport:
    """One structured call per topic without a note (saves after each); cached notes are kept
    unless *force*."""
    from edit.llm import EditLLMFailed, run_structured
    from eval.providers import BudgetExceeded, CallBudget, CountingProvider

    topics = TOPICS if topics is None else topics
    todo = [t for t in topics if force or topic_id(t) not in store.patterns]
    rep = NotesReport(cached=len(topics) - len(todo), dry_run=dry_run)
    if dry_run:
        rep.pending = len(todo)
        return rep
    budget = CallBudget(max_calls)
    counted = CountingProvider(provider, budget)
    pname = f"{getattr(provider, 'name', '')}:{getattr(provider, 'model', '') or ''}".strip(":")

    def parse(obj: DesignNote):
        errs = check_note(obj)
        return (None if errs else obj), errs, []

    for t in todo:
        if budget.exhausted:
            rep.stopped = True
            break
        try:
            run = run_structured(counted, NOTES_SYSTEM_PROMPT, note_messages(t), DesignNote, parse,
                                 max_retries=max_retries, what=f"the design note on {t['game']}")
        except BudgetExceeded:
            rep.stopped = True
            break
        except EditLLMFailed as exc:
            rep.errors.append(f"{t['game']} / {t['level_or_section']}: {exc}")
            continue
        store.patterns.pop(topic_id(t), None)
        store.add(note_record(t, run.value, pname))
        rep.generated += 1
        store.save()
    rep.calls = budget.total
    rep.pending = sum(1 for t in topics if topic_id(t) not in store.patterns)
    return rep


def guidance_line(rec: Dict[str, Any], max_words: int = 45) -> str:
    """One short "design guidance" line for a prompt."""
    n = rec.get("note") or {}
    words = (n.get("description") or "").split()
    desc = " ".join(words[:max_words]) + (" ..." if len(words) > max_words else "")
    return f"- {n.get('title') or rec.get('title')} ({n.get('game', '')}, {n.get('technique', '')}): {desc}"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m patterns.notes", description=__doc__.split("\n")[0])
    ap.add_argument("--provider", default="ollama", help="ollama (default) | gemini | ...")
    ap.add_argument("--model", default=None, help="default for ollama: qwen2.5:14b")
    ap.add_argument("--max-calls", type=int, default=60)
    ap.add_argument("--embedder", default=None, help="default: the index's embedder (else hash)")
    ap.add_argument("--force", action="store_true", help="regenerate cached notes")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    store = PatternStore()
    provider = None
    if not args.dry_run:
        from llm.providers import make_provider

        model = args.model or ("qwen2.5:14b" if args.provider == "ollama" else None)
        provider = make_provider(args.provider, model=model)
    rep = generate_notes(store, provider, max_calls=args.max_calls, dry_run=args.dry_run, force=args.force)
    print(f"notes: {rep.generated} new, {rep.cached} cached, {rep.pending} pending, {rep.calls} call(s)"
          + (" [dry run]" if args.dry_run else "") + (" [budget reached]" if rep.stopped else ""))
    for e in rep.errors:
        print("  error:", e)
    if args.dry_run:
        return 0
    from patterns.embed import make_embedder, sync_vectors

    name = args.embedder or (store.embedder or {}).get("name") or "hash"
    n = sync_vectors(store, make_embedder(name))
    store.save()
    print(f"embed: {n} new vector(s) with {name}; index: {store.stats()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DesignNote", "MAX_WORDS", "NOTES_SYSTEM_PROMPT", "NotesReport", "TOPICS", "check_note",
           "excluded_notes", "generate_notes", "guidance_line", "load_prefs", "note_record",
           "review_note", "save_prefs", "topic_id"]
