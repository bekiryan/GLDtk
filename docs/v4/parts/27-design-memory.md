# Part 27: Design memory (Python first; the sidebar panel comes later)

Plan: [../PLAN.md](../PLAN.md) §27. GLDtk learns the user's taste from every change and uses it in
later work, **across projects**.

## A. Python (in a worktree)
- **Storage:** new package `memory/`.
  - The default folder is `~/Library/Application Support/GLDtk/memory/` on macOS. Override it
    with `GLDTK_MEMORY_DIR`; tests always use a temp dir.
  - `events.jsonl` is append-only, rotated at about 5 MB. `preferences.json` holds the learned
    rules and statistics.
  - A `memory.enabled` flag lives in `preferences.json`, and when it is off nothing is recorded
    or used.
- **Events:**
  - `generated`, `edited`, `undone`, `regenerated` (level), `profile_changed`: with the prompt,
    mode, section structure, style, theme and difficulty.
  - **`hand_edit`:** a diff between the file GLDtk last wrote, which must be kept for this (save
    a copy of the last GLDtk output per file in the history folder), and the file as the user
    saved it. It is computed lazily when the next GLDtk request arrives for that file, or with
    `POST /memory/scan {path}`.
    - The diff contains: entities moved (distance, kind), added and removed (kind, context),
      changed field values (kind, field, from→to); tiles painted or erased, summarised as
      platform widths, gap sizes and pits created or filled, using the extractor spans before
      and after.
  - Keep events compact, with no full level JSON.
- **Analysis**, in `memory/analyze.py`:
  - **Statistics:** for example the median gap after user edits compared with GLDtk's, typical
    enemy spacing, entity kinds the user removes or adds, field values the user sets (enemy
    health), how often each mode is undone, and structure types regenerated.
  - **LLM summary:** optional, through the existing provider layer, at most once per N new
    events, into ≤ 10 short rules with confidence. It must work with a FakeProvider in tests.
- **Use:**
  - add a "Designer preferences (learned)" block to the generate and edit prompts
    (`llm/prompt.py` and `edit/prompts.py`), with only high-confidence rules, and ≤ 600 chars;
  - deterministic nudges, each bounded and logged in the warnings: comfort limits and densify
    density adjusted towards the user's median, and entity field defaults towards the user's
    typical values.
- **API:**
  - `GET /memory` returns the summary, rules and statistics;
  - `PUT /memory` edits or deletes rules and toggles `enabled`;
  - `DELETE /memory` resets;
  - `POST /memory/scan` takes `{path}`.
  - Server hooks record the events around generate, edit and undo.
- **Tests:**
  - recording;
  - the hand-edit diff (move an entity, widen a gap in a saved file, then scan);
  - statistics after a few synthetic edits;
  - the prompt block appears, and is absent when disabled;
  - the nudges are bounded;
  - the API;
  - nothing is written outside `GLDTK_MEMORY_DIR` in tests;
  - all of `pytest` passes.
- **Parallel work:** Part 21 (terrain) and Part 22 (soft-locks) run in other worktrees. Keep your
  edits to `llm/prompt.py`, `edit/prompts.py` and `server.py` small and additive.
- First run `git reset --hard feat/v2`.
- Commit `v4 Part 27a: design memory backend`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No push, do not edit PROGRESS.md, no real LLM, do not use port 8765, and do not touch
  `ldtk-src`.

## B. Sidebar (later, when `ldtk-src` is free)
A "Design memory" panel lists the learned rules with confidence and the statistics. Each rule can
be deleted or edited, and the panel has an on/off switch and a reset button.
