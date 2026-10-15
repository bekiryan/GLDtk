# GLDtk v3: enhancements after M4

These features were requested by the user on 2026-10-15, after the M4 review. They are suggestions
1, 2, 3, 4, 5, 7, 8, 11 and 12 from the M4 list; suggestions 6 (variants), 9 (CI) and 10 (full eval)
were left out. Branch: `feat/v2`, in both repos, continuing PR #1. Each part has a spec in
`docs/v3/parts/`, and status lives in `docs/v2/PROGRESS.md`.

| # | Part | Suggestion | Repos | Wave |
|---|---|---|---|---|
| 11 | Physics accuracy | 1 | Python | 1 |
| 12 | Densify inside edits | 2 | Python | 1 |
| 13 | Route overlay | 4 | Python + Haxe | 1 |
| 14 | Progress and cancel | 3 | Python + Haxe | 2 |
| 15 | Difficulty curve | 7 | Python + Haxe | 3 |
| 16 | Play mode | 5 | Haxe (+ Python route data) | 4 |
| 17 | Multi-level worlds | 8 | Python + Haxe | 5 |
| 20 | Short hops, walk-first paths (user feedback) | — | Python | 4 (parallel) |
| 21 | Terrain from section structure types (user feedback) | — | Python (+ small Haxe) | after 17a and 20 |
| 18 | Packaged Mac app | 12 | Python + ldtk-src build | last |
| 19 | Armenian README | 11 | docs | last (orchestrator) |

**Waves:** parts that touch the Haxe sidebar (`ldtk-src`, a single working tree) run one at a time.
Python-only parts run alongside them, each in its own worktree.

**Defaults chosen by the orchestrator** (the user can override any of them):
- **Play mode:**
  - an in-app modal with an HTML canvas, drawing the IntGrid, entity sprites and the player as a box;
  - a platformer controller with the Game Profile's physics and abilities;
  - win at the Exit, respawn at the last checkpoint after a hazard or enemy hit.
- **Worlds:**
  - one LLM "outline" call, then one level generate per level;
  - LDtk levels placed with a linear world layout;
  - each level's Exit links to the next level's Start (an entity ref);
  - edits work on the level that is open in LDtk.
- **Packaged app:**
  - an unsigned `.dmg` for this Mac (no Apple notarization);
  - the backend is bundled with PyInstaller and started by the Electron main process.

**User feedback on 2026-10-15:**
1. Levels should be full designs, not just platforms: floors, walls, ceilings, corridors,
   labyrinths and arenas, in the style of Blasphemous 2, Neon Abyss 2, Sonic Origins and Spelunky 2.
   They should be mostly walking, fighting and short hops, with some parkour.
2. Paths should not be all jumps. Jumps should be short, from one platform to the next.

The user chose **section structure types**: the LLM picks the types, and deterministic
generators carve the terrain. This became Parts 20 and 21. The Mac app moved to the end so it
packages the final version.
