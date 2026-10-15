# Part 4 — Entity catalog + full LDtk export

Design context: [../DESIGN.md](../DESIGN.md) (Q3, Q9, §4 entity catalog). Builds on Part 1
(`SpecEntity` fields in `spec/level_spec.py`). Branch `feat/v2`. **Runs in parallel with Part 3**
in its own git worktree; see "Parallel-work rules" below.

## Goal

Every entity in a generated level appears in LDtk as its proper kind with **typed, editable
fields**. The level also carries GLDtk's own state in **LDtk custom fields**: sections, the
original prompt, the requirements, and the Game Profile reference. The `.ldtk` file is then the
single source of truth (Q9), which Part 8 (regional editing) depends on.

## Input contract (produced by Part 3, which is being built at the same time)

Part 3 appends `IREntity` objects to `ir.entities` with:
- `entity_type`: the spec kind — `"Start"|"Exit"|"Enemy"|"Pickup"|"Weapon"|"Interactive"|"NPC"|"Checkpoint"|"Key"`.
  Legacy aesthetic entities (`"Coin"`, `"Key"`, `"Enemy"`, `"Checkpoint"`) can still occur.
- `metadata`:
  - `spec_id`, `section`, `anchor`, `description`, `sprite`
  - every non-null kind field, using the `SpecEntity` field names: `facing`, `starting_items`,
    `starting_weapon`, `requires_key`, `archetype`, `health`, `damage`, `patrol_range`, `attack`,
    `pickup_kind`, `value`, `weapon_kind`, `range`, `interactive_kind`, `linked_to`, `contents`,
    `name`, `dialogue`, `opens`
  - `linked_to` and `opens` hold the **spec id** of another entity.

Part 3 also sets `ir.metadata["sections"]` to a list of dicts with `id, name, col_start, col_end,
gravity_scale, theme, difficulty`. Build test `IRLevel`s by hand to this contract. Do not wait for
Part 3's code.

## Deliverables

### 1. `spec/catalog.py`: the catalog as data

One table that is the single source of truth for each kind:
- the LDtk identifier;
- a colour;
- size in px (Start uses the player hitbox, 16×32 by default; the rest are 16×16, and a `boss`
  Enemy is 32×32);
- a stable def uid (keep 10 and 11 for Start and Exit; leave room for more kinds);
- `fieldDefs`. Each field has a name, an LDtk type and a default:
  - `F_String`, `F_Int`, `F_Bool`, `F_Text` (for dialogue);
  - `F_Enum(...)` for closed sets (`facing`, `archetype`, `attack`, `pickup_kind`,
    `interactive_kind`), backed by enum defs in `defs.enums`;
  - `Array<String>` for `starting_items`;
  - `F_EntityRef` for `linked_to` and `opens`.

  Every kind also has `description: F_Text` and `sprite: F_String`.
- Map the legacy names: `Coin` → `Pickup` (`pickup_kind=coin`); `Key`, `Enemy` and `Checkpoint`
  map to themselves.
- An unknown `entity_type` maps to the closest kind. Default `Interactive`, and keep the original
  name in `description`; do not silently map to Enemy as the adapter does today.

### 2. `layout/ldtk_adapter.py`: export

- `defs.entities` contains one def per catalog kind, with full `fieldDefs`. `defs.enums` contains
  the enum defs. `defs.levelFields` contains:
  - `gldtk_prompt` (`F_Text`)
  - `gldtk_sections` (`F_Text`: a JSON string of the sections list)
  - `gldtk_requirements` (`Array<String>`)
  - `gldtk_game_profile` (`F_String`: a path relative to the `.ldtk` file)
  - `gldtk_version` (`F_String`: `"2"`)
  - `gldtk_spec` (`F_Text`: the full `LevelSpec` JSON, so Part 8 can rebuild context)
- Entity instances get `fieldInstances` for **every** field def of their kind: the value when
  present, otherwise the default. Every `__value`, `__type`, `realEditorValues`, `defUid` and
  `__tile` must follow the formats in `ldtk-src/docs/JSON_SCHEMA.json` and `JSON_DOC.md`. Pay
  particular attention to how `F_EntityRef` stores `entityIid`, `layerIid`, `levelIid` and
  `worldIid`, how enums are stored, and how arrays are stored.
- Entity refs: assign each entity's `iid` first, then resolve `linked_to` and `opens` spec ids to
  those iids. If a ref is missing, write a null value and add a warning to the returned warnings
  list. Also fill `__smartColor` and the `__tags` the schema needs.
- New signature (backwards compatible):
  `to_ldtk_dict(ir, aesthetic=None, cfg=None, level_meta: Optional[LevelMeta] = None)`.
  `LevelMeta` holds `prompt`, `sections`, `requirements`, `game_profile_relpath` and `spec_json`.
  Add `to_ldtk_dict_with_warnings(...) -> (dict, warnings)`, or a similar way to get warnings out.
  Existing callers and `example.py` keep working.
- Keep every computed or cached field that CLAUDE.md lists as required (missing ones cause silent
  blank rendering in LDtk).

### 3. Reading back: `layout/ldtk_reader.py`

Part 8 needs the inverse, so build it now while the format is fresh in your head.
- `read_level(ldtk_path_or_dict) -> ReadLevel` returns:
  - the IntGrid as `grid[row][col]`;
  - the entities, as `IREntity` objects with kind and metadata rebuilt from `fieldInstances`. Map
    entity refs back to the spec ids recorded in the fields; also store each entity's `iid`.
  - the level fields, parsed: `sections`, `requirements`, `prompt`, `spec` (as a `LevelSpec`, or
    None if absent) and `game_profile_relpath`.
- It must accept files that the user has hand-edited in LDtk: extra entities, missing GLDtk
  fields, or a v1 file with no GLDtk fields. In those cases return what is present and use None
  or empty for the rest.

### 4. Tests

- Schema conformance:
  - Add `jsonschema` as a **dev** dependency in pyproject and install it into the venv.
  - Validate a full export, with every kind, every field type, refs and level fields, against
    `ldtk-src/docs/JSON_SCHEMA.json`. If the schema file uses a draft or `$ref` style that
    `jsonschema` cannot handle, adapt it in the test and explain how.
- Field types: each kind's instance has every field def; enum values are members of their enum
  def; `F_EntityRef` points at a real iid in the same level.
- Round trip: IR + `LevelMeta` → export → `read_level` → same kinds, fields, refs, grid and level
  fields.
- Hand-edit tolerance: delete some GLDtk fields from an export, add an unknown entity def and
  instance → `read_level` still works.
- Legacy: a v1-style IR (Start/Exit plus Coin/Enemy from the aesthetic pass) still exports and
  validates.
- All existing tests keep passing.

## Parallel-work rules

- You are working in a **separate git worktree**. Only edit:
  - `spec/catalog.py`
  - `layout/ldtk_adapter.py`
  - `layout/ldtk_reader.py`
  - `layout/__init__.py` (exports)
  - `pyproject.toml` (the dev dependency only)
  - new test files under `tests/` named `test_catalog*.py`, `test_ldtk_*.py`
- Do **not** edit `server.py`, `api/models.py`, `aesthetic/`, `symbolic/`, `llm/` or
  `layout/sugiyama.py`. Part 3 is changing those, and the orchestrator does the server wiring
  after merging.
- Python imports: the shared venv's editable install points at the main checkout. Run everything
  from your worktree root as `python -m pytest`. Check once that
  `python -c "import layout; print(layout.__file__)"` prints your worktree path; if it does not,
  prefix commands with `PYTHONPATH=$PWD`.
- Commit your work in the worktree on its branch: one commit, message
  `v2 Part 4: entity catalog and full LDtk export`, ending with the line
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push and do not merge. The
  orchestrator merges into `feat/v2`.

## Done when

- `python -m pytest` passes offline in the worktree.
- Report:
  - the worktree path and branch name, and the commit hash;
  - files changed;
  - how `F_EntityRef`, enums and level fields are encoded;
  - whether schema validation needed workarounds;
  - deviations and open issues, including anything Part 3's output must provide that this spec
    did not foresee.
