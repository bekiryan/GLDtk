# Part 15: Difficulty curve (Python + Haxe)

Context: [../PLAN.md](../PLAN.md). Sections have a difficulty label (easy, medium or hard) that the
LLM sets from the prompt, but nothing measures whether the built level matches it.

## A. Python (in a worktree)
- **New module `fidelity/difficulty.py`:**
  `measure_difficulty(grid, entities, sections, profile, route?) -> DifficultyReport`.
  Compute per section (column or row band, for both orientations):
  - **Jumps:** the gaps of the moves on the golden route, using `layout/route.py` from Part 13:
    mean and max horizontal gap in tiles, max rise, and the share of moves that need an ability
    or are near the jump limit (more than 85% of the reach for that Δy).
  - **Hazards:** hazard tiles per 100 columns (or rows), and hazard cells next to landing spots.
  - **Enemies:** count, the sum of their `health · damage`, and bosses.
  - **Recovery:** checkpoints, and health pickups.
  - **Platform density and sizes:** mean platform width, where narrow ledges are harder.
- **Score:** combine these into a **score from 0 to 10** with documented weights, normalised so
  that typical easy sections score about 2–3, medium about 4–6 and hard about 7–9. Calibrate the
  weights on the fixtures and the eval recordings (`eval/recordings/gemini-2026-10-15`; replay
  them offline with `eval/run.py --replay`). Report the calibration in your summary.
- **Checks:**
  - A section whose score does not fit its label (easy above 5, or hard below 4) gets a
    **warning**, and an `approximated` fidelity item when the prompt stated that difficulty.
  - If the curve goes down where the prompt implied "gets harder", add a warning only.
- **API:** add `difficulty: {sections: [{id, name, label, score, metrics: {...}}], overall}` to
  `GenerateResponse`, the edit responses and `GET /route`, or a new `GET /difficulty?path=`. Your
  choice; keep it cheap, with no LLM call.
- **Eval:** add the mean score per label to the eval report (`eval/metrics.py`).
- **Tests:**
  - synthetic grids where the hard version scores higher than the easy one;
  - both orientations;
  - the mismatch warning;
  - the eval aggregation;
  - all of `pytest` passes.

## B. Haxe (`ldtk-src`)
- **Chart:** a small "Difficulty" chart in the sidebar, under the fidelity report. It has one
  bar per section in level order, with height = score, a colour by label (green, amber, red),
  and the label text. A mismatch gets a ⚠ with a tooltip giving the reason and the metrics.
- **Hover:** hovering a bar highlights that section's band on the canvas, reusing the section
  highlight from Part 8b.
- **Updates:** after generate, edit, undo and project open, the same way the route is refreshed
  (Part 13).
- **Testing:** Playwright against a fake server, with screenshots. Rebuild with no errors.
- **Commits:** worktree `v3 Part 15a: difficulty metrics`; `ldtk-src`
  `v3 Part 15b: difficulty chart`.

## Shared rules
As in Part 14: reset the worktree to `feat/v2` first; no push; do not edit PROGRESS.md; no real
LLM; port 8765 is yours; stop every process; end each commit with the Co-Authored-By line.
