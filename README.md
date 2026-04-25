# GLDtk

Generative Level Design toolkit for 2D platformers.

GLDtk is a neuro-symbolic pipeline that combines LLM creativity with deterministic graph and physics validation. It turns a level concept into a playable abstract graph, maps that graph to a tile layout, and exports an LDtk project JSON.

## What This Project Does

GLDtk is split into four layers:

1. Symbolic Foundation (`symbolic/`)
   - Abstract level graph schema (nodes, edges, kinematic data)
   - Deterministic jump/fall reachability oracle
   - Tile-grid to graph extractor

2. Structural Engine (`layout/`)
   - IRLevel format (internal, serializer-agnostic)
   - Sugiyama-style layout that prioritizes the golden path
   - LDtk v1.5.3 adapter

3. Neural Integration (`llm/`)
   - Strict JSON prompt engineering
   - Generate -> validate -> patch -> repair control loop
   - Deterministic symbolic patcher for minor issues

4. Aesthetic Layer (`aesthetic/`)
   - Auto-tiling rules
   - Golden-path weighted entity placement
   - Keyword-based theme mapping
   - One-call aesthetic pipeline integration

## Architecture Summary

Input options:
- Existing tile map -> `symbolic.extract_graph`
- Text prompt -> `llm.LLMController.generate` (Anthropic API)

Core flow:
1. Build or generate `AbstractLevelGraph`
2. Validate playability with deterministic physics checks
3. Layout graph into `IRLevel` via `layout.sugiyama_layout`
4. Enrich visuals/entities via `aesthetic.build_aesthetic_layer`
5. Export LDtk JSON via `layout.to_ldtk_dict`

Output:
- Valid LDtk project JSON that can be opened in LDtk

## Requirements

- Python 3.11+
- macOS/Linux shell instructions below use `bash` or `zsh`
- Anthropic API key only if using the LLM generation layer

## Installation

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

Installed dependencies from `pyproject.toml`:
- `networkx`
- `anthropic`

## How To Use

### 1. Build a Graph From a Tile Grid

Tile legend used by `symbolic.extract_graph`:
- `0` empty
- `1` solid platform
- `2` hazard
- `3` start
- `4` exit

```python
from symbolic import extract_graph

grid = [
    [0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 4, 0],
    [0, 1, 1, 0, 1, 0],
    [3, 1, 1, 2, 1, 1],
]

graph = extract_graph(grid, tile_size=16.0, gravity=980.0, jump_v=600.0)
print(graph)
print("Solvable:", graph.is_solvable())
```

### 2. Layout and Build IR Level

```python
from layout import sugiyama_layout, LayoutConfig

node_layouts, ir_level = sugiyama_layout(
    graph,
    config=LayoutConfig(width_tiles=60, height_tiles=20, tile_size=16),
)

print(ir_level)
print(ir_level.ascii_render())
```

### 3. Add Aesthetic Layer (Theme, Tiles, Entities)

```python
from aesthetic import build_aesthetic_layer

aesthetic_result = build_aesthetic_layer(
    description="A mossy forest with floating ruins",
    graph=graph,
    node_layouts=node_layouts,
    ir_level=ir_level,
    density=1.0,
    seed=42,
)

print("Theme:", aesthetic_result.theme.identifier)
print("Placed entities:", len(aesthetic_result.placed_entities))
```

### 4. Export LDtk JSON

```python
import json
from layout import to_ldtk_dict

ldtk_project = to_ldtk_dict(
    ir_level,
    aesthetic=aesthetic_result.aesthetic,
)

with open("level.ldtk", "w", encoding="utf-8") as f:
    json.dump(ldtk_project, f, indent=2)
```

Open `level.ldtk` in the LDtk editor.

## End-to-End Example Script

Create `example_run.py`:

```python
import json

from symbolic import extract_graph
from layout import sugiyama_layout, to_ldtk_dict
from aesthetic import build_aesthetic_layer


def main() -> None:
    grid = [
        [0, 0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 4, 0],
        [0, 0, 0, 1, 1, 0, 1, 0],
        [0, 1, 1, 1, 1, 0, 1, 0],
        [3, 1, 1, 2, 1, 1, 1, 1],
    ]

    graph = extract_graph(grid)
    node_layouts, ir_level = sugiyama_layout(graph)

    aesthetic_result = build_aesthetic_layer(
        description="Dungeon level with spikes and skeleton guards",
        graph=graph,
        node_layouts=node_layouts,
        ir_level=ir_level,
        seed=42,
    )

    ldtk_project = to_ldtk_dict(ir_level, aesthetic=aesthetic_result.aesthetic)

    with open("level.ldtk", "w", encoding="utf-8") as f:
        json.dump(ldtk_project, f, indent=2)

    print("Wrote level.ldtk")


if __name__ == "__main__":
    main()
```

Run:

```bash
source .venv/bin/activate
python example_run.py
```

## LLM Generation Mode (Optional)

If you want prompt-driven graph generation:

```bash
export ANTHROPIC_API_KEY="your_api_key_here"
```

```python
from llm import LLMController

controller = LLMController(max_retries=3)
graph = controller.generate("A short forest level with one hazard pit and one exit")
```

The LLM output is validated and repaired using deterministic symbolic checks before acceptance.

## Coordinates and Determinism Notes

- Physics uses a Y-up coordinate model in symbolic validation.
- Extractor converts row-major grids into world-space Y-up values.
- Entity placement is deterministic when `seed` is fixed.
- Patcher and validator logic are deterministic for the same graph/physics parameters.

## Current Status

See `plan.md` for implementation coverage and roadmap tracking.

## Troubleshooting

1. Import errors
   - Ensure you installed in editable mode: `pip install -e .`
   - Run from repository root with `.venv` activated

2. LLM controller fails
   - Check `ANTHROPIC_API_KEY`
   - Verify network/API access

3. LDtk tileset not visible
   - Ensure `tileset_rel_path` points to an existing image path used by your LDtk project
