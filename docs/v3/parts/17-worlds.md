# Part 17: Multi-level worlds (Python + Haxe)

Context: [../PLAN.md](../PLAN.md); the design is in `docs/v2/DESIGN.md` Q4 ("leaves room for
multi-level worlds later"). A prompt such as *"a 3-level campaign: a forest, then a cave, then a
castle with the final boss"* should produce one LDtk project with several linked levels.

## A. Python (in a worktree)
- **Detection.** `GenerateRequest.levels: Optional[int]` (1–8). When it is null, the LLM decides
  in the outline step: one level unless the prompt asks for several.
- **Outline call.** A new `WorldOutline` schema (structured output), as one LLM call. It
  contains:
  - the world title and the shared Game Profile (unchanged);
  - an ordered list of `{id, title, prompt, size_hint, difficulty, theme}`. Each `prompt` is a
    self-contained level description, written by the LLM from the user's prompt;
  - world-level requirements, such as "the final level has the boss" or "the key is found in
    level 2 and used in level 3". Cross-level keys are optional; if you support them, check them
    in the fidelity report.
- **Levels.**
  - Generate each level with the existing `build_level` pipeline (N calls, sequentially). Each
    level gets its own fidelity report.
  - A world fidelity report covers the world requirements plus a roll-up of the levels.
  - Report progress per level through Part 14's reporter ("level 2/3: layout").
- **Export.** One `.ldtk` with N levels (`to_ldtk_dict` gains a multi-level path), using a
  `LinearHorizontal` world layout and unique level identifiers (`Forest_1`, …).
  - Each level keeps its own `gldtk_*` fields; the project stores the outline in a project-level
    custom field, or in level 0's `gldtk_world` field.
  - **Links:** level k's Exit gets a `leads_to` entity-ref field pointing to level k+1's Start;
    LDtk supports refs across levels. The last Exit is marked `final: true`.
  - The file must validate against the schema.
- **Edits.**
  - Every edit request, plus `/route`, `/history`, `/undo` and `/difficulty`, gains an optional
    `level` (an iid or identifier). The default is the first level, so single-level files keep
    working.
  - `edit/context.py` and `layout/ldtk_reader.py` read the chosen level, and the edit writes back
    only that level. The other levels stay byte-identical; test this.
  - Snapshots stay per file.
- **Regenerate one level of a world:** `POST /generate` with `level=<iid>` rebuilds only that
  level, keeping the links.
- **Tests (offline, FakeProvider with an outline fixture plus per-level fixtures):**
  - a 3-level world exports, validates and is linked;
  - each level verifies;
  - an edit on level 2 leaves levels 1 and 3 byte-identical;
  - single-level behaviour is unchanged (all old tests pass);
  - `levels=1` skips the outline call.

## B. Haxe (`ldtk-src`)
- **Generate.** A "Levels" control (Auto, 1, 2, … 8), sent as `levels`.
- **After a world generate:** LDtk shows the world. The sidebar lists the levels with their
  fidelity scores; clicking one selects it in LDtk.
- **Which level the panels work on.** Edits, the route, the difficulty chart and undo apply to
  the **current level** in LDtk (`Editor.ME.curLevel`), and the sidebar shows which one ("Editing:
  Cave_2"). They update when the user switches levels (listen to `LevelSelected`).
- **Regenerating a level.** A "Regenerate this level" action (prompt prefilled from its
  `gldtk_prompt`).
- **Testing:** Playwright against a fake server, covering a world generate, selecting level 2, a
  section edit on it, the route on level 2, and undo. Take screenshots. Rebuild with no errors.
- **Commits:** worktree `v3 Part 17a: multi-level worlds`; `ldtk-src`
  `v3 Part 17b: world support in the sidebar`.

## Rules
As in Part 14: reset the worktree to `feat/v2` first; no push; do not edit PROGRESS.md; no real
LLM; port 8765 is yours; stop every process; end each commit with the Co-Authored-By line.
