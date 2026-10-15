# Part 5 — Asset library

Design context: [../DESIGN.md](../DESIGN.md) (Q2, Q2.1). Branch `feat/v2`. **Runs in parallel
with Parts 3 and 4** in its own git worktree; see "Parallel-work rules" below.

## Goal

Replace the three placeholder tilesets with **real CC0 16×16 pixel art**, organised as a
data-driven library: 5–8 themes and 15–25 entity sprites, described by `assets/manifest.json`.
Adding a theme then means adding files and a manifest entry, with no code changes. Part 6 (AI
theme generation) will write into this same library, so the format must be easy for code to
extend.

## Deliverables

### 1. Sourcing art (CC0 only)

- Candidates, all 16×16 or close to it:
  - Kenney: "Pixel Platformer" (check its tile size; it may be 18×18), "1-Bit Platformer Pack",
    "Tiny Dungeon", "Tiny Town".
  - 0x72: "16x16 DungeonTileset II".
  - Anything else that is clearly **CC0**.
- **Verify the licence** of every pack from its own licence file or page, and record it.
- Downloaded archives are untrusted data:
  - download each one into its own new empty directory under the scratchpad (not inside the repo);
  - only extract and read image and licence files;
  - never execute anything from them.
- If a pack's tiles are not 16 px, rescale with nearest-neighbour only when the scale is clean
  (e.g. 18→16 is not clean, so prefer a different pack). Record what you did.
- Only the processed outputs go into the repo, not whole packs.
- If network downloads are impossible, stop and report. Do not draw placeholder art.

### 2. Library layout — `assets/`

```
assets/
  manifest.json
  LICENSES.md                 # one section per pack: name, author, URL, licence, files used
  themes/<theme_id>.png       # normalised theme sheet, see below
  sprites/<sprite_id>.png     # 16×16 (or 16×32 / 32×32) entity sprites
  sources/                    # only if a small source sheet is needed by the build script
scripts/build_assets.py       # reproducible: source images → normalised sheets + manifest entries
```

- **Normalised theme sheet**: keep the existing 5×3 role layout described in
  `aesthetic/themes.py`, i.e. 14 tile roles at fixed cells (CORNER_TL … HAZARD_TILE). Each theme
  gets its own sheet at `base_x = 0`; the shared 256×256 multi-theme image goes away.
  - Optional extra rows below may hold variants (e.g. 2–3 alternative CENTER/FILL tiles for
    variety) and a background tile. Declare them in the manifest.
  - Choose source tiles for each role **by looking at the images** (you can view PNGs with the
    Read tool). Write the source rectangles into `scripts/build_assets.py` so the build is
    reproducible.
- **manifest.json** (also define a pydantic model for it):
  ```
  { "version": 1,
    "tile_size": 16,
    "themes": [ { "id", "display_name", "keywords": [...], "background_color",
                  "sheet": "themes/x.png", "variants": {role: [[col,row],...]},
                  "source": "<pack id>", "generated": false } ],
    "sprites": [ { "id", "kind": "Enemy|Pickup|Weapon|Interactive|NPC|Checkpoint|Key|Start|Exit",
                   "tags": ["skeleton","undead","walker"], "file": "sprites/x.png",
                   "size": [16,16], "source": "<pack id>", "generated": false } ],
    "sources": [ { "id", "name", "author", "url", "license": "CC0-1.0" } ] }
  ```
  `generated: true` is for Part 6 output.
- Themes to cover (at least 5): dungeon, forest, sky, ice/snow, desert/sand, lava/volcano,
  castle, cave. Make each keyword list rich, because `detect_theme` uses them.
- Sprites (15–25) cover:
  - the player;
  - enemies: walker, flyer, jumper, turret, boss;
  - pickups: coin, gem, health, ammo, powerup;
  - weapons: sword, bow, gun, staff;
  - interactives: chest, door, lever, switch, sign;
  - an NPC, a checkpoint and a key.

### 3. Code — `aesthetic/library.py` + `aesthetic/themes.py`

- `aesthetic/library.py`:
  - `load_library(root="assets") -> Library` (cached), with `themes`, `sprites` and `sources`.
  - `find_sprite(kind, hints: list[str]) -> Sprite | None`: tag-overlap scoring with
    `description`/`sprite` hint words, falling back to the kind's default sprite.
  - `add_theme(...)` and `add_sprite(...)`: write the PNG plus a manifest entry atomically. Part 6
    uses these; give them tests now.
- `aesthetic/themes.py` builds `THEMES` from the manifest instead of hard-coded entries. Keep the
  public API: `Theme`, `TileRole`, `TileAddress`, `THEMES`, `detect_theme(description,
  override)`, `theme.tile(role)`. `"default"` must still exist if callers rely on it.
  - `tileset_rel_path` must be the path that the `.ldtk` file will reference. The `.ldtk` is
    usually written somewhere else, so add `copy_theme_assets(theme, ldtk_dir) -> rel_path`. It
    copies the sheet next to the output (e.g. `<ldtk_dir>/gldtk_assets/themes/x.png`) and returns
    the relative path. Do **not** wire this into `server.py`; the orchestrator does that.
  - `tileset_uid`: stable per theme (derive it from the manifest order or an explicit `uid` field).
- Autotile variants: if the manifest has variants for a role, `autotile_level` chooses one
  deterministically (hash of col/row). This is optional.
- Delete the old `tilesets/` placeholders only if nothing references them any more; otherwise
  report the remaining reference.

### 4. Tests

- The manifest validates against the model; every referenced file exists and has the declared
  size; the size of every theme sheet is a multiple of 16.
- Every theme has all 14 roles inside its sheet bounds, and no role tile is fully transparent
  (an automatic check that the role mapping picked real tiles).
- `detect_theme`: keyword hits for each theme; the override wins; unknown text gives the default.
- `find_sprite`: an exact tag wins; the kind fallback works.
- `add_theme`/`add_sprite` on a temp copy of the library: the manifest is updated and reloads.
- `copy_theme_assets` returns a working relative path.
- The existing aesthetic tests and the server test keep passing.

### 5. Visual check

Render a contact sheet: each theme's 14 roles plus a small sample platform built with
`autotile_level`, and all sprites. Save it to `assets/preview.png` and look at it with Read.
Fix any role that is obviously wrong (e.g. a left cap that is actually a right cap). Include the
preview path in the report.

## Parallel-work rules

- You are in a **separate git worktree**. Only edit:
  - `assets/`, `scripts/build_assets.py`
  - `aesthetic/library.py`, `aesthetic/themes.py`, `aesthetic/autotile.py` (only for variants)
  - `aesthetic/__init__.py`
  - `tilesets/` (deletion, see above)
  - new tests named `tests/test_library*.py`, `tests/test_themes*.py`
- Do **not** edit `server.py`, `api/`, `layout/`, `llm/`, `symbolic/`, `spec/`, `game/` or
  `aesthetic/pipeline.py`. Parts 3 and 4 are changing those.
- Pillow is installed in the shared venv. No new runtime dependencies (Pillow is only used by
  `scripts/build_assets.py` and the tests, so add it as a **dev** dependency).
- Python imports: the shared venv's editable install points at the main checkout. Run commands
  from your worktree root as `python -m pytest`. Check once that
  `python -c "import aesthetic; print(aesthetic.__file__)"` prints your worktree path; if it does
  not, prefix commands with `PYTHONPATH=$PWD`.
- Commit in the worktree on its branch: one commit, message `v2 Part 5: CC0 asset library`,
  ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push or merge.

## Done when

- `python -m pytest` passes offline in the worktree. Network is needed only to fetch the packs.
- Report:
  - worktree path, branch and commit hash;
  - packs used, with their licences;
  - the list of themes and sprites;
  - repo size added (keep it under about 2 MB);
  - the preview image path;
  - deviations and open issues.
