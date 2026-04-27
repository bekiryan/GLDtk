# GLDtk — Generative Level Design Toolkit

GLDtk turns a plain-English level description into a playable, physics-verified LDtk project file.  
It is a **neuro-symbolic pipeline**: an LLM handles creativity, deterministic graph theory and kinematics handle correctness.

---

## Table of Contents

1. [The Core Idea](#the-core-idea)
2. [How It Works — End to End](#how-it-works--end-to-end)
3. [Repository Layout](#repository-layout)
4. [Installation](#installation)
5. [Usage](#usage)
   - [Path A — From a Tile Grid](#path-a--from-a-tile-grid)
   - [Path B — From a Text Prompt (LLM Mode)](#path-b--from-a-text-prompt-llm-mode)
   - [Full Pipeline in One Script](#full-pipeline-in-one-script)
6. [LLM Providers](#llm-providers)
7. [Coordinate System](#coordinate-system)
8. [Can We Train This?](#can-we-train-this)
9. [Troubleshooting](#troubleshooting)

---

## The Core Idea

Most generative level design tools are either **purely neural** (fast but unreliable — the level might be unbeatable) or **purely algorithmic** (reliable but rigid — every level feels the same).

GLDtk takes a third path: **neuro-symbolic AI**.

```
LLM (neural)          →  creativity, narrative, variation
Physics oracle (symbolic) →  correctness, playability, determinism
```

The LLM imagines the level as an abstract graph of platforms and jumps.  
The physics oracle (kinematic equations) immediately rejects any jump that a player could not physically make.  
If the LLM hallucinates an impossible edge, a deterministic **Symbolic Patcher** fixes it without spending another API call. Only if the patcher cannot fix the error does the system re-prompt the LLM with a precise error message.

The result is a level that is **both creative and guaranteed playable**.

---

## How It Works — End to End

```
┌──────────────────────────────────────────────────────────────────────┐
│  INPUT                                                               │
│  • Text prompt  "a dark cave with spike pits and crumbling bridges"  │
│  • OR existing tile grid  (0=empty 1=solid 2=hazard 3=start 4=exit) │
└──────────────────────┬───────────────────────────────────────────────┘
                       │
          ┌────────────▼────────────┐
          │  LLMController          │  llm/validator.py
          │                         │
          │  1. Prompt LLM → JSON   │  ← llm/prompt.py (system prompt
          │  2. Parse JSON           │    + few-shot examples)
          │  3. Validate structure  │
          │  4. Check physics        │  ← symbolic/physics.py
          │     (jump oracle)        │    check_jump_arc()
          │  5. SymbolicPatcher      │  ← llm/patcher.py
          │     tries to fix errors  │    (no API call)
          │  6. If still broken →   │
          │     repair re-prompt    │
          │  7. Repeat up to N      │
          └────────────┬────────────┘
                       │  AbstractLevelGraph  (symbolic/schema.py)
                       │  ✓ solvable  ✓ all edges physics-valid
                       │
          ┌────────────▼────────────┐
          │  Sugiyama Layout Engine │  layout/sugiyama.py
          │                         │
          │  Golden path → spine    │  Start→Exit path placed first
          │  Layer = x position     │  JUMP → row goes up
          │  Barycenter ordering    │  FALL → row goes down
          │  Rasterise → tile grid  │
          └────────────┬────────────┘
                       │  IRLevel  (layout/ir.py)
                       │  version-independent tile grid + entities
                       │
          ┌────────────▼────────────┐
          │  Aesthetic Layer        │  aesthetic/
          │                         │
          │  detect_theme()         │  keyword → dungeon/forest/sky
          │  autotile_level()       │  4-bit bitmask → tile roles
          │  place_entities()       │  golden-path difficulty weighting
          │                         │  → coins, keys, enemies, checkpoints
          └────────────┬────────────┘
                       │  AestheticData  (tile entries + theme)
                       │
          ┌────────────▼────────────┐
          │  LDtk Adapter           │  layout/ldtk_adapter.py
          │                         │
          │  IntGrid collision layer│
          │  Tiles visual layer     │
          │  Entities layer         │
          │  Tileset + entity defs  │
          └────────────┬────────────┘
                       │
┌──────────────────────▼───────────────────────────────────────────────┐
│  OUTPUT:  level.ldtk  — valid LDtk v1.5.3 project JSON               │
│  Open in the LDtk editor, attach your tileset PNG, press Play.        │
└──────────────────────────────────────────────────────────────────────┘
```

### The Physics Oracle in Detail

Every `Jump` edge in the graph is validated by `check_jump_arc(p1, p2, gravity, jump_v)`.  
It solves the vertical kinematic quadratic:

```
½g·t² − jump_v·t + Δy = 0
discriminant = jump_v² − 2·g·Δy
```

If `discriminant < 0`, the jump apex is below the target — physically impossible.  
The oracle is **deterministic and millisecond-fast** — it runs on every generated graph, every repair attempt, and every patch, with zero variance.

### The Symbolic Patcher

Before spending an API call on a repair, the patcher tries four strategies in order:

| Patch | What it fixes | Cost |
|---|---|---|
| `EdgeReclassifyPatch` | `Jump` typed going down → reclassify as `Fall` | O(edges) |
| `VelocityPatch` | Wrong launch velocities on valid edges | O(edges) |
| `SteppingStonePatch` | Impossible jump (target too high) → insert midpoint platform | O(edges) |
| `DefaultTerminalsPatch` | Missing Start or Exit node | O(1) |

Each patch works on a **copy** of the graph; the original is never mutated. After every patch the graph is re-validated — the loop exits as soon as it becomes valid.

---

## Repository Layout

```
GLDtk/
│
├── symbolic/          # Layer 1 — Symbolic Core
│   ├── schema.py      # AbstractLevelGraph, LevelNode, LevelEdge, Vec2
│   ├── physics.py     # check_jump_arc, required_launch_velocity, fall_time
│   └── extractor.py   # tile grid → AbstractLevelGraph (dataset seed builder)
│
├── layout/            # Layer 2 — Structural Engine
│   ├── ir.py          # IRLevel (format-agnostic internal representation)
│   ├── sugiyama.py    # Sugiyama-style layout → tile coordinates
│   └── ldtk_adapter.py# IRLevel + AestheticData → LDtk JSON v1.5.3
│
├── llm/               # Layer 3 — Neural Integration
│   ├── prompt.py      # System prompt, few-shot examples, message builders
│   ├── validator.py   # LLMController: generate → validate → patch → repair
│   └── patcher.py     # SymbolicPatcher: deterministic graph surgery
│
├── aesthetic/         # Layer 4 — Aesthetic Layer
│   ├── themes.py      # Theme registry (Dungeon/Forest/Sky), keyword detection
│   ├── autotile.py    # 4-bit NSEW bitmask → tile roles → TileEntry list
│   ├── entities.py    # Golden-path difficulty → coin/key/enemy/checkpoint
│   └── pipeline.py    # build_aesthetic_layer() — one-call integration
│
├── example.py         # End-to-end usage example
├── pyproject.toml     # Dependencies: networkx, anthropic
└── plan.md            # Implementation status and roadmap
```

---

## Installation

```bash
git clone <repo>
cd GLDtk
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Dependencies installed automatically: `networkx`, `anthropic`.  
An API key is only required if you use a hosted LLM provider.

---

## Usage

### Path A — From a Tile Grid

Use this when you already have a level (hand-crafted, from another tool, or as a training seed).

```python
from symbolic import extract_graph
from layout import sugiyama_layout, to_ldtk_dict
from aesthetic import build_aesthetic_layer
import json

# Tile values: 0=empty  1=solid  2=hazard  3=start  4=exit
grid = [
    [0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 4, 0],
    [0, 0, 0, 1, 1, 0, 1, 0],
    [0, 1, 1, 1, 1, 0, 1, 0],
    [3, 1, 1, 2, 1, 1, 1, 1],
]

# Step 1 — build the abstract graph
graph = extract_graph(grid, tile_size=16.0, gravity=980.0, jump_v=600.0)
print(graph)                  # AbstractLevelGraph(nodes=N, edges=M)
print("Solvable:", graph.is_solvable())

# Step 2 — layout
node_layouts, ir = sugiyama_layout(graph)
print(ir.ascii_render({"Start": "S", "Exit": "E"}))

# Step 3 — aesthetic pass
result = build_aesthetic_layer(
    description="cave level with hazards",
    graph=graph,
    node_layouts=node_layouts,
    ir_level=ir,
    seed=42,
)

# Step 4 — export
doc = to_ldtk_dict(ir, aesthetic=result.aesthetic)
with open("level.ldtk", "w") as f:
    json.dump(doc, f, indent=2)
```

---

### Path B — From a Text Prompt (LLM Mode)

The LLM generates a graph; the oracle validates it; the patcher and repair loop ensure the result is always physically playable.

```python
from llm import LLMController
from layout import sugiyama_layout, to_ldtk_dict
from aesthetic import build_aesthetic_layer
import json

# Pick a provider (see LLM Providers section below)
controller = LLMController(provider="anthropic", model="claude-sonnet-4-6")

# Generate a verified graph — may retry internally if the LLM hallucinates
graph = controller.generate(
    "A dungeon with three platforms of increasing height, a spike pit below "
    "the second platform, and the exit guarded at the top"
)

node_layouts, ir = sugiyama_layout(graph)
result = build_aesthetic_layer(
    description="dungeon with spikes",
    graph=graph,
    node_layouts=node_layouts,
    ir_level=ir,
    seed=0,
)

doc = to_ldtk_dict(ir, aesthetic=result.aesthetic)
with open("level.ldtk", "w") as f:
    json.dump(doc, f, indent=2)
```

Open `level.ldtk` in the [LDtk editor](https://ldtk.io). Point the tileset path to your sprite sheet and the level is ready to play.

---

### Full Pipeline in One Script

`example.py` at the repo root shows the complete flow. Run it:

```bash
source .venv/bin/activate
python example.py
# → Writes level.ldtk
```

---

## LLM Providers

The `LLMController` supports four providers. Validation and patching logic is identical regardless of which provider you choose.

| Provider | Env var | Default model | Notes |
|---|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` | Prompt caching active on system prompt |
| `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` | |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-chat` | OpenAI-compatible endpoint |
| `ollama` | — | `llama3.1` | Fully local, needs `ollama serve` |

```python
# Anthropic (default)
LLMController(provider="anthropic")

# OpenAI
LLMController(provider="openai", model="gpt-4o")

# Local — no API key, no data leaves your machine
LLMController(provider="ollama", model="qwen2.5-coder:7b")
```

---

## Coordinate System

GLDtk uses a **Y-up** world-space coordinate system internally:

- X increases to the right
- Y increases **upward** (higher Y = higher in the world)
- The `surface` of a platform is its **top edge** — the point where a player stands
- Jump Δy is positive (upward), Fall Δy is negative (downward)

The LDtk tile grid is **Y-down** (row 0 = top). The extractor and layout engine convert between the two automatically. You do not need to handle this manually.

---

## Can We Train This?

**Yes — and the architecture was designed for it.**

The system has three distinct training opportunities:

---

### Training Opportunity 1 — Fine-tune the LLM (Cheapest wins)

**Goal**: Replace the expensive hosted model with a small local model that reliably outputs valid graphs on the first attempt, eliminating most repair-loop iterations.

**How to build the training dataset**:

```
Step 1: Collect raw level data
  → Existing LDtk / Tiled / hand-crafted tile grids
  → Public domain game levels (e.g. open-source platformer repos)

Step 2: Convert to abstract graphs
  from symbolic import extract_graph
  graph = extract_graph(grid)

Step 3: Filter — keep only solvable graphs
  if not graph.is_solvable():
      continue  # discard

Step 4: Validate all edges pass the physics oracle
  from llm.validator import validate_graph, PhysicsParams
  result = validate_graph(graph, PhysicsParams())
  if not result.valid:
      continue  # discard

Step 5: Serialise to the LLM training format
  Each example = (description, graph_json)
  description: write by hand or auto-generate from graph structure
  graph_json:  the validated graph serialised to the prompt schema

Step 6: Fine-tune
  → Anthropic fine-tuning API (when available)
  → HuggingFace + LoRA on Llama / Qwen / Mistral
  → Ollama model import for local serving
```

The `symbolic/extractor.py` module was built **specifically** to generate this synthetic dataset. Even a few hundred hand-crafted grids produce thousands of valid (graph, description) pairs after augmentation (mirroring, recoloring, difficulty rescaling).

---

### Training Opportunity 2 — Reinforcement Learning (No labels needed)

The physics oracle and solvability check are **deterministic reward functions** — you do not need human labelers.

```
State:    a partially-constructed level graph
Action:   add / move / remove a node or edge
Reward:   +1 if graph.is_solvable() and all edges pass check_jump_arc()
          −0.1 per impossible edge (partial credit)
          −0.01 per extra repair iteration used
```

This frames level generation as a token-level or node-level policy optimisation problem. The reward signal is:

- **Binary**: solvable or not (0 / 1)
- **Dense**: fraction of edges that pass the oracle (0.0 – 1.0)
- **Free**: no human annotation cost

Tools that plug directly into this: `trl` (PPO / GRPO on HuggingFace models), `verl`, or any RL framework that accepts a Python reward function.

---

### Training Opportunity 3 — Distillation (Teacher → Student)

A practical workflow used in production:

```
1. Run LLMController with Claude (large teacher model)
   → Keep all (prompt, validated_graph_json) pairs where the oracle accepts
   → Discard repair-loop failures

2. You now have verified (input, output) pairs — no labeling needed.

3. Fine-tune a small open-source model (7 B parameters) on these pairs.

4. Replace the provider in LLMController:
   LLMController(provider="ollama", model="your-finetuned-model")

5. The symbolic patcher and validator remain as a safety net —
   even if the student model hallucinates, the oracle catches it.
```

This is the most practical path: use a large model to generate verified training data cheaply, then distil that knowledge into a local model that runs at negligible cost with no internet connection.

---

### Summary: What Acts as the Training Signal

| Signal | Source | Human labeling needed |
|---|---|---|
| Jump feasibility | `check_jump_arc()` — kinematic equations | No |
| Level solvability | `graph.is_solvable()` — NetworkX shortest-path | No |
| Structural validity | `validate_graph()` — rule checks | No |
| Design quality | Human preference (fun, difficulty balance) | Yes (optional) |

The first three signals are free and deterministic. You can build a fully supervised fine-tuning pipeline or a complete RL training loop **without a single human label**. Human preference data can be added later to optimise for "fun" rather than just "valid".

---

## Troubleshooting

**Import errors**
- Run from the repo root with the venv active: `source .venv/bin/activate`
- Ensure editable install: `pip install -e .`

**LLM controller raises `MaxRetriesExceeded`**
- The model failed to output a valid graph within `max_retries` attempts
- Try a larger/smarter model, or increase `max_retries`
- Local models (Ollama) often need 4–5 retries on first use; consider `max_retries=5`

**Provider connection errors**
- Anthropic/OpenAI/DeepSeek: check the corresponding `*_API_KEY` environment variable
- Ollama: verify `ollama serve` is running and `ollama list` shows your model

**LDtk tileset is blank / pink**
- The `tileset_rel_path` in the theme must point to a PNG file **relative to the `.ldtk` file's location**
- Create a placeholder PNG at that path (e.g. `tilesets/dungeon.png`) matching the 256×256 tileset layout described in `aesthetic/themes.py`
- The tile positions are documented in the tileset layout convention comment at the top of `aesthetic/themes.py`

**Level not solvable after extraction**
- Check that your grid has exactly one `3` (start) and at least one `4` (exit) tile
- Verify platforms are reachable under the given `gravity` and `jump_v` parameters
- Call `graph.is_solvable()` to confirm and inspect `graph.edges()` for disconnected nodes
