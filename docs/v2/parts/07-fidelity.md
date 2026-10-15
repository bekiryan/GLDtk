# Part 7: Fidelity report + completeness loop

Design context: [../DESIGN.md](../DESIGN.md) (Q3, Q6). This builds on Part 1 (`Requirement` in
`spec/level_spec.py`), Part 2 (`GameProfile`) and Part 3 (`pipeline/level.py`). It runs **in
parallel with "M1 fixes"** in its own git worktree. See "Parallel-work rules" below.

## Problem (from the M1 demo)

A 3-section prompt produced 11 requirements, but the LLM emitted only 5 entities: no coins,
skeletons, bat, chest or boss. Nothing noticed. Requirements are free text, so nothing can check
them. The LLM also built only 7 small (3-tile) platforms on one row band of a 30-row level.

## Goal

1. Every requirement is **checked deterministically** and gets a status:
   - `satisfied` ✅
   - `approximated` ⚠: the concept was mapped to the nearest kind (e.g. "ghost" → Enemy flyer),
     or the requirement was only partly met;
   - `impossible` ❌: an ability that is not in the Game Profile, or a size outside the limits;
   - `missing` ❌: a fixable requirement that is not met;
   - `unchecked`: style, other, or anything without a structured check. Each has a reason string.
2. **Before layout**, missing items are fixed. Deterministic fill comes first; an LLM re-prompt
   is the last resort. The Gemini free tier is about 20 requests/day, so every LLM call counts.
3. **After layout**, the report is recomputed on the final level (entities can be dropped or
   moved during placement) and returned in the API response.

## Deliverables

### 1. Structured checks on `Requirement` (`spec/level_spec.py`)
Add a nullable, flat `check` object to `Requirement`, so the JSON schema stays portable and
strict-mode compatible. Suggested fields (refine as needed, but keep it flat):
- `type`: `"entity_count" | "section" | "ability" | "size" | "gravity" | "none"`
- `entity_kind` (EntityKind | null), `subkind` (string | null: the archetype, pickup_kind,
  interactive_kind or weapon_kind value), `min_count` (int | null)
- `section` (section id | null: where the requirement applies; null = anywhere)
- `ability` (AbilityName or free string | null), `min_width_tiles` / `min_height_tiles`, and
  `gravity_scale` (number | null)

`check` defaults to `None`, so **existing fixtures still parse**. `LevelSpec.json_schema()` must
still produce the strict, inlined, all-required shape. Add or update tests for that.
"The player starts with a sword" can be an `entity_count` on `Start`, or a dedicated
`type: "start_item"`. Your choice: pick whatever keeps the checker simple.

### 2. `fidelity/` package: the checker
- `check_spec(spec, profile) -> FidelityReport`: runs before layout, on the spec only.
- `check_level(spec, ir, profile, verified: bool) -> FidelityReport`: runs on the final level and
  counts the entities actually placed in `ir.entities` (metadata `spec_id`, `section`, and the
  kind fields; see `layout/spec_entities.py`).
- `FidelityReport`: `items: [FidelityItem(id, text, category, status, reason, auto_filled: bool)]`
  plus `score` = the share of checkable requirements (all except `unchecked`) that are
  `satisfied` or `approximated`, with approximated counting as 0.5. Document the formula. Part 10
  uses it as the evaluation metric.
- Approximation detection: an entity whose `description` names a concept the kind does not cover.
  Keep this simple: report `approximated` when the requirement's `subkind` is not a valid enum
  value for that kind and the closest kind was used.
- `ability` requirements are `impossible` when the profile lacks the ability, with a reason
  naming the profile.

### 3. Completeness loop (`pipeline/level.py`, or a new module it calls)
After `controller.generate_spec` and before layout:
1. `check_spec`. For each `missing` entity_count, **synthesize the missing entities
   deterministically**: the right kind and subkind, in the required section (or spread over all
   sections), spread across that section's non-hazard nodes, with fresh unique ids and
   `description` taken from the requirement text. Mark them `auto_filled`.
2. Anything still missing that cannot be filled deterministically (e.g. a missing section) →
   **one** LLM re-prompt with a "COMPLETENESS CHECK FAILED" message listing the missing
   requirements (add a builder to `llm/prompt.py` next to `build_grid_repair_messages`, and a
   `controller.complete_spec(...)` beside `repair_spec`). The result goes back through
   `check_spec` and fill. Do not loop more than once. If the problem persists, the item stays
   `missing` in the report.
3. Count the re-prompts and fills in `stats` (`completeness_reprompts`, `entities_filled`).

`BuiltLevel` gets a `fidelity: FidelityReport` from `check_level` on the final layout.

### 4. Prompt (`llm/prompt.py`)
- Explain `check` and show it in the example, for at least an entity_count, a section and an
  ability requirement.
- Strengthen completeness: every entity_count requirement must be matched by that many entities.
- **Layout guidance** (M1 bug 5, prompt half): platforms of varied width (3–10 tiles), use the
  vertical space of the level rather than one row band, scale the platform count with level size
  and section count, and spread entities over several platforms instead of stacking them on one.
  Keep `SYSTEM_PROMPT` static (prompt caching).

### 5. API (`api/models.py`, `server.py`)
`GenerateResponse.fidelity` holds the items plus the score (a pydantic model mirroring the
report). Keep `requirements: List[str]` for compatibility. Only touch the response-building part
of `server.py`.

### 6. Tests (offline, FakeProvider + fixtures)
- Unit tests for each check type and status, and for the score formula.
- A completeness fixture: a spec whose requirements ask for 3 coins and a boss but has no
  entities → the fill adds them, the report is all satisfied, and there are no extra LLM calls.
- A fixture that needs the re-prompt (a missing section) → exactly one extra provider call.
- A server test: `fidelity` is present in the response.
- All existing tests must still pass (`pytest`).

## Parallel-work rules
"M1 fixes" runs at the same time and owns `layout/ldtk_adapter.py`, `layout/spec_entities.py`,
`layout/spec_layout.py`, `aesthetic/`, `spec/catalog.py` and the aesthetic/serialise block of
`server.py`. **Do not edit those files.** Persisting the report into the `.ldtk` is out of scope
for this part. You own `spec/level_spec.py`, `llm/`, `pipeline/`, `fidelity/`, `api/models.py`
and the response block of `server.py`.

## Done when
`pytest` passes, the new behaviour is covered, and the diff is committed in the worktree as one
commit: `v2 Part 7: fidelity report and completeness loop`.
