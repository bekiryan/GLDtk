# GLDtk v2 — Progress

Design: [DESIGN.md](DESIGN.md). Branch: `feat/v2` (main repo and `ldtk-src/`). Baseline commit: `347d02a`.

To resume in a fresh session, say: **"continue GLDtk v2 from docs/v2/PROGRESS.md"**.
Workflow for each part: write a spec in `docs/v2/parts/NN-name.md` → dispatch a subagent → verify the tests and diff → commit (one commit per part) → update this file.

| # | Part | Model | Status | Commit |
|---|---|---|---|---|
| 1 | Level spec schema + structured-output providers | strong | ⏳ next | |
| 2 | Game Profile + ability-aware physics | strong | ⬜ | |
| 3 | Layout v2 (+ physics re-verification on the final grid) | strong | ⬜ | |
| 4 | Entity catalog + full LDtk export | medium | ⬜ | |
| **M1** | **User review: prompt → detailed level end to end** | | ⬜ | |
| 7 | Fidelity report | strong | ⬜ | |
| 9 | Sidebar UI (Haxe) | medium | ⬜ | |
| **M2** | **User review in LDtk** | | ⬜ | |
| 5 | Asset library | light | ⬜ | |
| 8 | Regional editing + undo | strong | ⬜ | |
| **M3** | **User review** | | ⬜ | |
| 10 | Evaluation harness | light | ⬜ | |
| 6 | AI theme/sprite generation | medium | ⬜ | |
| **M4** | **Final review** | | ⬜ | |

## Notes
- 2026-10-13: Design interview complete. Baseline committed. `.env` (git-ignored) holds `GEMINI_API_KEY`. Ollama is installed but has no models.
