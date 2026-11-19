# GLDtk Offline Replay Ablation Report

- Generated at: `2026-11-19T19:12:04Z`
- Seed: `0`

## Overview

| ID | Ablation | Prompts | Primary Metric | Baseline | Ablated | Delta |
|---|---|---:|---|---|---|---|
| A1 | `no-SymbolicPatcher` | 12 | Playable share | 11/12 (91.7%) | 5/12 (41.7%) | -50.0 pp |
| A2 | `no-CompletenessFill` | 14 | Playable / expect_failed | 14/14, expect_fail=0 | 14/14, expect_fail=2 | expect_fail +2 |
| A3 | `no-GridAndSoftlockRepair` | 22 | Playable share | 21/22 (95.5%) | 20/22 (90.9%) | -4.5 pp |
| A4 | `no-PatternStamping` | 6 | Stamped sections / prompts | 2 (2/6) | 0 (0/6) | -2 |
| A5 | `no-ClosedLoopComplexity` | 3 | Mean band error / strict in-range | err=0.20 (66.7%) | err=0.40 (0.0%) | +0.20 |

## A1: `no-SymbolicPatcher`

Bypass LLMController._patch_spec and SymbolicPatcher.patch (no deterministic spec/graph first-aid).

| Metric | Baseline | Ablated | Delta (Ablated - Baseline) |
|---|---:|---:|---:|
| Verified | 11 | 5 | -6 |
| Playable share | 91.7% | 41.7% | -50.0 pp |
| Fidelity mean | 0.931 | 0.938 | +0.007 |
| Patches mean | 2.08 | 0.00 | -2.08 |
| Failures | replay_missing x1 | MaxRetriesExceeded x5, replay_missing x2 | - |

#### By recording suite

| Recording | N | Baseline verified | Ablated verified | Baseline fidelity | Ablated fidelity |
|---|---:|---:|---:|---:|---:|
| `qwen-2026-10-16` | 6 | 5/6 (83.3%) | 0/6 (0.0%) | 0.930 | - |
| `gemini-2026-10-19c` | 6 | 6/6 (100.0%) | 5/6 (83.3%) | 0.931 | 0.938 |

#### Per prompt

| Recording | Prompt | Baseline | Ablated | Notes |
|---|---|---|---|---|
| `qwen-2026-10-16` | `structure-crypt` | ok (fid=1.000, patches=4) | FAIL (MaxRetriesExceeded) | expect_fail: ['no Interactive entity'] -> [] |
| `qwen-2026-10-16` | `structure-labyrinth` | FAIL (replay_missing) | FAIL (MaxRetriesExceeded) | - |
| `qwen-2026-10-16` | `structure-tower` | ok (fid=1.000, patches=4) | FAIL (MaxRetriesExceeded) | - |
| `qwen-2026-10-16` | `structure-hub-key` | ok (fid=0.750, patches=3) | FAIL (MaxRetriesExceeded) | expect_fail: ['no hub section'] -> [] |
| `qwen-2026-10-16` | `structure-mixed-outdoor` | ok (fid=1.000, patches=4) | FAIL (replay_missing) | grid_repairs: 1 -> 0 |
| `qwen-2026-10-16` | `v4-mission-metroidvania` | ok (fid=0.900, patches=5) | FAIL (MaxRetriesExceeded) | - |
| `gemini-2026-10-19c` | `structure-crypt` | ok (fid=0.938, patches=0) | ok (fid=0.938, patches=0) | - |
| `gemini-2026-10-19c` | `structure-labyrinth` | ok (fid=0.750, patches=0) | ok (fid=0.750, patches=0) | - |
| `gemini-2026-10-19c` | `structure-tower` | ok (fid=1.000, patches=0) | ok (fid=1.000, patches=0) | - |
| `gemini-2026-10-19c` | `structure-hub-key` | ok (fid=1.000, patches=0) | ok (fid=1.000, patches=0) | - |
| `gemini-2026-10-19c` | `structure-mixed-outdoor` | ok (fid=1.000, patches=0) | ok (fid=1.000, patches=0) | - |
| `gemini-2026-10-19c` | `v4-mission-metroidvania` | ok (fid=0.900, patches=5) | FAIL (replay_missing) | - |

## A2: `no-CompletenessFill`

Bypass pipeline.level.ensure_complete and fill_missing (no prompt-derived requirements, section retyping, or entity filling).

| Metric | Baseline | Ablated | Delta (Ablated - Baseline) |
|---|---:|---:|---:|
| Verified | 14 | 14 | +0 |
| Playable share | 100.0% | 100.0% | +0.0 pp |
| Fidelity mean | 0.959 | 0.981 | +0.022 |
| Requirements satisfied | 56 | 53 | -3 |
| Requirements missing | 1 | 0 | -1 |
| Expectation failures | 0 | 2 | +2 |
| Entities filled mean | 0.00 | 0.00 | +0.00 |
| Failures | none | none | - |

#### By recording suite

| Recording | N | Baseline verified | Ablated verified | Baseline fidelity | Ablated fidelity |
|---|---:|---:|---:|---:|---:|
| `gemini-2026-10-19c` | 6 | 6/6 (100.0%) | 6/6 (100.0%) | 0.931 | 0.950 |
| `gemini-2026-10-15` | 8 | 8/8 (100.0%) | 8/8 (100.0%) | 0.979 | 1.000 |

#### Per prompt

| Recording | Prompt | Baseline | Ablated | Notes |
|---|---|---|---|---|
| `gemini-2026-10-19c` | `structure-crypt` | ok (fid=0.938, patches=0) | ok (fid=1.000, patches=0) | expect_fail: [] -> ['no parkour section'] |
| `gemini-2026-10-19c` | `structure-labyrinth` | ok (fid=0.750, patches=0) | ok (fid=0.750, patches=0) | - |
| `gemini-2026-10-19c` | `structure-tower` | ok (fid=1.000, patches=0) | ok (fid=1.000, patches=0) | - |
| `gemini-2026-10-19c` | `structure-hub-key` | ok (fid=1.000, patches=0) | ok (fid=1.000, patches=0) | - |
| `gemini-2026-10-19c` | `structure-mixed-outdoor` | ok (fid=1.000, patches=0) | ok (fid=1.000, patches=0) | - |
| `gemini-2026-10-19c` | `v4-mission-metroidvania` | ok (fid=0.900, patches=5) | ok (fid=-, patches=5) | expect_fail: [] -> ['no Key entity', 'no hub section'] |
| `gemini-2026-10-15` | `single-forest` | ok (fid=1.000, patches=2) | ok (fid=1.000, patches=2) | - |
| `gemini-2026-10-15` | `single-cave-hazards` | ok (fid=1.000, patches=2) | ok (fid=1.000, patches=2) | - |
| `gemini-2026-10-15` | `two-sections` | ok (fid=1.000, patches=2) | ok (fid=1.000, patches=2) | - |
| `gemini-2026-10-15` | `three-sections` | ok (fid=1.000, patches=2) | ok (fid=1.000, patches=2) | - |
| `gemini-2026-10-15` | `vertical-tower` | ok (fid=1.000, patches=1) | ok (fid=1.000, patches=1) | - |
| `gemini-2026-10-15` | `vertical-shaft-multi` | ok (fid=0.833, patches=1) | ok (fid=1.000, patches=1) | - |
| `gemini-2026-10-15` | `enemy-walker` | ok (fid=1.000, patches=3) | ok (fid=1.000, patches=3) | - |
| `gemini-2026-10-15` | `enemy-flyer-turret` | ok (fid=1.000, patches=2) | ok (fid=1.000, patches=2) | - |

## A3: `no-GridAndSoftlockRepair`

Bypass layout.repair.repair_grid (stepping-stone/ceiling/softlock repair) and set TERRAIN_ATTEMPTS = 1.

| Metric | Baseline | Ablated | Delta (Ablated - Baseline) |
|---|---:|---:|---:|
| Verified | 21 | 20 | -1 |
| Playable share | 95.5% | 90.9% | -4.5 pp |
| Fidelity mean | 0.945 | 0.932 | -0.013 |
| Grid repairs mean | 0.05 | 0.00 | -0.05 |
| Terrain retries mean | 0.18 | 0.00 | -0.18 |
| Failures | replay_missing x1 | replay_missing x2 | - |

#### By recording suite

| Recording | N | Baseline verified | Ablated verified | Baseline fidelity | Ablated fidelity |
|---|---:|---:|---:|---:|---:|
| `gemini-2026-10-15` | 8 | 8/8 (100.0%) | 8/8 (100.0%) | 0.979 | 0.979 |
| `gemini-2026-10-19c` | 6 | 6/6 (100.0%) | 6/6 (100.0%) | 0.931 | 0.915 |
| `qwen-2026-10-16` | 8 | 7/8 (87.5%) | 6/8 (75.0%) | 0.918 | 0.888 |

#### Per prompt

| Recording | Prompt | Baseline | Ablated | Notes |
|---|---|---|---|---|
| `gemini-2026-10-15` | `single-forest` | ok (fid=1.000, patches=2) | ok (fid=1.000, patches=2) | - |
| `gemini-2026-10-15` | `single-cave-hazards` | ok (fid=1.000, patches=2) | ok (fid=1.000, patches=2) | - |
| `gemini-2026-10-15` | `two-sections` | ok (fid=1.000, patches=2) | ok (fid=1.000, patches=2) | - |
| `gemini-2026-10-15` | `three-sections` | ok (fid=1.000, patches=2) | ok (fid=1.000, patches=2) | - |
| `gemini-2026-10-15` | `vertical-tower` | ok (fid=1.000, patches=1) | ok (fid=1.000, patches=1) | - |
| `gemini-2026-10-15` | `vertical-shaft-multi` | ok (fid=0.833, patches=1) | ok (fid=0.833, patches=1) | - |
| `gemini-2026-10-15` | `enemy-walker` | ok (fid=1.000, patches=3) | ok (fid=1.000, patches=3) | - |
| `gemini-2026-10-15` | `enemy-flyer-turret` | ok (fid=1.000, patches=2) | ok (fid=1.000, patches=2) | - |
| `gemini-2026-10-19c` | `structure-crypt` | ok (fid=0.938, patches=0) | ok (fid=0.938, patches=0) | - |
| `gemini-2026-10-19c` | `structure-labyrinth` | ok (fid=0.750, patches=0) | ok (fid=0.750, patches=0) | - |
| `gemini-2026-10-19c` | `structure-tower` | ok (fid=1.000, patches=0) | ok (fid=1.000, patches=0) | - |
| `gemini-2026-10-19c` | `structure-hub-key` | ok (fid=1.000, patches=0) | ok (fid=1.000, patches=0) | - |
| `gemini-2026-10-19c` | `structure-mixed-outdoor` | ok (fid=1.000, patches=0) | ok (fid=1.000, patches=0) | - |
| `gemini-2026-10-19c` | `v4-mission-metroidvania` | ok (fid=0.900, patches=5) | ok (fid=0.800, patches=5) | expect_fail: [] -> ['no Key entity'] |
| `qwen-2026-10-16` | `structure-crypt` | ok (fid=1.000, patches=4) | ok (fid=1.000, patches=4) | - |
| `qwen-2026-10-16` | `structure-labyrinth` | FAIL (replay_missing) | FAIL (replay_missing) | - |
| `qwen-2026-10-16` | `structure-tower` | ok (fid=1.000, patches=4) | ok (fid=1.000, patches=4) | - |
| `qwen-2026-10-16` | `structure-hub-key` | ok (fid=0.750, patches=3) | ok (fid=0.750, patches=3) | - |
| `qwen-2026-10-16` | `structure-mixed-outdoor` | ok (fid=1.000, patches=4) | FAIL (replay_missing) | grid_repairs: 1 -> 0 |
| `qwen-2026-10-16` | `v4-mission-metroidvania` | ok (fid=0.900, patches=5) | ok (fid=0.800, patches=5) | expect_fail: [] -> ['no Key entity'] |
| `qwen-2026-10-16` | `v4-complexity-low` | ok (fid=0.900, patches=3) | ok (fid=0.900, patches=3) | - |
| `qwen-2026-10-16` | `v4-complexity-high` | ok (fid=0.875, patches=2) | ok (fid=0.875, patches=2) | - |

## A4: `no-PatternStamping`

Compare StampFinder retrieval stamping vs. procedural carvers only (stamp_finder=None).

| Metric | Baseline (`StampFinder`) | Ablated (`stamp_finder=None`) | Delta (Ablated - Baseline) |
|---|---:|---:|---:|
| Stamped sections total | 2 | 0 | -2 |
| Stamped prompts | 2 | 0 | -2 |
| Verified | 6 | 6 | +0 |
| Playable share | 100.0% | 100.0% | +0.0 pp |
| Fidelity mean | 0.931 | 0.931 | +0.000 |

#### Per prompt

| Prompt | Baseline verified | Baseline stamped | Patterns stamped | Ablated verified | Baseline fid | Ablated fid |
|---|---|---|---|---|---|---|
| `structure-crypt` | yes | 0 | `-` | yes | 0.938 | 0.938 |
| `structure-labyrinth` | yes | 0 | `-` | yes | 0.750 | 0.750 |
| `structure-tower` | yes | 0 | `-` | yes | 1.000 | 1.000 |
| `structure-hub-key` | yes | 1 | `62b6330330d1` | yes | 1.000 | 1.000 |
| `structure-mixed-outdoor` | yes | 1 | `239d60d9ba99` | yes | 1.000 | 1.000 |
| `v4-mission-metroidvania` | yes | 0 | `-` | yes | 0.900 | 0.900 |

## A5: `no-ClosedLoopComplexity`

Bypass pipeline.level._close_loop deterministic difficulty adjustment against complexity targets.

| Metric | Baseline (`_close_loop`) | Ablated (bypassed) | Delta (Ablated - Baseline) |
|---|---:|---:|---:|
| Mean band error | 0.200 | 0.400 | +0.200 |
| In-range share (strict band) | 66.7% | 0.0% | -66.7 pp |
| Within 1.5 count | 3 | 3 | +0 |
| Complexity rounds mean | 0.67 | 0.00 | -0.67 |
| Verified | 3 | 3 | +0 |
| Playable share | 100.0% | 100.0% | +0.0 pp |

#### Per prompt

| Recording | Prompt | Complexity | Target band | Baseline overall | Baseline err | Baseline rounds | Ablated overall | Ablated err | Ablated rounds |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| `gemini-2026-10-16` | `v4-complexity-low` | 0.30 | [2.3, 4.3] | 4.9 | 0.60 | 0 | 4.9 | 0.60 | 0 |
| `gemini-2026-10-16` | `v4-complexity-high` | 0.80 | [5.3, 7.3] | 5.7 | 0.00 | 1 | 5.2 | 0.10 | 0 |
| `gemini-2026-10-19c` | `structure-crypt` | 0.75 | [5.0, 7.0] | 5.6 | 0.00 | 1 | 4.5 | 0.50 | 0 |
