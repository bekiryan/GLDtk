# Part 23: Mission graph and space graph (Python, in a worktree)

Plan: [../PLAN.md](../PLAN.md) §23 (Dormans-style cyclic generation). This builds on Part 21
(terrain section types, especially `hub`, `labyrinth` and `arena`) and Part 22 (the item-state
soft-lock checker).

## Build
- **Mission graph** (new `mission/` package):
  - It is a small directed graph of goals:
    - start;
    - key → lock (door or locked Exit);
    - ability pickup → gate (a move that needs the ability);
    - optional treasure;
    - boss;
    - exit;
    - shortcut (a one-way door that opens a loop back).
  - **Graph-rewriting grammar** (`mission/grammar.py`): a handful of rules, applied by a seeded
    rewriter up to a budget. Each rule must keep the graph solvable, and that is checked after
    every rewrite.
    - `lock_key_cycle`: split an edge into key, then lock, with the key on a side branch.
    - `ability_gate`: an ability pickup, then a gate later, with an optional backtrack.
    - `shortcut_loop`: after a gate, a one-way door back to an earlier hub.
    - `treasure_branch`.
    - `boss_before_exit`.
  - **LLM.** The prompt says which rules and themes to use. Add an optional `mission` block to
    `LevelSpec` (rule names plus parameters such as the number of keys) so the LLM can request
    structure ("a key in the cave opens the castle gate"). The grammar builds the graph
    deterministically from it.
- **Space graph:**
  - Map mission nodes onto sections and rooms of the Part 21 terrain:
    - hub rooms hold keys and side branches;
    - labyrinth dead ends hold keys or treasure;
    - arenas hold the boss;
    - a gate is a door (an `Interactive` door with `linked_to` the key) or an ability-gated
      passage;
    - a shortcut is a one-way drop or a one-way door.
  - Order the sections so that each key comes before its lock along the path and the cycles
    loop back through hubs.
  - Then place the entities on terrain anchors (Part 21's `place`) with the right links:
    `opens`, `linked_to`, `requires_key`.
- **Verification:** run Part 22's item-state graph on the built level.
  - The mission is realised when every lock is reachable only after its key and the Exit only
    after the required items, with no soft-locks.
  - On failure, re-seed the space mapping (up to 3 times), then fall back to fewer rules and
    warn.
- **Fidelity:** a new check type, `mission`, checks that "the key in the cave opens the castle
  gate", or "the level loops back to the hub", is structurally true.
- **API:**
  - `GenerateResponse.mission` returns `{nodes, edges}` for the sidebar to draw later as a small
    graph.
  - The route overlay already shows the path.

## Tests
- The grammar is deterministic and solvable after every rule.
- A lock-and-key level: the key is placed before its door, and the item-state check passes.
  Removing the key makes Part 22 report the trap.
- A shortcut loop exists in the movement graph as a cycle back to the hub.
- An ability gate is only passable with the ability.
- Old specs without `mission` are byte-identical.
- Fixtures: a "crypt with a key" and a "hub with 2 keys".
- All of `pytest` passes.

## Rules
- **Parallel work:** Part 24 (complexity) and Part 25a (noise fill) run in other worktrees, and
  they touch `pipeline/level.py`, `spec/`, `llm/prompt.py` and `server.py`. Keep your hooks there
  small and additive.
- First run `git reset --hard feat/v2`.
- Commit `v4 Part 23: mission graph and space graph`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No push, do not edit PROGRESS.md, no real LLM, do not use port 8765, and do not touch
  `ldtk-src`.
