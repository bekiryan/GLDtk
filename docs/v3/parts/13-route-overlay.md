# Part 13: Route overlay (Python + Haxe)

Context: [../PLAN.md](../PLAN.md). The verifier already proves a Start→Exit path; show it, so the
user can see why the level is beatable and spot physics problems.

## A. Python (in a worktree)
- **New module `layout/route.py`:** `level_route(grid, entities, profile, sections/gravity) -> Route`.
  - It computes the golden route on the **final grid**: the same spans and moves verification uses
    (extractor graph plus `layout/verify.py` / `edit/check.py` helpers). Reuse them; do not
    re-implement the physics.
  - Use the shortest path from the Start span to an Exit span, weighted by move cost: walk < fall
    < jump < ability.
  - **Each step** has `kind` (walk, jump, fall, double_jump, dash, wall_jump), `from`/`to` cells,
    and `points`: a polyline in level pixels (x right, y down, the LDtk convention). For an arc,
    sample the trajectory the verifier accepted (launch point, velocity, gravity) every about 4 px.
    A walk is a straight segment along the span.
  - It also returns `reachable_spans` (rects), so a UI can shade what is reachable, and
    `unreachable_required` (iids of required entities that are not reachable).
- **API:** add `route` to `GenerateResponse` and the edit responses (`api/models.py`), with
  `RouteModel` = `{steps: [{kind, points: [[x,y]...], ability?}], length_px, reachable_spans: [{col,row,w}]}`.
  - Add `GET /route?path=<.ldtk>` (`server.py`). It reads the file with the edit context loader
    (`edit/context.py load_context`), so hand edits are respected, and returns the route, or
    `{success:false, error}` when there is no path.
  - The route is computed after the final verification and costs no LLM call.
- **Tests:**
  - A route exists for the fixtures; it starts at the Start and ends at the Exit.
  - The polyline points stay inside the level and never pass through solid tiles: sample them.
  - The step kinds match the edge types.
  - `/route` on a hand-broken file returns a clean error.
  - The generate response includes `route`.
- **You own** `layout/route.py`, the route parts of `api/models.py`/`server.py`, and new tests.
  Parts 11 (physics; `symbolic/`, `layout/verify.py`) and 12 (`edit/`, `layout/densify.py`) run in
  parallel. Import their public functions; do not edit their files. Part 11 may refine the arcs;
  use the public sampling or `move_between` helpers, so you pick up its changes after the merge.
- Run `git reset --hard feat/v2` first. Commit once in the worktree:
  `v3 Part 13a: golden route API`.

## B. Haxe (`ldtk-src`, branch `feat/v2`)
- **Toggle:** a "Show route" toggle in the sidebar, stored in localStorage and on by default. It
  draws the route over the level canvas on the level's render root, like the `GldtkAreaTool`
  overlay (`h2d.Graphics`):
  - walks are a thin line;
  - jumps and falls are dotted arcs, with ability steps in a distinct colour (double jump, dash,
    wall jump);
  - small arrowheads show direction;
  - reachable spans get a faint tint, and unreachable required entities get a red ring.
- **When it updates:** after generate, edit and undo (from the response's `route`), and after any
  hand edit or project open (call `GET /route` with a small debounce, about 800 ms after the
  last change, by listening to LDtk's change events). Clear it on project close and level
  switch.
- **Staying out of the way:** the overlay must not take mouse input or mark the project modified,
  and it must follow camera pan and zoom (draw in level space).
- **Sidebar line:** "Route: N moves (J jumps, A ability moves)", or "No route: <reason>" in red.
- **Testing:** Playwright against a fake server that returns a canned route (the p9 and sw
  drivers in the session scratchpad are a good base). Take screenshots with the route on and
  off, and after a hand edit that breaks the path. Rebuild with `renderer.debug.hxml`; there must
  be no errors.
- **Commit** in `ldtk-src`: `v3 Part 13b: route overlay`.

## Shared rules
- End each commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Do not push, do not edit `docs/v2/PROGRESS.md`, and never call a real LLM.
- You may use port 8765 for the app tests (the Python-only agents do not use it). Stop every
  server and Electron process you start.
