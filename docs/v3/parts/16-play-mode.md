# Part 16: Play mode (Haxe, with small Python additions)

Context: [../PLAN.md](../PLAN.md). Let the user press **▶ Play** in the sidebar and play the open
level right away with the Game Profile's physics, without leaving LDtk.

## Behaviour
- **Opening and closing.** ▶ Play saves unsaved changes first (as edits do) and opens a modal
  that covers most of the window. Esc closes it, and nothing is ever written to the project.
- **Drawing.** Draw on an HTML canvas: the IntGrid (solid, hazard), the level's tiles if
  practical (the tileset images are already loaded by LDtk; otherwise flat colours per theme),
  the entities (sprite icons if available, otherwise coloured boxes with the kind's colour), and
  the player as a box of the profile's hitbox size. The camera follows the player.
- **Controls:**
  - ←/→ or A/D to run;
  - Space, W or ↑ to jump;
  - Shift to dash;
  - double jump and wall jump when the profile enables them;
  - R to restart;
  - Esc to close.
- **Physics** use exactly the Game Profile values (gravity, jump_v, run_speed, walk_step,
  abilities and their parameters) and per-section `gravity_scale`.
  - Fetch the profile (`GET /profile`) and the sections (from the `gldtk_sections` level field).
  - Use a fixed 60 Hz timestep and axis-separated AABB collision against the IntGrid.
  - It must feel like what the verifier assumes, so a route the verifier accepts can be played.
    Document any intentional differences, such as coyote time; prefer none.
- **Rules of the game:**
  - Pickups are collected and counted in a small HUD (coins, gems, health).
  - Hazard tiles and enemies cost a life and respawn the player at the last checkpoint (or the
    Start). Enemies patrol their `patrol_range`, and flyers bob.
  - A Key opens the Exit when `requires_key` is set.
  - Reaching the Exit shows "Level complete" with the time and pickups, and offers Replay or
    Close.
- **Route ghost (optional):** a toggle to show the golden route from Part 13 as a faint line
  while playing.

## Python (only if needed)
If the client needs data it cannot read easily from the loaded LDtk project, add a tiny
`GET /play-data?path=` that returns grid, entities, sections, profile and route. Otherwise keep it
all client-side.

## Implementation notes
- Write it in Haxe under `src/electron.renderer/ui/gldtk/` (for example `GldtkPlay.hx`), using a
  plain `js.html.CanvasElement` with a 2D context and `requestAnimationFrame`. That is simpler
  than h2d and independent of the editor's render loop.
- Pause the editor's input while the modal is open, so keystrokes do not edit the level.

## Testing
- Playwright, with level A from the session scratchpad.
- Open Play, and screenshot the first frame.
- Simulate input: hold → for 1 s, then jump. Assert that the player's position changed, by
  exposing a debug hook (`window.__gldtkPlay.state()`) in debug builds only.
- Reach the Exit in a tiny test level that you build for this, and check that the win screen
  shows.
- Close the modal and check that the project is not marked modified.

Rebuild with no errors. Commit in `ldtk-src` as `v3 Part 16: play mode`, plus a worktree commit
`v3 Part 16a: play data` if you changed Python.

## Rules
As in Part 14: reset the worktree to `feat/v2` first; no push; do not edit PROGRESS.md; no real
LLM; port 8765 is yours; stop every process; end each commit with the Co-Authored-By line.
