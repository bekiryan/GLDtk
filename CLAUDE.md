# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

GLDtk (Generative Level Design Toolkit) is a neuro-symbolic pipeline that converts text prompts into playable 2D platformer levels exported as LDtk JSON. It is integrated directly into LDtk as a native Haxe sidebar panel — not a browser extension or JS injection.

**Two processes run together:**
- `server.py` — FastAPI backend (Python) at `http://127.0.0.1:8765`
- `ldtk-src/app/` — LDtk Electron app (Haxe, compiled to JS) with the GLDtk panel built in

## Running everything

```bash
# Start both together (recommended)
./start.sh

# Or separately:
source .venv/bin/activate && python server.py   # backend
cd ldtk-src/app && npm start                    # LDtk frontend
```

## Building the LDtk integration (Haxe)

Both `.hxml` files must be built whenever Haxe source changes. Run from `ldtk-src/`:

```bash
cd ldtk-src

# Renderer (the UI — GldtkSidebar.hx lives here)
HAXE_STD_PATH="/opt/homebrew/lib/haxe/std" /opt/homebrew/bin/haxe renderer.debug.hxml

# Main process (Electron main — only needed once or if main.hxml sources change)
HAXE_STD_PATH="/opt/homebrew/lib/haxe/std" /opt/homebrew/bin/haxe main.hxml
```

Warnings about `WDeprecatedEnumAbstract` and `WDeprecated` are expected and harmless. Any actual error stops the build.

**Library version pin:** `deepnightLibs` must be at commit `e1237f8`. It is pinned in `~/haxelib/deepnightLibs/git/`. Do not run `haxelib update` or `haxelib install deepnightLibs` — it will break the build with API incompatibilities.

## Python environment

```bash
source .venv/bin/activate
pip install -e ".[server,dev]"   # installs all deps including fastapi/uvicorn and pytest
pip install pillow            # required for tileset generation script
```

## Pipeline architecture

Stage order (`pipeline/level.py::build_level`; worlds in `pipeline/world.py`, endless chunks in `pipeline/chunks.py`):

```
Prompt (+ retrieved patterns from patterns/, + design-memory rules from memory/)
  → LLMController (llm/validator.py): generate → validate → SymbolicPatcher → re-prompt   → LevelSpec
  → completeness (pipeline/completeness.py, fidelity/): deterministic fill, ≤1 LLM re-prompt
  → terrain/layout (layout/terrain/*, layout/spec_layout.py) + mission placement (mission/)
  → verify/repair (layout/verify.py, repair.py; soft-locks: layout/softlock.py, softlock_repair.py)
  → comfort (layout/comfort.py) → densify (layout/densify.py)
  → complexity closed loop (pipeline/complexity.py)
  → rock fill (layout/fill.py, noise.py; optional)
  → aesthetic (aesthetic/pipeline.py: theme, autotile, entities)
  → export (layout/ldtk_adapter.py → LDtk 1.5.3 JSON)
  → App.ME.loadProject(path)   ← GldtkSidebar.hx, after the server responds
```

**IRLevel** (`layout/ir.py`): `grid[row][col]`, row 0 = visual top. `TileValue`: EMPTY=0, SOLID=1, HAZARD=2. Entities stored separately in `ir.entities`.

**AbstractLevelGraph** (`symbolic/schema.py`): `nx.DiGraph` wrapper. Nodes have `NodeType` (PLATFORM, START, EXIT, HAZARD). Edges have `EdgeType` (WALK, JUMP, FALL). Physics oracle (`symbolic/physics.py`) validates jump arcs using `½g·t² − v·t + Δy = 0`.

**GameProfile** (`game/profile.py`, file `gldtk.game.json` beside the `.ldtk`): physics (gravity, jump_v, run_speed, walk_step_px) and abilities (double_jump, dash, wall_jump). It replaces the old `PhysicsParams`. The oracle entry points are `reachable()` / `best_ability()` in `symbolic/physics.py`; edges carry `metadata["ability"]` and `metadata["gravity_scale"]` (from the source section).

**Soft-lock guarantee:** every reachable span must reach an Exit (SCCs of the movement graph plus an item-state graph of keys/abilities/switches; mission locks must be reachable only after their key). Violations are verification errors; `softlock_repair.py` fixes them deterministically. A level is never written unless it is verified.

**Comfort limits:** `GameProfile.design` (`comfort_hop_tiles`, `comfort_rise_tiles`, `comfort_drop_tiles`) say what a normal move should be; the route prefers walking and the comfort pass adds platforms. Default physics (Part 28): gravity 1800, jump_v 500, run_speed 220 — a ~4.3-tile-high, ~7.6-tile-wide jump; saved profiles keep their own numbers (details in `game/profile.py`).

**Structure types:** each `Section` has a structure type (corridor, parkour, labyrinth, arena, shaft, hub, open) and a `style` (walk | mixed | parkour; null = mixed; only parkour keeps long jumps). `layout/terrain/` generators carve from rock; a section edit that only changes structure makes no LLM call. Mission graphs (`mission/`: grammar, space embedding, item-state verify) add keys, locks, ability gates and loops. Rock fill and chunks never touch the protected route (arcs, headroom, comfort platforms).

**Multi-level files:** a world is several LDtk levels; `GenerateRequest.level` regenerates one level, and `level` (iid or identifier) on `/route`, `/difficulty` and the edit endpoints selects which level they act on (default: the first).

**Other packages:** `patterns/` (pattern library / RAG, licensed sources only; `python -m patterns.build`, `--gaps` adds rare-type / tall windows; `--vision [--vision-provider gemini|ollama]` adds a vision-model description of a rendered PNG as `label.visual`, checked against the grid's counts, `patterns/vision.py`; `python -m patterns.coverage [--real]` prints the stamping coverage), `memory/` (cross-project design memory, endpoints `/memory*`), `layout/route.py` + `GET/POST /route`, `GET /difficulty`, `GET /progress` + `POST /cancel` (via `job_id`), `POST /chunks`.

**Asset library** (`assets/manifest.json`, loaded by `aesthetic/library.py`): 8 CC0 themes and 25 sprites (licences in `assets/LICENSES.md`). Each theme is its own 80×64 sheet: the 14 tile roles in a 5×3 grid plus a row of CENTER variants. `THEMES` in `aesthetic/themes.py` is built from the manifest. The server copies the sheet to `<ldtk_dir>/gldtk_assets/themes/` and the `.ldtk` references it there. Rebuild sheets with `python scripts/build_assets.py`. A level with several section themes gets one Tiles layer per theme (layer order: Entities, Tiles…, Collisions, which must match between `defs.layers` and `layerInstances` or LDtk marks the level modified on load), and the library sprites are packed into `gldtk_assets/sprites.png` and shown as entity icons (`sprite_tile` F_Tile field).

**LDtk export** (`layout/ldtk_adapter.py`, catalog in `spec/catalog.py`, reader in `layout/ldtk_reader.py`): every entity kind has typed `fieldDefs`; GLDtk state is stored in the level fields `gldtk_prompt`, `gldtk_sections`, `gldtk_requirements`, `gldtk_game_profile`, `gldtk_spec` and `gldtk_version`. Exports are validated in tests against `ldtk-src/docs/JSON_SCHEMA.json`.

**LDtk JSON format** (`layout/ldtk_adapter.py`): Layer instances require `__tilesetRelPath`, `__tilesetDefUid`, `__opacity`, `optionalRules` (cached computed fields). Levels require `__bgColor`, `__smartColor`. Project root requires `bgColor`, `worldLayout`, `flags`, etc. Missing any of these causes silent blank rendering in LDtk.

**LDtk integration** (`ldtk-src/src/electron.renderer/ui/GldtkSidebar.hx`):
- Instantiated in `App.hx` after `initKeyBindings()`
- Uses `js.html.XMLHttpRequest` directly — not `haxe.Http` (hxnodejs overrides haxe.Http to use Node's http stack, which breaks on localhost)
- Current project path: `Editor.ME.project.filePath.full`
- Reload after generation: `App.ME.loadProject(path)` with 400ms delay

**CSP** (`ldtk-src/app/assets/app.html`): `connect-src` includes both `http://localhost:8765` and `http://127.0.0.1:8765`.

## LLM providers

Providers live in `llm/providers/` (one module each, built via `make_provider()` in `registry.py`; default models in `_DEFAULT_PROVIDER_MODELS` there). Default is `gemini` (key `GEMINI_API_KEY` in the git-ignored `.env`, loaded by `server.py`). Gemini, Claude (`anthropic`) and Ollama are first-class and all use schema-enforced output with `LevelSpec.json_schema()`; `openai`/`deepseek` are best effort. The Gemini free tier allows ~20 requests/day per Flash model, so when no model is given the provider falls back to `gemini-3.1-flash-lite` on 429/5xx. When Gemini still cannot answer (429/404/5xx, timeout, no connection), the server's default factory falls back to a local model, also when a Gemini model is chosen explicitly (`FallbackProvider`, `make_provider_with_fallback`): `GLDTK_LOCAL_FALLBACK` (default `ollama:qwen2.5:14b`, `off` disables; pull it with `ollama pull qwen2.5:14b`). The switch appears as the first response warning. Ollama calls get a 900 s timeout. Provider is set per-request via `GenerateRequest.provider`. Anthropic calls use prompt caching — `SYSTEM_PROMPT` in `llm/prompt.py` is the static cacheable prefix.

The LLM emits a `LevelSpec` (`spec/level_spec.py`): size, sections, the node/edge graph, entities anchored on nodes, and requirements. `LLMController.generate_spec()` runs generate → validate (`validate_spec`) → patch → repair; `spec.to_graph()` feeds the layout.

**Environment variables:** `GEMINI_API_KEY` (`.env`), `GLDTK_LOCAL_FALLBACK` (local model, `off` disables), `GLDTK_MEMORY_DIR` (design memory folder), `GLDTK_PATTERNS_DIR` (pattern index folder), `GLDTK_CONFIG_DIR` (Mac app settings / `.env`), `GLDTK_FAKE_LLM` (tests: canned replies from a fixture file or directory), `GLDTK_HOST` / `GLDTK_PORT`. Tests set the memory, patterns and config dirs to temp folders.

**Mac app:** `scripts/build_mac_app.sh` (unsigned, Apple Silicon; PyInstaller backend in `packaging/`). It overwrites `ldtk-src/app/assets/js/renderer.js` with a release build; re-run `renderer.debug.hxml` for development.

## Tests

```bash
source .venv/bin/activate
pytest                                   # whole offline suite (FakeProvider + tests/fixtures/llm/); no file lists needed
pytest -m e2e tests/e2e/test_gemini.py -s   # real Gemini call (uses free-tier quota)
```

## Adding new parameters

To add a new generation parameter (e.g. a new constraint):
1. `api/models.py` — add field to `GenerateRequest`
2. `server.py` — thread into `LayoutConfig` or `constraints` dict
3. `GldtkSidebar.hx` — add DOM input, read value, `Reflect.setField(payload, "key", value)`
4. Rebuild Haxe renderer

## v2 / v3 / v4 work

Extension work lives on branch `feat/v2`. Settled design: `docs/v2/DESIGN.md`; plans: `docs/v3/PLAN.md`, `docs/v4/PLAN.md`; part specs in `docs/v*/parts/`; status in `docs/v2/PROGRESS.md`. Read the relevant ones before working on a part.
