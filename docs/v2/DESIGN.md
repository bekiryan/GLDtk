# GLDtk v2 — Design

Goal: the user describes a whole level in text and gets a playable LDtk level containing
everything the prompt asked for (layout, sections, player start, enemies, coins, weapons,
interactives, style). The user can then select or describe any part of the level and
regenerate only that part.

All decisions below were settled in a design interview on 2026-10-13. Progress is tracked in
[PROGRESS.md](PROGRESS.md).

---

## 1. Product decisions

| # | Topic | Decision |
|---|---|---|
| Q1 | "Playable" | **Statically guaranteed beatable.** The physics oracle proves Start→Exit is reachable. Output is a valid LDtk file. No game runtime. Enemies, weapons and so on are entities with typed fields (data, not behaviour). |
| Q2 | Visuals | **Curated asset library by default**, plus an optional "✨ generate theme" action that uses AI image generation and saves its results into the library. |
| Q2.1 | Art style | **16×16 pixel art** (matches the current tile size). |
| Q2.2 | Image provider | **Pluggable provider interface**, default **Gemini Flash Image**. Pipeline: generate → downscale to 16 px → quantize palette → slice into tile layout → seam check → save to library. Free-tier quota must be verified early. |
| Q3 | Entity vocabulary | **Fixed catalog of kinds with typed fields**, plus free-text `description` and `sprite` on every entity. Unknown concepts map to the nearest kind, and the fidelity report notes the approximation. |
| Q3.1 | Player abilities | Abilities belong to the **game, not the level**. A per-project **Game Profile** holds physics, abilities and the player's look, and the generator only reads it. Levels may set a per-section `gravity_scale` (the only physics knob), starting items and weapons, and weapon pickups. A prompt asking for an ability the profile lacks is reported as ❌. |
| Q4 | Level structure | **One level** with prompt-driven size (about 30–300 × 15–100 tiles) and named **sections** (each with theme, difficulty, `gravity_scale` and features). The design leaves room for multi-level worlds later. |
| Q5 | LLM | **Pluggable, with structured (schema-enforced) output.** Gemini, Claude and Ollama are first-class, and the default is **Gemini Flash**. |
| Q6 | Impossible or unsupported requests | **Best effort plus a fidelity report.** The LLM extracts a requirement list from the prompt, and a deterministic checker marks each one ✅ / ⚠ approximated / ❌ impossible, with a reason, shown in the sidebar. The share of requirements satisfied is the evaluation metric. |
| Q7 | Regional editing | Four modes, built in this order: **rectangle selection → section dropdown → entity edits → text-described location**. A text location is highlighted for the user to confirm before it is applied. |
| Q7.1 | Seams | The region's boundary entry and exit points are **locked**, and the whole level is re-checked after every edit. A 1-tile margin around the region is re-autotiled. Entities outside the region are never touched, and broken links are reported as warnings. The **server keeps a version history** (`.ldtk` snapshots), with an "↶ Undo last GLDtk edit" button in the sidebar. |
| Q9 | Source of truth | **The `.ldtk` file only.** Edits read the current file: the grid goes through `symbolic/extractor.py` to get a graph, and entities come from the entity layer. Sections, the original prompt, requirements and the Game Profile reference live in LDtk **custom fields**. Hand edits are always respected. |

## 2. Process decisions

| # | Topic | Decision |
|---|---|---|
| Q8 | Order | 1 → 2 → 3 → 4 → **M1** → 7 → 9 → **M2** → 5 → 8 → **M3** → 10 → 6 → **M4**. No deadline. Tests are written alongside every part. |
| Q10 | Git | One branch `feat/v2` (in both the main repo and `ldtk-src/`), **one commit per part**. |
| Q11 | Testing | Unit tests use a **fake LLM** (recorded responses), so they are offline and repeatable. Real-provider runs are opt-in via `pytest -m e2e`, using `GEMINI_API_KEY` from `.env` (git-ignored). |
| Q12 | Review | The user reviews at **milestones M1–M4** only. |
| — | Execution | The orchestrating session writes a short spec for each part and dispatches subagents (a strong model for complex parts, a lighter one for simple parts). It then verifies the tests and diff itself before committing. |

## 3. Parts

| # | Part | Complexity | Summary |
|---|---|---|---|
| 1 | Level spec schema + structured-output providers | high | New `LevelSpec` (size, sections, platforms/graph, entities, requirements) replacing the bare `{nodes, edges}` output. Provider layer with JSON-schema structured output for Gemini, Claude and Ollama. Fake provider for tests. |
| 2 | Game Profile + ability-aware physics | high | `gldtk.game.json` (gravity, jump_v, run speed, tile size, abilities: double_jump, dash, wall_jump with parameters, player look). Oracle extended: double jump as chained arcs, dash as a horizontal bonus, wall jump, per-section `gravity_scale`. |
| 3 | Layout v2 | high | Honour spec size, sections and positional intent. Place prompt-specified entities. **Re-verify physics on the final tile grid** (extractor + oracle) and repair or retry if it fails. |
| 4 | Entity catalog + LDtk export | medium | Catalog: Player/Start, Exit, Enemy, Pickup, Weapon, Interactive, NPC, Checkpoint, Key, with typed `fieldDefs` and `fieldInstances` in LDtk. Level custom fields for sections, prompt and requirements. |
| 7 | Fidelity report | high | Requirement extraction (LLM) plus a deterministic checker against the final level. Returned in the API response. |
| 9 | Sidebar UI (Haxe) | medium | Game Profile panel, fidelity report view, section dropdown, rectangle-selection mode, undo button, provider selector. |
| 5 | Asset library | low | Real CC0 16×16 art (Kenney, 0x72), data-driven manifest (`assets/manifest.json`), 5–8 themes, 15–25 entity sprites. |
| 8 | Regional editing + undo | high | Read the `.ldtk`, run region regeneration with locked seams, entity edits, section edits, text-location resolution with confirmation, and keep the version history. |
| 10 | Evaluation harness | low | Prompt suite. Reports fidelity %, playability %, retries and patch counts per provider. |
| 6 | AI theme and sprite generation | medium | Image provider interface (Gemini default) plus the post-processing pipeline into the library. |

## 4. Entity catalog (initial)

Every entity has `description: String` and `sprite: String`, plus the kind-specific fields below:

- `Start` (player spawn): `facing`, `starting_items: Array<String>`, `starting_weapon`
- `Exit`: `requires_key: Bool`
- `Enemy`: `archetype` (walker|flyer|jumper|turret|boss), `health`, `damage`, `patrol_range`, `facing`, `attack` (melee|ranged|none)
- `Pickup`: `kind` (coin|gem|health|ammo|powerup), `value`
- `Weapon`: `kind` (sword|bow|crossbow|gun|staff|…), `damage`, `range`
- `Interactive`: `kind` (chest|door|lever|switch|sign), `linked_to` (entity ref), `contents`
- `NPC`: `name`, `dialogue`
- `Checkpoint`, `Key` (`opens` entity ref)

## 5. Open items

- **`ldtk-src/` in git:** it is its own repository (fork `github.com/bekiryan/ldtk`) and is currently untracked by the main repo. The planned `git submodule add` was blocked by a permission check. The user needs to decide: add it as a submodule themselves (`git submodule add https://github.com/bekiryan/ldtk.git ldtk-src`), or leave it as a separate repo. Haxe changes are committed in `ldtk-src` on its own `feat/v2` branch either way.
- Gemini image free-tier quota: verify at the start of Part 6 (or earlier in Part 1 for text).
- `plan.md` and `ui/index.html` are stale (from the old JS-injection approach) and should be removed or archived at some point.
