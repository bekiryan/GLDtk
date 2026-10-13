"""AI art endpoints (v2 Part 6): generate themes and sprites into the asset library.

Mounted by the server with ``app.include_router(art_routes.router)``.

* ``POST /art/theme  {description, theme_id?, provider?, model?, keywords?, seed?}``
  -> ``{theme_id, sheet_path, quality}``
* ``POST /art/sprite {description, kind, sprite_id?, provider?, model?}``
  -> ``{sprite_id, file, quality}``
* ``GET  /art/library`` -> themes and sprites with their ``generated`` flag.

Tests override the module-level hooks ``image_provider_factory`` (provider name,
model -> ImageProvider) and ``library_root`` (None = the repo's ``assets/``).
Pillow is imported only inside the generation handlers, so the router (and
``GET /art/library``) work without it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/art", tags=["art"])


def _default_image_provider_factory(name: Optional[str], model: Optional[str] = None):
    from art.providers import make_image_provider

    return make_image_provider(name or "gemini", model=model)


# Hooks (module level so tests and the server can swap them).
image_provider_factory: Callable[..., Any] = _default_image_provider_factory
library_root: Optional[Union[str, Path]] = None


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


class ThemeArtRequest(BaseModel):
    description: str = Field(..., min_length=1, description="e.g. 'crystal cavern with glowing purple moss'")
    theme_id: Optional[str] = Field(None, description="None = derived from the description")
    provider: Optional[str] = Field(None, description="image provider; None = gemini")
    model: Optional[str] = Field(None, description="None = provider default (with fallbacks)")
    keywords: Optional[List[str]] = None
    seed: int = 0
    overwrite: bool = False


class ThemeArtResponse(BaseModel):
    theme_id: str
    sheet_path: str
    quality: Dict[str, Any] = Field(default_factory=dict)


class SpriteArtRequest(BaseModel):
    description: str = Field(..., min_length=1)
    kind: str = Field(..., description="Player | Enemy | Pickup | Weapon | Interactive | NPC | ...")
    sprite_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    overwrite: bool = False


class SpriteArtResponse(BaseModel):
    sprite_id: str
    file: str
    quality: Dict[str, Any] = Field(default_factory=dict)


class LibraryThemeInfo(BaseModel):
    id: str
    display_name: str
    sheet_path: str
    keywords: List[str]
    generated: bool
    quality: Optional[Dict[str, Any]] = None


class LibrarySpriteInfo(BaseModel):
    id: str
    kind: str
    file: str
    tags: List[str]
    generated: bool
    quality: Optional[Dict[str, Any]] = None


class LibraryResponse(BaseModel):
    themes: List[LibraryThemeInfo]
    sprites: List[LibrarySpriteInfo]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _provider(name: Optional[str], model: Optional[str]):
    try:
        return image_provider_factory(name, model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _pipeline():
    try:
        from art import pipeline
    except ImportError as exc:  # PillowMissing is an ImportError
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return pipeline


def _provider_error(exc: Exception) -> HTTPException:
    status = getattr(exc, "status", None)
    code = 429 if status == 429 else 502
    return HTTPException(status_code=code, detail=f"Image provider error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints (sync: FastAPI runs them in a worker thread)
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/theme", response_model=ThemeArtResponse)
def generate_theme_endpoint(req: ThemeArtRequest) -> ThemeArtResponse:
    from aesthetic.library import load_library
    from art.providers import ProviderError

    pipeline = _pipeline()
    provider = _provider(req.provider, req.model)
    try:
        entry = pipeline.generate_theme(
            req.description,
            req.theme_id,
            provider,
            keywords=req.keywords,
            seed=req.seed,
            root=library_root,
            overwrite=req.overwrite,
        )
    except ProviderError as exc:
        raise _provider_error(exc) from exc
    except ValueError as exc:  # invalid / duplicate id
        raise HTTPException(status_code=409 if "exists" in str(exc) else 400, detail=str(exc)) from exc
    lib = load_library(library_root)
    return ThemeArtResponse(
        theme_id=entry.id,
        sheet_path=str(lib.theme_sheet_path(entry)),
        quality=entry.quality or {},
    )


@router.post("/sprite", response_model=SpriteArtResponse)
def generate_sprite_endpoint(req: SpriteArtRequest) -> SpriteArtResponse:
    from aesthetic.library import load_library
    from art.providers import ProviderError

    pipeline = _pipeline()
    provider = _provider(req.provider, req.model)
    try:
        entry = pipeline.generate_sprite(
            req.description,
            req.kind,
            req.sprite_id,
            provider,
            root=library_root,
            overwrite=req.overwrite,
        )
    except ProviderError as exc:
        raise _provider_error(exc) from exc
    except ValueError as exc:  # invalid id / kind, duplicate id
        raise HTTPException(status_code=409 if "exists" in str(exc) else 400, detail=str(exc)) from exc
    lib = load_library(library_root)
    return SpriteArtResponse(
        sprite_id=entry.id, file=str(lib.sprite_path(entry)), quality=entry.quality or {}
    )


@router.get("/library", response_model=LibraryResponse)
def library_endpoint() -> LibraryResponse:
    from aesthetic.library import load_library

    lib = load_library(library_root)
    return LibraryResponse(
        themes=[
            LibraryThemeInfo(
                id=t.id,
                display_name=t.display_name,
                sheet_path=str(lib.theme_sheet_path(t)),
                keywords=list(t.keywords),
                generated=t.generated,
                quality=t.quality,
            )
            for t in lib.themes
        ],
        sprites=[
            LibrarySpriteInfo(
                id=s.id,
                kind=s.kind,
                file=str(lib.sprite_path(s)),
                tags=list(s.tags),
                generated=s.generated,
                quality=s.quality,
            )
            for s in lib.sprites
        ],
    )
