# GLDtk v4: future work (planned 2026-10-15)

The user asked for these after the v3 feature set (Parts 11–21). They start after v3 is wrapped
up: Part 21 (terrain) and its sidebar follow-up, Part 18 (Mac app), Part 19 (Armenian README) and
a real-Gemini app test of v3. Each part gets a spec in `docs/v4/parts/` when it starts. The order
below follows the dependencies.

| # | Part | Depends on |
|---|---|---|
| 22 | Soft-lock detection (SCC and item-state graph) | — |
| 23 | Mission graph and space graph (cyclic, metroidvania structure) | 22, 21 |
| 24 | Level complexity target (one number drives generation) | 15, 21 |
| 25 | Noise-based terrain fill and endless chunks | 21, 17 |
| 26 | Pattern library and retrieval (RAG) | 22, 21 |
| 27 | Design memory (learns from the user's edits, across projects) | — (better after 26) |

---

## 22. Soft-lock detection
**Goal:** the guarantee becomes "the player can never get stuck", not only "Start→Exit exists".
- **Strongly connected components.** Compute the SCCs of the movement graph (extractor edges,
  including one-way falls). Every span reachable from the Start must be able to reach an Exit; a
  reachable span with no path to an Exit is a **soft-lock** (a pit you cannot climb out of, a drop
  into a sealed room).
- **Item state.** Make it a product graph of (span, items held), with keys, abilities picked up
  and switches. It finds traps like "you dropped past the key and cannot go back", and doors
  whose key is behind them.
- **Report and repair.**
  - Soft-locks become verification errors with a location.
  - Deterministic repairs: a ledge or stepping stone out of the pit, a one-way exit, or moving
    the key before the drop.
  - The route overlay shows soft-lock spans in red.
  - Play mode can also flag them.
- **Cost:** linear in the graph size (Tarjan or Kosaraju; networkx is already a dependency).

## 23. Mission graph and space graph (Dormans-style cyclic generation)
**Goal:** real metroidvania and Spelunky structure, with loops, keys and locks, ability gates,
shortcuts and backtracking, and solvability guaranteed by construction.
- **Mission graph:** an abstract graph of the player's goals (start → key → locked door →
  ability → gated area → boss → exit). It is built with **graph-rewriting rules** (a graph
  grammar): lock-and-key cycles, a hidden shortcut, an optional treasure branch, a
  "return with a new ability" cycle. The LLM picks rules and themes from the prompt; the grammar
  keeps it valid.
- **Space graph:** the mission graph is embedded into rooms and sections, the Part 21 terrain
  types (hub, labyrinth, arena, shaft, corridor), and their connections. Then it is laid out on
  the grid.
- **Verification:** Part 22's item-state graph checks that the space graph realises the mission
  (each lock reachable only after its key) with no soft-locks.
- **Fidelity:** "the key is in the cave, the door is in the castle" becomes a structural check.

## 24. Level complexity target
**Goal:** one number, for example `complexity` from 0 to 1 (shown as 1–10), drives generation.
- **Mapping.** It maps to concrete targets:
  - jump tightness (share of the comfort limits used, Part 20);
  - hazard and enemy density and threat;
  - checkpoint spacing;
  - labyrinth branching and dead ends (Part 21);
  - mission-graph cycles (Part 23);
  - the number of ability-gated moves.
- **Whole level or per section.** It can be set per level or per section. The sidebar gets a
  slider, and the LLM sees the targets in the prompt and fills the content.
- **Closing the loop.** Part 15 measures the result (later, Part 2 of the M4 list: the automated
  playtester). If the measured score misses the target by more than a tolerance, the generator
  adjusts deterministically (add or remove enemies, tighten or widen gaps) or re-prompts once.
- **Fidelity:** "difficulty 7/10" becomes a checked requirement.

## 25. Noise-based terrain fill and endless chunks (Minecraft-style)
**Why it is useful:** Minecraft builds terrain from **seeded noise** (Perlin or simplex, several
octaves), evaluated per **chunk**. The same seed and chunk coordinates always give the same
chunk, so the world is endless and neighbours always match. GLDtk benefits in two ways.
- **Fill empty space (the main use):**
  - noise plus thresholds, then a few cellular-automata smoothing passes, turns empty rock,
    backgrounds and unused areas into natural caves, strata, stalactites and ore veins;
  - **designed paths are protected cells**: the golden route, its arcs, headroom and the
    comfort platforms;
  - the verifier runs after the fill as always;
  - background or decor layers get the same treatment, with no gameplay effect.
- **Endless mode:**
  - a seed plus chunk coordinates gives a chunk;
  - chunks join through **fixed seam openings** (a deterministic hash of the border coordinates
    decides where the openings are), so any chunk connects to its neighbours;
  - "Generate the next N chunks" extends a world (built on Part 17's multi-level world, one LDtk
    level per chunk or per group of chunks);
  - an LLM can theme groups of chunks ("the next 5 chunks are a frozen cave");
  - each chunk is verified (and soft-lock checked) on its own and across its seams.

## 26. Pattern library and retrieval (RAG)
**Goal:** levels that look designed, are easy to build, are playable, and never soft-lock, by
showing the LLM real examples.
- **Sources (licensing):** commercial game levels (Blasphemous, Spelunky, Sonic, …) cannot be
  copied. The library is built from:
  - open level corpora, with each licence checked (for example research corpora such as the
    VGLC, used only where the terms allow);
  - LDtk's own sample projects;
  - the user's own levels;
  - GLDtk levels the user approved (kept, not undone).

  Each entry records its source and licence.
- **Ingestion:**
  - convert each entry to the GLDtk grid and IR (the existing reader and extractor);
  - cut it into rooms and set-pieces (spike corridor, key-door detour, ambush arena, shaft,
    labyrinth block);
  - compute metrics (route, difficulty, graph analytics, soft-lock status). Soft-locking
    patterns are rejected.
- **Labelling:** an LLM describes each pattern (purpose, mood, difficulty, mechanics, structure
  type) in a fixed schema. The vision model can look at a render.
- **Retrieval:**
  - embed the descriptions (Gemini embeddings, or a local embedding model through Ollama) and
    store them in a local vector index (a SQLite or FAISS file in the app data folder);
  - at generate or edit time, retrieve the top-k patterns that match the prompt, structure type
    and complexity target;
  - send them to Gemini as compact ASCII examples plus descriptions (few-shot);
  - the terrain generators can also **stamp a retrieved pattern** directly, adapted to the
    section's size and seams, then verify.
- **Fidelity and attribution:** the response lists which patterns were used.

## 27. Design memory (learns the user's taste, across projects)
**Goal:** GLDtk learns from every change the user makes and uses it in later work, in any project.
- **What is recorded (a local event log):**
  - every hand edit, as a diff of grid and entities between GLDtk's output and what the user
    saved: moved, added or removed entities, painted or erased tiles, changed fields;
  - undos of GLDtk edits (a negative signal);
  - edits that were accepted and kept, and regenerations;
  - Game Profile changes;
  - prompts.

  Each event is tagged with context (structure type, theme, complexity, section style).
- **Analysis:** periodically, and on demand, the log becomes **preferences**:
  - **statistics** (typical gap sizes after the user's edits, enemy spacing, platform widths,
    which structure types get regenerated);
  - **an LLM summary** of patterns into short rules ("prefers 2–3 enemies per room", "always
    widens jump gaps to ≤ 3 tiles", "removes coins from hazard areas").
- **Use:**
  - the preferences go into the generation and edit prompts;
  - they adjust deterministic parameters (comfort limits, densify density, placement spacing);
  - edited levels the user approved feed the pattern library (Part 26).
- **Storage and privacy:**
  - per user, not per project: `~/Library/Application Support/GLDtk/memory/` (an event log plus
    a preferences file);
  - local only, never uploaded except as the short preference text in the LLM prompt;
  - a sidebar panel shows what was learned, with edit, delete and reset, and an off switch.
