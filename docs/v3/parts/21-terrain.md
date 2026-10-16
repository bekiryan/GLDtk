# Part 21: Terrain from section structure types (Python; small Haxe follow-up)

Context: the user decided on 2026-10-15 to use **section types**. The goal is levels that feel like
Blasphemous 2, Neon Abyss 2, Sonic Origins or Spelunky 2: real floors, walls and ceilings carved
from rock, mostly walking and fighting, some parkour, labyrinths to find a way through, and
arenas. Today a level is floating platforms in empty space. This part depends on Part 20
(comfort limits and the `Section.style` field) and runs after Part 17a (worlds) is merged.

## Design
- **Section structure.** In `spec/level_spec.py`, add `structure` to each `Section`, with these
  types:
  - `corridor`: walk and fight. A continuous floor with steps and slopes made of 1-tile stairs,
    a ceiling 4–7 tiles above the floor, pillars, small pits.
  - `parkour`: a shaft or gap run. Walls on the sides, ledges and short hops, and longer jumps
    allowed (Part 20's `parkour` style).
  - `labyrinth`: a maze of corridors sized for the player (corridor height ≥ player height + 1,
    width ≥ 3). It has dead ends, optional side rooms with pickups, and the exit door on the far
    side.
  - `arena`: a closed room with a flat floor, a few platforms or pillars and the enemies or boss.
    It has an entrance and an exit.
  - `shaft`: vertical travel, up or down, with ledges or drops. In vertical levels this is the
    default.
  - `hub` (or `rooms`): connected rooms with doors and openings, branching side paths, a key or
    secret.
  - `open`: today's behaviour, platforms in the open, for sky or outdoor levels.

  There are also per-type parameters, all optional, with sensible defaults: `ceiling_height`,
  `corridor_width`, `complexity` (0–1), `branches`, `pits`, `vertical: up|down`.
- **The LLM's job.** It chooses the types and their order from the prompt, for example "crypt:
  corridors with skeletons, a parkour shaft, a small labyrinth, a boss arena". It still places
  **entities** (enemies, pickups, keys, chests), now anchored to logical places, not exact
  coordinates:
  - `room`, `floor_position` (0–1 along the section), `dead_end`, `arena_center`, `ledge`, …;
  - the generator resolves these to cells.

  The old node/edge graph stays valid for `open` sections. Keep backward compatibility: old
  specs without `structure` behave as `open`.
- **Generators.** Write deterministic, seeded generators in a new `layout/terrain/` package, one
  module per type.
  - Each gets the section band (rect), the entry and exit points on its borders, the
    profile/comfort limits and the parameters. It returns:
    - grid cells (SOLID, EMPTY, HAZARD);
    - named anchor cells for entity placement;
    - the spans the designer intends the player to use.
  - Sections are **joined** so the exit of section k lines up with the entry of section k+1:
    the same floor height or opening, or a connecting ramp or stairs.
  - The outer border of the level is solid (a frame), except where a level-wide `open` sky
    theme needs open air.
  - Generators carve **from solid rock**: the default fill is SOLID, and walkable space is carved
    out.
  - Labyrinths use a maze algorithm (recursive backtracker or Prim) on a coarse cell grid. Each
    cell is (corridor width + wall) tiles, scaled so the player fits with headroom. The main path
    from entry to exit is guaranteed, and dead ends hold optional pickups.
- **Pipeline.** In `pipeline/level.py`, the steps are:
  1. spec;
  2. terrain generation per section, then joining;
  3. entity placement on anchors;
  4. the Part 20 comfort pass, which only applies in `open` and `parkour` sections;
  5. densify, only in `open` sections;
  6. **grid verification as today**. The extractor and the oracle work on any grid, so walls,
     ceilings and mazes are verified the same way;
  7. repair.

  Then the same aesthetic and export steps as today. The re-prompt loop stays. On a
  verification failure, try another seed for that section's generator, up to 3 times, before an
  LLM re-prompt.
- **Art.** Interior solid tiles (rock mass) use the theme's CENTER variants (the sheets already
  have them). Autotiling must look right for large solid masses, ceilings and walls; check it
  with renders.
- **Regional edits.** A section edit can change the section's structure ("make this a labyrinth").
  It uses the same generators, with the locked seams as entry and exit points. Region edits
  inside a terrain section keep the surrounding rock.
- **Fidelity.** Add a `structure` requirement check type. For example, "there is a labyrinth"
  is satisfied when that section type exists and its maze has at least N dead ends. Hazard and
  enemy checks work as today.
- **Route and difficulty.** They work unchanged on the grid. Make sure the difficulty metrics
  make sense for corridors: walk-heavy, so the enemies' share counts more.

## Tests
- Each generator, over many seeds:
  - it is beatable from entry to exit;
  - corridors have headroom;
  - a labyrinth has a unique main path and dead ends;
  - arenas are closed except at the entry and exit;
  - output is deterministic per seed.
- Joining keeps entry and exit aligned.
- A crypt fixture (corridor, parkour, labyrinth, arena) generates, verifies and exports, and the
  ASCII render looks like a level. Include it in the report.
- Old specs (no `structure`) give byte-identical output for the same seed.
- The eval replay still verifies.
- Section edits that change the structure.
- Add 4–6 eval prompts about structure to `eval/prompts.json`, with tags.

## Haxe (small; orchestrator or a later agent)
- The section dropdown shows each section's structure type.
- "Make this section a …" quick actions.
- Done separately after the Python merge.

## Rules
- Run in a worktree; first run `git reset --hard feat/v2` (after 17a and 20 are merged).
- Commit: `v3 Part 21: terrain from section structure types`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Do not push, do not edit `docs/v2/PROGRESS.md`, never call a real LLM, and do not use port 8765.
