# Part 1 — Level spec schema + structured-output providers

Design context: [../DESIGN.md](../DESIGN.md) (Q3, Q4, Q5, Q6, Q11). Branch `feat/v2`.

## Goal

The LLM stops emitting a bare `{nodes, edges}` graph. It emits a **`LevelSpec`**: the whole level
as described by the prompt — size, named sections, the platform graph, every entity the prompt
asked for, and the list of requirements extracted from the prompt. Output is enforced by a JSON
schema at the provider level (structured output), not by prompt wording alone.

Downstream stages (layout, aesthetic, export) are **not** rewritten in this part. They keep
consuming an `AbstractLevelGraph`, which `LevelSpec.to_graph()` produces. Parts 3, 4 and 7 consume
the rest of the spec later.

## Deliverables

### 1. `spec/` package — `spec/level_spec.py` (pydantic v2)

Add `spec*` to `[tool.setuptools.packages.find] include` in `pyproject.toml`.

```
LevelSpec
  title: str
  size: LevelSize { width_tiles: int [30..300], height_tiles: int [15..100] }
  sections: list[Section]  (min 1)
  nodes: list[SpecNode]    (the platform graph — same semantics as today)
  edges: list[SpecEdge]
  entities: list[SpecEntity]
  requirements: list[Requirement]

Section
  id: str, name: str
  theme: str               (free text, e.g. "dungeon", "ice cave"; mapped to a real theme later)
  difficulty: "easy"|"medium"|"hard"
  gravity_scale: float [0.25..2.0], default 1.0
  description: str         (what happens in this section)
  order: int               (left-to-right position of the section, 0-based)

SpecNode
  id, type ("Start"|"Exit"|"Platform"|"Hazard"), x, y, w, h  — pixels, Y-up, exactly as today
  section: str             (Section.id)

SpecEdge
  from, to, type ("Walk"|"Jump"|"Fall"), vx, vy — exactly as today (use pydantic alias for "from")

SpecEntity
  id: str
  kind: "Start"|"Exit"|"Enemy"|"Pickup"|"Weapon"|"Interactive"|"NPC"|"Checkpoint"|"Key"
  anchor: str              (SpecNode.id the entity stands on; layout v2 repositions nodes, so
                            entities are attached to nodes, not to absolute coordinates)
  section: str
  description: str         (free text from the prompt, e.g. "a fire-breathing skeleton")
  sprite: str              (free-text sprite hint, may be "")
  -- kind-specific fields, all Optional (null when not applicable to the kind):
  facing ("left"|"right"), starting_items: list[str], starting_weapon: str   (Start)
  requires_key: bool                                                         (Exit)
  archetype ("walker"|"flyer"|"jumper"|"turret"|"boss"), health: int, damage: int,
    patrol_range: int (tiles), attack ("melee"|"ranged"|"none")              (Enemy)
  pickup_kind ("coin"|"gem"|"health"|"ammo"|"powerup"), value: int          (Pickup)
  weapon_kind: str, range: int (tiles)  (+ damage)                           (Weapon)
  interactive_kind ("chest"|"door"|"lever"|"switch"|"sign"), linked_to: str (entity id),
    contents: str                                                            (Interactive)
  name: str, dialogue: str                                                   (NPC)
  opens: str (entity id)                                                     (Key)

Requirement
  id: str ("R1", "R2", …)
  text: str                (one atomic, checkable statement taken from the prompt)
  category: "size"|"section"|"layout"|"entity"|"ability"|"style"|"other"
```

Use a **flat** `SpecEntity` (one model, `kind` enum, optional per-kind fields) rather than a
discriminated union: `anyOf` support differs across providers and a flat object is portable.

Also provide:
- `LevelSpec.json_schema() -> dict` — a provider-portable schema: inline all `$ref`/`$defs`
  (Gemini and Ollama handle refs poorly), express optionals as `"type": [T, "null"]` or
  `nullable`, as each provider needs (put per-provider adaptation in the provider module, not
  here — this returns the canonical inlined schema).
- `LevelSpec.to_graph() -> AbstractLevelGraph` — reuses the existing node/edge parsing logic
  (Δx/Δy re-derived from positions; never trusted from the LLM). Node `metadata["section"]` is
  set. Move/refactor `_parse_graph` from `llm/validator.py` so there is one implementation.
- `LevelSpec.with_graph(graph) -> LevelSpec` — writes a (patched) graph back into the spec.
  Nodes the patcher added get the section of the edge's source node.

### 2. Semantic validation (`spec/validate.py` or inside `llm/validator.py`)

`validate_spec(spec, physics) -> ValidationResult` = existing structural + physics checks on
`spec.to_graph()` **plus** new codes, each with an LLM-readable message:
- `DUPLICATE_ID` (nodes, entities, sections, requirements each unique)
- `UNKNOWN_SECTION` (node/entity references a missing section)
- `UNKNOWN_ANCHOR` (entity anchor not a node id), `ANCHOR_ON_HAZARD`
- `UNKNOWN_ENTITY_REF` (`linked_to`/`opens` not an entity id)
- `NODE_OUT_OF_BOUNDS` (x/y outside `[0, width_tiles*16] × [0, height_tiles*16]`)
- `START_ENTITY_MISMATCH`: exactly one `Start` entity, anchored on the `Start` node; at least one
  `Exit` entity, anchored on an `Exit` node. (The SymbolicPatcher may add a missing Start/Exit
  entity deterministically — add that as a small patch if straightforward.)
- `NO_REQUIREMENTS` (the list must be non-empty)

Parsing must never raise: pydantic validation errors become `INVALID_SPEC` with the pydantic
error text, so the repair loop can feed them back to the LLM.

### 3. Provider layer — `llm/providers/`

```python
class LLMProvider(Protocol):
    name: str
    def complete_json(self, system: str, messages: list[dict], schema: dict) -> str: ...
```
- `gemini.py` — **default provider**. REST via `urllib` (no new dependency) to
  `generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`, `system_instruction`,
  `generationConfig.responseMimeType="application/json"` + `responseJsonSchema` (or
  `responseSchema` if the JSON-schema field is rejected — pick whichever works for the model and
  document it). Key from `GEMINI_API_KEY`. Default model: the newest stable **Flash** text model;
  verify the name with the ListModels endpoint during the e2e run and record it.
- `anthropic_provider.py` — structured output via a single forced tool
  (`tools=[{name:"emit_level_spec", input_schema: schema}]`, `tool_choice={"type":"tool",...}`);
  return `json.dumps(tool_use.input)`. Keep `SYSTEM_PROMPT` as a cached system block. Default
  model `claude-sonnet-5-5`.
- `ollama.py` — `/api/chat` with `"format": schema`, `"stream": false`. Default `llama3.1`.
- `openai_compat.py` — keep openai/deepseek working (`response_format` json_schema for openai,
  `json_object` for deepseek). Not first-class; no e2e test required.
- `fake.py` — `FakeProvider(responses: list[str] | path to a fixtures dir)`; returns them in
  order, records every call (system, messages, schema) for assertions; raises when exhausted.
- `registry.py` — `make_provider(name, model=None, api_key=None, base_url=None, timeout=...)`;
  keep `_DEFAULT_PROVIDER_MODELS` (CLAUDE.md references it) with `"gemini"` added.

`LLMController` becomes provider-agnostic: it takes a provider (or a name + config, built via the
registry) and keeps the generate → validate → patch → repair loop. Remove the per-provider
`_call_*` methods from `validator.py`.

### 4. Controller API

- New: `LLMController.generate_spec(description, constraints=None) -> LevelSpec` (raises
  `MaxRetriesExceeded`). The controller also records `attempts`, `patches_applied` (names) and the
  final error codes on a result object or attributes — Part 10 needs retries/patch counts.
- Keep `generate(...) -> AbstractLevelGraph` as `generate_spec(...).to_graph()` for compatibility.

### 5. Prompt — `llm/prompt.py`

Rewrite `SYSTEM_PROMPT` for the `LevelSpec` contract. It stays a static constant (prompt
caching). It must explain: sizing from the prompt (default ~60×20 if unstated), sections in order
left→right, node coordinates in pixels within the level bounds, the same physics rules as today
(gravity 980, jump_v 600, tile 16 — the Game Profile replaces these in Part 2), entity kinds and
their fields, anchoring entities on non-hazard nodes, mapping unknown concepts to the nearest kind
with the original wording kept in `description`, and requirement extraction (atomic, one per
checkable claim, including things the system may not be able to satisfy). Include one compact
full example. Repair messages keep working (now they echo spec JSON and the error list).

### 6. Server + API

- `GenerateRequest.provider` default `"gemini"`, `model` default `None` (registry default).
- `server.py` uses `generate_spec`; layout uses `spec.to_graph()`; when the request does not set
  `width_tiles`/`height_tiles`, use `spec.size`. Add `requirements: list[str]` and
  `sections: list[str]` to the response (`GenerateResponse` / `LevelStats`) — just the texts and
  names; the fidelity report is Part 7.
- Load `.env` at server start with a tiny built-in loader (no new dependency): `KEY=VALUE` lines,
  ignore comments/blank lines, do not override variables already set.

### 7. Tests — `tests/` (pytest)

- Add `dev = ["pytest>=8"]` to optional deps; `[tool.pytest.ini_options]` with an `e2e` marker and
  `addopts = "-m 'not e2e'"`. Install pytest into `.venv`.
- Recorded fixtures in `tests/fixtures/llm/*.json` (hand-written valid and invalid specs).
- Unit tests (offline, FakeProvider):
  - schema: `json_schema()` has no `$ref`; a valid fixture round-trips; `to_graph()` derives dx/dy.
  - each new validation code fires on a minimal bad spec.
  - controller: valid on first try; invalid → patched without a second LLM call; invalid JSON →
    repair re-prompt whose messages contain the error text → success; exhaustion →
    `MaxRetriesExceeded`; the schema is passed to the provider on every call.
  - each real provider's request payload is built correctly (monkeypatch the HTTP function /
    anthropic client — no network).
  - server: `/generate` with the fake provider end to end writes a `.ldtk` into `tmp_path` and
    returns sections + requirements (inject the provider via a FastAPI dependency or a module-level
    factory that tests can override).
- `tests/e2e/test_gemini.py` (`@pytest.mark.e2e`): real Gemini call with a short prompt produces a
  valid `LevelSpec` (after the loop). Run it once and report the result, the model name used and
  any quota/rate-limit info in the response headers or errors.

## Constraints

- No new runtime dependencies (pydantic comes with fastapi; `anthropic` already present).
  Python 3.11+ (venv is 3.14).
- Do not touch `layout/`, `aesthetic/` or `ldtk-src/` beyond what the server wiring needs.
- Match the existing code style (module docstrings, `# ───` section banners, type hints).
- Do not commit. The orchestrator reviews and commits.

## Done when

- `source .venv/bin/activate && pytest` passes (offline).
- `pytest -m e2e tests/e2e/test_gemini.py` passes, or its failure is explained (quota, key).
- `python -c "import server"` works, and a fake-provider `/generate` run writes a loadable `.ldtk`.
- A short report: files changed, Gemini model + structured-output field used, open issues.
