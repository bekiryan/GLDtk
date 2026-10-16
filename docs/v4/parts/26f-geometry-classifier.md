# Part 26f: geometry structure classifier, plus taller stamps

Part 26e left the library with **no labyrinth, arena or hub windows**:
- the labeller rarely uses those types;
- the heuristic `patterns/ingest.py::structure_of` never produces them;
- 34-row and taller sections seldom stamp.

The user chose a deterministic geometry classifier (2026-10-16).

## 1. Classifier (`patterns/classify.py`, no LLM)
Compute features from each window's movement graph (the extractor and route code that ingest
already uses):
- **branching:** spans with 3 or more move targets, counted after merging A↔B pairs;
- **dead ends:** reachable spans whose only exit is back the way you came, and not an Exit;
- **cycles:** the cycle rank of the undirected movement graph, E − V + C;
- **enclosed room:** the largest air region bounded by rock on its left, right and top, its
  size and its floor width;
- **openings:** air cells on the window border that a route can use, counted per side;
- **vertical extent:** the route's rise compared with its run.

Rules (tune the thresholds on the real index and write them down in the module doc):
- **labyrinth:** at least 2 branching points and at least 2 dead ends, or cycle rank of at
  least 2 with enclosed corridors;
- **arena:** an enclosed room at least 12 columns wide and 6 rows high, with a mostly flat floor
  and no more than 2 openings;
- **hub:** a central room with at least 3 openings on at least 2 sides;
- **shaft:** the route rises at least 2× its run;
- **parkour, corridor, open:** as now, from `structure_of`.

The result is `features.structure_geo` with a confidence value.

## 2. Use it
- `Filters.matches()` and the stamp finder treat a window as type T when the label says T, or
  when the label is missing or generic (corridor or open) and `structure_geo` says T with
  confidence ≥ 0.6.
- The `recheck` step of `python -m patterns.build` fills in `structure_geo` for every record,
  with no LLM call.

## 3. Taller stamps (`layout/terrain/stamp.py`)
When a window is shorter than the section by more than `MAX_RESIZE`:
- if a second fitting window of the same type exists, **stack** the two vertically and join
  them with a stair line;
- otherwise **pad with rock at the top**, up to 40 %, but only for structure types where the
  playable band sits near the floor (corridor, arena, open).

The result is still verified as before; a failure falls back to the generator.

## Report
- Counts of each type in the real index before and after the classifier.
- 3 ASCII samples each for labyrinth, arena and hub.
- The stamping coverage table, as in 26e, at heights 24, 34 and 40.
