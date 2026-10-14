# Part 10: Evaluation harness

Design context: [../DESIGN.md](../DESIGN.md), Q6. The share of requirements satisfied is the
evaluation metric. Part 10 runs **in parallel with Part 8a**, in its own worktree, and owns only
`eval/`, `tests/test_eval*.py` and its fixtures. It must not edit any other module; it may only
import them.

## Goal
One command runs a prompt suite through the real pipeline and reports, per provider and model:
- **fidelity:** the mean `FidelityReport.score`, and the counts per status;
- **playability:** the share of prompts that produce a verified level, and the failures by type
  (`MaxRetriesExceeded`, `LevelBuildFailed` and so on);
- the mean LLM attempts, patches applied, completeness re-prompts, entities filled, layout
  retries and grid repairs (from `BuiltLevel.stats` and the controller stats);
- the mean and p95 wall time, plus the LLM call count.

## Deliverables
1. **`eval/prompts.yaml` (or `.json`):** about 24 prompts, each with an id and tags. Cover:
   - single and multi-section levels;
   - horizontal and vertical levels;
   - every entity kind;
   - abilities the default profile has and lacks;
   - gravity sections;
   - explicit sizes;
   - vague prompts;
   - impossible requests ("the player can fly", "music plays");
   - long and detailed prompts.

   Optional per-prompt expectations (for example "expects an impossible item") can be checked
   as extra assertions.
2. **`eval/run.py`:**

   ```
   python -m eval.run --provider gemini [--model M] [--tags t1,t2] [--limit N] [--seed S]
     [--profile path] [--out eval/results/<timestamp>] [--max-calls N] [--sleep SEC]
     [--replay DIR | --record DIR]
   ```

   - It uses `pipeline.build_level` with an `LLMController`. Layout, verification and fidelity
     are enough; the aesthetic and LDtk export steps are optional (`--export` writes `.ldtk` files
     too).
   - **Quota safety:**
     - `--max-calls` stops before the budget is spent;
     - `--sleep` adds a pause between calls;
     - 429 errors count as `quota` failures, not crashes;
     - a run can be resumed: prompts that already have results in `--out` are skipped.
   - **Record and replay:** a provider wrapper saves every raw LLM response under `--record DIR`,
     keyed by prompt id and call index, so runs can be replayed offline (`--replay DIR`). This is
     how prompt or layout changes get compared without spending quota.
3. **Results:**
   - `results.jsonl`: one line per prompt, with every metric and every fidelity item;
   - `summary.json`;
   - `report.md`: a summary table per provider and model, a per-prompt table, and the worst
     requirements (most often missing or approximated).
   - Add a `--compare A B` mode that diffs two result folders.
4. **Tests (offline):** run the harness on 3 prompts with `--replay` against recorded fixtures
   that you create with the FakeProvider responses in `tests/fixtures/llm/`. Cover:
   - the metric aggregation;
   - resume;
   - `--max-calls`;
   - a 429 counted as a quota failure;
   - compare.
5. A short `eval/README.md` explaining how to run it with Gemini on the free tier: about 20
   requests a day per Flash model, so use `--limit` and `--max-calls`, and `--record` so the
   responses can be replayed.

**Do not run it against a real provider.** The orchestrator does that after merging.

Commit message: `v2 Part 10: evaluation harness`, ending with
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push, and do not edit
`docs/v2/PROGRESS.md`.
