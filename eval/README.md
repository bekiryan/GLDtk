# GLDtk evaluation harness

Runs the prompt suite (`prompts.json`, 27 prompts with tags) through the real pipeline
(`LLMController` + `pipeline.build_level`: spec, completeness, layout, verification, fidelity) and
reports, per provider and model: mean fidelity score, share of verified (playable) levels, failures
by type, mean LLM attempts / patches / re-prompts / entities filled / layout retries / grid repairs,
mean and p95 wall time and LLM call count.

Run from the repo root with the venv active.

## Gemini free tier

The free tier allows about 20 requests a day per Flash model, and one prompt can use several calls
(retries, a completeness re-prompt, a layout re-prompt). Always bound a run and record it:

```bash
python -m eval.run --provider gemini --limit 6 --max-calls 15 --sleep 4 \
    --record eval/recordings/gemini-day1 --out eval/results/gemini-day1
```

- `--max-calls N` stops before call N+1; a prompt cut off by the budget is not recorded.
- `--sleep SEC` pauses between calls.
- A 429 is recorded as a `quota` failure and the run stops (`--keep-going` continues). Re-run the
  same command with the same `--out` the next day: prompts with results are skipped, `quota` ones
  are retried. `--limit` / `--tags` select a subset (`--tags gravity,impossible`).
- `--record DIR` saves every raw LLM response as `DIR/<prompt_id>/<call index>.txt` plus
  `DIR/meta.json`. Keep recording into the same `DIR` across days.

## Replay (no quota)

```bash
python -m eval.run --replay eval/recordings/gemini-day1 --out eval/results/after-layout-change
python -m eval.run --compare eval/results/gemini-day1 eval/results/after-layout-change
```

Replay answers each prompt from its recording, so layout, repair, fidelity or prompt-independent
changes can be compared for free. A prompt that makes more calls than were recorded fails as
`replay_missing` (a prompt change alters the conversation, so re-record for those).

## Options

`--provider --model --tags --limit --seed --profile PATH --out DIR --max-calls --sleep --retries N
--prompts FILE --export --keep-going --record DIR | --replay DIR`; `--compare A B`.
`--export` also writes `<out>/ldtk/<id>.ldtk` (plain tile export, no art).
`--profile` points at a `gldtk.game.json`; with the default profile no ability is enabled, so the
`ability-lacks` prompts are expected to score `impossible` items. Enable abilities to test them.

## Output (in `--out`)

- `results.jsonl`: one line per prompt: `id, tags, verified, failure {type, message}, fidelity_score,
  fidelity_counts, fidelity_items, expect_failures, llm_calls, llm_attempts, patches,
  completeness_reprompts, entities_filled, layout_retries, grid_repairs, wall_time_s, provider, model`.
  Failure types: `MaxRetriesExceeded`, `LevelBuildFailed`, `quota`, `replay_missing`, or the exception name.
- `summary.json`: aggregate per `provider/model`.
- `report.md`: summary table, per-prompt table, worst requirements.

## Prompt suite

Each prompt has `id`, `tags`, `prompt` and optional `expect`: `min_sections`, `min_width_tiles`,
`min_height_tiles`, `entity_kinds` (list), `min_impossible` (fidelity items). Unmet expectations are
listed in `expect_failures` and the report; they do not change `verified`.
