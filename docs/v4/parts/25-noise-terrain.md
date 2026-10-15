# Part 25a: Noise-based terrain fill and endless chunks (Python, in a worktree)

Plan: [../PLAN.md](../PLAN.md) §25. This is the part that does not depend on Part 21 (terrain
types). Part 21 will call the fill later for its rock masses.

## Build
- **`layout/noise.py`**: deterministic, seeded 2D noise and the tools built on it. Use numpy if
  it is already a dependency; otherwise write pure Python (no new heavy dependency).
  - Noise: value noise or OpenSimplex-style gradient noise, with octaves (fBm). Lacunarity and
    gain are parameters.
  - Cellular-automata smoothing, using the 4-5 rule.
  - Domain warping, optional.
  - The noise must be continuous across chunk borders: sample with world coordinates.
- **`layout/fill.py`**: `noise_fill(grid, protected, rng_seed, params, region=None) -> FillResult`.
  - It turns large EMPTY areas that are far from the playable space into natural rock. The
    result has caves and pockets, strata near the bottom, ceilings and stalactites near the top,
    and hanging rock.
  - **Protected cells are never filled**, extended by a margin. They are:
    - the golden route and its arcs (`layout/route.py`);
    - the headroom of every reachable span;
    - entity cells and their headroom;
    - the comfort and densify additions;
    - the locked seams in edits.
  - Run verification after the fill and drop fill blobs (connected components) that break it.
    The fill must never make a level fail.
  - Params: `density` (0–1), `roughness`, `min_distance_from_path` (tiles), and `style`, one of
    `cave`, `ruins` or `sky`. Sky means very sparse floating islands.
  - Pipeline hook, after densify:
    - `GenerateRequest.fill: Optional[float]` (null = off for now, so outputs stay the same);
    - also apply it to region and section edits, inside the rect only;
    - add `fill_added` to the stats and a warning line.
  - The art is already autotiled; check that large masses use the CENTER variants and look
    right. Save a PNG render of one level before and after to your scratch folder.
- **Endless chunks** (`pipeline/chunks.py`), built on the Part 17 world export (`layout/world.py`,
  `pipeline/world.py`):
  - **Chunk layout.** A chunk is a fixed-size level (for example 64×32 tiles) at chunk
    coordinate k, either horizontal or vertical.
  - **Seams.** Seam openings come from a hash of (seed, k, side): the row band of the opening on
    the shared border. That makes chunks k and k+1 agree with no shared state.
  - **Contents.** Each chunk is noise terrain (the fill above, with density high and a guaranteed
    carved path from the entry seam to the exit seam), plus densify and comfort hops. The
    entities come from a simple, seeded rule table (coins along the path, enemies by distance),
    or from an LLM theme per group of chunks (optional, `theme` per chunk group).
  - **Checks.** Each chunk verifies entry→exit, and soft-locks once Part 22 lands (leave a hook).
  - **API.** `POST /chunks {path, count, seed, start_k?, theme?}` appends N chunks to a world
    file, as LDtk levels with the links from Part 17 (Exit `leads_to` the next chunk's Start).
    Generating chunk k twice gives the same level, byte-identical.
- **Tests:**
  - the noise is deterministic and continuous across chunk borders (sample both sides);
  - the fill never touches protected cells, and the level still verifies;
  - `fill=null` gives byte-identical output to today;
  - a chunk's seams agree with its neighbour's;
  - chunk k is the same when generated alone or in a run;
  - `/chunks` appends linked, schema-valid levels;
  - all of `pytest` passes.

## Rules
- **Parallel work:** Part 21 (terrain) is changing `symbolic/extractor.py`, `pipeline/level.py`,
  `spec/`, `layout/terrain/`, `llm/prompt.py` and `edit/region.py`. Part 22 (soft-locks) is
  changing `layout/verify.py`, `edit/check.py`, `layout/route.py` and `layout/repair.py`. Part
  27a (memory) adds `memory/` plus small hooks in `server.py`. Keep your hooks in
  `pipeline/level.py`, `edit/region.py` and `server.py` to a few lines each.
- First run `git reset --hard feat/v2`.
- Commit once: `v4 Part 25a: noise terrain fill and endless chunks`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No push, do not edit PROGRESS.md, no real LLM, do not use port 8765, and do not touch
  `ldtk-src`.
