# Part 26a: Pattern library and retrieval (Python, in a worktree)

Plan: [../PLAN.md](../PLAN.md) §26. This covers ingestion, labelling, indexing, retrieval and
prompt examples. Later parts add stamping patterns into terrain sections (needs Part 21) and the
soft-lock filter (needs Part 22).

## Sources (licences)
- **LDtk's sample projects**, MIT (`ldtk-src/LICENSE`):
  `/Users/bekiryan/Projects/GLDtk/ldtk-src/app/extraFiles/samples/*.ldtk` (19 files; the
  platformer ones matter most, for example `Typical_2D_platformer_example.ldtk`).
  - Use only the **layout structure** (IntGrid collision values and entity positions and kinds),
    never their art.
  - Many samples have different IntGrid value meanings, so map collision values per file (a
    small mapping table plus a heuristic: values named wall, ground or solid → SOLID; lava,
    spikes or water → HAZARD) and skip files that do not map.
- **GLDtk's own levels:** the demo folders and any `.ldtk` the user approves, added by
  `POST /patterns/add {path, level?}`.
- **Open corpora:** only after checking their licence; leave a documented hook for them, and do
  not download any now.

Every entry stores `source`, `license` and `origin_path`.

## Build (new package `patterns/`)
- **Ingestion** (`patterns/ingest.py`):
  - read a level through `layout/ldtk_reader.py` / `edit/context.py`, or a raw IntGrid for foreign
    samples;
  - convert it to the GLDtk grid with Start and Exit (guess them when missing, for example from
    a "Player" entity);
  - cut it into **patterns**: rooms or windows of about 16–40 tiles wide, aligned to walkable
    structure (split at vertical walls or at regular intervals with overlap);
  - compute features: platform widths, gaps, the number of hazards and enemies, a route summary
    (`layout/route.py` within the window), and a difficulty estimate (`fidelity/difficulty.py`);
  - store the ASCII (the same alphabet as `ir.ascii_render`) plus the features.
- **Labelling** (`patterns/label.py`): one structured-output LLM call per pattern, through the
  provider layer, with a fixed schema: `{title, purpose, mood, mechanics[], structure_type
  (corridor|parkour|labyrinth|arena|shaft|hub|open), difficulty 1-10, tags[]}`. Cache the
  results, so already-labelled patterns are never sent again, and respect a `--max-calls` budget
  like the eval harness does.
- **Embeddings** (`patterns/embed.py`): a pluggable embedder.
  - `gemini` (the `text-embedding` model, with the API key from `.env`);
  - `ollama` (for example `nomic-embed-text`; do not pull it, only support it);
  - `hash` (a deterministic bag-of-words hashing embedder used in tests and as an offline
    fallback).

  Store vectors with their metadata in a local index: SQLite plus numpy arrays, or plain JSON
  with cosine search. There are a few thousand entries at most, so no FAISS dependency is
  needed. The default location is `~/Library/Application Support/GLDtk/patterns/`, overridable
  with `GLDTK_PATTERNS_DIR`, and tests use a temp dir.
- **Retrieval** (`patterns/retrieve.py`): `retrieve(query_text, k, filters)`.
  - Filters cover the structure type, the difficulty range and the tags.
  - Use a hybrid score: cosine similarity plus a small bonus for matching filters, and
    max-marginal relevance for diversity.
- **Prompt integration:**
  - **Generate:** retrieve 2–3 patterns for the prompt and each section's description, and add
    them to the LLM user message as compact reference examples: ASCII up to about 30×16 each,
    plus a one-line description and "use as inspiration for rhythm and spacing; do not copy".
    Do not put them in the static system prompt (prompt caching).
  - **Region and section edits:** the same, for the edit prompt.
  - **Controls:** `GenerateRequest.patterns: Optional[int]` (k; null = off for now, so outputs
    stay the same); the response lists the pattern ids and titles used.
- **CLI** (`python -m patterns.build`):
  - ingest the samples and the demo folders;
  - label with a budget (`--provider`, `--max-calls`, `--dry-run`);
  - embed;
  - report the counts.
  - Do **not** run it with a real provider. The orchestrator does that later.
- **API:**
  - `GET /patterns?query=&k=` (preview);
  - `POST /patterns/add {path, level?}`;
  - `GET /patterns/stats`.
- **Soft-lock hook:** add an `accept(pattern)` filter step that Part 22 will fill in. For now it
  rejects patterns with no Start→Exit route in the window.

## Tests (offline: FakeProvider for labelling, hash embedder)
- Ingest `Typical_2D_platformer_example.ldtk` → N patterns with features.
- Labelling caches, and the budget is respected.
- The index round-trips.
- Retrieval ranks a matching pattern first and the filters work.
- Retrieved examples are in the generate prompt, and absent when `patterns=null`.
- Generate output is byte-identical when patterns are off.
- `/patterns` endpoints.
- No writes outside `GLDTK_PATTERNS_DIR` in tests.
- All of `pytest` passes.

## Rules
- **Parallel work:** Parts 21, 22, 25a and 27a run in parallel. Keep your hooks in
  `llm/prompt.py`, `edit/prompts.py`, `pipeline/level.py` and `server.py` small and additive.
- First run `git reset --hard feat/v2`.
- Commit once: `v4 Part 26a: pattern library and retrieval`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No push, do not edit PROGRESS.md, no real LLM, do not use port 8765, and do not touch
  `ldtk-src` (reading the samples is fine).
