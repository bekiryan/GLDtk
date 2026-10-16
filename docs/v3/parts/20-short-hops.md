# Part 20: Short hops and walk-first paths (Python, in a worktree)

Context: user feedback on 2026-10-15, after seeing the route overlay.

## Problem
Paths are made of long jumps that clear one or more platforms, instead of walking and hopping
from one platform to the next. There are two causes:
- **Route cost.** In `layout/route.py`, the cost per move is fixed (`MOVE_COST`: walk 1, fall 2,
  jump 3, ability 5, plus a tiny distance tie-breaker). One huge jump therefore beats three
  small hops and the walks between them.
- **Generation.** The layout keeps the LLM's coordinates, so gaps are as large as the LLM made
  them. The default physics can jump about 11 tiles high and 15 tiles across, and repair and
  densify only make sure a move is *possible*, not comfortable. Platforms therefore end up far
  apart.

## Required
1. **Comfort limits in the Game Profile.** Add a `design` block to `game/profile.py`, with
   defaults that keep current files valid (missing means the default):
   - `comfort_hop_tiles`, default 4: the horizontal gap of a normal jump;
   - `comfort_rise_tiles`, default 3: how far a normal jump goes up;
   - `comfort_drop_tiles`, default 6: how far a normal fall goes down.

   Expose these in `GET/PUT /profile` (`server.py`) and in `GameProfile.summary()` for the LLM
   prompt.
2. **Route cost by effort** (`layout/route.py`).
   - **Walk cost** grows with distance.
   - **Jump and fall cost** grows faster than linearly with gap and rise. Choose it so that any
     jump beyond the comfort limits costs more than walking plus two comfortable hops over the
     same ground, and an ability move costs more again. Document the formula.
   - **Effect:** a route that can use intermediate platforms uses them.
   - **Test:** on a grid with A, B and C in a row (each gap comfortable), and A→C also possible
     as one long jump, the route goes A→B→C.
3. **Comfortable golden path** (`pipeline/level.py`, `layout/repair.py` or a new
   `layout/comfort.py`).
   - **Check:** after layout and before densify, check every golden-path move against the comfort
     limits. This is a design check, not a physics one.
   - **Fix:** for each move that exceeds them, outside sections tagged `parkour` (see 4), insert
     intermediate platforms (3–5 tiles wide) and/or ground segments. Then every move on the
     golden path from Start to Exit is comfortable, or a walk.
   - **Reuse stone placement:** reuse the existing stone placement in `layout/repair.py`,
     extended with a target-size bound, so headroom and arcs are respected.
   - **Bookkeeping:** record what was added in the stats (`comfort_added`) and the warnings, the
     same way densify does.
   - **Cannot fix:** if a move cannot be made comfortable, leave it and add a warning; never fail
     the level.
   - **Edits:** run this inside region and section edits too (`edit/region.py`), for the rect only.
4. **A parkour exception.** Add `style: Optional[Literal["walk", "parkour", "mixed"]]` to
   `Section` in `spec/level_spec.py`.
   - A null style means `mixed`.
   - **Parkour sections** keep longer jumps, up to the physics limit, but still verified.
   - **Mixed sections** allow at most one long jump in a row.
   - **Walk sections** get the comfortable rule strictly.
   - **Prompt and storage:** update `llm/prompt.py` so the LLM uses `parkour` only when the prompt
     asks for parkour or precise platforming, and states that most of a level is walking,
     fighting and short hops. Store `style` in `gldtk_sections`.

   Part 21 will extend this field into full section structure types, so keep it a separate,
   small field.
5. **Densify:** add-on platforms should also respect the comfort limits, which means preferring
   positions within comfortable reach of an existing reachable platform.

## Tests (offline)
- Route A→B→C rather than A→C.
- The comfort fix:
  - a spec with a 12-tile golden-path jump gets intermediate platforms, and every golden-path
    move is then comfortable;
  - the same spec in a `parkour` section keeps the jump;
  - an impossible fix gives a warning and no failure.
- Old profiles without `design` still load.
- `/profile` round-trips `design`.
- Edits apply the comfort pass inside the rect only.
- The eval replay still verifies 8/8:
  `python -m eval.run --replay eval/recordings/gemini-2026-10-15 --out <tmp>`. Compare with
  `--compare eval/results/gemini-2026-10-15 <tmp>` and report the number of comfort additions per
  prompt.
- Before/after ASCII plus route-step counts for the replayed `three-sections` prompt (from
  `--export`, or rebuilt from the recording).
- All of `pytest` passes.

## Rules
- **You own:**
  - `game/profile.py`;
  - `layout/route.py`;
  - the new `layout/comfort.py`, plus small hooks in `pipeline/level.py`, `layout/repair.py`,
    `layout/densify.py` and `edit/region.py`;
  - `spec/level_spec.py` (the `style` field only);
  - `llm/prompt.py`;
  - the `/profile` bits of `server.py`;
  - tests.
- Part 17a (worlds) runs in parallel in another worktree. It touches `server.py`, `api/models.py`,
  `layout/ldtk_adapter.py`, `layout/ldtk_reader.py`, `edit/context.py` and `pipeline/` for
  multi-level generation, so keep your changes there small.
- First run `git reset --hard feat/v2`.
- Commit once: `v3 Part 20: short hops and walk-first paths`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Do not push, do not edit `docs/v2/PROGRESS.md`, never call a real LLM, and do not use port 8765.
