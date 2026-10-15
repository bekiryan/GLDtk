# Part 9: Sidebar UI (Haxe)

Design context: [../DESIGN.md](../DESIGN.md) (Q3.1, Q5, Q6, Q7). The sidebar is
`ldtk-src/src/electron.renderer/ui/GldtkSidebar.hx`, with CSS in
`ldtk-src/app/assets/gldtk-sidebar.css`. `ldtk-src/` is its own git repository, on branch
`feat/v2`. Build instructions are in CLAUDE.md.

## Scope decision
The design lists a section dropdown, a rectangle-selection mode and an undo button for Part 9.
All three drive **regional editing, which has no backend until Part 8**. This part therefore
builds what works today, and leaves clean extension points (a `GldtkSidebar` section or tab for
"Edit") for Part 8 to add those controls next to their endpoints.

## Deliverables

### 1. Fidelity report view
After a generate, show `GenerateResponse.fidelity` (see `api/models.py`; its shape is in
`FidelityModel`):
- the score as a percentage, or "n/a" when it is null, plus counts per status;
- one row per requirement: a status icon (✅ satisfied, ⚠ approximated, ❌ impossible or missing,
  · unchecked), the text, and the reason (collapsed or as a tooltip). Auto-filled items get a
  small "auto" tag.

`warnings` go in a collapsible list. Keep the ASCII preview, but make it collapsible as well.

### 2. Provider selector
A provider dropdown (gemini, anthropic, ollama, openai, deepseek) plus an optional model text
field. Both are stored in localStorage and sent as `provider` and `model` in the payload. If the
server needs an endpoint to list providers and their default models, add a small
`GET /providers` in `server.py` (reading `_DEFAULT_PROVIDER_MODELS` in
`llm/providers/registry.py`), with a test.

### 3. Game Profile panel
A collapsible panel that loads the profile with `GET /profile?path=<project .ldtk>` and saves it
with `PUT /profile`. Check `server.py` for the exact request shape.
- Physics: gravity, jump_v, run_speed, walk_step_px.
- Abilities: double_jump, dash and wall_jump, each a toggle plus its parameters (see
  `game/profile.py`).
- Server validation errors (422) appear inline.
- It loads automatically when a project is opened (see §4).

### 4. App-test bugs from M1 (see PROGRESS.md)
- **Bug 1:** after a sidebar generate, the in-app reload leaves the canvas blank and the status
  stuck at "Saved — reloading…". The file itself is fine: it opens normally when loaded fresh.
  Find the cause in `reloadGeneratedProject` and in LDtk's `App.loadProject` and `Editor`
  lifecycle (for example: reloading the project that is already open, the callback never firing,
  the 3 s timeout triggering a window reload, or the camera fit running before the level is
  rendered). Fix it so the canvas shows the new level and the status reaches "ready". Prefer
  LDtk's own reload path if there is one (look for how LDtk reloads a project that changed on
  disk).
- **Bug 4:** the file label says "No project open" until Generate is clicked. Refresh it, and
  load the Game Profile, when a project is opened or closed. Use LDtk's events (`GlobalEvent`,
  `Editor.ME` creation, `ProjectSelected` or similar) instead of polling, if possible.
- Old chat history from localStorage can show stale errors. Add a small "clear" control.

### 5. Build and test
- Rebuild with `renderer.debug.hxml`. `main.hxml` only needs a rebuild if its sources change.
  Expected warnings are listed in CLAUDE.md; there must be no errors.
- **Test in the real app with Playwright.** A working driver is in
  `/private/tmp/claude-501/-Users-bekiryan-Projects-GLDtk/e8d14c20-b375-4528-8b61-66b273aadf76/scratchpad/drive.js`
  (see the PROGRESS.md "App testing" note). It launches Electron, waits for the main window,
  stubs the open dialog and loads a level. Copy and extend it. Do not commit it.
- To check a generate without using LLM quota, run the server with a fake provider. Write a
  small launcher script in your scratchpad that imports `server`, sets
  `server.provider_factory = lambda req: FakeProvider([...fixture...])`
  (`tests/fixtures/llm/completeness_fill.json` has fidelity checks), and runs uvicorn on 8765.
  Check:
  - opening a level updates the file label and the profile panel;
  - Generate → the canvas shows the new level, the status is "ready", and the fidelity report
    renders;
  - changing the provider changes the payload;
  - a profile edit is saved.

  Save screenshots in your scratchpad and mention them in your report.
- Python: if you add `GET /providers`, run `pytest` (it must pass).

## Commits
- `ldtk-src` (branch `feat/v2`): one commit, `v2 Part 9: sidebar UI (fidelity, provider, game profile, reload fixes)`.
- Main repo: only if Python changed, one commit with the same subject.
- Do not push and do not edit `docs/v2/PROGRESS.md`; the orchestrator does both.
- End each commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Do not commit `ldtk-src/.DS_Store` or `app/assets/js/test_validate.py`.
