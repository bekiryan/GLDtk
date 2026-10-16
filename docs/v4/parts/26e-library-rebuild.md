# Part 26e: rebuild the pattern library (quality pass)

Part 26d added SuperTux and Open Surge levels and 42 design notes. Retrieval works well for
notes but poorly for patterns:
- most windows are sparse;
- the labels are generic, and many copied the prompt's own example title "Spike pit stairs";
- only 4 of 50 whole levels pass the route check;
- the index was accepted with the old (pre-Part 28) physics;
- every window is 20 rows, so stamping (26b) only fits levels up to about 26 rows.

## Changes
1. **Scale.**
   - **SuperTux:** convert 1 SuperTux tile (32 px) to **1 GLDtk tile**, not 2×2. Tux jumps
     about 3–4 of his tiles, which matches the Part 28 short hop. Add a `scale` option to the
     converter and default it to 1.
   - **Open Surge:** keep 16 px cells. Re-ingest it, because the bounds fix landed after the
     real run.
2. **Window quality.** Drop windows that are mostly sky or mostly rock. The rule: at least 2
   standable spans, a solid fraction between 0.12 and 0.7, and a route through the window.
   Crop each window to its content, padding with at most 2 rows. Drop near-duplicates (the
   same normalised grid hash).
3. **Window sizes.** Cut windows of 20 rows **and** 32 rows (40 and 48 columns), so 26b can
   stamp levels up to about 40 rows. Check how `layout/terrain/stamp.py` limits height, so the
   taller windows are used there.
4. **Acceptance with the new physics.** Recompute route features and soft-lock status for
   every record with the current default `GameProfile`, including the LDtk samples and kept
   levels. Re-check whole levels too.
5. **Relabel.**
   - Clear the old labels.
   - Label the accepted, structurally richest windows first, rotating across sources, with a
     budget (`--label-budget`, default 600).
   - Label with `ollama:qwen2.5:14b` and embed with `nomic-embed-text`.
   - Reject labels whose title equals any example title in the label prompt, and re-ask once.
   - Notes stay as they are; do not regenerate them.
6. **Packaging.** Add `patterns*` to the `pyproject.toml` package list, and add
   `patterns/sources/manifest.json` as package data.
7. **Speed.** If retrieval over about 2,000 vectors takes more than 50 ms per query, cache the
   vectors as a numpy array (numpy is already installed).

## Report
- **Counts:** per source, windows kept and dropped (and why), accepted, and labelled.
- **Samples:** 5 sample labels.
- **Retrieval:** the top-3 patterns for "labyrinth with dead ends", "boss arena", "Sonic speed
  loop", "Mario-style teaching first level", "vertical shaft climb", and "hub room with three
  exits".
- **Stamping coverage:** for each of the 7 structure types, at level heights 24 and 34.
