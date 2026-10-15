# Part 9b: M2 fixes

Context: the M2 app test with real Gemini prompts (notes for 2026-10-14 in
[../PROGRESS.md](../PROGRESS.md)). The test levels are `demo_m2/p0.ldtk` (a 120×30, three-section
adventure) and `demo_m2/p1.ldtk` (a 40×60 icy climb). Two agents run in parallel: **A
(Python, in a worktree)** and **B (Haxe, in `ldtk-src`)**. Their files do not overlap.

## A: Python

### A1. Hazard requirements (bug 1)
The LLM typed "a spike pit in the cave" as `entity_count` of an Enemy with subkind "hazard". The
fill then added a fake Enemy, although the spec already had a Hazard node there.
- Add a `hazard` check type to `RequirementCheck` (`spec/level_spec.py`). It counts Hazard
  **nodes**, optionally per section, with `min_count`.
- Document it in the prompt (`llm/prompt.py`), with an example.
- Defensive: when an `entity_count` has a subkind that names a hazard concept (hazard, spike(s),
  pit, lava, acid, …) or the requirement text does, check it as a `hazard` instead. The fill must
  **never** synthesise an Enemy for it.
- A missing hazard can be filled deterministically: add a Hazard node under a gap in that
  section, without breaking the graph (Hazard nodes have no outgoing edges). If that is too
  risky, leave it to the single re-prompt. Your choice; document it.

### A2. Vertical sections (bug 5)
Sections always map to left-to-right column bands, so in the 40×60 climb the "summit" section was
squeezed into columns 23..34 instead of sitting at the top.
- Add a level-level orientation: `horizontal` (sections are column bands, left → right) or
  `vertical` (sections are row bands, **bottom → top**).
- Derive it deterministically: `vertical` when `height_tiles > width_tiles`. Optionally let the
  spec state it with a nullable `orientation` field (the LLM may set it; null = derive). Update
  the prompt so the LLM places each section's nodes in its band for that orientation.
- Everything that uses section bands must respect the orientation: `layout/spec_layout.py` (the
  "remapped section" logic), `ir.metadata["sections"]`, and per-section tilesets and themes in
  `aesthetic/` and `layout/ldtk_adapter.py` (a vertical section's tiles are its rows, not its
  columns). Also the entity relocation "same section" check, `gravity_scale` per section in
  verification, the fill's section placement and the `gldtk_sections` level field. Add a
  `row_start`/`row_end` (or `axis` plus `start`/`end`) to the sections metadata, keeping
  `col_start`/`col_end` for horizontal levels so older files still read.

### A3. Sparse levels (bug 4)
The LLM emits about 10 small platforms, even for 120×30 levels. Prompt 1 used only the bottom
third; the climb is isolated 3-tile ledges far apart.
Add a **deterministic densify step** after layout and before verification (in `layout/` as a new
module, called from `pipeline/level.py`):
- Fill large empty regions with extra Platform nodes so the level reads as a level. Use a
  seeded RNG (the request seed), and place each platform where it is reachable from or to the
  golden path (use the same reach limits as the oracle; the profile has them).
- Horizontal levels get ground segments under long stretches where it fits the theme. Vertical
  levels get intermediate ledges between far-apart climb steps, and walls at the sides are
  optional.
- Target density: about one platform per 40–60 empty tiles, or something you can justify with a
  rendering. Platform widths vary from 3 to 8 tiles, and there is headroom for the player hitbox.
- Never block an existing jump arc. Run verification after the step, and if densify breaks
  verification, drop the offending additions; never fail a level because of densify.
- Added nodes are marked (for example an id prefix `fill_`), placed in their section, and
  appear in `spec.nodes` like repair's stepping stones (see `_with_added_nodes`). They get edges
  only if that is needed for verification.
- Add a request flag `densify: bool = True` (`api/models.py`, `GenerateRequest`) so it can be
  turned off.
- Check the result visually: render `ir.ascii_render` for the two demo specs. You can rebuild
  them offline from the spec stored in the `gldtk_spec` level field of `demo_m2/p0.ldtk` and
  `p1.ldtk` (use `layout/ldtk_reader.py`). Include before/after ASCII in your report.

### A4. Assets written during a generate (part of bug 2)
The server copies a theme sheet into `<ldtk_dir>/gldtk_assets/themes/` the first time a theme is
used. LDtk's file watcher then reloads the tileset after the level is loaded and marks it
modified. Agent B fixes this on the LDtk side. On the Python side, keep the
skip-when-unchanged behaviour. Optionally, copy **all** library theme sheets on the first
generate in a project, so later generates never create new files. They are small, about 1 KB
each. Do it if it is cheap.

### Tests
Offline, as before: one test for each item above, both orientations, and the densify invariants
(verification still passes; nothing lies on the golden path's arcs; deterministic for a seed).
All of `pytest` passes. Commit in the worktree as one commit:
`v2 M2 fixes (Python): hazard checks, vertical sections, densify`.

**You own:** `spec/`, `fidelity/`, `llm/`, `pipeline/`, `layout/`, `aesthetic/`, `api/models.py`,
`server.py` and `tests/`.

## B: Haxe (`ldtk-src`, branch `feat/v2`)

### B1. Level shows as modified after a generate (bug 2)
After a sidebar generate whose theme is new to the project, the level shows as `Level_0*`. The
same file opened fresh shows no `*`. Cause: the server writes a new theme PNG into
`gldtk_assets/themes/`, and LDtk's `FileWatcher` then reloads that tileset after the swap
(`reloadTileset` → `TilesetDefChanged` → level JSON cache invalidated). Fix it in the sidebar's
reload. Options include: ignore or flush watcher events for the project's assets that were
written before the swap; re-sync the caches after the swap; or clear `needSaving` and the cache
flags only when the in-memory project equals the file on disk. Pick the cleanest option and
explain it.

### B2. Leftover render of the previous level (bug 3)
After a generate replaces the open level with one that has fewer layers or a different size,
tiles from layers that no longer exist (e.g. `Tiles_Cave`) stay drawn outside the new level.
The in-place project swap (`editor.selectProject`) does not destroy the old level's layer
renders. Find out where the world and level renders are kept (`display/WorldRender.hx`,
`LevelRender`) and clear them properly on the swap.

### B3. Overlapping labels (cosmetic)
Entities on one platform show overlapping identifier and field labels ("Coin Coin Coin / Pickup
Pickup"). Decide what reads best. One option: hide the entity identifier for kinds whose sprite
already says what they are. Note that the entity defs are produced by the Python exporter
(`spec/catalog.py`, `layout/ldtk_adapter.py`, e.g. `showName`). If the fix belongs in Python,
**do not edit Python**; describe the exact change in your report and the orchestrator will
apply it.

### Testing
Rebuild with `renderer.debug.hxml`. Test with Playwright against a server running a
FakeProvider. Reuse `drive9.js` and `fake_server.py` from
`/private/tmp/claude-501/-Users-bekiryan-Projects-GLDtk/e8d14c20-b375-4528-8b61-66b273aadf76/scratchpad/p9/`.
- To reproduce B1, delete `gldtk_assets/themes/<theme>.png` from the test project before
  generating, so the generate writes a new sheet.
- To reproduce B2, generate a multi-section level, then a single-section one.

Save screenshots in your scratchpad. Commit in `ldtk-src` as one commit:
`v2 M2 fixes (Haxe): no modified flag after generate, clear stale renders`.

**You own:** `ldtk-src/` only.

## Shared rules
- Do not push.
- Do not edit `docs/v2/PROGRESS.md`.
- End each commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never call a real LLM.
- Stop every server and Electron process you start; port 8765 must be free when you finish.
- Agent A must not use port 8765; B uses it for app tests.
