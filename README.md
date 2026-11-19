# GLDtk — Generative Level Design Toolkit

GLDtk turns a text description into a **playable 2D platformer level** in [LDtk](https://ldtk.io), right
inside the editor. Describe a whole level (sections, enemies, coins, weapons, hazards, mood), and GLDtk
builds it, proves it can be beaten, and tells you how much of the prompt it achieved. You can then select
or describe any part of the level and regenerate only that part.

It is a **neuro-symbolic pipeline**: an LLM handles creativity, while deterministic layout, physics and
checks handle correctness. Every level written to disk is statically verified: the physics oracle proves
that the player can get from Start to Exit with the game's own jump and ability settings.

New to GLDtk? Read the **[user guide](docs/USER_GUIDE.md)**: installing (macOS, Windows, from source),
the first run, every sidebar function, example prompts and troubleshooting.

---

## Contents

1. [Quick start](#quick-start)
2. [Using the GLDtk sidebar](#using-the-gldtk-sidebar)
3. [LLM providers and the local fallback](#llm-providers-and-the-local-fallback)
4. [How it works](#how-it-works)
5. [Level structure, missions and patterns](#level-structure-missions-and-patterns)
6. [Repository layout](#repository-layout)
7. [Without the app: Python and HTTP](#without-the-app-python-and-http)
8. [Evaluation](#evaluation)
9. [Tests](#tests)
10. [Troubleshooting](#troubleshooting)
11. [Build the Mac app](#build-the-mac-app)

---

## Quick start

Requirements: macOS/Linux, Python 3.11+, Node.js (to run LDtk), and an LLM: a free
[Gemini API key](https://aistudio.google.com/apikey) or a local [Ollama](https://ollama.com) model.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[server,dev]"

echo 'GEMINI_API_KEY=your-key' > .env          # git-ignored; read by server.py
ollama pull qwen2.5:14b                        # optional: local fallback (about 9 GB)

./start.sh                                     # starts the backend, then LDtk
```

`start.sh` runs the FastAPI backend (`server.py`, `http://127.0.0.1:8765`) in the background, starts the
LDtk app from `ldtk-src/app`, and stops the backend when LDtk closes. To run them separately:

```bash
source .venv/bin/activate && python server.py   # backend
cd ldtk-src/app && npm start                    # LDtk with the GLDtk sidebar
```

Prefer an app? Download the macOS `.dmg` from the GitHub Releases page, or build it yourself (see
[Build the Mac app](#build-the-mac-app)).

`ldtk-src/` is a fork of LDtk ([github.com/bekiryan/ldtk](https://github.com/bekiryan/ldtk), branch
`feat/v2`) with the GLDtk sidebar built in. After changing its Haxe sources, rebuild it (see
[CLAUDE.md](CLAUDE.md#building-the-ldtk-integration-haxe)).

---

## Using the GLDtk sidebar

Open or create a `.ldtk` project in LDtk; the sidebar on the right works on that file.

- **Generate:** type a prompt, for example *"A sunny forest with 5 coins and a slime, then a dark cave
  with 2 bats, a spike pit and a treasure chest, and finally a castle with a skeleton boss guarding the
  exit. The player starts with a sword."*, then press **Generate** (or Ctrl+Enter). The level appears in
  place.
- **Fidelity report:** every requirement extracted from the prompt is shown as ✅ satisfied, ⚠
  approximated, ❌ impossible or missing, or · unchecked (for style requests such as "spooky"), with the
  reason. Items marked *auto* were added by the deterministic completeness step.
- **Edit** (regenerates part of the level; everything outside it is kept exactly, including your hand
  edits):
  - **Region:** *Select area*, drag a rectangle on the canvas, describe what should be there.
  - **Section:** pick a section (for example "Dark Cave") and describe the change.
  - **Entities:** add, remove or change entities only ("make the bats stronger", "add 3 coins here"),
    for the selected entities, a selected area or the whole level.
  - **Describe:** say where ("the gap before the boss"); GLDtk highlights the area it found, and you
    confirm with *Apply here*.
- **↶ Undo last GLDtk edit:** every generate and edit snapshots the file first (in
  `.gldtk_history/` next to it, last 30 kept). Undo walks back through them.
- **Game Profile:** physics (gravity, jump speed, run speed, step height), abilities (double jump,
  dash, wall jump) and **design comfort limits** (longest hop, tallest rise, longest drop) for the
  whole game, saved as `gldtk.game.json` next to the project. Defaults: gravity 1800 px/s², jump
  speed 500 px/s, run speed 220 px/s (a short hop: about 4 tiles high, 7–8 tiles across). Levels are verified against it, and a
  prompt asking for an ability the profile lacks is reported as ❌. The default numbers are listed in
  the profile panel (see the Game Profile).
- **Provider:** choose the LLM and optionally a model.
- **Complexity:** a slider (1–10) for the whole level, and optionally per section. It drives enemy and
  hazard density, jump tightness, branching and mission loops. After generation GLDtk measures the
  result and adjusts (add or remove enemies, tighten or widen gaps) until the score is close to your
  target.
- **Rock fill:** a density slider that fills unused space with natural cave rock (noise plus smoothing).
  The route, its jump arcs and headroom are never touched.
- **Progress and Cancel:** a generate or edit shows live steps (LLM attempt, retry, model fallback,
  verification, repair). **Cancel** stops it and writes nothing.
- **Route overlay:** shows the verified Start→Exit route on the canvas (walks, hops, falls, the
  ability used), plus soft-lock spans in red. It refreshes after your hand edits.
- **Difficulty chart:** a per-section difficulty curve with warnings (a spike, a flat stretch); hover a
  point to flash its section on the canvas.
- **▶ Play:** LDtk has no play mode, so GLDtk adds one. It opens a canvas modal that runs the level with
  the Game Profile's physics and abilities (within about 1 px of the verifier): pickups, lives,
  checkpoints, keys, hazards, the Exit, and a ghost of the verified route.
- **Levels (worlds):** a prompt that describes several levels (or the *Levels* setting, 1–8) makes a
  world: one outline call, then one level per entry, each Exit linked to the next Start. A level list
  with fidelity scores lets you switch levels; edits and the chart act on the open level, and you can
  **regenerate a single level** and keep the rest.
- **Structure:** each section has a type (see
  [Level structure](#level-structure-missions-and-patterns)). Pick **Make this section a…** to change
  a section's structure; no LLM call is needed.
- **Memory:** a panel showing what GLDtk learned from your edits, with edit, delete, reset and an off
  switch (see [Design memory](#design-memory)).

Levels use a curated CC0 asset library (8 tile themes and 25 entity sprites, see `assets/LICENSES.md`).
A level with several sections gets one tile layer per section theme. GLDtk state (prompt, sections,
requirements, spec, profile path) is stored in the level's custom fields, so the `.ldtk` file is the
only source of truth.

---

## LLM providers and the local fallback

| Provider | Key (`.env`) | Default model | Notes |
|---|---|---|---|
| `gemini` (default) | `GEMINI_API_KEY` | `gemini-3.8-flash` | Falls back to `gemini-3.1-flash-lite`, then to the local model |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5` | Prompt caching on the system prompt |
| `ollama` | none | `llama3.1` | Fully local; use `qwen2.5:14b` or larger for best results |
| `openai` / `deepseek` | `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` | `gpt-4o-mini` / `deepseek-chat` | Best effort |

Gemini, Claude and Ollama use schema-enforced structured output (`LevelSpec.json_schema()`).

**Why Gemini sometimes fails:** the free tier allows about 20 requests a day per Flash model (HTTP 429),
Google's servers are often overloaded (503 "high demand"), and older models are retired (404). None of
these are GLDtk bugs. When you use Gemini with its default model, GLDtk tries `gemini-3.8-flash`, then
`gemini-3.1-flash-lite`, and then a **local model**. The local model is set by `GLDTK_LOCAL_FALLBACK`
(default `ollama:qwen2.5:14b`; set it to `off` to disable). The sidebar shows a warning when the fallback
answered. A local 14B model takes about 1–5 minutes per level on an Apple M-series Mac.

---

## How it works

```
Prompt
  │
  ▼  LLMController (llm/validator.py)            LLM → LevelSpec JSON (spec/level_spec.py):
  │    generate → validate → symbolic patch →     size, sections (with structure type and style),
  │    re-prompt with precise errors              graph, entities, requirements, complexity.
  │                                               Retrieved patterns and your design memory
  │                                               are added to the prompt.
  ▼
  Completeness (pipeline/completeness.py)         fidelity check; deterministic fill of missing
  │                                               entities/items; at most one LLM re-prompt
  ▼
  Terrain + layout (layout/terrain/, spec_layout) per-section generators carve rock into floors,
  │  (+ mission placement, mission/)               walls, corridors, labyrinths…; keys, locks and
  │                                               gates are placed from the mission graph
  ▼
  Verify + repair (layout/verify.py, softlock.py) extract the graph from the final tile grid, prove
  │                                               Start→Exit with hitbox-swept arcs and the Game
  │                                               Profile's abilities, and prove there is no
  │                                               soft-lock; repair, or re-prompt as a last resort
  ▼
  Comfort pass (layout/comfort.py)                short hops and walk-first paths (Game Profile
  │                                               comfort limits)
  ▼
  Densify (layout/densify.py)                     extra reachable platforms and ground
  ▼
  Complexity loop (pipeline/complexity.py)        measure difficulty, adjust toward the target
  ▼
  Rock fill (layout/fill.py, noise.py)            optional noise fill outside the protected route
  ▼
  Aesthetic (aesthetic/)                          per-section themes, autotiling, entity sprites
  ▼
  LDtk export (layout/ldtk_adapter.py)            LDtk 1.5.3 JSON with typed entity fields,
                                                  validated against LDtk's JSON schema
```

**Physics oracle** (`symbolic/physics.py`): jumps are checked with the kinematic equation
`½g·t² − v·t + Δy = 0`, extended for double jump (chained arcs), dash, wall jump and per-section
`gravity_scale`. It is deterministic and takes milliseconds, so it runs on every candidate.

**Symbolic patcher** (`llm/patcher.py`): before spending another LLM call, deterministic patches fix
common mistakes (a downward Jump reclassified as Fall, wrong launch velocities, missing stepping stones,
missing Start or Exit).

**Regional editing** (`edit/`): the current file is read (grid, entities, sections). The cells where the
player's paths cross the region border are **locked seams**. The LLM regenerates only the region, in
region-local coordinates. The result is spliced into the existing JSON, the 1-tile margin is re-autotiled,
and the **whole level is re-verified**. A level that fails verification is never written.

The design and part-by-part history are in [docs/v2/DESIGN.md](docs/v2/DESIGN.md) and
[docs/v2/PROGRESS.md](docs/v2/PROGRESS.md).

---

## Level structure, missions and patterns

### Section structure types

The LLM picks a type for each section and deterministic generators (`layout/terrain/`) carve the
terrain from solid rock, so levels have floors, walls, ceilings and rooms, not only platforms.

| Type | What you get |
|---|---|
| `corridor` | a walled passage with a floor and ceiling, mostly walking |
| `parkour` | platforms and short hops over a gap or pit |
| `labyrinth` | a maze of rooms and branches, with dead ends |
| `arena` | a large room for a fight |
| `shaft` | a vertical climb with ledges |
| `hub` | a central room with several exits |
| `open` | open ground and a few platforms |

Paths are mostly **walking, fighting and short hops**: the comfort limits in the Game Profile cap a hop,
a rise and a drop, and the route prefers walking. Sections also have a **style**: `walk` (every
jump comfortable), `mixed` (the default; an occasional longer jump) or `parkour` (long jumps up to the
physics limit, only when the prompt asks for it). Select a section and use **Make this section a…** to re-carve it as another type
without an LLM call.

### Soft-lock detection

The guarantee is "the player can never get stuck", not only "Start→Exit exists". GLDtk computes the
strongly connected components of the movement graph and an **item-state graph** (position plus keys,
abilities and switches held). A span the player can reach but cannot leave for an Exit (a pit with no
way out, a door whose key is behind it, a drop past the key) is an error. Deterministic repairs add a
ledge or a one-way exit, or move the key. Soft-lock spans show in red on the route overlay.

### Mission graphs

For prompts with keys, locks, abilities or backtracking, GLDtk builds a **mission graph** with a small
graph grammar: lock-and-key cycles, ability gates, shortcut loops and optional branches. It is embedded
into sections (`mission/`), and the item-state graph proves every lock is reachable only after its key,
with no soft-lock. "The key is in the cave, the door is in the castle" is a structural check in the
fidelity report.

### Endless chunks

`POST /chunks` appends seeded noise chunks to a world, one LDtk level per chunk. The same seed and chunk
number always give the same chunk, and neighbours join through fixed seam openings. Each chunk is
verified, and soft-lock checked, on its own and across its seams.

### Pattern library

GLDtk can show the LLM real level fragments as examples (retrieval-augmented generation). **Licensed
sources only:** LDtk's own MIT-licensed sample projects, plus your levels and GLDtk levels you
approved (kept, not undone). No commercial game levels are used. Each fragment is cut into a room or
set-piece, checked (soft-locking patterns are rejected), labelled by one LLM call, embedded, and stored
in a local index in the app data folder (`GLDTK_PATTERNS_DIR` overrides it). At generate or edit time the
top matches go into the prompt, and the response lists which patterns were used. Only the layout is kept,
never the art.

```bash
python -m patterns.build --dry-run                          # ingest only, no LLM call
python -m patterns.build --provider gemini --max-calls 40   # label and embed
python -m patterns.build --gaps                             # add rare-type (labyrinth, arena, hub) and tall parkour windows
python -m patterns.build --vision --vision-provider gemini  # describe a rendered PNG of each pattern (or ollama, qwen2.5vl:7b)
python -m patterns.coverage --real                          # which structure types stamp at which heights
```

### Design memory

GLDtk learns your taste from what you change: hand edits to its output, undos, accepted edits, Game
Profile changes. A local event log is turned into short preference rules ("prefers 2–3 enemies per
room") that go into later prompts and adjust deterministic parameters. It is **local and
cross-project**, stored in `~/Library/Application Support/GLDtk/memory/` (`GLDTK_MEMORY_DIR` overrides
it), and never uploaded except the short rule text inside an LLM prompt. The sidebar **Memory** panel
(or `GET/PUT/DELETE /memory`) lets you view, edit, delete or reset rules, or turn memory off.

---

## Repository layout

```
server.py         FastAPI backend (endpoints listed below)
api/              request and response models, art routes
spec/             LevelSpec schema, validation, entity catalog
llm/              prompts, LLMController, patcher, providers/ (gemini, anthropic, ollama, openai, fallback)
pipeline/         build_level (spec → verified level), completeness, complexity loop, worlds, chunks.py
layout/           spec layout, comfort, densify, rock fill and noise, route, soft-locks, grid
                  verification and repair, LDtk export and reader, world.py
layout/terrain/   structure generators: corridor, parkour, labyrinth, arena, shaft, hub, open
mission/          mission graph grammar, space embedding, item-state verification
patterns/         pattern library: ingest, label, embed, retrieve, store (RAG)
memory/           design memory: event log, hand-edit diffs, analysis, preferences
symbolic/         graph schema, physics oracle, grid → graph extractor
game/             Game Profile (gldtk.game.json)
fidelity/         requirement checker, report, deterministic fill
edit/             regional editing, seams, stitching, version history
aesthetic/        themes, autotiling, sprites, asset library loader
assets/           CC0 tile themes and sprites (manifest.json)
art/              optional AI theme/sprite generation (Gemini image models; needs a paid key)
eval/             evaluation harness and prompt suite
packaging/        PyInstaller spec for the bundled backend
scripts/          build_assets.py, build_mac_app.sh
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

`example.py` uses the same code path as the sidebar and writes `example_out/level.ldtk`.

Over HTTP, with the backend running:

```bash
curl -s http://127.0.0.1:8765/generate -H 'Content-Type: application/json' -d '{
  "description": "a forest with 5 coins and a slime",
  "output_path": "/absolute/path/level.ldtk"
}'
```

Edits take the same `.ldtk` path; rects are in tiles, with row 0 at the top. Every generate and edit
request accepts `provider` and `model`.

| Endpoint | Purpose |
|---|---|
| `POST /generate` | build a level or world (`description`, `output_path`, `seed`, `complexity`, `fill`, `levels`, `level`, `job_id`, `densify`, …) |
| `POST /edit/region` | `{path, rect: {col,row,w,h}, prompt}` regenerate a rectangle |
| `POST /edit/section` | `{path, section_id, prompt}`; a structure change needs no LLM call |
| `POST /edit/entities` | `{path, prompt, rect?, entity_iids?}` entity-only edit |
| `POST /edit/locate` | `{path, text}` find the area a description means |
| `POST /undo`, `GET /history` | step back through snapshots; list them |
| `GET /route`, `POST /route` | verified route and soft-lock spans (POST re-extracts after hand edits) |
| `GET /difficulty` | per-section difficulty curve |
| `POST /chunks` | `{path, count, seed, theme?, orientation?}` append endless chunks |
| `GET /progress`, `POST /cancel` | live steps and cancel for a `job_id` |
| `GET /memory`, `PUT /memory`, `DELETE /memory`, `POST /memory/scan` | design memory: view, edit, reset, scan a file for hand edits |
| `GET /profile`, `PUT /profile` | Game Profile |
| `GET /providers`, `GET /themes` | available LLMs; asset themes |
| `GET /settings`, `POST /settings` | API key and local fallback (used by the Mac app) |
| `GET /health` | liveness |
| `POST /art/theme`, `POST /art/sprite`, `GET /art/library` | optional AI art |

Environment variables: `GEMINI_API_KEY` (`.env`), `GLDTK_LOCAL_FALLBACK`, `GLDTK_MEMORY_DIR`,
`GLDTK_PATTERNS_DIR`, `GLDTK_CONFIG_DIR` (where the Mac app keeps its settings), `GLDTK_HOST`,
`GLDTK_PORT`, and `GLDTK_FAKE_LLM` (tests only: canned LLM replies).

---

## Evaluation

`python -m eval.run` runs a 27-prompt suite through the real pipeline and reports fidelity, playability,
retries and timing per provider and model. See [eval/README.md](eval/README.md). The first real run
(Gemini, 8 prompts, 2026-10-15) scored **100% playable and 100% fidelity**
(`eval/results/gemini-2026-10-15/report.md`). Its responses are recorded, so the run can be replayed
offline with `--replay eval/recordings/gemini-2026-10-15`. A 503 is retried after 20, 60 and 120 s
(`--backoff`, `0` turns it off); `--fallback` lets the local model answer when Gemini cannot, and each
record's `answered_by` names the model that did.

---

## Tests

```bash
source .venv/bin/activate
pytest                                       # offline: fake LLM with recorded responses
pytest -m e2e tests/e2e/test_gemini.py -s    # one real Gemini call (uses free-tier quota)
```

---

## Troubleshooting

- **"Cannot reach GLDtk server":** the backend is not running. Start it with `./start.sh` or
  `python server.py`.
- **Gemini 429 / 503 / 404:** see [the providers section](#llm-providers-and-the-local-fallback).
  Pull the local fallback model, or pick another provider in the sidebar.
- **Ollama "model not found":** run `ollama pull <model>` and check `ollama list`.
- **A generate or edit fails with "not verified" or a soft-lock error:** the level could not be proven beatable with the
  current Game Profile, so nothing was written. Try again (another seed) or simplify the request.
- **Tiles are blank in LDtk:** the theme sheets are copied to `gldtk_assets/` next to the `.ldtk`. Keep
  that folder with the project.

---

## Build the Mac app

`scripts/build_mac_app.sh` builds an unsigned, Apple Silicon `GLDtk.app` and `.dmg` that bundle the
backend (no terminal, venv or Node needed to run it):

1. PyInstaller one-folder build of `server.py` (`packaging/gldtk-server.spec`) into `packaging/dist/gldtk-server`;
2. Haxe release builds (`renderer.hxml`, `main.hxml`);
3. `electron-builder --mac dmg --arm64` with `CSC_IDENTITY_AUTO_DISCOVERY=false` (no code signing or notarization).

The `.dmg` lands in `ldtk-src/app/redist/`. The app starts `gldtk-server` on port 8765 (or a free port) and stops it on
quit. Enter the Gemini API key in the sidebar on first launch; it is saved to
`~/Library/Application Support/GLDtk/.env`. Note the release renderer build overwrites
`ldtk-src/app/assets/js/renderer.js`; re-run `renderer.debug.hxml` for development. `gldtk-server --check` smoke-tests a bundle.

**Haxe libraries.** The script needs Haxe 4.3 and the LDtk haxelibs. `scripts/setup_haxelibs.sh` (bash) installs them at the tested versions, with `deepnightLibs` pinned to `e1237f8`; run it against a fresh
haxelib repo (`haxelib setup <dir>`), since it re-points existing libraries. Clone the LDtk fork into `ldtk-src/` first
(`git clone https://github.com/bekiryan/ldtk ldtk-src`).

**CI.** `.github/workflows/release.yml` builds it on `macos-latest` (manual run, or a `v*` tag), smoke-tests the
bundled backend and uploads the `.dmg` as a workflow artifact; on a tag it also attaches it to a draft GitHub
Release.
