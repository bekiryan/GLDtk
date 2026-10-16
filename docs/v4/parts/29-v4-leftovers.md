# Part 29: v4 leftovers

These items were in the v4 plan (§22, §25, §26) or in Part 26b, but were never built. The user
asked for all of them on 2026-10-16.

## 29a. Sidebar switch for pattern stamping (Haxe, `ldtk-src`)
- **Checkbox:** add a checkbox "Use real level pieces" (checked by default) next to the
  existing "examples" control from the v4 sidebar controls.
- **Request:** send it as `stamp_patterns` on generate and section-edit requests. The server
  already accepts it (Part 26b).
- **Result:** show the stamped entries of `patterns_used` (`use: "stamped"`) in the result
  list, next to the examples ("Section *cave*: built from *SuperTux – Crystal Mine (GPL-2.0)*").

## 29b. Stuck spots in ▶ Play (Haxe, `ldtk-src`)
- **Data:** when Play opens, fetch `GET /route` (it already returns `softlock_spans`) for the
  current level. Draw those spans in Play as a faint red band, the same style as the route
  overlay.
- **Warning:** if the player's feet enter a soft-lock span, show a small toast "You are in a
  spot with no way to the Exit". Do not kill the player.
- **Failures:** if the server is unreachable or no spans come back, show nothing (Play stays
  client-only).

## 29c. Decorative background noise (Python)
- **Layer:** a new optional layer `Background` (IntGrid or AutoLayer, whichever fits the
  exporter best). It is exported **below** the gameplay layers and has **no collision or
  gameplay effect**.
- **Content:** seeded noise (reuse `layout/noise.py`) fills it with caves, strata and
  stalactite shapes, darker tiles from the section's theme. Where the gameplay grid is SOLID,
  the background is hidden anyway.
- **Request:** `GenerateRequest.background: bool = False`, and on `POST /chunks`.
- **Order:** the layer order rule in CLAUDE.md applies (`defs.layers` and `layerInstances`
  must match).
- **Tests:** the exported file validates against `ldtk-src/docs/JSON_SCHEMA.json`. The
  gameplay grid and the verification are identical with the background on or off.

## 29d. Vision labelling of patterns (Python, after Part 26d merges)
- **Render:** `patterns/label.py` can render each pattern to a small PNG (grid colours: rock,
  air, hazard, route), and send it with the text to a local vision model (`ollama:qwen2.5vl:7b`,
  pulled 2026-10-16).
- **CLI:** opt-in with `python -m patterns.build --vision`.
- **Merge:** the vision model's description is merged into the label (`label.visual`) and into
  the embedded text.
- **Fallback:** if the model is missing, use text-only labels and log a warning once.
- **Tests:** offline, with a fake vision provider.
