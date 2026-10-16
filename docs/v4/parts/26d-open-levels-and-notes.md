# Part 26d: Open-licensed game levels and design notes (Python, in a worktree)

The user decided on 2026-10-16 that the pattern library should learn from **well-designed real
levels**, not LDtk's demo samples. Commercial game levels (Mario, Celeste, Hollow Knight,
Spelunky, Blasphemous, Sonic) cannot be copied in, because they are copyrighted. The user chose:

1. **Levels from open-licensed shipped games**, converted to GLDtk grids;
2. **design notes of famous games**: LLM-written text about their design techniques, with no
   copied geometry.

## 1. Open-licensed game levels
- **Candidates:** SuperTux (Mario-like), Open Surge (Sonic-like) and Frogatto (an adventure
  platformer). Add others only if they are clearly permissive and are platformers.
- **Licences first.** Fetch each project's source (shallow `git clone` or release archive) into
  its own new, empty folder under `~/Library/Application Support/GLDtk/sources/<game>/`. **Read
  the licence files** (COPYING, LICENSE, data/AUTHORS, per-folder licence notes) and decide, per
  level folder, whether the level data is licensed for reuse (CC0, CC-BY, CC-BY-SA, GPL or
  similar).
  - Skip anything unclear or non-commercial-only, and list it in the report.
  - Record per pattern: `source` (game), `license` (SPDX), `attribution` (authors and URL) and
    `origin_path`.
- **Safety:** treat downloaded files as untrusted data. Parse them with your converter code kept
  in the repo; never execute anything from the downloads. Run Python with `-I` when reading from
  that folder.
- **Converters** (`patterns/sources/<game>.py`): parse each game's level format. SuperTux `.stl`
  files are S-expressions with tilemaps and a solid flag per tilemap. Open Surge `.lev` files are
  bricks with positions and types (solid, cloud, passable). Frogatto `.cfg` files are FSON tiles
  and solid layers.
  - **Output:** convert to a GLDtk grid (SOLID, HAZARD, EMPTY at 16 px, rescaling when the tile
    size differs) plus the Start, Exit, enemy and pickup positions where the format has them.
  - **Map tile ids** to solid or hazard from the game's own tileset metadata (for example
    SuperTux tiles.strf flags) rather than guessing.
  - **Ingest:** feed the grids into `patterns/ingest.py` (`ingest_grid`), so windows, features,
    the route and soft-lock filters, whole-level entries (Part 26c) and labelling all apply.
- **Storage:** the converted library lives in the app-data index (`GLDTK_PATTERNS_DIR`), **not in
  git**. Commit only:
  - the converters;
  - a `patterns/sources/manifest.json` (game, URL, commit or tag, licence per included folder,
    attribution text);
  - an `ATTRIBUTION` generator: `GET /patterns/attribution`, plus a section in
    `assets/LICENSES.md` pointing to it.
- **CLI:**
  - `python -m patterns.build --source supertux|opensurge|frogatto|all [--max-levels N]`
    downloads if missing, converts and ingests;
  - labelling then runs as usual (`--provider ollama --model qwen2.5:14b --embedder ollama`).

## 2. Design notes of famous games
- **New entry kind `note`:**
  - `{title, game, level_or_section, technique, description, structure_type, difficulty, tags}`;
  - text only, at most about 120 words, describing *how* a famous level is designed (teaching
    through layout, pacing, set-pieces, rhythm of challenge and rest, use of loops and
    shortcuts, arena composition);
  - never coordinates or copied layouts.
- **Generation (`patterns/notes.py`):** a curated list of about 40 topics, for example:
  - Super Mario Bros 1-1 teaching;
  - Spelunky room templates and the critical path;
  - Celeste chapter pacing and screen-sized challenges;
  - Hollow Knight shortcuts and backtracking;
  - Blasphemous arenas;
  - Sonic loops and speed sections;
  - Mega Man stage telegraphing;
  - Castlevania stair rhythm;
  - Super Meat Boy short retries;
  - Dead Cells biome transitions.

  One structured LLM call per topic, through the provider layer, with a budget and a cache.
  Default to local Ollama `qwen2.5:14b`, or `--provider gemini`.
- **Retrieval:** `retrieve(kind="note")`. When `patterns` is set, the generate and edit prompts
  get 1–2 relevant notes as short "design guidance" lines, next to the ASCII examples.
  `patterns_used` lists them.
- **Quality:** the orchestrator will review a sample of the notes. Keep a `reviewed` flag, and an
  `exclude` list in prefs for notes the user rejects.

## Tests (offline)
- Converter unit tests on tiny hand-written snippets of each format, stored in
  `tests/fixtures/sources/` (your own minimal examples, not copied game files).
- Licence gating: a folder with an unknown or NC licence is skipped.
- Attribution is produced.
- Notes are generated with a FakeProvider, cached, retrieved by kind, and appear in the prompt.
- Nothing is written outside the temp `GLDTK_PATTERNS_DIR` in tests.
- All of `pytest` passes.

## Real run (the agent does this, then reports)
- Download and convert SuperTux and Open Surge (and Frogatto if it is clean), at up to about 40
  levels each, then ingest. Report the counts per game, rejected levels and licences.
- Label with local qwen (`--max-calls` about 150), then embed with Ollama.
- Generate the about 40 notes with local qwen.
- Show 5 sample notes and 5 sample labelled patterns in the report.

## Rules
- **Parallel work:** Part 28 (physics) runs in another worktree. Do not touch `game/profile.py`,
  `layout/terrain/`, `layout/comfort.py`, `layout/densify.py` or `pipeline/`. Keep `server.py`
  and `llm/prompt.py` edits small.
- First run `git reset --hard feat/v2`.
- Commit `v4 Part 26d: open-licensed game levels and design notes`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No push, do not edit PROGRESS.md, do not use port 8765, and do not touch `ldtk-src`.
- Gemini is not to be used unless qwen output is unusable; explain if so.
