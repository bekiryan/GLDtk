# Part 6 — AI theme and sprite generation

Design context: [../DESIGN.md](../DESIGN.md) (Q2, Q2.1, Q2.2). Builds on Part 5's library
(`aesthetic/library.py`: `add_theme`, `add_sprite`, `assets/manifest.json`). Branch `feat/v2`.
**Runs in parallel with Part 4** in its own git worktree; see "Parallel-work rules" below.

## Goal

An optional "✨ generate theme" action. From a text description (e.g. "crystal cavern with
glowing purple moss"), generate a 16×16 pixel-art theme sheet in the library's 14-role layout
and/or entity sprites. Post-process them deterministically, save them into the library with
`generated: true`, and make them usable right away by `detect_theme` / `find_sprite`.

## Deliverables

### 1. Image provider interface — `art/providers/`

New package `art`; add `art*` to setuptools `include`.

```python
class ImageProvider(Protocol):
    name: str
    def generate(self, prompt: str, *, size_hint: tuple[int, int] | None = None,
                 reference_png: bytes | None = None) -> bytes:   # PNG bytes
        ...
```

- `gemini_image.py`, the default:
  - REST via `urllib` to `generateContent` on the newest Gemini image-capable Flash model. Check
    the exact model name with the ListModels endpoint (look for `image` in the name or for
    `responseModalities` support); `gemini-2.5-flash-image` is the historical name.
  - Request `responseModalities: ["IMAGE"]` (or `["TEXT","IMAGE"]` if IMAGE alone is rejected) and
    read the `inlineData` image part.
  - Key: `GEMINI_API_KEY` from the environment / `.env`, sent in the `x-goog-api-key` header
    only. Never print it.
  - Reuse the transient-retry idea from `llm/providers/gemini.py`, and raise a clear
    `ProviderError` on 429 (quota).
- `fake.py`: returns canned PNGs from `tests/fixtures/art/` (make small synthetic ones with
  Pillow in a fixture script or in conftest) and records the calls.
- `registry.py`: `make_image_provider(name="gemini", model=None)`.

### 2. Post-processing pipeline — `art/pipeline.py` (Pillow; deterministic; unit-tested offline)

`generate_theme(description, theme_id, provider, *, keywords=None, seed=0) -> ThemeEntry`:
1. **Prompt.** Build an image prompt asking for a seamless 16-px-grid pixel-art tile sheet
   laid out as the 5×3 role grid (describe each cell's role in words), on a flat background,
   with a limited palette.
2. **Generate.** Call the provider. Models usually return about 1024 px images that do not
   follow a grid exactly.
3. **Downscale to 16 px tiles.**
   - Detect the effective pixel scale; estimate it from the run lengths of colour changes.
   - Crop or align to the grid, then use nearest-neighbour or box sampling to reach an
     80×48 sheet (5×3 tiles of 16 px).
   - If the model ignored the layout, fall back to a single-texture strategy: take the most
     tile-like 16×16 texture region and build the 14 roles with Part 5's derivation approach
     (texture + outline + top band). Factor that code out of `scripts/build_assets.py` into
     `art/derive.py` so both use it.
4. **Quantise the palette** to at most 16 colours (Pillow `quantize`, with a fixed method and
   no dithering, for determinism). Make the background colour transparent where the role
   needs it (caps, singles, hazard).
5. **Slice** into the 14 role tiles, plus CENTER variants like Part 5's 5×4 layout.
6. **Seam check.**
   - Measure the mean colour difference across the seams of CENTER tiled 2×2, and of
     FILL→FILL horizontally.
   - Above a threshold, repair: blend edge columns, or mirror-wrap the texture.
   - Record the seam score in the manifest entry (add an optional `quality` dict to the
     manifest model; keep older manifests valid).
7. **Save** via `add_theme(..., generated=True, source="gemini-image")`, with
   `keywords` = the user's description words plus any given keywords.

`generate_sprite(description, kind, sprite_id, provider, size=(16,16)) -> SpriteEntry`: the same
steps for a single sprite. Isolate the subject from the background (flood-fill the corner
colour to transparent), downscale to `size`, quantise, and save with tags taken from the
description.

All steps are pure functions on `PIL.Image` that the tests can call one at a time.

### 3. API — new endpoints in a separate module `api/art_routes.py` (FastAPI `APIRouter`)

- `POST /art/theme {description, theme_id?, provider?}` → `{theme_id, sheet_path, quality}`
- `POST /art/sprite {description, kind, sprite_id?, provider?}` → `{sprite_id, file, quality}`
- `GET /art/library` → the list of themes and sprites (with the `generated` flag) for Part 9's
  panel.

Do **not** edit `server.py`. The orchestrator adds `app.include_router(...)` when merging.
Provide the router plus a `image_provider_factory` hook (module level) that tests override.

### 4. Pillow becomes a runtime dependency

Pillow is now needed at runtime by `art/`. Move it from the `dev` extra to an `art` extra
(`pip install -e ".[server,art]"`). `art/` imports Pillow lazily, with a clear error if it is
missing, so the rest of the server runs without it.

### 5. Tests

Offline, with the fake provider and synthetic images:
- a clean 1024 px "grid-respecting" image comes out as an exact 80×64 sheet with the expected
  colours;
- a non-grid image takes the fallback path;
- the quantiser gives ≤ 16 colours and is deterministic (same input → byte-identical PNG);
- the seam check flags a deliberately seamed texture and the repair lowers its score;
- sprite background removal works;
- `generate_theme` writes into a temp copy of the library, after which `detect_theme(description)`
  returns the new theme;
- the router endpoints work, with the fake provider factory.

`tests/e2e/test_gemini_image.py` (`@pytest.mark.e2e`): one real theme generation. **Make at most
3 real image calls in total during your whole session** (free-tier quota is tiny), and only after
every offline test passes. Report the model name used, whether the free tier allows image
generation at all (a 429 / "limit: 0" counts as an answer), and if it works, save the resulting
sheet path and look at it with Read. A generated theme from that e2e run must **not** be
committed into `assets/`; run the e2e against a temp copy of the library.

## Parallel-work rules

- Separate git worktree. Only edit:
  - `art/`
  - `api/art_routes.py`
  - `aesthetic/library.py` (only the optional `quality` field)
  - `scripts/build_assets.py` (only to use `art/derive.py`, and its output must stay
    byte-identical: rebuild and `git diff --stat assets/` must show no changes)
  - `pyproject.toml` (extras / packages)
  - `tests/test_art*.py`, `tests/fixtures/art/`, `tests/e2e/test_gemini_image.py`
- Do **not** edit `server.py`, `layout/`, `spec/`, `llm/`, `symbolic/` or `pipeline/`. Part 4 is
  editing `layout/`.
- The shared venv's editable install points at the main checkout. Run commands from your
  worktree root as `python -m pytest`, and check once that `python -c "import aesthetic;
  print(aesthetic.__file__)"` shows your worktree path; prefix `PYTHONPATH=$PWD` if not.
- Commit in the worktree: one commit, message `v2 Part 6: AI theme and sprite generation`,
  ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push or merge.

## Done when

- `python -m pytest` passes offline in the worktree, and `assets/` is unchanged.
- Report:
  - worktree path, branch and commit;
  - files changed;
  - image model name;
  - free-tier image result (works / quota 0 / error), and the generated sheet's quality as
    observed with Read, if any;
  - deviations and open issues.
