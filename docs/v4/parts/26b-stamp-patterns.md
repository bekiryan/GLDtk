# Part 26b: stamp retrieved patterns into terrain sections

**Goal:** a terrain section can be carved from a real, retrieved pattern (an accepted
`kind="pattern"` entry of the Part 26 index) instead of the procedural generator, adapted to the
section's size and ports, then verified like any other terrain. No LLM call.

## Behaviour
- **When:** for each terrain section, the pipeline asks the index for the best accepted
  pattern with the section's structure type (`retrieve(..., Filters(structure_type=…))`; the
  query text is the prompt plus the section's description). It stamps the pattern when the score
  is at least `STAMP_MIN_SCORE` (0.6) and the pattern fits (below). Otherwise the procedural
  generator runs, exactly as today.
- **Switch:** `GenerateRequest.stamp_patterns: bool = True`, threaded like the other
  generation options. False, an empty index or no embedder means procedural only.
  Section edits ("Make this section a…") use the same rule.
- **Only geometry:** stamp the pattern's grid (SOLID, HAZARD, EMPTY). Never its entities;
  entities still come from the spec and mission placement. Records with `kind="note"` (Part 26d)
  or no grid are never stamped.
- **Fit to the section:**
  - The pattern is cropped or tiled horizontally to the section's inner width. Repeat whole
    columns from the middle, never cutting a jump arc. It is cropped or padded vertically with
    rock to the inner height; reject the pattern if it needs more than 25 % padding or cropping
    in either direction.
  - Carve the section's entry and exit **ports** and connect them to the nearest pattern air
    with the existing helpers (`carve_path`, `link`, `stair_line` in `layout/terrain/base.py`)
    so the seams always match the neighbours.
- **Verify, then fall back:** the stamped section goes through the normal verify, soft-lock,
  comfort and densify passes. If the level fails to verify with the stamp, retry that section
  with the next hit (at most 2 patterns), then use the procedural generator. A level is never
  written unverified.
- **Physics:** the index was built with the old physics. Do not trust stored `features`
  acceptance for physics; the verifier is the oracle.
- **Attribution:** the response's existing `patterns_used` (or the equivalent field from 26a)
  lists stamped patterns with their id, source and licence, marked `"use": "stamped"` (vs
  `"example"` for prompt few-shot). The `gldtk_sections` level field records the stamped
  pattern id per section, so a later edit can tell.

## Where
- New `layout/terrain/stamp.py`: `fit_pattern(grid, ctx) -> Optional[TerrainResult]` (pure,
  deterministic) and `stamp_section(...)`.
- `layout/terrain/__init__.py::generate_section` / `build_terrain`: try the stamp first.
- The retrieval call lives in the pipeline (`pipeline/level.py` or a small helper), not in
  `layout/`. Pass the chosen pattern grids into `build_terrain`, so `layout/` stays offline
  and testable.

## Tests (offline; use a temp `GLDTK_PATTERNS_DIR` and a fake embedder)
- `fit_pattern`: crop, tile and pad; reject when the size is too far off; ports are open and
  connected.
- A terrain level with a matching pattern stamps it, verifies, and lists it in
  `patterns_used`.
- If the stamp breaks verification, the level falls back to procedural and is still verified.
- `stamp_patterns=False`, an empty index and `note` records all mean procedural only.
- The same seed and index give the same level.

Out of scope: a sidebar toggle (added later with the other v4 UI), stamping whole `level`
records.
