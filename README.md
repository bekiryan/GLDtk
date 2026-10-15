# GLDtk — Generative Level Design Toolkit

GLDtk turns a text description into a **playable 2D platformer level** in [LDtk](https://ldtk.io), right
inside the editor. Describe a whole level (sections, enemies, coins, weapons, hazards, mood), and GLDtk
builds it, proves it can be beaten, and tells you how much of the prompt it achieved. You can then select
or describe any part of the level and regenerate only that part.

It is a **neuro-symbolic pipeline**: an LLM handles creativity, while deterministic layout, physics and
checks handle correctness. Every level written to disk is statically verified: the physics oracle proves
that the player can get from Start to Exit with the game's own jump and ability settings.

---

## Contents

1. [Quick start](#quick-start)
2. [Using the GLDtk sidebar](#using-the-gldtk-sidebar)
3. [LLM providers and the local fallback](#llm-providers-and-the-local-fallback)
4. [How it works](#how-it-works)
5. [Repository layout](#repository-layout)
6. [Without the app: Python and HTTP](#without-the-app-python-and-http)
7. [Evaluation](#evaluation)
8. [Tests](#tests)
9. [Troubleshooting](#troubleshooting)

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
- **Game Profile:** physics (gravity, jump speed, run speed, step height) and abilities (double jump,
  dash, wall jump) for the whole game, saved as `gldtk.game.json` next to the project. Levels are
  verified against it, and a prompt asking for an ability the profile lacks is reported as ❌.
- **Provider:** choose the LLM and optionally a model.

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
  │    generate → validate → symbolic patch →     size, sections, platform graph, entities,
  │    re-prompt with precise errors              requirements extracted from the prompt
  ▼
  Completeness (pipeline/completeness.py)         fidelity check; deterministic fill of missing
  │                                               entities/items; at most one LLM re-prompt
  ▼
  Layout (layout/spec_layout.py)                  keeps the LLM's coordinates and section bands
  │                                               (horizontal, or vertical bottom → top)
  ▼
  Densify (layout/densify.py)                     extra reachable platforms and ground so the
  │                                               level reads as a level
  ▼
  Grid verification + repair (layout/verify.py)   extract the graph from the final tile grid,
  │                                               prove Start→Exit with hitbox-swept arcs and the
  │                                               Game Profile's abilities; repair or re-prompt
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

## Repository layout

```
server.py         FastAPI backend: /generate, /edit/*, /undo, /history, /profile, /providers, /art/*
api/              request and response models
spec/             LevelSpec schema, validation, entity catalog
llm/              prompts, LLMController, patcher, providers/ (gemini, anthropic, ollama, openai, fallback)
pipeline/         build_level (spec → verified level), completeness loop
layout/           spec layout, densify, grid verification and repair, LDtk export and reader
symbolic/         graph schema, physics oracle, grid → graph extractor
game/             Game Profile (gldtk.game.json)
fidelity/         requirement checker, report, deterministic fill
edit/             regional editing, seams, stitching, version history
aesthetic/        themes, autotiling, sprites, asset library loader
assets/           CC0 tile themes and sprites (manifest.json)
art/              optional AI theme/sprite generation (Gemini image models; needs a paid key)
eval/             evaluation harness and prompt suite
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

Edits take the same `.ldtk` path; rects are in tiles, with row 0 at the top:
`POST /edit/region {path, rect: {col,row,w,h}, prompt}`, `/edit/section {path, section_id, prompt}`,
`/edit/entities {path, prompt, rect?, entity_iids?}`, `/edit/locate {path, text}`, `/undo {path}`,
`GET /history?path=…`. Every request accepts `provider` and `model`.

---

## Evaluation

`python -m eval.run` runs a 27-prompt suite through the real pipeline and reports fidelity, playability,
retries and timing per provider and model. See [eval/README.md](eval/README.md). The first real run
(Gemini, 8 prompts, 2026-10-15) scored **100% playable and 100% fidelity**
(`eval/results/gemini-2026-10-15/report.md`). Its responses are recorded, so the run can be replayed
offline with `--replay eval/recordings/gemini-2026-10-15`.

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
- **A generate or edit fails with "not verified":** the level could not be proven beatable with the
  current Game Profile, so nothing was written. Try again (another seed) or simplify the request.
- **Tiles are blank in LDtk:** the theme sheets are copied to `gldtk_assets/` next to the `.ldtk`. Keep
  that folder with the project.
