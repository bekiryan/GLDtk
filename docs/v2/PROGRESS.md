# GLDtk v2 — Progress

Design: [DESIGN.md](DESIGN.md). Branch: `feat/v2` (main repo and `ldtk-src/`). Baseline commit: `347d02a`.

To resume in a fresh session, say: **"continue GLDtk v2 from docs/v2/PROGRESS.md"**.
Workflow for each part: write a spec in `docs/v2/parts/NN-name.md` → dispatch a subagent → verify the tests and diff → commit (one commit per part) → update this file → **push `feat/v2`** (user request, 2026-10-13). Haxe parts are also pushed from `ldtk-src/`.

| # | Part | Model | Status | Commit |
|---|---|---|---|---|
| 1 | Level spec schema + structured-output providers | strong | ✅ | see git log |
| 2 | Game Profile + ability-aware physics | strong | ✅ | see git log |
| 3 | Layout v2 (+ physics re-verification on the final grid) | strong | ✅ | see git log |
| 4 | Entity catalog + full LDtk export | medium | ✅ | see git log |
| **M1** | **User review: prompt → detailed level end to end** | | ⏳ waiting for user | |
| 7 | Fidelity report + completeness loop | strong | ✅ | see git log |
| 7b | M1 fixes (export, layout, art) | medium | ✅ | see git log |
| 9 | Sidebar UI (Haxe) | strong | ✅ (edit controls deferred to Part 8) | see git log (both repos) |
| **M2** | **User review in LDtk** | | ✅ app test with real Gemini (orchestrator, at the user's request) | |
| 9b | M2 fixes (Python + Haxe) | strong | ✅ | see git log (both repos) |
| 5 | Asset library | light | ✅ (pulled forward) | see git log |
| 8 | Regional editing + undo | strong | ⬜ | |
| **M3** | **User review** | | ⬜ | |
| 10 | Evaluation harness | light | ✅ | see git log |
| 6 | AI theme/sprite generation | medium | ✅ code done, not tested on real images (pulled forward) | see git log |
| **M4** | **Final review** | | ⬜ | |

## Notes
- 2026-10-13: Design interview complete. Baseline committed. `.env` (git-ignored) holds `GEMINI_API_KEY`. Ollama is installed but has no models.
- 2026-10-13: Part 1 done ([spec](parts/01-level-spec.md)). 64 offline tests pass; the Gemini e2e run passes. Gemini free tier: **20 requests/day for `gemini-3.8-flash`** (quota is per model), and Flash was returning 503 "high demand" all session. The provider falls back to `gemini-3.1-flash-lite` on 429/5xx when no model is specified. Spec deviations: separate `EXIT_ENTITY_MISMATCH` and `UNKNOWN_EDGE_NODE` codes; strict schema (all fields required, optional = nullable); `controller.stats` holds the attempt and patch counts Part 10 needs. Ollama and Anthropic structured output are covered only by request-building tests (Ollama has no models installed).
- 2026-10-13: Part 2 done ([spec](parts/02-game-profile.md)). 99 offline tests pass. The double-jump oracle is exact and closed-form; the orchestrator checked it against a brute-force τ search (20k random cases, no mismatches). Open items for Part 3: Fall edges are not yet capped by `run_speed`; `TILE_SIZE = 16` is fixed in `spec/level_spec.py` and the prompt, while the profile has its own `tile_size`; wall-jump edges must be checked for a real wall on the final grid; `SteppingStonePatch` ignores abilities.
- 2026-10-13: Part 3 done ([spec](parts/03-layout-v2.md)). 136 offline tests pass. The layout keeps the LLM's coordinates (`col=round(x/16)`, `row=H-1-round(y/16)`) and section order. The final grid is verified with hitbox-swept trajectories (12×24 px, 6 px samples), repaired deterministically (stepping stones, nudges), and the LLM is re-prompted only as a last resort. A 300×100 level with 87 nodes builds in about 1.6 s; the 50-seed property test verified 50/50. Open: the gravity scale of the starting section applies to the whole arc; walking under a low ceiling along one span is not checked; wall-jump paths are approximate; the server writes no `.ldtk` when verification fails.
- 2026-10-13: The user asked for independent parts to run in parallel. Parts 4 and 5 run at the same time in separate git worktrees (they edit disjoint files), and the orchestrator merges them and does the server wiring. Milestone review points are unchanged.
- 2026-10-13: Part 5 done ([spec](parts/05-asset-library.md)). Three Kenney CC0 packs (Tiny Dungeon, Tiny Town, 1-Bit), 8 themes and 25 sprites in `assets/manifest.json`, about 250 KB. Six of the themes use derived edge tiles (texture plus outline and top band), because the packs have no platformer edge sets; only forest and castle use real 3×3 blocks. The orchestrator reviewed `assets/preview.png` and had cave and sky redone. The server copies the theme sheet to `<ldtk_dir>/gldtk_assets/themes/`. Still to do: delete `tilesets/` once the default in `ldtk_adapter.AestheticData` (Part 4) no longer references it; use `find_sprite` for entity sprites when wiring Part 4.
- 2026-10-13: Part 4 done ([spec](parts/04-entity-catalog.md)). It was merged from its worktree into `feat/v2` (only conflict: dev deps in pyproject). There are 193 tests, and exports validate against LDtk's JSON schema. Fixing that schema validation also fixed about 45 schema violations the v1 export already had. The server passes `LevelMeta` (prompt, requirements, profile path, full spec) and the player hitbox size, and returns export warnings. `read_level` round-trips the export and keeps iids, which Part 8 needs. Open: no export has been opened in the LDtk app yet (check at M1); LDtk re-saving doubles backslashes in string fields, and the reader compensates; entity sprites are not shown in LDtk yet (Part 4 exports kinds with colour only; `find_sprite` exists).
- 2026-10-13: M1 demo at `demo_m1/level.ldtk` (not committed), made with `gemini-3.1-flash-lite` because the Flash quota was used up. The 3-section prompt produced a verified level (1 stepping stone inserted) that is **incomplete**. The LLM listed all 11 requirements but emitted only 5 entities: no coins, skeletons, bat, chest or boss. It also made only 7 small (3-tile) platforms on one row band of a 30-row level, and used a single tileset (forest) for all three sections. Waiting for the user's review.
- 2026-10-13: Part 6 merged ([spec](parts/06-ai-art.md)); 231 tests pass; `/art/theme`, `/art/sprite` and `/art/library` are live. The image model is `gemini-3.1-flash-image`, falling back to flash-lite-image and then 2.5-flash-image. **All 3 real image calls returned 429**: the free tier has no image quota, so the post-processing has only run on synthetic images. Tuning it needs a paid key.
- 2026-10-13: **App test** (Playwright driving the Electron app; driver in the session scratchpad, not committed). `demo_m1/level.ldtk` loads in LDtk with all 9 entity kinds, fields shown on the canvas, the patrol radius drawn, and the key→exit ref arrow. A sidebar Generate with Gemini worked ("snowy mountain…" → 3 coins, a yeti walker and a checkpoint, all present; saved as `demo_m1/snowy.ldtk`). Bugs found:
  1. After a sidebar generate, the in-app reload leaves the canvas blank and the status stuck at "Saved — reloading…". The file itself is fine and opens normally when loaded fresh. (Part 9)
  2. The tile art looks washed out: the layer order is Entities, Collisions, Tiles, so the IntGrid overlay sits on top of the tiles. The order should be Entities, Tiles, Collisions, or the IntGrid should be made transparent. (export)
  3. LDtk marks the level modified (`Level_0*`) as soon as it is opened, so it is changing something on load. Find out what.
  4. The sidebar file label says "No project open" until Generate is clicked. (Part 9)
  5. Platforms are 3 tiles wide, and entities crowd onto one platform (coin, enemy and checkpoint together). Needs prompt and layout guidance.

- 2026-10-13: Part 7 done ([spec](parts/07-fidelity.md)); 248 tests pass. `Requirement.check` is a flat, nullable object (`type`: entity_count, start_item, section, ability, size, gravity or none). `fidelity/` holds the checker, the report and the deterministic fill; the loop is in `pipeline/completeness.py`. The fill adds missing entities, start items and size; then there is **at most one** LLM re-prompt (`complete_spec`) for whatever it cannot fill. Score = (satisfied + 0.5·approximated) / checkable items, or None when nothing is checkable. `GenerateResponse.fidelity` carries the report. The fill runs again after every grid re-prompt. Open: overlapping requirements can be met by the same entities; the fill can overcrowd small sections (placement handles it with warnings); the new prompt and layout guidance has not been tried on a real LLM; the report is not stored in the `.ldtk` yet (Part 8/9).

- 2026-10-13: M1 fixes done ([spec](parts/07b-m1-fixes.md)) and merged; 265 tests pass.
  - **Bug 3 root cause:** the `defs.layers` order did not match `layerInstances`. `Level.tidy()` (`Level.hx` ~628) re-sorted the layers and invalidated the JSON cache, so the editor flagged the level as needing a save. The same re-sort put the IntGrid on top of the tiles (bug 2).
  - **Release builds:** `appBuildId` was 0, which would make LDtk ask to re-save (`ProjectLoader.hx`, `#if !debug`).
  - **Layers:** now Entities, then one Tiles layer per section theme, then Collisions at 0.5 opacity.
  - **Crowding:** one entity per 2 tiles. A crowded platform is widened first; otherwise the extra entities move to a platform in the same section.
  - **Sprites:** packed into `gldtk_assets/sprites.png`. Each instance carries an `F_Tile` field `sprite_tile`, chosen with `find_sprite`.
  - **Cleanup:** `tilesets/` and the root v1 `level.ldtk` were deleted. Pillow is now in the `server` extra.
  - **App test** (`demo_m1/fix/level.ldtk`, fake provider): layer order correct, tiles in full colour with a per-section sheet, sprite icons in the palette and on the canvas, and no `*` after opening.

- 2026-10-14: Part 9 done ([spec](parts/09-sidebar.md)); 268 tests pass. Commits: ldtk-src `c55f392`, main `7902073`. The section dropdown, rectangle selection and undo button were **deferred to Part 8**, because they need its backend; an empty `#g-edit` slot is left for them.
  - **Sidebar:** fidelity report (score, rows, auto tags, reasons), collapsible warnings and preview, provider and model selector (with a new `GET /providers`), Game Profile panel (GET/PUT `/profile`, 422 errors shown inline), and a clear-history button.
  - **Bug 1 (blank canvas after generate):** `App.loadProject` builds a new Editor while the old one is still being disposed. The old one's `onDispose` then hides the canvas and removes the mouse handlers. Fix: swap the project into the open editor the way `LastChance` does (`ProjectLoader`, then `editor.selectProject`).
  - **Bug 4 (label):** a hook in `App.loadPage` calls `gldtkSidebar.onPageLoaded()`, which listens for `ProjectSelected` and `ProjectSaved`.
  - **New load-modified cause:** rewriting `gldtk_assets/*.png` on every generate made LDtk's file watcher reload the tilesets. Assets are now only written when their content changes.
  - **Open:** the warning and failure rows have not been seen in the app (the fixture has only satisfied and unchecked items); the profile panel needs scrolling; the "AI" tab partly covers the auto tags; switching projects during a generate swaps the result into the open editor.

- 2026-10-14: **M2 app test** with real Gemini (the user asked the orchestrator to do it). Two prompts, both verified. Findings:
  1. A spike pit was typed as an Enemy, and the fill then added a fake Enemy for it.
  2. The level was marked `*` after a generate that writes new theme sheets.
  3. Stale renders of removed layers were left after the in-place swap.
  4. Levels were sparse (about 10 platforms).
  5. Vertical levels got column sections.

  Also: labels overlapped.
- 2026-10-14: **M2 fixes** done ([spec](parts/09b-m2-fixes.md)); 307 tests pass.
  - **Python:** a `hazard` check type, and hazard text is never filled as an Enemy. `LevelSpec.orientation` (vertical when height > width) stacks sections bottom → top through layout, aesthetic, verification and fill. `layout/densify.py` adds ground segments, intermediate ledges and fill platforms (about 1 per 50 empty tiles, capped at 100), each reachable and kept off golden-path arcs; it never fails a level (`GenerateRequest.densify`). All theme sheets are copied on the first generate.
  - **Haxe:** LDtk ignores watcher events for images whose bytes are unchanged (`Project.isCachedImageUpToDate`, `Editor.onProjectImageChanged`), and `LevelRender` disposes the old layer renders on `ProjectSelected`.
  - **Orchestrator:** Enemy, Pickup, Weapon, Interactive and NPC hide their name (the subtype label is enough); tileset defs carry `cachedPixelData`, so the title loses `[UNSAVED]`; Gemini falls back to the next model on a read timeout.
  - **Re-test with real Gemini** (`demo_m2b/`): the 40×60 climb has 3 vertical sections in the right order, 37 fill platforms, no `*` and clean labels. The 3-section adventure prompt hit a timeout, then 503 "high demand" on both Flash models, so it is to be re-run at M3.
- 2026-10-14: **Part 10** done ([spec](parts/10-evaluation.md)). `python -m eval.run` runs 27 prompts and reports fidelity, playability, retries and timing, with `--record`/`--replay`, `--max-calls` and `--compare`. It has not been run against real Gemini yet.

## Next steps (resume here)
1. **Part 8** ([spec](parts/08-regional-editing.md)): 8a (backend, in a worktree) and 8b (sidebar, `ldtk-src`) run in parallel. Then M3: the orchestrator runs an app test with real Gemini, including the 3-section prompt that failed in the M2 re-test.
2. Then run the evaluation harness on real Gemini (a small `--limit`), then M4.
- App testing: Playwright from `~/.npm/_npx/9833c18b2d85bc59/node_modules/playwright`, `_electron.launch({executablePath: ldtk-src/app/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron, args: ['.'], cwd: ldtk-src/app})`. Wait for the window that has `button.load` (the first window is a splash). Stub both `dialog.showOpenDialog` and `dialog.showOpenDialogSync`, then click `button.load`. Sidebar selectors: `#g-prompt`, `.g-gen-btn`. To avoid using quota, run the server with `server.provider_factory = lambda req: FakeProvider([fixture])`. On macOS the server process is named `Python`, so stop it with `kill $(lsof -tiTCP:8765 -sTCP:LISTEN)`.
