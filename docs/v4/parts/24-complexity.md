# Part 24: Level complexity target (Python, in a worktree)

Plan: [../PLAN.md](../PLAN.md) §24. One number drives generation.

## Build
- **The field.** `GenerateRequest.complexity: Optional[float]`, from 0 to 1, shown to users as
  1–10. Add the same field as `LevelSpec.complexity` and `Section.complexity` (nullable,
  per-section override; the LLM may set it from words like "very hard" or "relaxing"). Null keeps
  today's behaviour, byte-identical.
- **The mapping**, in a new `pipeline/complexity.py`: `targets(c) -> ComplexityTargets`. It gives
  concrete knobs:
  - the comfort-limit scale (share of the hop, rise and drop limits used, Part 20);
  - the share of the route made of long or parkour moves;
  - hazard density, enemy count and threat per 100 tiles;
  - checkpoint spacing;
  - for terrain sections (Part 21): labyrinth `complexity` and `branches`, corridor `pits`, and
    arena platforms;
  - the expected Part 15 difficulty score range.

  Document the table. The LLM prompt gets the targets as a short block in the user message.
- **Closed loop.** After the build, measure with `fidelity/difficulty.py`. If a section's score
  misses its target range by more than 1.5:
  - adjust deterministically first, at most 2 rounds: add or remove enemies, hazards or
    checkpoints; tighten or widen terrain params by re-seeding the section with adjusted params;
  - re-verify;
  - then warn if it still misses (no extra LLM call).
- **Fidelity.** "difficulty N/10" in the prompt becomes a checked requirement (a new check type
  `complexity`, using the measured score).
- **Edits.** Section edits accept `complexity`.
- **Eval.** Add `complexity` to the records, plus a report table of target against measured
  score.
- **Tests:**
  - the targets are monotonic in c;
  - the measured score increases with c on the terrain fixtures (`tests/fixtures/llm/terrain_crypt.json`)
    and an open fixture;
  - the closed-loop adjustment moves a mis-scored section towards its target;
  - null is byte-identical;
  - the check type;
  - all of `pytest` passes.

## Rules
- **Parallel work:** Parts 22 (soft-locks), 25a (noise fill) and 26a (patterns) run in parallel
  in worktrees. Keep your hooks in `pipeline/level.py`, `llm/prompt.py` and `server.py` small.
- First run `git reset --hard feat/v2`.
- Commit `v4 Part 24: level complexity target`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No push, do not edit PROGRESS.md, no real LLM, do not use port 8765, and do not touch
  `ldtk-src`.
