# Part 3 — Layout v2 (+ physics re-verification on the final grid)

Design context: [../DESIGN.md](../DESIGN.md) (Q1, Q4). Builds on Part 1 (`LevelSpec`) and Part 2
(`GameProfile`, `reachable`/`best_ability`, profile-aware `extract_graph`). Branch `feat/v2`.

## Goal

Today `sugiyama_layout` throws away the LLM's coordinates and lays every platform out on a fixed
stride and `v_step`, so the final tile grid is never re-checked. Part 3 makes the final grid the
thing that is proven beatable:

1. Lay the spec out on a grid of `spec.size`, keeping the LLM's **positional intent** (its
   coordinates) and keeping **sections** in left-to-right order.
2. Place every **spec entity** on its anchor platform.
3. **Re-verify physics on the final grid** (extractor + oracle + collisions). If verification
   fails, repair the grid deterministically; if that fails, re-prompt the LLM with the grid errors.
   The result is a level that is verified, or an explicit failure. Never return an unverified level
   that claims to be valid.

## Deliverables

### 1. `layout/spec_layout.py`: `layout_spec(spec, profile, seed=0) -> SpecLayout`

`SpecLayout`: `node_layouts: Dict[str, NodeLayout]` (reuse `NodeLayout`), `ir: IRLevel`,
`sections: List[SectionBand]` (`id, name, col_start, col_end` inclusive, `gravity_scale`,
`theme`, `difficulty`), plus the list of adjustments made (for reporting).

- Canvas: `spec.size` (width × height tiles), `tile_size = profile.tile_size`.
- **Coordinate conversion** (document it in the module docstring and test it): the spec is Y-up
  pixels with `y` = top-surface height; the IR is `grid[row][col]` with row 0 at the top. Choose
  the convention so that a node with `y = 0` sits on the bottom row and the standing surface maps
  to the platform's top row. `w` rounds to whole tiles (minimum 1); `h` is 1 tile.
- **Sections:**
  - Each section gets a contiguous column band, ordered by `Section.order`. Bands come from the
    x-extent of that section's nodes when the LLM's layout already respects the order. Otherwise,
    remap each section's nodes affinely into bands sized in proportion to the section's node
    extent (minimum 8 columns).
  - Bands cover the full width with no gaps.
  - `gravity_scale_at(col)` comes from the bands.
- **Conflict resolution:**
  - Overlapping platforms, and platforms with less vertical clearance than the player hitbox above
    a horizontally overlapping platform, get nudged vertically within bounds. Move off-golden-path
    nodes first.
  - Every adjustment is recorded.
- Hazards rasterise as `TileValue.HAZARD`. Start and Exit nodes rasterise as solid, as today.
- `layout/sugiyama.py` stays as it is (legacy; `example.py` may still use it).

### 2. Entity placement — `layout/spec_entities.py`

`place_spec_entities(spec, spec_layout) -> List[IREntity]` (appended to `ir.entities`):
- Every spec entity gets an `IREntity` standing on its anchor platform (the row above the surface).
  Several entities on one anchor are spread evenly across its width with no two on the same cell.
  Flyers (`archetype == "flyer"`) go 2–3 tiles above the surface if there is room.
- `entity_type` = spec `kind` (`"Start"`, `"Exit"`, `"Enemy"`, `"Pickup"`, …).
- `metadata`:
  - every non-null kind field
  - `description`, `sprite`, `spec_id`, `section`, `anchor`
- Start and Exit spec entities replace the structural Start/Exit markers. There must not be two
  Starts.
- An entity's cell must be empty and have headroom for its height (1 tile, or the player hitbox
  for Start). Otherwise, try other cells on the anchor and record a warning if none works.
- `patrol_range` is clamped to the anchor platform width. Record a warning when it is reduced.

### 3. Grid verification — `layout/verify.py`

`verify_grid(spec_layout, profile, spec) -> GridReport` with `ok: bool`, `errors:
List[GridError]` (`code`, `message`, `node_ids`), `golden_path: List[str]` and a stats dict.

- Add `player: { width_px: 12, height_px: 24 }` hitbox fields to the profile's `Player`, with
  defaults so existing profiles stay valid. While at it, constrain `GameProfile.tile_size` to 16
  (validation error otherwise, with the message "only 16 px tiles are supported for now"). This
  closes the Part 2 open item about the fixed `TILE_SIZE`.
- **Collision-aware arcs** in `symbolic/physics.py`:
  - `trajectory_clear(grid, p1, p2, profile, gravity_scale, ability, tile_size) -> bool`. It
    samples the player hitbox along candidate trajectories and returns True if any is free of solid
    and hazard tiles (landing on the target surface is allowed).
  - Candidates: both flight-time roots, at a few launch speeds (100 %, 85 %, 70 % of `jump_v`,
    for variable jump height) with |vx| ≤ `run_speed`.
  - Double jump: sample τ. Dash: the dash segment must be clear too.
  - Walking off a ledge (vy = 0) is a candidate for downward moves. Downward moves are also capped
    by `run_speed` here (this closes the Part 2 open item about uncapped Fall edges at grid level).
- `extract_graph(..., collision=True)` (new flag, default False so old callers are unchanged)
  keeps only edges whose trajectory is clear.
- Map grid spans back to spec nodes by overlap so errors can name spec node ids. The grid graph
  may split or merge spans.
- **Checks:**
  - `NO_GRID_PATH`: no Start→Exit path in the grid graph. Name the last reachable spec node and
    the unreachable next hop on the spec's golden path.
  - `WALL_MISSING`: a wall-jump hop on the path needs a solid column of at least 3 tiles next to
    the arc. If there is none, the hop is invalid.
  - `ENTITY_UNREACHABLE`: an entity's anchor is not reachable from Start. Pickups, keys, weapons
    and checkpoints count; decorative NPCs or signs only warn.
  - `KEY_LOCK_ORDER`: the Exit has `requires_key`, but no Key is reachable before the Exit.
  - `START_BLOCKED`: the Start has no headroom for the player.

### 4. Repair — `layout/repair.py`

`repair_grid(spec_layout, report, profile, spec) -> (SpecLayout, List[str] applied)`:
deterministic, bounded (at most 5 rounds of verify → repair):
- clear headroom by nudging the blocking platform (not the path node) up or down;
- insert a stepping-stone platform (a new node id, added to the spec section that owns the column)
  in the gap of a failing hop, at a position the collision-aware oracle accepts from both ends;
- move a misplaced entity to another cell on its anchor, or to the nearest reachable non-hazard
  platform in the same section (record a warning: "moved X from A to B").

### 5. Pipeline — `pipeline/level.py` (new package; add `pipeline*` to setuptools `include`)

`build_level(description, controller, profile, constraints=None, seed=0, max_layout_retries=2)
-> BuiltLevel` with `spec`, `spec_layout`, `report`, `warnings`, and `stats` (LLM attempts and
patches from `controller.stats`, `layout_retries`, `grid_repairs` applied). Steps:
1. `controller.generate_spec(...)`.
2. `layout_spec` → `place_spec_entities` → `verify_grid` → `repair_grid` while it is failing.
3. If it still fails, re-prompt the LLM. Add a `controller.repair_spec(spec, errors)` (or extend
   `generate_spec` with an optional prior spec and extra errors). The repair message lists the
   grid errors in spec node ids.
4. After `max_layout_retries`, raise `LevelBuildFailed` with the last report.

### 6. Aesthetic + server wiring

- `build_aesthetic_layer(..., place_random_entities: bool = True)`. The server passes `False`
  when using a spec, so the level contains only the entities the prompt asked for (no random
  coins or enemies). Theme: map the first section's `theme` through `detect_theme` unless the
  request overrides it. Record the per-section mapped theme in `ir.metadata["sections"]`. Per-section
  tilesets come with Parts 4 and 5, so do not build them here.
- `server.py` uses `build_level`. Response additions:
  - `verified: bool`
  - `warnings: List[str]`
  - `stats.layout_retries`, `stats.grid_repairs`
  - `stats.entities` must count spec kinds (`Enemy`, `Pickup`, …).
- The LDtk adapter maps unknown entity types to `Enemy` today. Leave export as it is beyond not
  crashing; the full entity catalog is Part 4. Do record kind/fields in `IREntity.metadata` so
  Part 4 can use them.

### 7. Tests

- Coordinate conversion round trip; size honoured; section bands ordered, contiguous and
  full-width; out-of-order LLM sections are remapped.
- Overlap resolution moves the off-path node, not the path node.
- Entities: several on one platform, on distinct cells; flyer placed above; Start/Exit not
  duplicated; patrol clamped with a warning.
- `trajectory_clear`: an open jump passes; a jump under a low ceiling fails; a walk-off-ledge
  drop passes; a dash through a wall fails.
- `verify_grid`: each error code fires on a hand-built small case.
- `repair_grid`: inserts a stepping stone that fixes a too-wide gap; nudges a ceiling.
- `build_level` with a FakeProvider:
  - every valid fixture in `tests/fixtures/llm/` yields `report.ok`;
  - a fixture whose grid cannot be repaired triggers an LLM re-prompt whose message names the
    failing hop, then succeeds on the second fake response.
- Property-style test (deterministic seeds, about 50 cases): randomly generated small valid specs
  (Start → chain of reachable platforms → Exit, under the default profile) all come out of
  `build_level` either `ok`, or as `LevelBuildFailed`. Never "ok" with a grid that has no path.
- Server `/generate` with the fake provider: `verified` is true, the `.ldtk` loads as JSON, and the
  entity counts match the spec.
- All earlier tests keep passing. Do **not** run the Gemini e2e test.

## Constraints

- No new dependencies. Match the existing style.
- Do not touch `ldtk-src/`. Keep `layout/ldtk_adapter.py` changes minimal (Part 4 rewrites
  entity export).
- Performance: `build_level` on a 300×100 spec with about 80 nodes must finish in under 5 s on
  the fake provider. Add a test with a generous bound, and prune candidate pairs by distance
  before running arc checks.
- Do not commit; the orchestrator reviews and commits.

## Done when

- `pytest` passes offline; `python -c "import server"` works.
- Report: files changed, the coordinate convention, the collision-sampling approach and its
  resolution, timing of the large-level test, deviations, and open issues.
