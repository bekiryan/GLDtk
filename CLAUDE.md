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
pip install -e ".[server]"   # installs all deps including fastapi/uvicorn
pip install pillow            # required for tileset generation script
```

## Pipeline architecture

```
Prompt
  │
  ▼
LLMController (llm/validator.py)
  │  calls LLM → parses JSON → AbstractLevelGraph
  │  on error: SymbolicPatcher (llm/patcher.py) tries deterministic fixes
  │  on patch failure: re-prompts LLM (up to max_retries)
  │
  ▼
sugiyama_layout (layout/sugiyama.py)
  │  Sugiyama 4-phase: cycle removal → layer assignment → crossing minimisation → coord assignment
  │  Golden path (Start→Exit) is always the layout spine
  │  Returns: Dict[node_id, NodeLayout], IRLevel (tile grid)
  │
  ▼
build_aesthetic_layer (aesthetic/pipeline.py)
  │  detect_theme → autotile_level → place_entities
  │  Returns: AestheticBuildResult(aesthetic: AestheticData, theme, ...)
  │
  ▼
to_ldtk_dict (layout/ldtk_adapter.py)
  │  Serialises IRLevel + AestheticData → LDtk 1.5.3 JSON
  │  Writes file, returns dict
  │
  ▼
App.ME.loadProject(path)   ← called from GldtkSidebar.hx after server responds
```

## Key design invariants

**IRLevel** (`layout/ir.py`): `grid[row][col]`, row 0 = visual top. `TileValue`: EMPTY=0, SOLID=1, HAZARD=2. Entities stored separately in `ir.entities`.

**AbstractLevelGraph** (`symbolic/schema.py`): `nx.DiGraph` wrapper. Nodes have `NodeType` (PLATFORM, START, EXIT, HAZARD). Edges have `EdgeType` (WALK, JUMP, FALL). Physics oracle (`symbolic/physics.py`) validates jump arcs using `½g·t² − v·t + Δy = 0`.

**Tileset layout** (`aesthetic/themes.py`): Three themes share one 256×256 PNG. Each occupies a 5×3 tile region at `base_x` offset: Dungeon=0, Forest=80, Sky=160. Placeholder PNGs live in `tilesets/`. Replace with real art — no code changes needed.

**LDtk JSON format** (`layout/ldtk_adapter.py`): Layer instances require `__tilesetRelPath`, `__tilesetDefUid`, `__opacity`, `optionalRules` (cached computed fields). Levels require `__bgColor`, `__smartColor`. Project root requires `bgColor`, `worldLayout`, `flags`, etc. Missing any of these causes silent blank rendering in LDtk.

**LDtk integration** (`ldtk-src/src/electron.renderer/ui/GldtkSidebar.hx`):
- Instantiated in `App.hx` after `initKeyBindings()`
- Uses `js.html.XMLHttpRequest` directly — not `haxe.Http` (hxnodejs overrides haxe.Http to use Node's http stack, which breaks on localhost)
- Current project path: `Editor.ME.project.filePath.full`
- Reload after generation: `App.ME.loadProject(path)` with 400ms delay

**CSP** (`ldtk-src/app/assets/app.html`): `connect-src` includes both `http://localhost:8765` and `http://127.0.0.1:8765`.

## LLM providers

Default is `ollama` (requires `ollama serve` + `ollama pull llama3.1`). Provider is set per-request via `GenerateRequest.provider`. Models default via `_DEFAULT_PROVIDER_MODELS` in `llm/validator.py`. Anthropic calls use prompt caching — `SYSTEM_PROMPT` in `llm/prompt.py` is the static cacheable prefix.

## Adding new parameters

To add a new generation parameter (e.g. a new constraint):
1. `api/models.py` — add field to `GenerateRequest`
2. `server.py` — thread into `LayoutConfig` or `constraints` dict
3. `GldtkSidebar.hx` — add DOM input, read value, `Reflect.setField(payload, "key", value)`
4. Rebuild Haxe renderer

## Tileset regeneration

```bash
source .venv/bin/activate
python - << 'EOF'
# Run the inline tileset generator from the session that created tilesets/
# (see aesthetic/themes.py for layout spec)
EOF
```

The generator script is not saved as a file — it lives in the conversation history. To regenerate, recreate it using the spec in `aesthetic/themes.py` docstring (5×3 tile regions, `base_x` offsets 0/80/160).
