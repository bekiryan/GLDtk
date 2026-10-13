# Part 2 — Game Profile + ability-aware physics

Design context: [../DESIGN.md](../DESIGN.md) (Q1, Q3.1). Builds on Part 1 (`spec/level_spec.py`,
`llm/providers/`, `LLMController.generate_spec`). Branch `feat/v2`.

## Goal

Physics and player abilities belong to the **game**, not the level. A per-project **Game
Profile** (`gldtk.game.json`) holds them; the generator only reads it. The physics oracle,
validator, patcher and extractor all become ability-aware and honour a per-section
`gravity_scale` (already in `Section`). Edges can say which ability they need.

## Deliverables

### 1. `game/profile.py` (pydantic v2) — add `game*` to setuptools `include`

```
GameProfile
  version: int = 1
  name: str = "Untitled game"
  tile_size: int = 16
  physics: Physics
    gravity: float = 980        (px/s², > 0)
    jump_v: float = 600         (px/s, > 0)
    run_speed: float = 200      (px/s, > 0; caps |vx| for walk and air movement)
    walk_step_px: float = 8     (max |Δy| for a Walk edge — replaces the hard-coded 8.0)
  abilities: Abilities
    double_jump: { enabled: bool = False, jump_v: float = 500 }        (second jump, mid-air, once)
    dash:        { enabled: bool = False, distance_px: float = 64, air: bool = True }
    wall_jump:   { enabled: bool = False, jump_v: float = 550, max_climb_px: float = 160,
                   push_px: float = 48 }
  player: Player { description: str = "", sprite: str = "" }       (the player's look)
```
- `load_profile(path) -> GameProfile` (missing file → defaults; invalid file → clear error),
  `save_profile(profile, path)`, `PROFILE_FILENAME = "gldtk.game.json"`,
  `profile_path_for(ldtk_path) -> Path` (same directory as the `.ldtk` file).
- `GameProfile.summary_for_prompt() -> str` — a compact text block (physics constants, derived
  limits such as max jump height / max flat jump distance, and which abilities exist with their
  derived limits) for the LLM user message.

### 2. Oracle — `symbolic/physics.py`

Keep the existing functions (they are correct and tested indirectly). Add, with docstrings that
show the equations like the module does today:
- `gravity_for(profile, gravity_scale)`; every check takes the effective gravity.
- `check_double_jump(p1, p2, g, v1, v2, vmax)`: first jump at `v1`, second jump at `v2` launched
  at any time τ during the first arc (vertical velocity resets to `v2`). Reachable if some τ and
  landing time satisfy both axes with |vx| ≤ vmax. An exact or a finely-sampled τ search is
  fine; it must be deterministic and fast (< 1 ms per call).
- `check_dash_jump(p1, p2, g, v, vmax, dash_px)`: one horizontal dash of `dash_px` that takes no
  time and keeps height (a simplification; document it). Reachable if the normal arc reaches
  `|Δx| − dash_px` (clamped at 0).
- `check_wall_jump(p1, p2, g, v_wall, max_climb, push_px, normal_v, vmax)`: graph-level
  approximation — the player can gain up to `max_climb` extra height by wall-jumping, so Δy may
  be up to (normal apex + `max_climb`) and |Δx| must be ≤ the flat jump reach + `push_px`.
  Part 3 checks that a wall actually exists on the final grid; say so in the docstring.
- `reachable(p1, p2, profile, gravity_scale, ability: str | None) -> bool` — one entry point
  used by the validator and extractor. `ability=None` means a plain jump.
- `best_ability(p1, p2, profile, gravity_scale) -> str | None | False` — the cheapest way to make
  the move given the profile: `None` (plain jump), the first ability that works in the order
  dash → double_jump → wall_jump, or `False` (impossible).

### 3. Schema changes

- `SpecEdge.ability: Optional["double_jump"|"dash"|"wall_jump"]` (nullable, default null); carried
  to `LevelEdge.metadata["ability"]` by `to_graph()` and back by `with_graph()`.
- `to_graph()` also puts the source section's `gravity_scale` on the edge
  (`metadata["gravity_scale"]`) so validation does not need the spec.

### 4. Validator + patcher

- Replace `PhysicsParams` with `GameProfile` in `llm/validator.py`, `llm/patcher.py`,
  `LLMController` (`profile=` argument; default `GameProfile()`), `server.py` and the tests. Keep
  the name `PhysicsParams` importable only if removing it breaks something you cannot fix; do not
  keep two sources of truth for gravity/jump_v.
- Physics checks use effective gravity (`gravity * gravity_scale` of the source section) and
  `run_speed` as the horizontal cap (today the cap is `None` — enforcing it is intended; update
  the prompt and fixtures so valid fixtures stay valid).
- Jump edges: plain jump if `ability` is null; otherwise check with that ability.
- New codes:
  - `ABILITY_NOT_IN_PROFILE`: an edge uses an ability the profile lacks. The message names the
    ability and says the edge must become reachable without it.
  - `IMPOSSIBLE_JUMP`: the message now says which abilities (if any) would make the jump
    possible, from `best_ability`.
- Patcher:
  - New `AbilityAssignPatch` (runs before `SteppingStonePatch`): for an `IMPOSSIBLE_JUMP` edge,
    if `best_ability` finds an enabled ability, set `metadata["ability"]`.
  - For `ABILITY_NOT_IN_PROFILE`, clear the ability; if the jump is then impossible,
    `SteppingStonePatch` handles it.
  - `VelocityPatch` and `SteppingStonePatch` use effective gravity and `run_speed`.

### 5. Extractor — `symbolic/extractor.py`

`extract_graph(grid, ..., profile: GameProfile | None = None, gravity_scale_at=None)`:
`gravity_scale_at(col) -> float` is optional and defaults to 1.0. Edge classification uses the
profile. If a plain jump fails but `best_ability` succeeds, emit the Jump edge with
`metadata["ability"]`. Without a profile, behaviour is unchanged (existing call sites keep
working). Part 3 uses this to re-verify the final grid.

### 6. Prompt + controller

- Remove the physics constants and the jump formulas from the static `SYSTEM_PROMPT` (it must
  stay profile-independent so caching works). Explain the `ability` field and that limits come
  from the GAME PROFILE block in the user message.
- `build_generate_messages(description, constraints, profile)` appends
  `profile.summary_for_prompt()` as a `GAME PROFILE` block.
- Requirements: if the prompt asks for an ability the profile lacks, the LLM still lists the
  requirement (category `ability`) and builds the level without it. Say this in the prompt.
  Deterministic checking of requirements is Part 7; do not build it here.

### 7. Server + API

- `GenerateRequest.game_profile_path: Optional[str]` — default: `profile_path_for(output_path)`.
  Load it; if it is missing, use defaults (do not write a file during generate).
- `GET /profile?path=…` returns the profile JSON (defaults if missing). `PUT /profile?path=…`
  validates and writes it. Part 9's sidebar panel uses these.
- Response: `GenerateResponse.profile_path: Optional[str]` (the path that was used).

### 8. Tests

- Oracle unit tests with hand-computed cases:
  - plain jump: max height `v²/2g`; flat reach `run_speed · 2v/g`.
  - double jump: reaches above the single-jump apex and below `v1²/2g + v2²/2g`.
  - dash: extends flat reach by exactly `distance_px`.
  - wall jump: inside and outside its envelope.
  - gravity_scale 0.5: higher reach than at 1.0.
  - `best_ability`: ordering and the `False` case.
- Validator: `ABILITY_NOT_IN_PROFILE`; an edge that is impossible with the default profile becomes
  valid with `double_jump` enabled, and the patcher assigns the ability with no LLM call.
- Extractor: a grid with a gap only crossable with dash produces a dash edge when a profile with
  dash is given, and no edge without it.
- Profile: load/save round trip, defaults on a missing file, error on invalid JSON, prompt summary
  contains the enabled abilities only.
- Server: `/profile` GET/PUT round trip in `tmp_path`; `/generate` with the fake provider and a
  profile file.
- All Part 1 tests keep passing (update them where `PhysicsParams` was used).
- Do **not** run the Gemini e2e test (the free-tier quota is ~20 requests/day; the orchestrator
  runs it).

## Constraints

- No new dependencies. Match the existing style (module docstrings with the maths, `# ───`
  banners, type hints).
- Do not touch `layout/`, `aesthetic/` or `ldtk-src/`.
- Do not commit; the orchestrator reviews and commits.

## Done when

- `source .venv/bin/activate && pytest` passes offline.
- `python -c "import server"` works.
- Short report: files changed, the double-jump model you chose (exact or sampled, and why), any
  spec deviations, open issues.
