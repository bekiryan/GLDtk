# Part 22: Soft-lock detection (Python, in a worktree)

Plan: [../PLAN.md](../PLAN.md) §22. **Guarantee: from every place the player can reach, an Exit is
still reachable**, including the item state (keys, ability pickups).

## Build
- New module `layout/softlock.py`: `find_softlocks(graph, start_span, exit_spans, items) -> SoftlockReport`.
  - **SCC check:** run on the movement graph the verifier already builds (extractor edges, where
    falls and drops are one-way). Reachable spans with no path to any Exit are soft-locked; group
    them into "traps" (connected areas) with their entry move (the edge that leads in).
  - **Item-state product graph:** nodes are (span, frozenset of keys/abilities held).
    - Picking up a Key or an ability pickup adds it to the state.
    - A locked Exit (`requires_key`) or a locked door (`Interactive` door with `linked_to`/`opens`
      a key) is only passable with the key.
    - This catches "dropped past the key" and "the key is behind its own door".
    - Bound the number of item combinations: keys and abilities are few; cap at about 2^6 states
      and report "too many items to check" beyond that.
- **Integration:**
  - add a `SOFTLOCK` error in `layout/verify.py` (generate) and in `edit/check.py` (edits;
    pre-existing soft-locks outside the edited area are baseline, the same way other baseline
    errors are handled), with messages such as "falling into the pit at column X, row Y traps the
    player (no way back up)";
  - the route API (`layout/route.py`) gains `softlock_spans: [{col,row,w}]`, so the overlay can
    shade them later.
- **Deterministic repair** (`layout/repair.py` style, reusing `find_stone` and the comfort hop
  limits):
  - for a pit or sealed area, add a stepping-stone chain out to a span that can reach an Exit;
  - for an item trap, move the key or ability onto a span from which the trap is avoidable, or
    add a way back;
  - verify again after each fix, and never make a level fail because of a repair attempt. If
    unfixed, it stays an error, and the level is not written, as with other errors.
- **Performance:** it must add at most about 10% to the 300×100, 87-node build.

## Tests
- A pit trap is detected, then repaired.
- A one-way drop into a sealed room is detected.
- Dropping past the key before a locked Exit is detected, and the item-state check passes once
  the key is moved before the drop.
- A key behind its own door is detected.
- Levels without traps report none.
- The eval replay still verifies:
  `python -m eval.run --replay eval/recordings/gemini-2026-10-15 --out <tmp>`. Report any
  soft-locks it finds in the recorded levels.
- An edit that creates a trap is not written.
- All of `pytest` passes.

## Rules
- **Parallel work:** Part 21 (terrain) runs in another worktree and edits `symbolic/extractor.py`,
  `pipeline/level.py`, `spec/`, `layout/terrain/`, `llm/prompt.py` and `edit/region.py`. Do NOT
  change `symbolic/extractor.py`; read the graph it produces. Keep your hooks in
  `layout/verify.py`, `edit/check.py`, `layout/route.py` and `layout/repair.py` small.
- First run `git reset --hard feat/v2`.
- Commit once: `v4 Part 22: soft-lock detection`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No push, do not edit PROGRESS.md, no real LLM, do not use port 8765, and do not touch
  `ldtk-src`.
