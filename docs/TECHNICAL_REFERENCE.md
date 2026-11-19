# GLDtk Technical Reference

Detailed sidebar usage, LLM provider fallback rules, neuro-symbolic pipeline internals, HTTP/CLI API, evaluation/ablation commands, troubleshooting, and standalone desktop app build instructions.

For a quick start, see [README.md](../README.md). For a designer-focused walkthrough, see [USER_GUIDE.md](USER_GUIDE.md).

---

## Using the GLDtk sidebar

Open or create a `.ldtk` project in LDtk; the sidebar on the right works on that file.

- **Generate:** type a prompt, for example *"A sunny forest with 5 coins and a slime, then a dark cave with 2 bats, a spike pit and a treasure chest, and finally a castle with a skeleton boss guarding the exit. The player starts with a sword."*, then press **Generate** (or Ctrl+Enter). The level appears in place.
- **Fidelity report:** every requirement extracted from the prompt is shown as ✅ satisfied, ⚠ approximated, ❌ impossible or missing, or · unchecked (for style requests such as "spooky"), with the reason. Items marked *auto* were added by the deterministic completeness step.
- **Edit** (regenerates part of the level; everything outside it is kept exactly, including your hand edits):
  - **Region:** *Select area*, drag a rectangle on the canvas, describe what should be there.
  - **Section:** pick a section (for example "Dark Cave") and describe the change.
  - **Entities:** add, remove or change entities only ("make the bats stronger", "add 3 coins here"), for the selected entities, a selected area or the whole level.
  - **Describe:** say where ("the gap before the boss"); GLDtk highlights the area it found, and you confirm with *Apply here*.
- **↶ Undo last GLDtk edit:** every generate and edit snapshots the file first (in `.gldtk_history/` next to it, last 30 kept). Undo walks back through them.
- **Game Profile:** physics (gravity, jump speed, run speed, step height), abilities (double jump, dash, wall jump) and **design comfort limits** (longest hop, tallest rise, longest drop) for the whole game, saved as `gldtk.game.json` next to the project. Defaults: gravity 1800 px/s², jump speed 500 px/s, run speed 220 px/s (a short hop: about 4 tiles high, 7–8 tiles across). Levels are verified against it, and a prompt asking for an ability the profile lacks is reported as ❌.
- **Provider:** choose the LLM and optionally a model.
- **Complexity:** a slider (1–10) for the whole level, and optionally per section. It drives enemy and hazard density, jump tightness, branching and mission loops. After generation GLDtk measures the result and adjusts (add or remove enemies, tighten or widen gaps) until the score is close to your target.
- **Rock fill:** a density slider that fills unused space with natural cave rock (noise plus smoothing). The route, its jump arcs and headroom are never touched.
- **Progress and Cancel:** a generate or edit shows live steps (LLM attempt, retry, model fallback, verification, repair). **Cancel** stops it and writes nothing.
- **Route overlay:** shows the verified Start→Exit route on the canvas (walks, hops, falls, the ability used), plus soft-lock spans in red. It refreshes after your hand edits.
- **Difficulty chart:** a per-section difficulty curve with warnings (a spike, a flat stretch); hover a point to flash its section on the canvas.
- **▶ Play:** LDtk has no play mode, so GLDtk adds one. It opens a canvas modal that runs the level with the Game Profile's physics and abilities (within about 1 px of the verifier): pickups, lives, checkpoints, keys, hazards, the Exit, and a ghost of the verified route.
- **Levels (worlds):** a prompt that describes several levels (or the *Levels* setting, 1–8) makes a world: one outline call, then one level per entry, each Exit linked to the next Start. A level list with fidelity scores lets you switch levels; edits and the chart act on the open level, and you can **regenerate a single level** and keep the rest.
- **Structure:** each section has a type (see [Level structure, missions and patterns](#level-structure-missions-and-patterns)). Pick **Make this section a…** to change a section's structure; no LLM call is needed.
- **Memory:** a panel showing what GLDtk learned from your edits, with edit, delete, reset and an off switch (see [Design memory](#design-memory)).

Levels use a curated CC0 asset library (8 tile themes and 25 entity sprites, see [assets/LICENSES.md](../assets/LICENSES.md)). A level with several sections gets one tile layer per section theme. GLDtk state (prompt, sections, requirements, spec, profile path) is stored in the level's custom fields, so the `.ldtk` file is the only source of truth.

---

## LLM providers and the local fallback

| Provider | Key (`.env`) | Default model | Notes |
|---|---|---|---|
| `gemini` (default) | `GEMINI_API_KEY` | `gemini-3.8-flash` | Falls back to `gemini-3.1-flash-lite`, then to the local model |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5` | Prompt caching on the system prompt |
| `ollama` | none | `llama3.1` | Fully local; use `qwen2.5:14b` or larger for best results |
| `openai` / `deepseek` | `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` | `gpt-4o-mini` / `deepseek-chat` | Best effort |

Gemini, Claude and Ollama use schema-enforced structured output (`LevelSpec.json_schema()`).

**Why Gemini sometimes fails:** the free tier allows about 20 requests a day per Flash model (HTTP 429), Google's servers are often overloaded (503 "high demand"), and older models are retired (404). None of these are GLDtk bugs. When you use Gemini with its default model, GLDtk tries `gemini-3.8-flash`, then `gemini-3.1-flash-lite`, and then a **local model**. The local model is set by `GLDTK_LOCAL_FALLBACK` (default `ollama:qwen2.5:14b`; set it to `off` to disable). The sidebar shows a warning when the fallback answered. A local 14B model takes about 1–5 minutes per level on an Apple M-series Mac.

---

## How it works

1. **LLMController (`llm/validator.py`):** Prompt (+ retrieved RAG patterns + Design Memory rules) → `LevelSpec` JSON (`spec/level_spec.py`) → validate → `SymbolicPatcher` (`llm/patcher.py`) → re-prompt with precise errors.
2. **Completeness (`pipeline/completeness.py`, `fidelity/`):** Requirement checking, single-tower collapse, deterministic fill of missing entities/items/structures, and at most one LLM re-prompt.
3. **Mission & Terrain (`mission/`, `layout/terrain/`):** Dormans-style mission graph embedding (`lock_key_cycle`, `ability_gate`, `shortcut_loop`, `treasure_branch`, `boss_before_exit`) and per-section rock carving + RAG pattern stamping (`pipeline/stamps.py`).
4. **Verify + repair (`layout/verify.py`, `layout/softlock.py`, `layout/repair.py`):** Extracts platform graph from the final tile grid, proves Start→Exit with swept `12×24 px` hitbox arcs and `GameProfile` abilities (`symbolic/physics.py`: `½g·t² − v·t + Δy = 0`), proves zero soft-locks via Tarjan SCCs + Item-State Product Graph, and repairs deterministically.
5. **Comfort, Densify & Complexity (`layout/comfort.py`, `layout/densify.py`, `pipeline/complexity.py`):** Short hops, walk-first routing, 2D Chebyshev void infill, and closed-loop 5-factor difficulty tuning.
6. **Rock fill, Aesthetic & Export (`layout/fill.py`, `aesthetic/`, `layout/ldtk_adapter.py`):** Optional domain-warped fBm noise fill outside the protected route envelope, 14-role autotiling, packed entity sprites, and LDtk 1.5.3 schema-validated export.

**Regional editing (`edit/`):** Reads the current file (grid, entities, sections), locks border cells where paths cross the region (**locked seams**), regenerates only the region in local coordinates, splices into the existing JSON, re-autotiles a 1-tile margin, and re-verifies the **whole level** (never writing unverified edits).

Design specs and part-by-part history live in [v2/DESIGN.md](v2/DESIGN.md) and [v2/PROGRESS.md](v2/PROGRESS.md).

---

## Level structure, missions and patterns

### Section structure types

| Type | What you get |
|---|---|
| `corridor` | a walled passage with a floor and ceiling, mostly walking |
| `parkour` | platforms and short hops over a gap or pit |
| `labyrinth` | a maze of rooms and branches, with dead ends |
| `arena` | a large room for a fight |
| `shaft` | a vertical climb with ledges |
| `hub` | a central room with several exits |
| `open` | open ground and a few platforms |

Sections also have a **style**: `walk` (every jump comfortable), `mixed` (default; occasional longer jump) or `parkour` (long jumps up to the physics limit). Select a section and use **Make this section a…** to re-carve it as another type without an LLM call.

### Soft-lock detection & Mission graphs

GLDtk computes strongly connected components (Tarjan SCCs) of the movement graph and an **item-state graph** (`V × P(K)`). Any reachable span that cannot reach an Exit (one-way pit, key behind its door, drop past a key) is repaired deterministically. Mission graphs embed lock-and-key cycles, ability gates, and shortcut loops verified counterfactually (`mission/verify.py`).

### Endless chunks

`POST /chunks` appends seeded noise chunks to a world, one LDtk level per chunk, joined through fixed seam openings and verified across seams.

### Pattern library (RAG)

Indexes MIT-licensed LDtk samples, CC-BY-SA 4.0 *SuperTux*, CC-BY *Open Surge*, and user-approved levels (`GLDTK_PATTERNS_DIR` overrides location):

```bash
python -m patterns.build --dry-run                          # ingest only, no LLM call
python -m patterns.build --provider gemini --max-calls 40   # label with LLM and embed
python -m patterns.build --gaps --provider geometry         # deterministic geometry-based labels for gap windows
python -m patterns.build --vision --vision-provider gemini  # describe rendered PNGs (or ollama, qwen2.5vl:7b)
python -m patterns.coverage --real                          # stamping coverage per type and height
```

### Design memory

Stores local hand-edit diffs, undos, and profile changes in `~/Library/Application Support/GLDtk/memory/` (`GLDTK_MEMORY_DIR` overrides), distilling preference rules into prompts. View, edit, or toggle via the sidebar **Memory** panel or `/memory` endpoints.

---

## Repository layout

```
server.py         FastAPI backend (endpoints listed below)
api/              request and response models, art routes
spec/             LevelSpec schema, validation, entity catalog
llm/              prompts, LLMController, patcher, providers/ (gemini, anthropic, ollama, openai, fallback)
pipeline/         build_level, completeness, complexity loop, worlds, chunks, stamps
layout/           spec layout, comfort, densify, rock fill/noise, route, soft-locks, LDtk adapter/reader
layout/terrain/   structure carvers: corridor, parkour, labyrinth, arena, shaft, hub, open
mission/          mission graph grammar, space embedding, item-state verification
patterns/         pattern library: ingest, label, classify, gaps, embed, retrieve, vision, store (RAG)
memory/           design memory: event log, hand-edit diffs, analysis, preferences
symbolic/         graph schema, physics oracle, wall-jump & piecewise gravity, grid extractor
game/             Game Profile (gldtk.game.json)
fidelity/         requirement checker, prompt requirements, report, deterministic fill, difficulty
edit/             regional editing, seams, stitching, version history
aesthetic/        themes, autotiling, sprites, asset library loader
assets/           CC0 tile themes and sprites (manifest.json)
art/              optional AI theme/sprite generation (Gemini image models; needs a paid key)
eval/             evaluation harness, prompt suite, replay recordings, and offline ablation suite
packaging/        PyInstaller spec for the bundled backend
scripts/          asset builder, Mac build script, Draw.io diagram generator, LaTeX/figure scripts
tests/            offline tests (fake LLM with recorded responses)
ldtk-src/         LDtk fork with the GLDtk sidebar (separate git repository)
example.py        generate a level from the command line
```

---

## Without the app: Python and HTTP

```bash
python example.py "A frozen mountain climb with 3 checkpoints and a yeti at the top"
python example.py --provider ollama --model qwen2.5:14b "a small cave with a spike pit"
```

Over HTTP with `server.py` running:

```bash
curl -s http://127.0.0.1:8765/generate -H 'Content-Type: application/json' -d '{
  "description": "a forest with 5 coins and a slime",
  "output_path": "/absolute/path/level.ldtk"
}'
```

| Endpoint | Purpose |
|---|---|
| `POST /generate` | build a level or world (`description`, `output_path`, `seed`, `complexity`, `fill`, `levels`, `level`, `job_id`, `densify`, …) |
| `POST /edit/region` | `{path, rect: {col,row,w,h}, prompt}` regenerate a rectangle |
| `POST /edit/section` | `{path, section_id, prompt}`; a structure change needs no LLM call |
| `POST /edit/entities` | `{path, prompt, rect?, entity_iids?}` entity-only edit |
| `POST /edit/locate` | `{path, text}` find the area a description means |
| `POST /undo`, `GET /history` | step back through snapshots; list them |
| `GET /route`, `POST /route` | verified route and soft-lock spans |
| `GET /difficulty` | per-section difficulty curve |
| `POST /chunks` | `{path, count, seed, theme?, orientation?}` append endless chunks |
| `GET /progress`, `POST /cancel` | live steps and cancel for a `job_id` |
| `GET /memory`, `PUT /memory`, `DELETE /memory`, `POST /memory/scan` | design memory management |
| `GET /profile`, `PUT /profile` | Game Profile |
| `GET /providers`, `GET /themes` | available LLMs; asset themes |
| `GET /settings`, `POST /settings` | API key and local fallback |
| `GET /health` | liveness |
| `POST /art/theme`, `POST /art/sprite`, `GET /art/library` | optional AI art |

Environment variables: `GEMINI_API_KEY` (`.env`), `GLDTK_LOCAL_FALLBACK`, `GLDTK_MEMORY_DIR`, `GLDTK_PATTERNS_DIR`, `GLDTK_CONFIG_DIR`, `GLDTK_HOST`, `GLDTK_PORT`, and `GLDTK_FAKE_LLM` (tests only).

---

## Evaluation, Ablations & Tests

```bash
source .venv/bin/activate
pytest                                              # offline unit & pipeline suite
pytest -m e2e tests/e2e/test_gemini.py -s           # live Gemini e2e check
python -m eval.run --tags structure --fallback      # live eval with fallback & reason tracking
python -m eval.ablation                             # offline replay ablations A1–A5 -> eval/results/ablations/
```

See [eval/README.md](../eval/README.md) and [eval/results/ablations/report.md](../eval/results/ablations/report.md).

---

## Troubleshooting

- **"Cannot reach GLDtk server":** start the backend with `./start.sh` or `python server.py`.
- **Gemini 429 / 503 / 404:** pull the local fallback model (`ollama pull qwen2.5:14b`) or pick another provider in Settings.
- **Ollama "model not found":** run `ollama pull <model>` and check `ollama list`.
- **Generate or edit fails with "not verified" / soft-lock error:** nothing was written; retry with another seed or simplify the prompt.
- **Tiles are blank in LDtk:** keep the generated `gldtk_assets/` folder next to the `.ldtk` file.

---

## Build the Mac app

`scripts/build_mac_app.sh` builds an unsigned Apple Silicon `GLDtk.app` and `.dmg` in `ldtk-src/app/redist/`:

1. PyInstaller one-folder build of `server.py` (`packaging/gldtk-server.spec`) into `packaging/dist/gldtk-server`;
2. Haxe release builds (`renderer.hxml`, `main.hxml`);
3. `electron-builder --mac dmg --arm64` (`CSC_IDENTITY_AUTO_DISCOVERY=false`).

Re-run `renderer.debug.hxml` for development after a release build. Smoke-test a bundle with `python scripts/smoke_test_app.py packaging/dist/gldtk-server/gldtk-server`.
