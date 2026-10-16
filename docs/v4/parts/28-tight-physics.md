# Part 28: Tighter default physics, about a 4-tile jump (Python, in a worktree)

The user decided on 2026-10-15 to move to a Spelunky, Blasphemous or Sonic feel. The current
defaults (gravity 980, jump_v 600, run 200) give an 11.5-tile jump, a 5.6-tile minimum hop and
8 empty rows needed above a take-off. That forces terrain to work around low ceilings, and jumps
look huge.

## Change
- **New defaults** in `game/profile.py`, picked so that:
  - a full jump is about 4 tiles high and about 7 tiles across at run speed;
  - the shortest hop (the 0.7 × jump_v launch the verifier tries) is about 2 tiles;
  - corridor ceilings of 5–6 tiles allow jumping and fighting.

  Suggested values: **gravity 1800, jump_v 480, run_speed 220**; tune them and document the
  derived limits. Retune the ability defaults to match (double-jump jump_v, dash distance, wall
  kick, max climb) so each ability adds a sensible amount: about +2 tiles of height for the
  double jump, and about 4 tiles of distance for the dash.
- **Saved profiles keep their values.** Projects with a saved `gldtk.game.json` keep their
  numbers. Only new projects and missing fields use the new defaults. Make sure `/profile`
  reports where each value comes from (saved or default) only if that is cheap; otherwise skip it.
- **Retune everything that baked in the old numbers:**
  - `GameProfile.design` comfort limits (hop, rise, drop), so they stay within the new physics:
    a hop of about 4–5 tiles, a rise of about 3, a drop of about 6;
  - the Part 21 terrain generators: ceiling heights, the "8 empty rows" assumption, link
    pedestals, zigzag spacing, labyrinth cell size, arena air height. Where the old workarounds
    (stairs and holes everywhere) are no longer needed, simplify them, so corridors get real
    jumps over pits and onto ledges;
  - densify and comfort spacing;
  - the Part 24 complexity mapping;
  - the Part 25a chunk hop sizes;
  - the Part 23 space mapping, if it assumed sizes;
  - the Play mode client (`ldtk-src`) reads the profile from the server, so no Haxe change
    should be needed. Grep for hard-coded 600/980/200 there, and report any you find rather
    than editing `ldtk-src`;
  - the LLM prompt's physics summary, which comes from the profile automatically; check it.
- **Tests and recordings:** expect many hard-coded expectations (node counts, gaps, ASCII).
  - Prefer tests that pin their own profile when they test a specific geometry.
  - Re-baseline the rest deliberately, never by weakening assertions.
  - The eval replay must still verify:
    `python -m eval.run --replay eval/recordings/gemini-2026-10-15 --out <tmp>`. The recorded
    specs were made for the old physics, so the layout may need repair and comfort stones; the
    levels must still verify. Report what changed (repairs, comfort additions).

## Report
- before/after derived limits;
- ASCII of the crypt fixture and one open fixture with the new physics;
- route step sizes;
- test count;
- the eval replay comparison.

## Rules
- Run in a worktree; first run `git reset --hard feat/v2`. Nothing else runs in parallel.
- Commit `v4 Part 28: tighter default physics (~4-tile jump)`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No push, do not edit PROGRESS.md, no real LLM, do not use port 8765, and do not touch
  `ldtk-src`.
