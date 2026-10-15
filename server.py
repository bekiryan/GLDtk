"""GLDtk Companion Server — backend only.

Run:
    python server.py

The GLDtk panel inside LDtk sends requests to this server.
POST /generate  — run the pipeline and write a .ldtk file.
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
"""

from __future__ import annotations

import collections
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import logging
from fastapi.middleware.cors import CORSMiddleware

from aesthetic import build_aesthetic_layer, copy_aesthetic_assets
from aesthetic.themes import THEMES, Theme, detect_theme
from api import art_routes
from api.models import (
    DifficultyModel,
    EditInfo,
    EditResponse,
    EntityEditRequest,
    GenerateRequest,
    GenerateResponse,
    HistoryEntry,
    HistoryResponse,
    LevelStats,
    LocateRequest,
    LocateResponse,
    PathRequest,
    RectModel,
    RegionEditRequest,
    RouteModel,
    RouteRequest,
    SectionEditRequest,
    UndoResponse,
)
from edit import history as edit_history
from edit.context import Rect
from edit.entities import edit_entities
from edit.locate import locate as locate_text
from edit.outcome import EditOutcome
from edit.region import edit_region, edit_section
from layout import LDtkConfig, LevelMeta, to_ldtk_dict_with_warnings
from layout.route import file_route, unsaved_route
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


load_env_file(Path(__file__).resolve().parent / ".env")


# ─────────────────────────────────────────────────────────────────────────────
# Provider factory  (module-level so tests can swap in a FakeProvider)
# ─────────────────────────────────────────────────────────────────────────────


def default_provider_factory(req: Any) -> LLMProvider:
    """*req* is any request with ``provider`` and ``model`` (generate or edit).

    Gemini with its default model falls back to a local model (``GLDTK_LOCAL_FALLBACK``).
    """
    return make_provider_with_fallback(req.provider, model=req.model)


def provider_notes(provider: Any) -> List[str]:
    """Fallback switches made while answering (e.g. Gemini 429 → local model)."""
    return list(getattr(provider, "notes", None) or [])


provider_factory: Callable[[Any], LLMProvider] = default_provider_factory


app = FastAPI(title="GLDtk Companion", version="0.1.0")
app.include_router(art_routes.router)


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
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


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
    profile_path = resolve_profile_path(path)
    try:
        save_profile(profile, profile_path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write {profile_path}: {exc}")
    return profile.model_dump()


@app.post("/generate", response_model=GenerateResponse)
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

    # ── LLM spec → layout → grid verification → repair ──────────────────
    provider = None
    try:
        provider = provider_factory(req)
        controller = LLMController(
            profile=profile,
            provider=provider,
            max_retries=req.max_retries,
        )
        built = build_level(
            req.description,
            controller,
            profile,
            constraints=constraints or None,
            seed=req.seed,
            size_override=(req.width_tiles, req.height_tiles),
            densify=req.densify,
        )
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
    requirements = [r.text for r in spec.requirements]
    ordered_sections = sorted(spec.sections, key=lambda s: s.order)
    sections = [s.name for s in ordered_sections]

    # ── aesthetic (spec entities only — no random coins / enemies) ───────
    try:
        for band in ir.metadata.get("sections", []):
            band["mapped_theme"] = section_theme(
                str(band["theme"]), req.description, req.theme
            ).identifier
        theme = section_theme(ordered_sections[0].theme, req.description, req.theme)
        result = build_aesthetic_layer(
            description=req.description,
            graph=spec.to_graph(),
            node_layouts=built.spec_layout.node_layouts,
            ir_level=ir,
            theme_override=theme.identifier,
            density=density,
            seed=req.seed,
            custom_enemy_types=req.enemy_types or None,
            place_random_entities=False,
        )
    except Exception as exc:
        return GenerateResponse(success=False, error=f"Aesthetic error: {exc}")

    # ── serialise ─────────────────────────────────────────────────────────
    try:
        out = Path(req.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        # The .ldtk references the theme sheets and the sprite atlas relative to itself.
        copy_aesthetic_assets(result, out.parent)
        edit_history.snapshot(out, edit_history.make_label("generate", req.description))
        level_meta = LevelMeta(
            prompt=req.description,
            requirements=list(spec.requirements),
            game_profile_relpath=os.path.relpath(Path(profile_path).resolve(), out.parent.resolve()),
            spec_json=spec,
        )
        tw, th = profile.player_tiles()
        ldtk_cfg = LDtkConfig(player_px=(tw * profile.tile_size, th * profile.tile_size))
        doc, export_warnings = to_ldtk_dict_with_warnings(
            ir, aesthetic=result.aesthetic, cfg=ldtk_cfg, level_meta=level_meta
        )
        out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    except Exception as exc:
        return GenerateResponse(success=False, error=f"Serialisation error: {exc}")

    # ── stats ─────────────────────────────────────────────────────────────
    entity_counts: Dict[str, int] = collections.Counter(
        e.entity_type for e in ir.entities
    )
    stats = LevelStats(
        theme=result.theme.display_name,
        nodes=len(spec.nodes),
        edges=len(spec.edges),
        entities=dict(entity_counts),
        layout_retries=built.stats["layout_retries"],
        grid_repairs=built.stats["grid_repairs"],
        completeness_reprompts=built.stats.get("completeness_reprompts", 0),
        entities_filled=built.stats.get("entities_filled", 0),
        densify_added=built.stats.get("densify_added", 0),
    )
    preview = ir.ascii_render({"Start": "S", "Exit": "X"})

    # before the fidelity report is serialised: a section that misses its stated difficulty
    # turns its fidelity item into `approximated`
    difficulty = difficulty_of(out, built.fidelity, req.description)
    return GenerateResponse(
        success=True,
        preview_ascii=preview,
        stats=stats,
        output_path=str(out.resolve()),
        requirements=requirements,
        sections=sections,
        profile_path=str(profile_path.resolve()),
        verified=built.report.ok,
        warnings=provider_notes(provider) + built.warnings + export_warnings,
        fidelity=built.fidelity.to_dict() if built.fidelity else None,
        route=route_of(out),
        difficulty=difficulty,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Regional editing + history (Part 8)
# ─────────────────────────────────────────────────────────────────────────────


def _rect(r: RectModel) -> Rect:
    return Rect(r.col, r.row, r.w, r.h)


def _rect_model(r: Rect | None) -> RectModel | None:
    return RectModel(**r.as_dict()) if r is not None else None


def edit_response(out: EditOutcome, path: str) -> EditResponse:
    """An edit outcome in the GenerateResponse shape, plus ``edit``."""
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
        route=route_of(path),
        difficulty=difficulty_of(path),
    )


def _provider_or_error(req: Any):
    try:
        return provider_factory(req), None
    except Exception as exc:
        return None, f"LLM provider error: {exc}"


@app.post("/edit/region", response_model=EditResponse)
def edit_region_route(req: RegionEditRequest) -> EditResponse:
    provider, err = _provider_or_error(req)
    if err:
        return edit_response(EditOutcome(False, "region", error=err, rect=_rect(req.rect)), req.path)
    out = edit_region(req.path, _rect(req.rect), req.prompt, provider, max_retries=req.max_retries,
                      densify=req.densify, seed=req.seed)
    out.warnings[:0] = provider_notes(provider)
    return edit_response(out, req.path)


@app.post("/edit/section", response_model=EditResponse)
def edit_section_route(req: SectionEditRequest) -> EditResponse:
    provider, err = _provider_or_error(req)
    if err:
        return edit_response(EditOutcome(False, "section", error=err), req.path)
    out = edit_section(req.path, req.section_id, req.prompt, provider, max_retries=req.max_retries,
                       densify=req.densify, seed=req.seed)
    out.warnings[:0] = provider_notes(provider)
    return edit_response(out, req.path)


@app.post("/edit/entities", response_model=EditResponse)
def edit_entities_route(req: EntityEditRequest) -> EditResponse:
    provider, err = _provider_or_error(req)
    if err:
        return edit_response(EditOutcome(False, "entities", error=err), req.path)
    out = edit_entities(
        req.path, req.prompt, provider,
        rect=_rect(req.rect) if req.rect else None,
        entity_iids=req.entity_iids or None,
        max_retries=req.max_retries,
    )
    out.warnings[:0] = provider_notes(provider)
    return edit_response(out, req.path)


@app.post("/edit/locate", response_model=LocateResponse)
def edit_locate_route(req: LocateRequest) -> LocateResponse:
    provider, err = _provider_or_error(req)
    if err:
        return LocateResponse(success=False, error=err)
    out = locate_text(req.path, req.text, provider, max_retries=req.max_retries)
    return LocateResponse(success=out.success, rect=_rect_model(out.rect),
                          explanation=out.explanation, error=out.error)


@app.post("/undo", response_model=UndoResponse, response_model_exclude_unset=True)
def undo_route(req: PathRequest) -> UndoResponse:
    res = edit_history.undo(req.path)
    out = UndoResponse(success=res.success, restored_from=res.restored_from,
                       remaining=res.remaining, error=res.error)
    if res.success:  # a failed undo keeps its v2 shape (no ``route`` key)
        out.route = route_of(req.path)
        out.difficulty = difficulty_of(req.path)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Golden route (v3 Part 13)
# ─────────────────────────────────────────────────────────────────────────────


def route_of(path: str | Path, project: Dict[str, Any] | None = None) -> RouteModel:
    """The golden route of the file at *path*, read like an edit reads it (no LLM call).

    With *project* (the LDtk JSON open in the editor) that JSON is used instead of the file.
    """
    try:
        route = file_route(path) if project is None else unsaved_route(path, project)
    except Exception as exc:  # never fail a generate / edit because of the overlay
        return RouteModel(success=False, error=f"Route error: {exc}")
    return RouteModel.model_validate(route.as_dict())


def difficulty_of(path: str | Path, fidelity: Any = None, prompt: str | None = None) -> DifficultyModel:
    """Per-section difficulty of the file at *path* (no LLM call; v3 Part 15a).

    With *fidelity* (the generate response's report, not yet serialised) the
    sections whose score contradicts a difficulty the *prompt* stated are marked
    ``approximated`` there.  Never raises: a failure is ``success: false``.
    """
    try:
        report = difficulty_of_file(path)
        if fidelity is not None:
            apply_to_fidelity(report, fidelity, prompt)
    except Exception as exc:  # never fail a generate / edit because of the metrics
        return DifficultyModel(success=False, error=f"Difficulty error: {exc}")
    return DifficultyModel.model_validate(report.to_dict())


@app.get("/difficulty", response_model=DifficultyModel)
def difficulty_route(path: str = Query(..., description=".ldtk path")) -> DifficultyModel:
    return difficulty_of(path)


@app.get("/route", response_model=RouteModel)
def route_route(path: str = Query(..., description=".ldtk path")) -> RouteModel:
    return route_of(path)


@app.post("/route", response_model=RouteModel)
def route_unsaved_route(req: RouteRequest) -> RouteModel:
    return route_of(req.path, req.project)


@app.get("/history", response_model=HistoryResponse)
def history_route(path: str = Query(..., description=".ldtk path")) -> HistoryResponse:
    snaps = edit_history.list_snapshots(path)
    return HistoryResponse(snapshots=[HistoryEntry(**s.as_dict()) for s in reversed(snaps)])


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.getenv("GLDTK_HOST", "127.0.0.1")
    port = int(os.getenv("GLDTK_PORT", "8765"))
    print(f"GLDtk companion → http://{host}:{port}")
    uvicorn.run("server:app", host=host, port=port, reload=False)
