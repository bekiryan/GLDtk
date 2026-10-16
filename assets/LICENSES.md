# Asset licences

All art in this library is derived from **CC0 1.0 (public domain dedication)** packs by Kenney.
Each licence was verified from the `License.txt` shipped inside the downloaded pack
("License: (Creative Commons Zero, CC0) - http://creativecommons.org/publicdomain/zero/1.0/",
"free to use in personal, educational and commercial projects"). Credit is not required.

`scripts/build_assets.py` regenerates everything from the unmodified source sheets in
`assets/sources/` (copied verbatim from each pack's `*packed*` sheet: 16x16 tiles, no spacing).

## kenney-tiny-dungeon - Tiny Dungeon (1.0)

- Author: Kenney (www.kenney.nl), created 2022-07-05
- URL: https://kenney.nl/assets/tiny-dungeon
- Licence: CC0 1.0
- Files used: `Tilemap/tilemap_packed.png` -> `sources/kenney-tiny-dungeon.png`
- Used for: dungeon / desert / lava / cave tile textures, characters, enemies, potions, weapons,
  chest, doors, lever, switch.

## kenney-tiny-town - Tiny Town

- Author: Kenney (www.kenney.nl)
- URL: https://kenney.nl/assets/tiny-town
- Licence: CC0 1.0
- Files used: `Tilemap/tilemap_packed.png` -> `sources/kenney-tiny-town.png`
- Used for: forest grass/dirt block, castle stone block, ice / sky / cave textures, coin, key,
  bow, arrow, sign.

## kenney-1-bit-pack - 1-Bit Pack

- Author: Kenney (www.kenney.nl)
- URL: https://kenney.nl/assets/1-bit-pack
- Licence: CC0 1.0
- Files used: `Tilesheet/colored-transparent_packed.png` -> `sources/kenney-1-bit-pack.png`
- Used for: spike hazard tile (recoloured per theme), gem, gun, checkpoint flag.

## Modifications

Derived assets are recoloured (colour ramp or silhouette tint), mirrored/rotated (centre-tile
variants), or composed from halves/quadrants of source tiles (platform caps, single blocks);
synthetic block borders are drawn 1 px at a time on top of source texture tiles. No resampling is
used. CC0 permits all of this without conditions.

## Considered and rejected

- Kenney "Pixel Platformer": tiles are 18x18 (not a clean scale to 16), so it was not used.

## Pattern library: open-licensed game levels (v4 Part 26d)

The pattern library (`patterns/`) can learn level *structure* (collision grid and entity
positions, no art, no code) from open-licensed games. Nothing from them is stored in git: the
converters in `patterns/sources/` build the user's local index in the app-data folder.

- **SuperTux** (https://github.com/SuperTux/supertux): each level's own `(license ...)` field,
  mostly CC-BY-SA 4.0 (a few GPL 2+ / CC-BY-SA 3.0).
- **Open Surge** (https://github.com/alemart/opensurge): each level's `license` header,
  CC-BY 3.0 / CC-BY 4.0.
- **Frogatto & Friends**: not used; its `data/levels` are CC-BY-NC-SA (non-commercial).

Patterns derived from these levels keep the level's licence and attribution (authors, URL,
origin file). `GET /patterns/attribution` produces the full ATTRIBUTION text for the current
index; `patterns/sources/manifest.json` records the commit and licence decision per folder.
Design notes (`patterns/notes.py`) are LLM-written descriptions of famous games' design
techniques: text only, with no copied geometry.
