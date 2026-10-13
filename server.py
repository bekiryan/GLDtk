"""GLDtk Companion Server — backend only.

Run:
    python server.py

The GLDtk panel inside LDtk sends requests to this server.
POST /generate  — run the pipeline and write a .ldtk file.
GET  /profile   — read the project's Game Profile (defaults if missing).
PUT  /profile   — validate and write the project's Game Profile.
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

from aesthetic import build_aesthetic_layer
from aesthetic.themes import THEMES, Theme, detect_theme
from api.models import GenerateRequest, GenerateResponse, LevelStats
from layout import to_ldtk_dict
from game.profile import (
    PROFILE_FILENAME,
    GameProfile,
    load_profile,
    profile_path_for,
    save_profile,
)
from llm import LLMController, MaxRetriesExceeded
from llm.providers import LLMProvider, make_provider
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


def default_provider_factory(req: GenerateRequest) -> LLMProvider:
    return make_provider(req.provider, model=req.model)


provider_factory: Callable[[GenerateRequest], LLMProvider] = default_provider_factory


app = FastAPI(title="GLDtk Companion", version="0.1.0")


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
    try:
        controller = LLMController(
            profile=profile,
            provider=provider_factory(req),
            max_retries=req.max_retries,
        )
        built = build_level(
            req.description,
            controller,
            profile,
            constraints=constraints or None,
            seed=req.seed,
            size_override=(req.width_tiles, req.height_tiles),
        )
    except MaxRetriesExceeded as exc:
        return GenerateResponse(
            success=False, error=str(exc), profile_path=str(profile_path)
        )
    except LevelBuildFailed as exc:
        return GenerateResponse(
            success=False,
            error=str(exc),
            profile_path=str(profile_path),
            verified=False,
        )
    except Exception as exc:
        return GenerateResponse(
            success=False, error=f"LLM error: {exc}", profile_path=str(profile_path)
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
                str(band["theme"]), req.description
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
        doc = to_ldtk_dict(ir, aesthetic=result.aesthetic)
        out = Path(req.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
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
    )
    preview = ir.ascii_render({"Start": "S", "Exit": "X"})

    return GenerateResponse(
        success=True,
        preview_ascii=preview,
        stats=stats,
        output_path=str(out.resolve()),
        requirements=requirements,
        sections=sections,
        profile_path=str(profile_path.resolve()),
        verified=built.report.ok,
        warnings=built.warnings,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.getenv("GLDTK_HOST", "127.0.0.1")
    port = int(os.getenv("GLDTK_PORT", "8765"))
    print(f"GLDtk companion → http://{host}:{port}")
    uvicorn.run("server:app", host=host, port=port, reload=False)
