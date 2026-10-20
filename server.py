"""GLDtk Companion Server — backend only.

Run:
    python server.py

The GLDtk panel inside LDtk sends requests to this server.
POST /generate  — run the pipeline and write a .ldtk file (one level, a multi-level world
                  after an outline call, or — with ``level`` — one level of an existing world).
                  Every edit / route / difficulty / undo request takes an optional ``level``
                  (iid or identifier; default the first level), v3 Part 17.
GET  /providers — LLM provider names and their default models.
GET  /profile   — read the project's Game Profile (defaults if missing).
PUT  /profile   — validate and write the project's Game Profile.
POST /edit/region, /edit/section, /edit/entities — regional edits (Part 8).
POST /edit/locate — text location → rect (writes nothing).
POST /undo      — restore the newest history snapshot.
GET  /history   — the snapshots of a .ldtk file.
GET  /difficulty — per-section difficulty scores of a .ldtk file (v3 Part 15a, no LLM).
GET  /route     — the golden Start → Exit route of a .ldtk file (v3 Part 13).
POST /route     — the same for the project JSON open in LDtk (unsaved edits).
GET  /progress  — progress events of a running job (v3 Part 14).
POST /cancel    — cancel a running job; it writes nothing.
"""

from __future__ import annotations

import collections
import functools
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import logging
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from aesthetic import build_aesthetic_layer, copy_aesthetic_assets
from aesthetic.themes import THEMES, Theme, detect_theme
from api import art_routes, jobs
from api.samples import refuse_sample_write, writes_outside_samples
from api.models import (
    CancelRequest,
    CancelResponse,
    DifficultyModel,
    EditInfo,
    EditResponse,
    EntityEditRequest,
    GenerateRequest,
    GenerateResponse,
    HistoryEntry,
    HistoryResponse,
    LevelStats,
    LevelSummary,
    LocateRequest,
    LocateResponse,
    PathRequest,
    ProgressResponse,
    RectModel,
    RegionEditRequest,
    RouteModel,
    RouteRequest,
    SectionEditRequest,
    UndoResponse,
    WorldFidelityModel,
)
from edit import history as edit_history
from memory import analyze as memory_analyze, handedit as memory_handedit, hooks as memory_hooks, prefs as memory_prefs, store as memory_store
from edit.context import Rect
from edit.llm import EditLLMFailed
from edit.entities import edit_entities
from edit.locate import locate as locate_text
from edit.outcome import EditOutcome
from edit.region import edit_region, edit_section
from layout import LDtkConfig, LevelMeta, to_ldtk_dict_with_warnings
from layout.ldtk_reader import all_levels, level_index, read_level
from layout.route import file_route, unsaved_route
from layout.world import graft_level, is_world, level_identifier, link_world, new_world_doc, world_levels
from fidelity.difficulty import apply_to_fidelity, difficulty_of_file
from game.profile import (
    PROFILE_FILENAME,
    GameProfile,
    load_profile,
    profile_path_for,
    save_profile,
)
from llm import LLMController, MaxRetriesExceeded
from llm.providers import _DEFAULT_PROVIDER_MODELS, LLMProvider, make_provider_with_fallback
from pipeline import LevelBuildFailed, build_level
from patterns import routes as pattern_routes
from pipeline.world import WorldLevelInfo, check_world, outline_world, wants_outline
from spec.world import SIZE_HINT_WIDTH
from api.models import ChunksRequest, ChunksResponse  # v4 Part 25a


# ─────────────────────────────────────────────────────────────────────────────
# .env loading  (tiny built-in loader — no python-dotenv dependency)
# ─────────────────────────────────────────────────────────────────────────────


def load_env_file(path: Path) -> None:
    """Load ``KEY=VALUE`` lines into ``os.environ``.

    Blank lines and ``#`` comments are ignored, surrounding quotes are
    stripped, and variables that are already set are never overridden.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def user_config_dir() -> Path:
    """Where the packaged app keeps its settings (``GLDTK_CONFIG_DIR`` overrides, for tests)."""
    override = os.getenv("GLDTK_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Application Support" / "GLDtk"


# The per-user file wins over the repo one (already-set variables are never overridden).
load_env_file(user_config_dir() / ".env")
load_env_file(Path(__file__).resolve().parent / ".env")


# ─────────────────────────────────────────────────────────────────────────────
# Provider factory  (module-level so tests can swap in a FakeProvider)
# ─────────────────────────────────────────────────────────────────────────────


def default_provider_factory(req: Any) -> LLMProvider:
    """*req* is any request with ``provider`` and ``model`` (generate or edit).

    Gemini falls back to a local model (``GLDTK_LOCAL_FALLBACK``) when it cannot answer.
    """
    fake = os.getenv("GLDTK_FAKE_LLM")  # tests only: a fixture file or directory of canned replies
    if fake:
        from llm.providers.fake import FakeProvider

        return FakeProvider(fake if Path(fake).is_dir() else [Path(fake).read_text(encoding="utf-8")])
    return make_provider_with_fallback(req.provider, model=req.model)


def provider_notes(provider: Any) -> List[str]:
    """Fallback switches made while answering (e.g. Gemini 429 → local model)."""
    return list(getattr(provider, "notes", None) or [])


provider_factory: Callable[[Any], LLMProvider] = default_provider_factory


app = FastAPI(title="GLDtk Companion", version="0.1.0")
app.include_router(art_routes.router)
app.include_router(pattern_routes.router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    logging.error(f"VALIDATION ERROR: {exc.errors()}")
    body = await request.body()
    logging.error(f"BODY: {body}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Difficulty → pipeline parameter mapping
# ─────────────────────────────────────────────────────────────────────────────

_DIFFICULTY: Dict[str, Dict[str, Any]] = {
    "easy": {"density": 0.5},
    "medium": {"density": 1.0},
    "hard": {"density": 1.5},
}


def section_theme(theme_text: str, description: str, override: str | None = None) -> Theme:
    """Map a section's free-text theme to a tileset theme.

    The request override wins; otherwise the section theme's keywords, then the
    level description's keywords, then the default theme.
    """
    if override:
        return detect_theme("", override=override)
    theme = detect_theme(theme_text)
    if theme.identifier == "default":
        theme = detect_theme(description)
    return theme


# ─────────────────────────────────────────────────────────────────────────────
# Jobs: progress + cancel (v3 Part 14)
# ─────────────────────────────────────────────────────────────────────────────


def job_route(cancelled: Callable[[], Any], start: str) -> Callable:
    """Run a long route as the request's ``job_id`` (see ``api/jobs.py``).

    *start* is the first progress line; a cancel returns ``cancelled()``.
    """

    def wrap(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def route(req: Any) -> Any:
            try:
                with jobs.job(req.job_id):
                    jobs.report("start", start)
                    return fn(req)
            except jobs.Cancelled:
                return cancelled()

        return route

    return wrap


def _cancelled_edit() -> EditResponse:
    return EditResponse(success=False, error="Cancelled", cancelled=True)


@app.get("/progress", response_model=ProgressResponse)
def progress_route(job: str = Query(..., description="job_id of a request")) -> ProgressResponse:
    return ProgressResponse.model_validate(jobs.registry.progress(job))


@app.post("/cancel", response_model=CancelResponse)
def cancel_route(req: CancelRequest) -> CancelResponse:
    return CancelResponse(job_id=req.job_id, running=jobs.registry.cancel(req.job_id))


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


# Cloud providers and the environment variable that holds each one's key (ollama needs none)
PROVIDER_KEY_ENV: Dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


class SettingsRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    provider: Optional[str] = None   # with api_key: whose key it is
    api_key: Optional[str] = None
    local_fallback: Optional[str] = None


def _settings_state() -> Dict[str, Any]:
    return {
        "has_key": bool(os.getenv("GEMINI_API_KEY")),
        "keys": {p: bool(os.getenv(env)) for p, env in PROVIDER_KEY_ENV.items()},
        "local_fallback": os.getenv("GLDTK_LOCAL_FALLBACK", ""),
    }


@app.get("/settings")
def get_settings() -> Dict[str, Any]:
    """Which provider keys are configured (the keys themselves are never returned)."""
    return _settings_state()


@app.post("/settings")
def post_settings(req: SettingsRequest) -> Dict[str, Any]:
    """Write a provider key / ``GLDTK_LOCAL_FALLBACK`` to the per-user ``.env``."""
    updates = {
        "GEMINI_API_KEY": (req.gemini_api_key or "").strip() or None,
        "GLDTK_LOCAL_FALLBACK": (req.local_fallback or "").strip() or None,
    }
    if req.api_key is not None:
        env = PROVIDER_KEY_ENV.get(req.provider or "")
        if env is None:
            raise HTTPException(status_code=400, detail=f"Provider {req.provider!r} takes no API key")
        updates[env] = req.api_key.strip() or None
    updates = {k: v for k, v in updates.items() if v is not None}
    for k, v in updates.items():
        if "\n" in v or "\r" in v:
            raise HTTPException(status_code=400, detail=f"{k} must be a single line")
    path = user_config_dir() / ".env"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        kept = [ln for ln in lines if ln.split("=", 1)[0].strip() not in updates]
        kept += [f"{k}={v}" for k, v in updates.items()]
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        path.chmod(0o600)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write settings: {exc}")
    os.environ.update(updates)
    return _settings_state()


@app.get("/themes")
def list_themes() -> Dict[str, List[str]]:
    return {"themes": [k for k in THEMES if k != "default"]}


@app.get("/providers")
def list_providers() -> Dict[str, Any]:
    """Provider names (in the sidebar's order) with their default models."""
    return {
        "default": GenerateRequest.model_fields["provider"].default,
        "providers": [
            {"name": name, "default_model": model}
            for name, model in _DEFAULT_PROVIDER_MODELS.items()
        ],
    }


def resolve_profile_path(path: str) -> Path:
    """``path`` may name the profile file itself or the project's ``.ldtk`` file."""
    p = Path(path)
    if p.suffix.lower() == ".ldtk":
        return profile_path_for(p)
    if p.is_dir():
        return p / PROFILE_FILENAME
    return p


@app.get("/profile")
def get_profile(path: str = Query(..., description=".ldtk or gldtk.game.json path")) -> Dict[str, Any]:
    profile_path = resolve_profile_path(path)
    try:
        profile = load_profile(profile_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return profile.model_dump()


@app.put("/profile")
def put_profile(
    path: str = Query(..., description=".ldtk or gldtk.game.json path"),
    profile: GameProfile = Body(...),
) -> Dict[str, Any]:
    refuse_sample_write(path)
    profile_path = resolve_profile_path(path)
    try:
        save_profile(profile, profile_path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write {profile_path}: {exc}")
    memory_hooks.profile_changed(profile_path, profile)
    return profile.model_dump()


@app.post("/generate", response_model=GenerateResponse)
@writes_outside_samples
@job_route(lambda: GenerateResponse(success=False, error="Cancelled", cancelled=True),
           "Generating the level")
@memory_hooks.track("generate")
def generate(req: GenerateRequest) -> GenerateResponse:
    # ── game profile (never written during generate) ──────────────────────
    profile_path = (
        Path(req.game_profile_path)
        if req.game_profile_path
        else profile_path_for(req.output_path)
    )
    try:
        profile = load_profile(profile_path)
    except ValueError as exc:
        return GenerateResponse(
            success=False, error=str(exc), profile_path=str(profile_path)
        )
    profile = memory_prefs.nudge_profile(profile)  # design memory (bounded, logged in warnings)

    # ── difficulty params ─────────────────────────────────────────────────
    diff_cfg = _DIFFICULTY.get(req.difficulty or "medium", _DIFFICULTY["medium"])
    density: float = diff_cfg["density"]

    # ── build constraints block for the LLM ───────────────────────────────
    constraints: Dict[str, Any] = {}
    if req.theme:
        constraints["theme"] = req.theme
    if req.enemy_types:
        constraints["enemy_types"] = req.enemy_types
    if req.platform_count:
        constraints["platform_count"] = req.platform_count
    if req.difficulty:
        constraints["difficulty"] = req.difficulty
    if req.width_tiles:
        constraints["width_tiles"] = req.width_tiles
    if req.height_tiles:
        constraints["height_tiles"] = req.height_tiles

    # ── one level of an existing world (v3 Part 17) ───────────────────────
    if req.level:
        return _regenerate_level(req, profile, profile_path, constraints, density)

    # ── a world: the outline call first (v3 Part 17, see pipeline/world.py) ──
    provider = None
    if wants_outline(req.description, req.levels):
        try:
            provider = provider_factory(req)
            jobs.report("outline", "Planning the levels")
            outline = outline_world(req.description, provider, profile, req.levels,
                                    max_retries=req.max_retries)
        except EditLLMFailed as exc:
            return GenerateResponse(success=False, error=f"World outline: {exc}",
                                    profile_path=str(profile_path),
                                    warnings=provider_notes(provider))
        except Exception as exc:
            return GenerateResponse(success=False, error=f"LLM error: {exc}",
                                    profile_path=str(profile_path),
                                    warnings=provider_notes(provider))
        if len(outline.levels) > 1:
            return _generate_world(req, outline, provider, profile, profile_path, constraints,
                                   density)
        # an outline of one level: the single-level pipeline on the original prompt

    # ── LLM spec → layout → grid verification → repair ──────────────────
    try:
        provider = provider or provider_factory(req)
        controller = LLMController(
            profile=profile,
            provider=provider,
            max_retries=req.max_retries,
        )
        patterns_used = attach_patterns(controller, req)
        built = build_level(
            req.description,
            controller,
            profile,
            constraints=constraints or None,
            seed=req.seed,
            size_override=(req.width_tiles, req.height_tiles),
            densify=req.densify, fill=req.fill,
            complexity=req.complexity,
            stamp_finder=stamp_finder_for(req),
        )
        patterns_used = patterns_used + built.patterns_stamped
    except MaxRetriesExceeded as exc:
        return GenerateResponse(
            success=False, error=str(exc), profile_path=str(profile_path),
            warnings=provider_notes(provider),
        )
    except LevelBuildFailed as exc:
        return GenerateResponse(
            success=False,
            error=str(exc),
            profile_path=str(profile_path),
            verified=False,
            warnings=provider_notes(provider),
        )
    except Exception as exc:
        return GenerateResponse(
            success=False, error=f"LLM error: {exc}", profile_path=str(profile_path),
            warnings=provider_notes(provider),
        )

    spec = built.spec
    ir = built.spec_layout.ir

    # ── aesthetic (spec entities only — no random coins / enemies) ───────
    jobs.report("aesthetic", "Tiling and decorating the level")
    try:
        result = _dress(built, req.description, req, density, req.seed)
    except Exception as exc:
        return GenerateResponse(success=False, error=f"Aesthetic error: {exc}")

    # ── serialise (the last cancel checkpoint: nothing is written before it) ──
    jobs.report("export", "Writing the level file")
    try:
        out = Path(req.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        # The .ldtk references the theme sheets and the sprite atlas relative to itself.
        copy_aesthetic_assets(result, out.parent)
        edit_history.snapshot(out, edit_history.make_label("generate", req.description))
        doc, export_warnings = to_ldtk_dict_with_warnings(
            ir, aesthetic=result.aesthetic, cfg=_ldtk_cfg(profile),
            level_meta=_level_meta(req.description, spec, profile_path, out),
        )
        out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    except Exception as exc:
        return GenerateResponse(success=False, error=f"Serialisation error: {exc}")

    # before the fidelity report is serialised: a section that misses its stated difficulty
    # turns its fidelity item into `approximated`
    difficulty = difficulty_of(out, built.fidelity, req.description)
    summary = LevelSummary(
        index=0, identifier=str(doc["levels"][0]["identifier"]), iid=doc["levels"][0]["iid"],
        title=spec.title, score=built.fidelity.score if built.fidelity else None,
        verified=built.report.ok, current=True,
    )
    return GenerateResponse(
        success=True,
        preview_ascii=ir.ascii_render({"Start": "S", "Exit": "X"}),
        stats=_level_stats(built, result),
        output_path=str(out.resolve()),
        requirements=[r.text for r in spec.requirements],
        sections=[s.name for s in sorted(spec.sections, key=lambda s: s.order)],
        profile_path=str(profile_path.resolve()),
        verified=built.report.ok,
        warnings=provider_notes(provider) + built.warnings + export_warnings,
        fidelity=built.fidelity.to_dict() if built.fidelity else None,
        route=route_of(out),
        difficulty=difficulty,
        levels=[summary],
        patterns_used=patterns_used,
        mission=built.mission,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Generate helpers (shared by one level, a world and a regenerated level)
# ─────────────────────────────────────────────────────────────────────────────


def _dress(built: Any, description: str, req: GenerateRequest, density: float, seed: int) -> Any:
    """The aesthetic layer of a built level (spec entities only — no random coins / enemies)."""
    spec = built.spec
    memory_prefs.apply_entity_defaults(spec)  # design memory: the user's typical field values
    ir = built.spec_layout.ir
    ordered_sections = sorted(spec.sections, key=lambda s: s.order)
    for band in ir.metadata.get("sections", []):
        band["mapped_theme"] = section_theme(
            str(band["theme"]), description, req.theme
        ).identifier
    theme = section_theme(ordered_sections[0].theme, description, req.theme)
    return build_aesthetic_layer(
        background=req.background,
        description=description,
        graph=spec.to_graph(),
        node_layouts=built.spec_layout.node_layouts,
        ir_level=ir,
        theme_override=theme.identifier,
        density=density,
        seed=seed,
        custom_enemy_types=req.enemy_types or None,
        place_random_entities=False,
    )


def _level_meta(description: str, spec: Any, profile_path: Path, out: Path,
                world_json: Any = None) -> LevelMeta:
    return LevelMeta(
        prompt=description,
        requirements=list(spec.requirements),
        game_profile_relpath=os.path.relpath(Path(profile_path).resolve(), out.parent.resolve()),
        spec_json=spec,
        world_json=world_json,
    )


def _ldtk_cfg(profile: GameProfile) -> LDtkConfig:
    tw, th = profile.player_tiles()
    return LDtkConfig(player_px=(tw * profile.tile_size, th * profile.tile_size))


def _level_stats(built: Any, result: Any) -> LevelStats:
    spec = built.spec
    entity_counts: Dict[str, int] = collections.Counter(
        e.entity_type for e in built.spec_layout.ir.entities
    )
    return LevelStats(
        theme=result.theme.display_name,
        nodes=len(spec.nodes),
        edges=len(spec.edges),
        entities=dict(entity_counts),
        layout_retries=built.stats["layout_retries"],
        grid_repairs=built.stats["grid_repairs"],
        completeness_reprompts=built.stats.get("completeness_reprompts", 0),
        entities_filled=built.stats.get("entities_filled", 0),
        densify_added=built.stats.get("densify_added", 0),
        comfort_added=built.stats.get("comfort_added", 0),
        fill_added=built.stats.get("fill_added", 0),
    )


def _outline_constraints(constraints: Dict[str, Any], entry: Any, req: GenerateRequest
                         ) -> Dict[str, Any]:
    """The request constraints plus an outline level's difficulty / theme / size hint
    (the request's own values win)."""
    cons = dict(constraints)
    if entry is None:
        return cons
    get = entry.get if isinstance(entry, dict) else lambda k: getattr(entry, k, None)
    if get("difficulty"):
        cons.setdefault("difficulty", get("difficulty"))
    if get("theme"):
        cons.setdefault("theme", get("theme"))
    if get("size_hint") in SIZE_HINT_WIDTH and not req.width_tiles:
        cons["width_tiles"] = SIZE_HINT_WIDTH[get("size_hint")]
    return cons


def attach_patterns(controller: Any, req: GenerateRequest, description: str | None = None
                    ) -> List[Dict[str, str]]:
    """Pattern-library examples for the generate prompt (Part 26a); [] and no change when off."""
    if not req.patterns:
        return []
    from patterns.prompt import examples_block

    text = description or req.description
    controller.examples, used = examples_block([text, req.theme or ""], req.patterns)
    return [{**u, "use": "example"} for u in used]


def stamp_finder_for(req: GenerateRequest, description: str | None = None) -> Any:
    """Patterns to stamp into terrain sections (v4 Part 26b); None when off."""
    if not req.stamp_patterns:
        return None
    from pipeline.stamps import StampFinder

    return StampFinder(description or req.description)


def _pattern_note(out: EditOutcome) -> EditOutcome:
    used = out.stats.get("patterns")
    if used:
        out.warnings.append("Pattern examples: " + ", ".join(f"{u['id']} ({u['title']})" for u in used))
    return out


def _build_one(description: str, theme_text: str, cons: Dict[str, Any], provider: Any,
               profile: GameProfile, req: GenerateRequest, density: float, seed: int):
    """Build and dress one level of a world; returns ``(built, result)``.  Raises."""
    controller = LLMController(profile=profile, provider=provider, max_retries=req.max_retries)
    attach_patterns(controller, req, description)
    built = build_level(
        description, controller, profile, constraints=cons or None, seed=seed,
        size_override=(req.width_tiles, req.height_tiles), densify=req.densify, fill=req.fill,
        complexity=req.complexity, stamp_finder=stamp_finder_for(req, description),
    )
    jobs.report("aesthetic", "Tiling and decorating the level")
    result = _dress(built, f"{theme_text} {description}".strip(), req, density, seed)
    return built, result


def _level_error(where: str, exc: BaseException, profile_path: Path, provider: Any
                 ) -> GenerateResponse:
    if isinstance(exc, (MaxRetriesExceeded, LevelBuildFailed)):
        error = f"{where}: {exc}"
    else:
        error = f"{where}: LLM error: {exc}"
    return GenerateResponse(success=False, error=error, profile_path=str(profile_path),
                            verified=False, warnings=provider_notes(provider))


def _generate_world(req: GenerateRequest, outline: Any, provider: Any, profile: GameProfile,
                    profile_path: Path, constraints: Dict[str, Any], density: float
                    ) -> GenerateResponse:
    """N levels from the outline, sequentially, then one ``.ldtk`` (v3 Part 17)."""
    n = len(outline.levels)
    made = []
    for k, entry in enumerate(outline.levels):
        with jobs.prefixed(f"Level {k + 1}/{n}: "):
            jobs.report("level", entry.title)
            try:
                built, result = _build_one(entry.prompt, entry.theme,
                                           _outline_constraints(constraints, entry, req),
                                           provider, profile, req, density, req.seed + k)
            except Exception as exc:
                return _level_error(f"Level {k + 1}/{n} ({entry.title})", exc, profile_path,
                                    provider)
        made.append((entry, built, result))

    jobs.report("export", "Writing the world file")
    identifiers: List[str] = []
    for k, (entry, built, _) in enumerate(made):
        identifiers.append(level_identifier(entry.title, k, identifiers))
        built.spec_layout.ir.identifier = identifiers[-1]
    world_json = {"version": 1, "prompt": req.description,
                  "outline": outline.model_dump(mode="json"), "identifiers": identifiers}
    warnings: List[str] = provider_notes(provider)
    try:
        out = Path(req.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        for _, _, result in made:
            copy_aesthetic_assets(result, out.parent)
        edit_history.snapshot(out, edit_history.make_label("generate", req.description))
        doc = None
        for k, (entry, built, result) in enumerate(made):
            fresh, export_warnings = to_ldtk_dict_with_warnings(
                built.spec_layout.ir, aesthetic=result.aesthetic, cfg=_ldtk_cfg(profile),
                level_meta=_level_meta(entry.prompt, built.spec, profile_path, out,
                                       world_json if k == 0 else None),
            )
            if doc is None:
                doc = new_world_doc(fresh)
            graft_level(doc, fresh)
            warnings += [f"{identifiers[k]}: {w}" for w in built.warnings + export_warnings]
        link_world(doc)
        out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    except Exception as exc:
        return GenerateResponse(success=False, error=f"Serialisation error: {exc}")

    infos: List[WorldLevelInfo] = []
    summaries: List[LevelSummary] = []
    difficulty = None
    for k, (entry, built, _) in enumerate(made):
        iid = doc["levels"][k]["iid"]
        diff = difficulty_of(out, built.fidelity, entry.prompt, level=iid)  # before scoring
        if k == 0:
            difficulty = diff
        infos.append(WorldLevelInfo(entry.id, identifiers[k], iid, entry.title,
                                    built.spec_layout.ir, built.fidelity, built.report.ok))
        summaries.append(LevelSummary(
            index=k, id=entry.id, identifier=identifiers[k], iid=iid, title=entry.title,
            score=built.fidelity.score if built.fidelity else None, verified=built.report.ok,
            current=k == 0,
        ))
    world = check_world(outline, infos)
    first, built0, result0 = made[0]
    spec0 = built0.spec
    return GenerateResponse(
        success=True,
        preview_ascii=built0.spec_layout.ir.ascii_render({"Start": "S", "Exit": "X"}),
        stats=_level_stats(built0, result0),
        output_path=str(out.resolve()),
        requirements=[r.text for r in spec0.requirements],
        sections=[s.name for s in sorted(spec0.sections, key=lambda s: s.order)],
        profile_path=str(profile_path.resolve()),
        verified=world.verified,
        warnings=warnings,
        fidelity=built0.fidelity.to_dict() if built0.fidelity else None,
        route=route_of(out, level=summaries[0].iid),
        difficulty=difficulty,
        levels=summaries,
        world=WorldFidelityModel.model_validate(
            {**world.to_dict(), "title": outline.title, "levels": [s.model_dump() for s in summaries]}
        ),
    )


def _regenerate_level(req: GenerateRequest, profile: GameProfile, profile_path: Path,
                      constraints: Dict[str, Any], density: float) -> GenerateResponse:
    """Rebuild level ``req.level`` of the file at ``output_path``; the other levels and
    the links stay (v3 Part 17).  An empty description reuses the level's prompt."""
    out = Path(req.output_path)
    try:
        doc = json.loads(out.read_text(encoding="utf-8"))
        idx = level_index(doc, req.level)
        old = read_level(doc, idx)
        world_json = read_level(doc, 0).world
    except (OSError, ValueError, KeyError, IndexError) as exc:
        msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        return GenerateResponse(success=False, error=f"Cannot regenerate level {req.level!r}: {msg}",
                                profile_path=str(profile_path))
    n = len(all_levels(doc))
    entries = ((world_json or {}).get("outline") or {}).get("levels") or []
    entry = entries[idx] if idx < len(entries) and isinstance(entries[idx], dict) else None
    description = req.description.strip() or (old.prompt or "")
    provider = None
    with jobs.prefixed(f"Level {idx + 1}/{n}: "):
        try:
            provider = provider_factory(req)
            built, result = _build_one(description, (entry or {}).get("theme") or "",
                                       _outline_constraints(constraints, entry, req),
                                       provider, profile, req, density, req.seed)
        except Exception as exc:
            return _level_error(f"Level {old.identifier}", exc, profile_path, provider)

    jobs.report("export", f"Writing {old.identifier} into the world file")
    ir = built.spec_layout.ir
    ir.identifier = old.identifier
    try:
        copy_aesthetic_assets(result, out.parent)
        edit_history.snapshot(out, edit_history.make_label(f"regenerate {old.identifier}",
                                                           description))
        fresh, export_warnings = to_ldtk_dict_with_warnings(
            ir, aesthetic=result.aesthetic, cfg=_ldtk_cfg(profile),
            level_meta=_level_meta(description, built.spec, profile_path, out,
                                   world_json if idx == 0 else None),
        )
        graft_level(doc, fresh, idx)
        if is_world(doc):
            link_world(doc)
        out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    except Exception as exc:
        return GenerateResponse(success=False, error=f"Serialisation error: {exc}")

    iid = doc["levels"][idx]["iid"] if idx < len(doc.get("levels") or []) else old.level_iid
    difficulty = difficulty_of(out, built.fidelity, description, level=iid)
    spec = built.spec
    return GenerateResponse(
        success=True,
        preview_ascii=ir.ascii_render({"Start": "S", "Exit": "X"}),
        stats=_level_stats(built, result),
        output_path=str(out.resolve()),
        requirements=[r.text for r in spec.requirements],
        sections=[s.name for s in sorted(spec.sections, key=lambda s: s.order)],
        profile_path=str(profile_path.resolve()),
        verified=built.report.ok,
        warnings=provider_notes(provider) + built.warnings + export_warnings,
        fidelity=built.fidelity.to_dict() if built.fidelity else None,
        route=route_of(out, level=iid),
        difficulty=difficulty,
        levels=levels_summary(out, iid, built.fidelity, built.report.ok),
    )


def levels_summary(path: str | Path, level: str | None = None, fidelity: Any = None,
                   verified: bool | None = None) -> List[LevelSummary]:
    """Every level of the file; *level* (iid / identifier, None = the first) is
    ``current`` and gets the *fidelity* score and *verified* flag.  Never raises."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        idx = level_index(doc, level)
        rows = world_levels(doc)
    except Exception:
        return []
    out = []
    for row in rows:
        cur = row["index"] == idx
        out.append(LevelSummary(
            **row, current=cur,
            score=(fidelity.score if fidelity is not None else None) if cur else None,
            verified=verified if cur else None,
        ))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Regional editing + history (Part 8)
# ─────────────────────────────────────────────────────────────────────────────


def _rect(r: RectModel) -> Rect:
    return Rect(r.col, r.row, r.w, r.h)


def _rect_model(r: Rect | None) -> RectModel | None:
    return RectModel(**r.as_dict()) if r is not None else None


def edit_response(out: EditOutcome, path: str, level: str | None = None) -> EditResponse:
    """An edit outcome in the GenerateResponse shape, plus ``edit``; *level* is the edited
    level of a world (None = the first)."""
    info = EditInfo(mode=out.mode, region=_rect_model(out.rect), snapshot=out.snapshot)
    if not out.success:
        return EditResponse(success=False, error=out.error, warnings=out.warnings, edit=info,
                            output_path=str(Path(path)))
    spec, ir = out.spec, out.ir
    stats = LevelStats(
        theme=out.theme,
        nodes=out.nodes,
        edges=out.edges,
        entities=out.entity_counts,
        layout_retries=out.stats.get("layout_retries", 0),
        grid_repairs=out.stats.get("grid_repairs", 0),
        densify_added=out.stats.get("densify_added", 0),
        comfort_added=out.stats.get("comfort_added", 0),
        fill_added=out.stats.get("fill_added", 0),
    )
    sections = [s.name for s in sorted(spec.sections, key=lambda s: s.order)] if spec else []
    return EditResponse(
        success=True,
        preview_ascii=ir.ascii_render({"Start": "S", "Exit": "X"}) if ir else None,
        stats=stats,
        output_path=str(Path(path).resolve()),
        requirements=[r.text for r in spec.requirements] if spec else [],
        sections=sections,
        profile_path=out.profile_path,
        verified=out.verified,
        warnings=out.warnings,
        fidelity=out.fidelity.to_dict() if out.fidelity else None,
        edit=info,
        route=route_of(path, level=level),
        difficulty=difficulty_of(path, level=level),
        levels=levels_summary(path, level, out.fidelity, out.verified),
    )


def _provider_or_error(req: Any):
    try:
        return provider_factory(req), None
    except Exception as exc:
        return None, f"LLM provider error: {exc}"


@app.post("/edit/region", response_model=EditResponse)
@writes_outside_samples
@job_route(_cancelled_edit, "Reading the level for a region edit")
@memory_hooks.track("edit")
def edit_region_route(req: RegionEditRequest) -> EditResponse:
    provider, err = _provider_or_error(req)
    if err:
        return edit_response(EditOutcome(False, "region", error=err, rect=_rect(req.rect)), req.path)
    out = edit_region(req.path, _rect(req.rect), req.prompt, provider, max_retries=req.max_retries,
                      densify=req.densify, seed=req.seed, level=req.level, patterns=req.patterns,
                      fill=req.fill)
    out.warnings[:0] = provider_notes(provider)
    return edit_response(_pattern_note(out), req.path, req.level)


@app.post("/edit/section", response_model=EditResponse)
@writes_outside_samples
@job_route(_cancelled_edit, "Reading the level for a section edit")
@memory_hooks.track("edit")
def edit_section_route(req: SectionEditRequest) -> EditResponse:
    provider, err = _provider_or_error(req)
    if err:
        return edit_response(EditOutcome(False, "section", error=err), req.path)
    out = edit_section(req.path, req.section_id, req.prompt, provider, max_retries=req.max_retries,
                       densify=req.densify, seed=req.seed, level=req.level,
                       structure=req.structure, patterns=req.patterns, fill=req.fill,
                       complexity=req.complexity, stamp_patterns=req.stamp_patterns)
    out.warnings[:0] = provider_notes(provider)
    return edit_response(_pattern_note(out), req.path, req.level)


@app.post("/edit/entities", response_model=EditResponse)
@writes_outside_samples
@job_route(_cancelled_edit, "Reading the level for an entity edit")
@memory_hooks.track("edit")
def edit_entities_route(req: EntityEditRequest) -> EditResponse:
    provider, err = _provider_or_error(req)
    if err:
        return edit_response(EditOutcome(False, "entities", error=err), req.path)
    out = edit_entities(
        req.path, req.prompt, provider,
        rect=_rect(req.rect) if req.rect else None,
        entity_iids=req.entity_iids or None,
        max_retries=req.max_retries,
        level=req.level,
    )
    out.warnings[:0] = provider_notes(provider)
    return edit_response(out, req.path, req.level)


@app.post("/edit/locate", response_model=LocateResponse)
@job_route(lambda: LocateResponse(success=False, error="Cancelled", cancelled=True),
           "Reading the level to locate the place")
def edit_locate_route(req: LocateRequest) -> LocateResponse:
    provider, err = _provider_or_error(req)
    if err:
        return LocateResponse(success=False, error=err)
    out = locate_text(req.path, req.text, provider, max_retries=req.max_retries, level=req.level)
    return LocateResponse(success=out.success, rect=_rect_model(out.rect),
                          explanation=out.explanation, error=out.error)


@app.post("/undo", response_model=UndoResponse, response_model_exclude_unset=True)
@writes_outside_samples
def undo_route(req: PathRequest) -> UndoResponse:
    memory_hooks.before(req.path, approve=False)
    snaps = edit_history.list_snapshots(req.path)
    undone_mode = (snaps[-1].label.split(":")[0] or None) if snaps else None
    res = edit_history.undo(req.path)
    if res.success:
        memory_hooks.undone(req.path, undone_mode)
    out = UndoResponse(success=res.success, restored_from=res.restored_from,
                       remaining=res.remaining, error=res.error)
    if res.success:  # a failed undo keeps its v2 shape (no ``route`` key)
        out.route = route_of(req.path, level=req.level)
        out.difficulty = difficulty_of(req.path, level=req.level)
        out.levels = levels_summary(req.path, req.level)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Design memory (v4 Part 27)
# ─────────────────────────────────────────────────────────────────────────────


def memory_summary() -> Dict[str, Any]:
    prefs = memory_store.load_prefs()
    return {"enabled": prefs["enabled"], "patterns_auto_add": prefs["patterns_auto_add"], "rules": prefs["rules"], "stats": prefs["stats"],
            "event_count": prefs["event_count"], "dir": str(memory_store.memory_dir())}


@app.get("/memory")
def get_memory() -> Dict[str, Any]:
    return memory_summary()


@app.put("/memory")
def put_memory(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """``enabled`` toggles memory; ``rules`` (id, text?, confidence?, enabled?) edits rules by
    id and adds unknown ids as user rules; ``delete`` lists rule ids to remove."""
    with memory_store._lock:
        prefs = memory_store.load_prefs()
        if "enabled" in body:
            prefs["enabled"] = bool(body["enabled"])
        if "patterns_auto_add" in body:
            prefs["patterns_auto_add"] = bool(body["patterns_auto_add"])
        rules = {r.get("id"): r for r in prefs["rules"]}
        for r in body.get("rules") or []:
            rid = str(r.get("id") or f"user{len(rules) + 1}")
            cur = rules.setdefault(rid, {"id": rid, "text": "", "confidence": 0.9, "source": "user"})
            if "text" in r:
                cur["text"] = str(r["text"])[:memory_analyze.MAX_RULE_CHARS]
                cur["source"] = "user"  # an edited rule is the user's from now on
            if "confidence" in r:
                cur["confidence"] = max(0.0, min(1.0, float(r["confidence"])))
            if "enabled" in r:
                cur["enabled"] = bool(r["enabled"])
        for rid in body.get("delete") or []:
            if rules.pop(rid, None) is not None and rid not in prefs["dismissed"]:
                prefs["dismissed"].append(rid)  # a deleted stat rule does not come back
        prefs["rules"] = list(rules.values())
        memory_store.save_prefs(prefs)
    return memory_summary()


@app.delete("/memory")
def delete_memory() -> Dict[str, Any]:
    memory_store.reset()
    return memory_summary()


@app.post("/memory/scan")
def scan_memory(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    path = body.get("path")
    if not path:
        raise HTTPException(status_code=422, detail="path is required")
    res = memory_handedit.scan(path)
    if res.get("edited"):  # the user saved the level after GLDtk wrote it: it is kept
        from memory import approve as memory_approve

        memory_approve.approve(path)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Golden route (v3 Part 13)
# ─────────────────────────────────────────────────────────────────────────────


def route_of(path: str | Path, project: Dict[str, Any] | None = None,
             level: str | None = None) -> RouteModel:
    """The golden route of the file at *path*, read like an edit reads it (no LLM call).

    With *project* (the LDtk JSON open in the editor) that JSON is used instead of the file.
    *level* (iid / identifier, None = the first) picks the level of a world.
    """
    try:
        route = (file_route(path, level) if project is None
                 else unsaved_route(path, project, level))
    except Exception as exc:  # never fail a generate / edit because of the overlay
        return RouteModel(success=False, error=f"Route error: {exc}")
    return RouteModel.model_validate(route.as_dict())


def difficulty_of(path: str | Path, fidelity: Any = None, prompt: str | None = None,
                  level: str | None = None) -> DifficultyModel:
    """Per-section difficulty of the file at *path* (no LLM call; v3 Part 15a).

    With *fidelity* (the generate response's report, not yet serialised) the
    sections whose score contradicts a difficulty the *prompt* stated are marked
    ``approximated`` there.  Never raises: a failure is ``success: false``.
    """
    try:
        report = difficulty_of_file(path, level)
        if fidelity is not None:
            apply_to_fidelity(report, fidelity, prompt)
    except Exception as exc:  # never fail a generate / edit because of the metrics
        return DifficultyModel(success=False, error=f"Difficulty error: {exc}")
    # success is set explicitly: /undo excludes unset fields, which would drop a default.
    return DifficultyModel.model_validate({**report.to_dict(), "success": True})


_LEVEL_QUERY = "Level of a world: iid or identifier; None = the first level (v3 Part 17)"


@app.get("/difficulty", response_model=DifficultyModel)
def difficulty_route(path: str = Query(..., description=".ldtk path"),
                     level: str | None = Query(None, description=_LEVEL_QUERY)) -> DifficultyModel:
    return difficulty_of(path, level=level)


@app.get("/route", response_model=RouteModel)
def route_route(path: str = Query(..., description=".ldtk path"),
                level: str | None = Query(None, description=_LEVEL_QUERY)) -> RouteModel:
    return route_of(path, level=level)


@app.post("/route", response_model=RouteModel)
def route_unsaved_route(req: RouteRequest) -> RouteModel:
    return route_of(req.path, req.project, level=req.level)


@app.get("/history", response_model=HistoryResponse)
def history_route(path: str = Query(..., description=".ldtk path"),
                  level: str | None = Query(None, description="Accepted for symmetry; snapshots "
                                                              "are per file (every level)")
                  ) -> HistoryResponse:
    snaps = edit_history.list_snapshots(path)
    return HistoryResponse(snapshots=[HistoryEntry(**s.as_dict()) for s in reversed(snaps)])


@app.post("/chunks", response_model=ChunksResponse)
@writes_outside_samples
@job_route(lambda: ChunksResponse(success=False, error="Cancelled", cancelled=True),
           "Appending chunks")
def chunks_route(req: ChunksRequest) -> ChunksResponse:
    """Append N endless chunks to a world file (v4 Part 25a, ``pipeline/chunks.py``)."""
    from pipeline.chunks import chunks_response
    return chunks_response(req)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def check() -> int:
    """Smoke test for the packaged bundle: everything imported above, assets load."""
    from aesthetic.library import load_library

    lib = load_library()
    print(f"gldtk-server check ok: {len(THEMES)} themes, {len(lib.sprites)} sprites")
    return 0


def _exit_with_parent(pid: int) -> None:
    """Daemon thread: the server never outlives the app that started it, however the app exits."""
    import threading
    import time

    def watch() -> None:
        while True:
            time.sleep(1.0)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                os._exit(0)
            except OSError:
                pass

    threading.Thread(target=watch, daemon=True).start()


def main(argv: Optional[List[str]] = None) -> None:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="GLDtk companion server")
    ap.add_argument("--host", default=os.getenv("GLDTK_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("GLDTK_PORT", "8765")))
    ap.add_argument("--parent-pid", type=int, default=0,
                    help="exit when this process is gone (the packaged app passes its own pid)")
    ap.add_argument("--check", action="store_true", help="import everything, load assets, exit")
    args = ap.parse_args(argv)
    if args.check:
        sys.exit(check())
    print(f"GLDtk companion → http://{args.host}:{args.port}")
    if args.parent_pid:
        _exit_with_parent(args.parent_pid)
    uvicorn.run(app, host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
