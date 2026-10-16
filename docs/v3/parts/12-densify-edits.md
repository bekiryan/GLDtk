# Part 12: Densify inside region and section edits (Python, in a worktree)

Context: [../PLAN.md](../PLAN.md).

## Problem
A generate runs `layout/densify.py`, which adds ground segments, intermediate ledges and fill
platforms (about 1 per 50 empty tiles) so a level reads as a level. Region and section edits
(`edit/region.py`) never run it. A regenerated section is therefore visibly sparser than its
neighbours: the LLM returns only about 6–10 small platforms for a whole band.

## Required
After the region is stamped and repaired, and **before** the final verification in
`edit_region()`, densify **inside the rect only**:
- **Reuse `layout/densify.py`'s logic.** It works on a `SpecLayout` plus a `GridReport`; the edit
  pipeline works on a raw grid, `Spans` (`edit/context.py`) and `verify_level` (`edit/check.py`).
  Add a grid-level entry point to `densify.py` (for example
  `densify_grid(grid, rect, profile, protected, rng, gravity_scale_at, player_h)`), shared by both
  paths, or a thin adapter. Do not fork the algorithm.
- **Protected cells** are left empty and are never filled:
  - the locked seams (`locks.clear`);
  - every move arc the current check uses inside the rect, including arcs that cross the rect
    border;
  - the headroom of every entity.
- **Stay inside the rect.** Nothing outside it changes, and the 1-tile margin rule stays as it is.
- **Determinism:** use a seeded RNG (an optional `seed` on the edit requests, default 0, like
  generate).
- **Verify after densify.** If an addition breaks verification, drop the offending additions,
  the same way generate does. Densify must never make an edit fail.
- **Request flag:** add `densify: bool = True` to the region, section and entity edit requests in
  `api/models.py`. Entity edits do not change the grid, so there it is ignored. Thread it through
  `server.py`, and add `densify_added` to the edit response stats.
- **Spec bookkeeping:** added platforms appear in the updated `gldtk_spec` as `fill_*` nodes, the
  same way generate records them (`_updated_spec` in `edit/region.py`).
- **Messages:** the edit's warnings include "densify: added N fill platform(s)", as generate's do.

## Tests (offline, FakeProvider)
- A section edit whose answer has only 2 small platforms:
  - the result has more platforms inside the band;
  - nothing outside the band changes (same comparison as the existing edit tests);
  - the result verifies;
  - `densify_added > 0`.
- `densify=false` adds nothing.
- The same seed gives the same result.
- Seams and arc cells stay empty: assert on the locked seam cells.
- Generate is unaffected: existing densify tests still pass, and output is byte-identical for
  the same seed if you refactored.
- All of `pytest` passes.

## Rules
- **You own** `edit/`, `layout/densify.py`, `api/models.py` (the edit request models only),
  `server.py` (the edit routes only) and new tests. Part 11 owns `symbolic/`, `layout/verify.py`
  and `layout/repair.py`. Part 13 adds `layout/route.py` plus `route` fields and a route endpoint
  in `api/models.py`/`server.py`, so keep your changes there small and local for an easy merge.
- First run `git reset --hard feat/v2` in your worktree.
- Commit once: `v3 Part 12: densify inside region and section edits`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Do not push, do not edit `docs/v2/PROGRESS.md`, never call a real LLM, and do not use port 8765.
