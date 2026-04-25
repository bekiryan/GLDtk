# GLDtk Implementation Plan and Status (April 26, 2026)

## Scope
This file maps the requested roadmap (Parts 1-4) to the current repository state,
including what is fully implemented and what still remains.

## Part 1: Symbolic Foundation

### 1.1 Graph Schema
Status: DONE

Implemented:
- Class-based typed schema in symbolic/schema.py
- Node types: Start, Exit, Platform, Hazard
- Edge types: Walk, Jump, Fall
- Edge stores dx, dy, launch velocity (vx, vy)
- NetworkX-backed graph wrapper with solvability queries

### 1.2 Jump Reachability Oracle
Status: DONE

Implemented:
- Deterministic jump feasibility check in symbolic/physics.py
- Kinematic validation with gravity/jump_v constraints
- Optional horizontal speed cap support
- Required launch velocity solver and fall-time utility

### 1.3 Level-to-Graph Extractor
Status: DONE

Implemented:
- Tile-grid to abstract graph conversion in symbolic/extractor.py
- Surface span detection and node construction
- Deterministic edge classification (walk/jump/fall) using physics oracle
- Hazard/source constraints applied during edge generation

## Part 2: Structural Engine

### 2.1 IRLevel (Internal Representation)
Status: DONE

Implemented:
- Version-independent IR model in layout/ir.py
- Tile grid, entities, metadata, bounds-safe accessors
- Serialization helpers and debug ASCII rendering

### 2.2 Sugiyama Layout Engine
Status: DONE

Implemented:
- Four-phase layered layout in layout/sugiyama.py
- Golden path extraction (Start -> Exit shortest path)
- Layer assignment, barycenter ordering, row assignment by edge type
- Rasterization into IRLevel

### 2.3 LDtk Adapter
Status: DONE

Implemented:
- LDtk v1.5.3 project dict serializer in layout/ldtk_adapter.py
- Stable UID mapping, layer definitions, entity definitions
- IntGrid and Entities layer instances
- Optional aesthetic Tiles layer support via AestheticData

## Part 3: Neural Integration

### 3.1 Prompt Engineering
Status: DONE

Implemented:
- Strict JSON system prompt with hard constraints in llm/prompt.py
- Embedded few-shot examples
- Message builders for initial generation and repair turns

### 3.2 Validator Loop
Status: DONE

Implemented:
- Parse + structural + physics validation in llm/validator.py
- Controller loop: generate -> validate -> patch -> repair
- Structured validation error messages for targeted re-prompts

### 3.3 Symbolic Patcher
Status: DONE

Implemented:
- Deterministic patch strategies in llm/patcher.py
- Edge reclassification, velocity recompute, stepping-stone insertion
- Missing terminal node defaults

## Part 4: Aesthetic Layer

### 4.1 Auto-tiling Rules
Status: DONE

Implemented:
- NSEW bitmask auto-tiling in aesthetic/autotile.py
- Corner/edge/fill/cap/single role mapping
- Hazard-specific tile role handling

### 4.2 Entity Placement
Status: DONE

Implemented:
- Golden-path weighted placement in aesthetic/entities.py
- Coins/Key/Enemy/Checkpoint placement with deterministic RNG seed
- Enemy archetype recorded in metadata while using canonical Enemy entity type

### 4.3 Theme Mapping
Status: DONE

Implemented:
- Keyword-driven theme detection in aesthetic/themes.py
- Theme registry for Dungeon/Forest/Sky + default fallback
- Tileset/background/enemy profile mapping

### 4.x Integration completion done in this pass
Status: DONE

Implemented now:
- Added aesthetic integration pipeline in aesthetic/pipeline.py
  - Theme detection + autotile + entity placement + AestheticData build
  - Optional entity attachment into IRLevel in one call
- Added aesthetic package exports in aesthetic/__init__.py
- Fixed LDtk entity identifier mapping for themed enemy aliases
- Updated package discovery in pyproject.toml to include llm* and aesthetic*

## Remaining Work

Roadmap-critical remaining items: NONE.

Recommended engineering follow-ups:
- Add unit tests for:
  - jump oracle edge cases
  - extractor edge typing
  - validator + patch loop behavior
  - aesthetic pipeline deterministic output snapshots
- Add one end-to-end demo script that runs:
  prompt -> graph -> layout -> aesthetic -> LDtk JSON file output
- Add README usage docs for API entry points and expected coordinate system
