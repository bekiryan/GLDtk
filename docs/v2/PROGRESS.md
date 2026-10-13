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
| 7 | Fidelity report | strong | ⬜ | |
| 9 | Sidebar UI (Haxe) | medium | ⬜ | |
| **M2** | **User review in LDtk** | | ⬜ | |
| 5 | Asset library | light | ✅ (pulled forward) | see git log |
| 8 | Regional editing + undo | strong | ⬜ | |
| **M3** | **User review** | | ⬜ | |
| 10 | Evaluation harness | light | ⬜ | |
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

## Next steps (resume here)
1. **Part 7 (fidelity report + completeness loop):** spec written: [parts/07-fidelity.md](parts/07-fidelity.md). Next, dispatch it in a worktree (strong model).
2. **In parallel, "M1 fixes"**, spec written: [parts/07b-m1-fixes.md](parts/07b-m1-fixes.md). Dispatch it in a worktree (medium model). It covers:
   - app-test bugs 2, 3 and 5 above;
   - library sprites as LDtk entity icons;
   - per-section tilesets.
3. Then **Part 9** (sidebar), including app-test bugs 1 and 4. Then M2.
- Merged worktree branches can be deleted: `worktree-agent-a4c95…` (Part 5), `aa72ba…` (Part 4), `ae8be5…` (Part 6).
- App testing: Playwright `_electron` with `args: ['.']`, `cwd = ldtk-src/app`. Open a level by stubbing `dialog.showOpenDialog` in the main process, then clicking `button.load`. Sidebar selectors: `#g-prompt`, `.g-gen-btn`.
