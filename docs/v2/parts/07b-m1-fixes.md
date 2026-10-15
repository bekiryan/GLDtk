# Part 7b: M1 fixes (export, layout, art)

Context: the M1 demo and the LDtk app test in [../PROGRESS.md](../PROGRESS.md) (2026-10-13 notes,
"App test" bugs 2, 3 and 5). This part runs **in parallel with Part 7** in its own git worktree.
See "Parallel-work rules" below.

## Deliverables

### 1. Washed-out tiles (app bug 2)
The layer order is Entities, Collisions, Tiles, so the IntGrid overlay draws over the tile art.
Fix it so the tiles render at full colour: change the order to Entities, Tiles, Collisions
(`layerInstances` and `defs.layers`; in LDtk the first entry is the top layer), and consider a
low IntGrid `displayOpacity` as well. Check what `ldtk-src` does with these fields
(`ldtk-src/src/electron.renderer/data/def/LayerDef.hx` and so on).

### 2. Level marked modified on open (app bug 3)
LDtk marks `Level_0*` as modified right after it opens our export. Find out what it changes on
load: read the load path in `ldtk-src/src/electron.renderer` (`Project.fromJson`, `tidy()`,
level and layer `fromJson`, and anything that calls `invalidate…` or sets a modified flag). Then
make the export already contain whatever LDtk would fill in or fix. Typical culprits: missing or
stale cached fields (`__cWid`, `__cHei`, `autoLayerTiles`, `pxOffset`, `__neighbours`,
`externalRelPath`, `jsonVersion`, `appBuildId`, `iid`s, the level's `__smartColor` or
`__bgColor`, `useAutoIdentifier`, `toc`), or a field-instance value LDtk normalises. Document
the root cause in your final report. Add a regression test that pins the fixed fields.

### 3. Entity crowding (app bug 5, layout half)
Entities pile onto one 3-tile platform (coin, enemy and checkpoint together). In
`layout/spec_entities.py` (and `layout/spec_layout.py` if needed):
- Allow about one entity per 2 tiles of platform width. When an anchor is over capacity,
  **widen the platform** first, if the cells next to it are free and the change stays inside the
  section. Otherwise, move the overflow to the nearest non-hazard platform in the **same
  section**.
- Every relocation produces a warning in `entity_warnings`. Keep Start and Exit on their own
  nodes.
- Grid verification already runs after placement (`pipeline/level.py`), so widened platforms are
  re-checked. Make sure the verify and repair tests still pass.

### 4. Library sprites as LDtk entity icons
`aesthetic/library.find_sprite(kind, hints)` exists, but entities show only as coloured boxes.
- Build a sprite atlas PNG from the library sprites (16×16 grid) and copy it next to the export,
  under `<ldtk_dir>/gldtk_assets/`, the same way `copy_theme_assets` handles theme sheets. Add it
  to `defs.tilesets`.
- Each entity def gets a default tile (`tileRect`, `renderMode: "Tile"`, `tileRenderMode`) from
  its kind's default sprite. Each instance shows the sprite `find_sprite` picks from its
  `sprite`/`description` hints, through an `F_Tile` field or whatever LDtk actually supports for
  per-instance tiles (check `ldtk-src/docs/JSON_DOC.md` and `JSON_SCHEMA.json`; instance `__tile`
  is a computed field). Keep the colour as a fallback.
- Exports must still validate against `ldtk-src/docs/JSON_SCHEMA.json`.

### 5. Per-section tilesets
Today one theme sheet is used for the whole level. Each section has a `theme`, and the server
already computes `band["mapped_theme"]` in `ir.metadata["sections"]`. Render each section's
columns with its own theme sheet. Recommended: one Tiles layer per distinct theme used (each with
its own tileset def, filled only in that section's columns), since an LDtk layer has a single
tileset. Copy every sheet that is used. Autotiling must look right at section seams.
If `tilesets/` is no longer referenced by the default `AestheticData`, delete it (keep the v1
sample `level.ldtk` working, or remove both if only tests used them; say which in your report).

### 6. Tests
Extend `tests/test_ldtk_export.py`, `tests/test_spec_layout.py` and the others: layer order, the
regression for bug 3, crowding relocation and widening, the atlas and entity tiles, and
multi-section tilesets. Every export validates against the schema. All of `pytest` passes.

## Parallel-work rules
Part 7 runs at the same time and owns `spec/level_spec.py`, `llm/`, `pipeline/`, `fidelity/`,
`api/models.py` and the response-building block of `server.py`. **Do not edit those files.** You
own `layout/ldtk_adapter.py`, `layout/spec_entities.py`, `layout/spec_layout.py`, `aesthetic/`,
`spec/catalog.py`, `assets/`, and the aesthetic/serialise block of `server.py` (keep server edits
small and local).

## Done when
`pytest` passes, the new behaviour is covered, and the work is committed in the worktree as one
commit: `v2 M1 fixes: layer order, load-modified, crowding, entity sprites, per-section tilesets`.
Your final report must include the root cause of bug 3.
