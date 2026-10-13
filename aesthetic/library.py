"""Asset library for GLDtk: themes (tile sheets) and entity sprites.

The library lives in ``assets/`` and is described by ``assets/manifest.json``::

    assets/
      manifest.json
      LICENSES.md
      themes/<theme_id>.png     normalised 5-column theme sheets (16 px tiles)
      sprites/<sprite_id>.png   entity sprites

Adding a theme or sprite means adding a file plus a manifest entry; no code
changes are needed. ``add_theme`` / ``add_sprite`` do both atomically and are
what the AI asset generation (Part 6) uses.

Pillow is *not* required at runtime: PNG dimensions are read from the header.
"""

from __future__ import annotations

import io
import json
import os
import re
import struct
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, Field, model_validator

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "assets"
MANIFEST_NAME = "manifest.json"

SpriteKind = Literal[
    "Player",
    "Enemy",
    "Pickup",
    "Weapon",
    "Interactive",
    "NPC",
    "Checkpoint",
    "Key",
    "Start",
    "Exit",
]

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


# ─────────────────────────────────────────────────────────────────────────────
# Manifest models
# ─────────────────────────────────────────────────────────────────────────────


class SourceEntry(BaseModel):
    id: str
    name: str
    author: str
    url: str = ""
    license: str = "CC0-1.0"


class ThemeEntry(BaseModel):
    id: str
    display_name: str
    keywords: List[str] = Field(default_factory=list)
    background_color: str = "#1a1a2e"
    sheet: str  # relative to the library root, e.g. "themes/dungeon.png"
    # role value (e.g. "center") -> extra [col, row] cells in the sheet
    variants: Dict[str, List[Tuple[int, int]]] = Field(default_factory=dict)
    source: str
    generated: bool = False
    uid: Optional[int] = None  # explicit LDtk tileset uid; default = 100 + index
    enemy_type: str = "Enemy"
    enemy_rate: float = 0.5


class SpriteEntry(BaseModel):
    id: str
    kind: SpriteKind
    tags: List[str] = Field(default_factory=list)
    file: str  # relative to the library root, e.g. "sprites/coin.png"
    size: Tuple[int, int] = (16, 16)
    source: str
    generated: bool = False
    default: bool = False  # fallback sprite for its kind


class Manifest(BaseModel):
    version: int = 1
    tile_size: int = 16
    themes: List[ThemeEntry] = Field(default_factory=list)
    sprites: List[SpriteEntry] = Field(default_factory=list)
    sources: List[SourceEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_refs(self) -> "Manifest":
        source_ids = {s.id for s in self.sources}
        for coll, label in ((self.themes, "theme"), (self.sprites, "sprite")):
            ids = [e.id for e in coll]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} ids in manifest")
            for e in coll:
                if e.source not in source_ids:
                    raise ValueError(f"{label} {e.id!r}: unknown source {e.source!r}")
        return self


# ─────────────────────────────────────────────────────────────────────────────
# Library
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Library:
    root: Path
    manifest: Manifest

    @property
    def themes(self) -> List[ThemeEntry]:
        return self.manifest.themes

    @property
    def sprites(self) -> List[SpriteEntry]:
        return self.manifest.sprites

    @property
    def sources(self) -> List[SourceEntry]:
        return self.manifest.sources

    def theme_sheet_path(self, theme: ThemeEntry) -> Path:
        return self.root / theme.sheet

    def sprite_path(self, sprite: SpriteEntry) -> Path:
        return self.root / sprite.file

    def get_sprite(self, sprite_id: str) -> Optional[SpriteEntry]:
        return next((s for s in self.sprites if s.id == sprite_id), None)


def _resolve(root: Union[str, Path, None]) -> Path:
    return (Path(root) if root is not None else DEFAULT_ROOT).resolve()


@lru_cache(maxsize=8)
def _load_cached(root: Path) -> Library:
    data = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    return Library(root=root, manifest=Manifest.model_validate(data))


def load_library(root: Union[str, Path, None] = None) -> Library:
    """Load (and cache) the library at *root* (default: the repo's ``assets/``)."""
    return _load_cached(_resolve(root))


def clear_cache() -> None:
    _load_cached.cache_clear()


# ─────────────────────────────────────────────────────────────────────────────
# Sprite lookup
# ─────────────────────────────────────────────────────────────────────────────


def _words(items: Iterable[str]) -> set:
    out: set = set()
    for it in items:
        out.update(re.findall(r"[a-z0-9]+", str(it).lower()))
    return out


def find_sprite(
    kind: str,
    hints: Optional[List[str]] = None,
    library: Optional[Library] = None,
) -> Optional[SpriteEntry]:
    """Best sprite of *kind* for the hint words, else the kind's default.

    Scoring is tag overlap with the words in *hints* (``description`` /
    ``sprite`` fields); the sprite id words count as tags. Ties keep manifest
    order. Returns None only if the library has no sprite of that kind.
    """
    lib = library or load_library()
    cands = [s for s in lib.sprites if s.kind.lower() == kind.lower()]
    if not cands:
        return None
    hint_words = _words(hints or [])
    best, best_score = None, 0
    for s in cands:
        score = len(hint_words & (_words(s.tags) | _words([s.id])))
        if score > best_score:
            best, best_score = s, score
    if best is not None:
        return best
    return next((s for s in cands if s.default), cands[0])


# ─────────────────────────────────────────────────────────────────────────────
# Writers (used by Part 6)
# ─────────────────────────────────────────────────────────────────────────────

PngInput = Union[bytes, str, Path, Any]  # Any: a PIL.Image.Image


def _png_bytes(png: PngInput) -> bytes:
    if isinstance(png, (bytes, bytearray)):
        return bytes(png)
    if isinstance(png, (str, Path)):
        return Path(png).read_bytes()
    buf = io.BytesIO()
    png.save(buf, format="PNG")  # PIL image
    return buf.getvalue()


def png_size(data: bytes) -> Tuple[int, int]:
    """(width, height) from a PNG header, without Pillow."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a PNG file")
    return struct.unpack(">II", data[16:24])


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _write_manifest(root: Path, manifest: Manifest) -> None:
    text = manifest.model_dump_json(indent=2) + "\n"
    _atomic_write(root / MANIFEST_NAME, text.encode("utf-8"))
    clear_cache()


def _ensure_source(manifest: Manifest, source: str, generated: bool) -> None:
    if any(s.id == source for s in manifest.sources):
        return
    if not generated:
        raise ValueError(f"unknown source {source!r}; add it to manifest sources first")
    manifest.sources.append(
        SourceEntry(id=source, name="Generated assets", author="GLDtk", license="generated")
    )


def add_theme(
    theme_id: str,
    display_name: str,
    sheet_png: PngInput,
    keywords: Optional[List[str]] = None,
    background_color: str = "#1a1a2e",
    variants: Optional[Dict[str, List[Tuple[int, int]]]] = None,
    source: str = "generated",
    generated: bool = True,
    enemy_type: str = "Enemy",
    enemy_rate: float = 0.5,
    overwrite: bool = False,
    root: Union[str, Path, None] = None,
) -> ThemeEntry:
    """Write ``themes/<theme_id>.png`` and its manifest entry (PNG first)."""
    if not _ID_RE.match(theme_id):
        raise ValueError(f"invalid theme id {theme_id!r}")
    data = _png_bytes(sheet_png)
    w, h = png_size(data)
    ts = load_library(root).manifest.tile_size
    if w % ts or h % ts or w < 5 * ts or h < 3 * ts:
        raise ValueError(f"theme sheet {w}x{h} must be a multiple of {ts} and at least 5x3 tiles")
    root_p = _resolve(root)
    manifest = Manifest.model_validate(load_library(root_p).manifest.model_dump())
    if any(t.id == theme_id for t in manifest.themes) and not overwrite:
        raise ValueError(f"theme {theme_id!r} already exists")
    _ensure_source(manifest, source, generated)
    entry = ThemeEntry(
        id=theme_id,
        display_name=display_name,
        keywords=[k.lower() for k in (keywords or [])],
        background_color=background_color,
        sheet=f"themes/{theme_id}.png",
        variants=variants or {},
        source=source,
        generated=generated,
        enemy_type=enemy_type,
        enemy_rate=enemy_rate,
    )
    old = next((t for t in manifest.themes if t.id == theme_id), None)
    if old is not None:
        entry.uid = old.uid
        manifest.themes[manifest.themes.index(old)] = entry
    else:
        manifest.themes.append(entry)
    manifest = Manifest.model_validate(manifest.model_dump())
    _atomic_write(root_p / entry.sheet, data)
    _write_manifest(root_p, manifest)
    return entry


def add_sprite(
    sprite_id: str,
    kind: str,
    png: PngInput,
    tags: Optional[List[str]] = None,
    source: str = "generated",
    generated: bool = True,
    overwrite: bool = False,
    root: Union[str, Path, None] = None,
) -> SpriteEntry:
    """Write ``sprites/<sprite_id>.png`` and its manifest entry (PNG first)."""
    if not _ID_RE.match(sprite_id):
        raise ValueError(f"invalid sprite id {sprite_id!r}")
    data = _png_bytes(png)
    w, h = png_size(data)
    root_p = _resolve(root)
    manifest = Manifest.model_validate(load_library(root_p).manifest.model_dump())
    if any(s.id == sprite_id for s in manifest.sprites) and not overwrite:
        raise ValueError(f"sprite {sprite_id!r} already exists")
    _ensure_source(manifest, source, generated)
    entry = SpriteEntry(
        id=sprite_id,
        kind=kind,  # type: ignore[arg-type]  (validated by pydantic)
        tags=[t.lower() for t in (tags or [])],
        file=f"sprites/{sprite_id}.png",
        size=(w, h),
        source=source,
        generated=generated,
    )
    old = next((s for s in manifest.sprites if s.id == sprite_id), None)
    if old is not None:
        manifest.sprites[manifest.sprites.index(old)] = entry
    else:
        manifest.sprites.append(entry)
    manifest = Manifest.model_validate(manifest.model_dump())
    _atomic_write(root_p / entry.file, data)
    _write_manifest(root_p, manifest)
    return entry
