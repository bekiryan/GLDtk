# Part 8: Regional editing + undo

Design context: [../DESIGN.md](../DESIGN.md), Q7 (modes), Q7.1 (seams and history) and Q9 (the
`.ldtk` file is the source of truth). This part builds on the reader (`layout/ldtk_reader.py`),
the extractor (`symbolic/extractor.py`), grid verification and repair (`layout/verify.py`,
`layout/repair.py`), the fidelity checker and the sidebar's empty `#g-edit` slot.

It is split into **8a, the backend (Python, in a worktree)** and **8b, the sidebar
(Haxe, in `ldtk-src`)**. They run in parallel against the API contract below. If 8a has to
change the contract, it records the change in its report, and the orchestrator reconciles the two.

## Principles (from the design)
- **The file is the truth.** Every edit starts by reading the current `.ldtk`, so hand edits are
  respected. The grid comes from the IntGrid; entities come from the entity layer; sections, the
  prompt, requirements, the spec and the profile reference come from the `gldtk_*` level fields.
  v1 or hand-made files without GLDtk fields must still work, falling back to one section.
- **Locked seams.** For a region edit, the cells where the player's paths cross the region
  boundary are fixed. A path crosses the boundary where an extracted graph edge, or a walkable
  span, goes from outside to inside. The new content must connect to those points.
- **Nothing outside is touched.** Entities, tiles and IntGrid values outside the region stay
  exactly as they were. The only exception is the 1-tile margin around the region, which is
  re-autotiled so the art joins up. Entity refs that pointed into a region whose entities were
  replaced become warnings.
- **The whole level is re-verified** after every edit: extract the graph from the final grid,
  then check Start→Exit reachability and the entity checks, the same way `verify_grid` does.
  Deterministic repair comes first, then at most one LLM re-prompt. A level that fails
  verification is **never** written. The error explains why and the file is unchanged.
- **Version history.** Before any write, snapshot the current file to
  `<ldtk_dir>/.gldtk_history/<ldtk stem>/<UTC timestamp>.ldtk`. Keep the last 30 snapshots.
  Undo restores the newest snapshot and removes it, so repeated undo walks back through the
  history. A generate (`/generate`) also snapshots before it overwrites an existing file.
- **The fidelity report is updated.** The level's requirements are re-checked after an edit.
  Requirements whose entities lived in the edited region can turn missing; report that.

## API contract (`server.py` and `api/models.py`)
All paths are absolute `.ldtk` paths. Every edit response has the same shape as
`GenerateResponse`, plus `edit: {mode, region, snapshot}` and `warnings`. Rects are in tiles:
`{col, row, w, h}`, with row 0 at the top.

| Endpoint | Body | Does |
|---|---|---|
| `POST /edit/region` | `path, rect, prompt, provider?, model?, seed?` | Regenerates the content inside `rect` from `prompt`, with locked seams. |
| `POST /edit/section` | `path, section_id, prompt, …` | Same as `/edit/region`, with the rect taken from the section's band. Works for either orientation. |
| `POST /edit/entities` | `path, prompt, rect?, entity_iids?, …` | Entity-only edit: add, remove or modify entities (for example "make the bats stronger" or "add 3 coins here") inside the selection, or level-wide. The tile grid is unchanged. The LLM returns entity operations against the current entities, and placement uses the existing placement rules. |
| `POST /edit/locate` | `path, text, provider?, model?` | Resolves a text location ("the gap before the boss") to `{rect, explanation}`. **Nothing is written.** The sidebar highlights the rect and asks the user to confirm, then calls `/edit/region`. |
| `POST /undo` | `path` | Restores the newest snapshot. Returns `{success, restored_from, remaining}`. |
| `GET /history` | `path` | Returns `{snapshots: [{file, timestamp, label}]}`. The label is the edit mode and the start of the prompt (store the labels in an index JSON beside the snapshots). |

## 8a: backend

- `edit/` package: `context.py` (read the file, then sections, graph, entities and seams),
  `region.py`, `entities.py`, `locate.py`, `history.py`, and `stitch.py` (write the region,
  re-autotile the margin, export).
- **The region LLM call.** Use a dedicated structured-output schema, a `RegionSpec`: the nodes,
  edges and entities in **region-local** coordinates, plus requirements that the prompt adds.
  Its prompt includes:
  - the region size;
  - the seam points, which must be reachable to and from;
  - the section's theme and difficulty;
  - the Game Profile summary;
  - a compact ASCII view of the region and its surroundings;
  - the user's prompt.

  Reuse the provider layer and the validate → patch → repair pattern from `LLMController`. The
  system prompt must be a static constant (prompt caching).
- **Export.** Re-export through `to_ldtk_dict` with the read level's meta, keeping iids
  (`read_level` already round-trips them). Untouched entities keep their iids and field values,
  including fields the user edited by hand. Keep the extra Tiles layers and theme mapping from
  the M2 fixes.
- **Tests.** Tests are offline: FakeProvider fixtures for region, entity and locate responses.
  Cover at least:
  - nothing outside the rect changes, by comparing the IntGrid and entities outside the
    rect + margin;
  - seams stay connected and the result verifies;
  - an unverifiable result is not written;
  - undo restores the file byte-for-byte, and repeated undo walks back;
  - snapshots are capped at 30;
  - a hand-edited file (move an entity, paint tiles) is respected;
  - a v1 file without GLDtk fields works;
  - entity edits only touch the chosen entities.
- Commit: `v2 Part 8a: regional editing backend + version history`.

**You own:** a new `edit/` package, `server.py`, `api/models.py`, `tests/`, plus small additions
in `layout/` or `llm/` where you reuse code. Part 10 runs at the same time and owns `eval/` only.

## 8b: sidebar

Fill `#g-edit` (`GldtkSidebar.hx`) with an **Edit** panel:
1. **Mode selector:** Region, Section, Entities, Describe.
2. **Region:** a "Select area" button, then drag a rectangle on the level canvas. Find a clean
   hook into LDtk's canvas mouse events (`Editor`, `ui/Cursor`, the tool system, …). Draw the
   rectangle as an overlay, using LDtk's own overlay graphics if possible, and convert it to
   tile coordinates for the current level. The edit prompt field and an "Apply" button call
   `/edit/region`.
3. **Section:** a dropdown filled from the level's `gldtk_sections` field (parse the JSON), plus
   a prompt field, calling `/edit/section`. Selecting a section highlights its band.
4. **Entities:** uses LDtk's current entity selection if there is one (send its iids);
   otherwise the selected rect, otherwise the whole level. Calls `/edit/entities`.
5. **Describe:** a text field, then `/edit/locate`. Highlight the returned rect with its
   explanation, then show "Apply here" and "Cancel". Apply calls `/edit/region` with that rect.
6. **↶ Undo last GLDtk edit:** calls `/undo`, then reloads (using the same in-place swap as
   generate). It is disabled when `/history` is empty, and shows a count.
7. After every edit or undo, reload in place, update the fidelity view and append the result to
   the chat history. An error leaves the level untouched and shows the reason.

Do the app tests with Playwright, with a fake server stubbing the endpoints with canned
responses that follow the contract (8a's real server may not be ready). Reuse the tooling in the
p9 scratchpad folder. Commit in `ldtk-src`: `v2 Part 8b: sidebar editing panel + undo`.

## Shared rules
- Do not push.
- Do not edit `docs/v2/PROGRESS.md`.
- End each commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never call a real LLM.
- Stop every process you start. 8b uses port 8765 for app tests; 8a must not use it.
