"""Entity sprite atlas for GLDtk.

LDtk shows an entity as a rectangle unless the entity def points at a tileset
tile.  This module packs the library's sprites (``assets/sprites``) into one
atlas PNG, a grid of ``tile_size`` cells in manifest order, and describes it
for the LDtk export:

* ``kind_defaults``: every LDtk entity kind -> the cell of its default sprite
  (the entity def's tile);
* ``pick``: ``(kind, hints)`` -> the cell of the library sprite that
  ``find_sprite`` picks for an instance's ``sprite`` / ``description`` hints.

The atlas is rebuilt on every export (it is tiny), so sprites added by the AI
art pipeline show up without a manual step.  Pillow is needed to compose it;
without Pillow ``build_sprite_atlas`` returns None and entities keep their
coloured boxes.

Like the theme sheets, the PNG is written next to the ``.ldtk`` file:
``<ldtk_dir>/gldtk_assets/sprites.png``.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from layout.ldtk_adapter import ENTITY_ATLAS_IDENTIFIER, ENTITY_ATLAS_UID, EntityAtlas, TilesetInfo
from spec.catalog import KINDS

from .library import Library, find_sprite, load_library
from .themes import ASSET_DIR

ATLAS_REL_PATH = f"{ASSET_DIR}/sprites.png"
ATLAS_COLUMNS = 8

# LDtk entity kind -> library sprite kind (the player sprite stands for Start).
_SPRITE_KIND = {"Start": "Player"}


def sprite_kind_for(kind: str) -> str:
    return _SPRITE_KIND.get(kind, kind)


@dataclass
class SpriteAtlas:
    """The packed atlas: PNG bytes plus where each sprite landed."""

    png: bytes
    width: int
    height: int
    tile_size: int
    cells: Dict[str, Tuple[int, int]] = field(default_factory=dict)  # sprite id -> (col, row)
    library: Optional[Library] = None
    rel_path: str = ATLAS_REL_PATH

    def pick(self, kind: str, hints: List[str]) -> Optional[Tuple[int, int]]:
        """Cell of the best library sprite for an entity of *kind* with *hints*."""
        sprite = find_sprite(sprite_kind_for(kind), hints, self.library)
        return self.cells.get(sprite.id) if sprite else None

    def kind_defaults(self) -> Dict[str, Tuple[int, int]]:
        out: Dict[str, Tuple[int, int]] = {}
        for kind in KINDS:
            cell = self.pick(kind, [])
            if cell is not None:
                out[kind] = cell
        return out

    def to_entity_atlas(self) -> EntityAtlas:
        return EntityAtlas(
            tileset=TilesetInfo(
                uid=ENTITY_ATLAS_UID,
                rel_path=self.rel_path,
                px_width=self.width,
                px_height=self.height,
                identifier=ENTITY_ATLAS_IDENTIFIER,
            ),
            tile_size=self.tile_size,
            kind_defaults=self.kind_defaults(),
            pick=self.pick,
        )


def build_sprite_atlas(library: Optional[Library] = None) -> Optional[SpriteAtlas]:
    """Pack every library sprite into an atlas; None if Pillow is unavailable
    or the library has no sprites."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is part of the dev / art extras
        return None
    lib = library or load_library()
    sprites = list(lib.sprites)
    if not sprites:
        return None
    ts = lib.manifest.tile_size
    cols = min(ATLAS_COLUMNS, len(sprites))
    rows = -(-len(sprites) // cols)
    sheet = Image.new("RGBA", (cols * ts, rows * ts), (0, 0, 0, 0))
    cells: Dict[str, Tuple[int, int]] = {}
    for i, sprite in enumerate(sprites):
        col, row = i % cols, i // cols
        img = Image.open(lib.sprite_path(sprite)).convert("RGBA")
        if img.width > ts or img.height > ts:  # fit inside one cell, keep the aspect
            k = min(ts / img.width, ts / img.height)
            img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.NEAREST)
        sheet.paste(img, (col * ts + (ts - img.width) // 2, row * ts + (ts - img.height) // 2))
        cells[sprite.id] = (col, row)
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return SpriteAtlas(
        png=buf.getvalue(), width=sheet.width, height=sheet.height,
        tile_size=ts, cells=cells, library=lib,
    )


def write_sprite_atlas(atlas: SpriteAtlas, ldtk_dir: Union[str, Path]) -> str:
    """Write the atlas to ``<ldtk_dir>/<atlas.rel_path>``; returns the relative path."""
    dest = Path(ldtk_dir) / atlas.rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Unchanged bytes are not rewritten, so LDtk does not hot-reload the image.
    if not (dest.is_file() and dest.read_bytes() == atlas.png):
        dest.write_bytes(atlas.png)
    return atlas.rel_path
