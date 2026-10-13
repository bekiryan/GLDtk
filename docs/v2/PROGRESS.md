# GLDtk v2 — Progress

Design: [DESIGN.md](DESIGN.md). Branch: `feat/v2` (main repo and `ldtk-src/`). Baseline commit: `347d02a`.

To resume in a fresh session, say: **"continue GLDtk v2 from docs/v2/PROGRESS.md"**.
Workflow for each part: write a spec in `docs/v2/parts/NN-name.md` → dispatch a subagent → verify the tests and diff → commit (one commit per part) → update this file → **push `feat/v2`** (user request, 2026-10-13). Haxe parts are also pushed from `ldtk-src/`.

| # | Part | Model | Status | Commit |
|---|---|---|---|---|
| 1 | Level spec schema + structured-output providers | strong | ✅ | see git log |
| 2 | Game Profile + ability-aware physics | strong | ✅ | see git log |
| 3 | Layout v2 (+ physics re-verification on the final grid) | strong | ✅ | see git log |
| 4 | Entity catalog + full LDtk export | medium | ⏳ running (worktree) | |
| **M1** | **User review: prompt → detailed level end to end** | | ⬜ | |
| 7 | Fidelity report | strong | ⬜ | |
| 9 | Sidebar UI (Haxe) | medium | ⬜ | |
| **M2** | **User review in LDtk** | | ⬜ | |
| 5 | Asset library | light | ⏳ running (worktree, pulled forward) | |
| 8 | Regional editing + undo | strong | ⬜ | |
| **M3** | **User review** | | ⬜ | |
| 10 | Evaluation harness | light | ⬜ | |
| 6 | AI theme/sprite generation | medium | ⬜ | |
| **M4** | **Final review** | | ⬜ | |

## Notes
- 2026-10-13: Design interview complete. Baseline committed. `.env` (git-ignored) holds `GEMINI_API_KEY`. Ollama is installed but has no models.
- 2026-10-13: Part 1 done ([spec](parts/01-level-spec.md)). 64 offline tests pass; the Gemini e2e run passes. Gemini free tier: **20 requests/day for `gemini-3.8-flash`** (quota is per model), and Flash was returning 503 "high demand" all session. The provider falls back to `gemini-3.1-flash-lite` on 429/5xx when no model is specified. Spec deviations: separate `EXIT_ENTITY_MISMATCH` and `UNKNOWN_EDGE_NODE` codes; strict schema (all fields required, optional = nullable); `controller.stats` holds the attempt and patch counts Part 10 needs. Ollama and Anthropic structured output are covered only by request-building tests (Ollama has no models installed).
- 2026-10-13: Part 2 done ([spec](parts/02-game-profile.md)). 99 offline tests pass. The double-jump oracle is exact and closed-form; the orchestrator checked it against a brute-force τ search (20k random cases, no mismatches). Open items for Part 3: Fall edges are not yet capped by `run_speed`; `TILE_SIZE = 16` is fixed in `spec/level_spec.py` and the prompt, while the profile has its own `tile_size`; wall-jump edges must be checked for a real wall on the final grid; `SteppingStonePatch` ignores abilities.
- 2026-10-13: Part 3 done ([spec](parts/03-layout-v2.md)). 136 offline tests pass. The layout keeps the LLM's coordinates (`col=round(x/16)`, `row=H-1-round(y/16)`) and section order. The final grid is verified with hitbox-swept trajectories (12×24 px, 6 px samples), repaired deterministically (stepping stones, nudges), and the LLM is re-prompted only as a last resort. A 300×100 level with 87 nodes builds in about 1.6 s; the 50-seed property test verified 50/50. Open: the gravity scale of the starting section applies to the whole arc; walking under a low ceiling along one span is not checked; wall-jump paths are approximate; the server writes no `.ldtk` when verification fails.
- 2026-10-13: The user asked for independent parts to run in parallel. Parts 4 and 5 run at the same time in separate git worktrees (they edit disjoint files), and the orchestrator merges them and does the server wiring. Milestone review points are unchanged.
