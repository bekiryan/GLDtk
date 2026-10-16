# Part 11: Physics accuracy (Python, in a worktree)

Context: [../PLAN.md](../PLAN.md); the v2 design is in `docs/v2/DESIGN.md` (Q1: "statically
guaranteed beatable"). PROGRESS.md (Parts 2 and 3) lists three known simplifications in the
verifier, which make that guarantee wrong in rare cases. Close all three.

## 1. Gravity across section borders
**Today:** a move uses the effective gravity `profile.gravity · gravity_scale` of the section it
**starts** in, for the whole arc (`symbolic/physics.py`, and the grid verification in
`layout/verify.py` and the extractor). A jump from a normal section into a 0.5-gravity section,
or the reverse, is judged with the wrong arc.

**Required:** when the swept trajectory crosses a section border, the gravity changes at the
border.
- Simulate the trajectory piecewise: integrate y(t) with the gravity of the section the hitbox
  centre is in, at each sample step.
- The closed-form oracle stays as the fast path for moves inside one section.
- Do the same in the grid verification (the hitbox sweep), so verification and the oracle agree.
- Vertical levels have row bands, so their borders are horizontal lines; handle both
  orientations (see `ir.metadata["sections"]` and `orientation`).

## 2. Headroom along a walk
**Today:** walking along a span is not checked for low ceilings. A ceiling 1 tile above a span
blocks a 2-tile-tall player, yet the walk edge is accepted.

**Required:**
- A walk (and the walk part of any move) needs `player_h` empty tiles above every cell the player
  passes through, using the profile's player hitbox (`profile.player_tiles()`).
- Spans where the player cannot stand are split at the blocked cells. The extractor must not
  produce walkable spans through them.
- Report blocked walks in the verification errors with a clear message, for example
  "low ceiling at column X, row Y blocks the walk".
- Check `layout/repair.py` too: a repair stone or nudge must not create a low ceiling over a walk.

## 3. Wall-jump paths
**Today:** `check_wall_jump` is an envelope: "the player gains at most …", and the existence of a
wall is only partly checked on the grid.

**Required:** an exact wall-jump check on the final grid.
- A wall-jump move is valid only if there is a real wall: a contiguous column of solid tiles at
  least `player_h` tall next to the trajectory.
- The player must be able to reach that wall in the air (an arc from the source).
- Each kick (up to `max_climb` per kick, `kick_speed` and `push` from the profile; see
  `game/profile.py`) must lead either to the target or to another reachable wall contact. Model
  it as a short search over wall contacts with bounded depth.
- The envelope can stay as a quick reject, but acceptance must come from the grid check.
- Keep `reachable()`, `best_ability()` and the edge `metadata["ability"]` contract unchanged for
  callers.

## Tests (offline)
- **Gravity:** hand-made two-section grids where:
  - the starting section's gravity would accept a jump that the real piecewise arc fails;
  - and the reverse, a jump that only succeeds because of lower gravity after the border.
  - Run both orientations.
- **Headroom:** a ceiling 1 tile above a span. The walk is rejected for a 2-tall player and
  accepted for a 1-tall player.
- **Wall jump:**
  - a shaft with walls on both sides is accepted;
  - the same shaft with one wall removed or too short is rejected;
  - a gap with no wall nearby is rejected even though the envelope allows it.
- **Property test:** the existing 50-seed property test still verifies 50/50, or explain any
  change.
- All of `pytest` passes.
- **Timing:** report the build time of the 300×100, 87-node case before and after (it was
  about 1.6 s), and keep it under about 3 s.

## Rules
- **You own** `symbolic/`, `layout/verify.py`, `layout/repair.py`, `layout/spec_layout.py` (only if
  needed) and new tests. Part 12 (edits, `edit/` and `layout/densify.py`) and Part 13 (a new
  `layout/route.py`, `server.py`, `api/models.py`) run at the same time. Do not edit their files.
  If you change a public function signature that they might use (`move_between`, `verify_grid`,
  `SpanIndex`), keep a compatible wrapper.
- First run `git reset --hard feat/v2` in your worktree (it may start from `main`).
- Commit once: `v3 Part 11: physics accuracy (cross-section gravity, headroom, exact wall jumps)`,
  ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Do not push, do not edit `docs/v2/PROGRESS.md`, never call a real LLM, and do not use port 8765.
