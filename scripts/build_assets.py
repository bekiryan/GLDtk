#!/usr/bin/env python3
"""Rebuild ``assets/`` (theme sheets, sprites, manifest) from the CC0 source sheets.

Run from the repo root:  python scripts/build_assets.py

Inputs : assets/sources/*.png  (Kenney sheets, 16 px tiles, no spacing)
Outputs: assets/themes/*.png, assets/sprites/*.png, assets/manifest.json

Theme sheet layout (5 x 4 tiles, 80 x 64 px):
    row 0: CORNER_TL  EDGE_T  CORNER_TR  CAP_LEFT   CAP_RIGHT
    row 1: EDGE_L     CENTER  EDGE_R     FILL       SINGLE
    row 2: CORNER_BL  EDGE_B  CORNER_BR  HAZARD     -
    row 3: three extra CENTER variants (cols 0-2)

Two ways of getting the 13 solid-block roles:
  * "real3x3": the source pack already contains a bordered 3x3 block (Tiny Town
    grass patch, Tiny Town castle floor). The nine tiles are copied verbatim and
    FILL / CAP_* / SINGLE are composed from halves/quadrants of them.
  * "synth": a seamless Kenney texture tile is used as the fill; the border
    (1 px outline, optional top band such as grass/snow/lava) is drawn on the
    open sides of each role. Ice and lava recolour a Kenney texture with a
    colour ramp.
The spike hazard tile is the 1-Bit Pack spikes recoloured per theme.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

# Load aesthetic/library.py directly: importing the package would load the
# theme registry, which needs the manifest this script is about to (re)write.
_spec = importlib.util.spec_from_file_location("gldtk_library", ROOT / "aesthetic" / "library.py")
_lib = importlib.util.module_from_spec(_spec)
import sys  # noqa: E402

sys.modules["gldtk_library"] = _lib
_spec.loader.exec_module(_lib)
Manifest, SourceEntry, SpriteEntry, ThemeEntry = _lib.Manifest, _lib.SourceEntry, _lib.SpriteEntry, _lib.ThemeEntry

ASSETS = ROOT / "assets"
T = 16

SRC_FILES = {
    "td": ("kenney-tiny-dungeon", "kenney-tiny-dungeon.png"),
    "tt": ("kenney-tiny-town", "kenney-tiny-town.png"),
    "1b": ("kenney-1-bit-pack", "kenney-1-bit-pack.png"),
}
SHEETS = {k: Image.open(ASSETS / "sources" / v[1]).convert("RGBA") for k, v in SRC_FILES.items()}


def tile(src: str, c: int, r: int) -> Image.Image:
    return SHEETS[src].crop((c * T, r * T, (c + 1) * T, (r + 1) * T))


# ── colour helpers ───────────────────────────────────────────────────────────


def tint(img: Image.Image, dark, light) -> Image.Image:
    """Map luminance of *img* onto a dark->light colour ramp (alpha kept)."""
    out = img.copy()
    px = out.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            lum = (0.3 * r + 0.59 * g + 0.11 * b) / 255
            px[x, y] = tuple(int(dark[i] + (light[i] - dark[i]) * lum) for i in range(3)) + (a,)
    return out


def recolor(img: Image.Image, color) -> Image.Image:
    """Silhouette: every non-transparent pixel becomes *color* (shaded by luminance)."""
    out = img.copy()
    px = out.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            k = 0.75 + 0.25 * ((0.3 * r + 0.59 * g + 0.11 * b) / 255)
            px[x, y] = tuple(min(255, int(c * k)) for c in color) + (255,)
    return out


# ── block renderers ──────────────────────────────────────────────────────────

# role -> (north_open, south_open, east_open, west_open)
OPEN = {
    "corner_tl": (1, 0, 0, 1), "edge_t": (1, 0, 0, 0), "corner_tr": (1, 0, 1, 0),
    "cap_left": (1, 1, 0, 1), "cap_right": (1, 1, 1, 0),
    "edge_l": (0, 0, 0, 1), "center": (0, 0, 0, 0), "edge_r": (0, 0, 1, 0),
    "fill": (1, 1, 0, 0), "single": (1, 1, 1, 1),
    "corner_bl": (0, 1, 0, 1), "edge_b": (0, 1, 0, 0), "corner_br": (0, 1, 1, 0),
}
CELL = {
    "corner_tl": (0, 0), "edge_t": (1, 0), "corner_tr": (2, 0), "cap_left": (3, 0), "cap_right": (4, 0),
    "edge_l": (0, 1), "center": (1, 1), "edge_r": (2, 1), "fill": (3, 1), "single": (4, 1),
    "corner_bl": (0, 2), "edge_b": (1, 2), "corner_br": (2, 2), "hazard_tile": (3, 2),
}


def render_synth(fill: Image.Image, openness, outline, top=None, hi=None) -> Image.Image:
    n, s, e, w = openness
    im = fill.copy()
    px = im.load()
    if n and top:
        for y, col in enumerate(top):  # top band rows 1..len(top)
            for x in range(T):
                px[x, y + 1] = col + (255,)
        drip = top[-1]
        for x in range(T):  # ragged lower edge
            if (x * 5 + 3) % 7 < 3:
                px[x, len(top) + 1] = drip + (255,)
    elif n and hi:
        for x in range(T):
            px[x, 1] = hi + (255,)
    for x in range(T):
        if n:
            px[x, 0] = outline + (255,)
        if s:
            px[x, T - 1] = outline + (255,)
    for y in range(T):
        if w:
            px[0, y] = outline + (255,)
        if e:
            px[T - 1, y] = outline + (255,)
    for (a, b, cx, cy) in ((n, w, 0, 0), (n, e, T - 1, 0), (s, w, 0, T - 1), (s, e, T - 1, T - 1)):
        if a and b:
            px[cx, cy] = (0, 0, 0, 0)
    return im


def half(top_img: Image.Image, bottom_img: Image.Image) -> Image.Image:
    out = Image.new("RGBA", (T, T))
    out.paste(top_img.crop((0, 0, T, 8)), (0, 0))
    out.paste(bottom_img.crop((0, 8, T, T)), (0, 8))
    return out


def single_from_corners(tl, tr, bl, br) -> Image.Image:
    out = Image.new("RGBA", (T, T))
    out.paste(tl.crop((0, 0, 8, 8)), (0, 0))
    out.paste(tr.crop((8, 0, T, 8)), (8, 0))
    out.paste(bl.crop((0, 8, 8, T)), (0, 8))
    out.paste(br.crop((8, 8, T, T)), (8, 8))
    return out


def fill_variants(fill: Image.Image) -> list:
    """Three CENTER variants: mirrored / flipped / rotated copies of the fill."""
    return [
        fill.transpose(Image.FLIP_LEFT_RIGHT),
        fill.transpose(Image.FLIP_TOP_BOTTOM),
        fill.transpose(Image.ROTATE_180),
    ]


def roles_real3x3(src: str, c0: int, r0: int) -> dict:
    g = lambda dc, dr: tile(src, c0 + dc, r0 + dr)  # noqa: E731
    roles = {
        "corner_tl": g(0, 0), "edge_t": g(1, 0), "corner_tr": g(2, 0),
        "edge_l": g(0, 1), "center": g(1, 1), "edge_r": g(2, 1),
        "corner_bl": g(0, 2), "edge_b": g(1, 2), "corner_br": g(2, 2),
    }
    roles["fill"] = half(roles["edge_t"], roles["edge_b"])
    roles["cap_left"] = half(roles["corner_tl"], roles["corner_bl"])
    roles["cap_right"] = half(roles["corner_tr"], roles["corner_br"])
    roles["single"] = single_from_corners(roles["corner_tl"], roles["corner_tr"], roles["corner_bl"], roles["corner_br"])
    return roles


def roles_synth(fill, outline, top=None, hi=None) -> dict:
    return {role: render_synth(fill, o, outline, top, hi) for role, o in OPEN.items()}


def make_sheet(roles: dict, hazard: Image.Image, variants: list) -> Image.Image:
    sheet = Image.new("RGBA", (5 * T, 4 * T), (0, 0, 0, 0))
    for role, (c, r) in CELL.items():
        img = hazard if role == "hazard_tile" else roles[role]
        sheet.paste(img, (c * T, r * T))
    for i, v in enumerate(variants):
        sheet.paste(v, (i * T, 3 * T))
    return sheet


SPIKES = tile("1b", 22, 0)

# ── themes ───────────────────────────────────────────────────────────────────
# (id, display, keywords, bg colour, enemy type, enemy rate, builder -> (roles, hazard, fill_for_variants))


def build_themes() -> list:
    themes = []

    def add(tid, name, kws, bg, enemy, rate, roles, hazard_col, fill, src):
        sheet = make_sheet(roles, recolor(SPIKES, hazard_col), fill_variants(fill))
        themes.append((tid, name, kws, bg, enemy, rate, sheet, src))

    # dungeon: Tiny Dungeon grey brick
    fill = tile("td", 4, 3)
    add("dungeon", "Dungeon",
        ["dungeon", "underground", "dark", "stone", "crypt", "tomb", "skeleton", "bone", "cursed",
         "ancient", "catacomb", "labyrinth", "prison", "dungeons", "brick", "torch", "undead", "ruins", "ruin"],
        "#0d0d1a", "Skeleton", 0.6,
        roles_synth(fill, (28, 28, 48), hi=(150, 165, 195)), (200, 210, 225), fill, "kenney-tiny-dungeon")

    # forest: real 3x3 grass/dirt patch from Tiny Town
    r = roles_real3x3("tt", 0, 1)
    add("forest", "Forest",
        ["forest", "jungle", "nature", "grass", "tree", "wood", "woods", "green", "moss", "vine", "leaf",
         "garden", "grove", "outdoor", "wildlife", "meadow", "swamp", "mushroom", "woodland", "village"],
        "#0a1f0a", "Slime", 0.4, r, (130, 80, 50), r["center"], "kenney-tiny-town")

    # sky: Tiny Town roof-scale texture pushed to pale blue/white = scalloped cloud bank,
    # soft white top band, pale-blue outline
    fill = tint(tile("tt", 1, 4), (168, 200, 240), (252, 254, 255))
    add("sky", "Sky",
        ["sky", "cloud", "clouds", "aerial", "floating", "heaven", "wind", "air", "blue", "high", "above",
         "celestial", "aurora", "storm", "island", "islands", "airship", "tower", "rooftop", "rooftops", "lightning"],
        "#1a2e4a", "Harpy", 0.5,
        roles_synth(fill, (140, 175, 225), top=[(255, 255, 255), (255, 255, 255), (232, 242, 255)]),
        (255, 225, 90), fill, "kenney-tiny-town")

    # ice: Tiny Dungeon brick tinted ice-blue, snow top
    fill = tint(tile("td", 4, 3), (60, 115, 190), (235, 250, 255))
    add("ice", "Ice and Snow",
        ["ice", "snow", "frozen", "frost", "winter", "glacier", "cold", "icy", "snowy", "arctic", "tundra",
         "freezing", "blizzard", "polar", "frostbite", "igloo"],
        "#10263d", "Yeti", 0.5,
        roles_synth(fill, (35, 70, 125), top=[(250, 253, 255), (205, 232, 248)]),
        (150, 220, 255), fill, "kenney-tiny-town")

    # desert: Tiny Dungeon sand, light top, brown outline
    fill = tile("td", 1, 4)
    add("desert", "Desert",
        ["desert", "sand", "sandy", "dune", "dunes", "oasis", "arid", "scorching", "pyramid", "cactus",
         "sahara", "canyon", "dry", "hot", "mirage", "sandstone", "egyptian", "scorpion"],
        "#2e1f12", "Scorpion", 0.5,
        roles_synth(fill, (150, 85, 45), top=[(252, 224, 165), (240, 195, 125)]),
        (70, 150, 60), fill, "kenney-tiny-dungeon")

    # lava: brick tinted red-black, glowing orange top
    fill = tint(tile("td", 4, 3), (45, 12, 10), (200, 80, 45))
    add("lava", "Lava and Volcano",
        ["lava", "volcano", "volcanic", "magma", "fire", "fiery", "inferno", "hell", "molten", "ember",
         "embers", "ash", "burning", "flame", "flames", "obsidian", "lavafall", "demon", "hellfire", "scorched"],
        "#2a0a05", "Imp", 0.6,
        roles_synth(fill, (35, 8, 8), top=[(255, 215, 70), (255, 140, 30)]),
        (255, 120, 20), fill, "kenney-tiny-dungeon")

    # castle: real 3x3 crenellated stone block from Tiny Town
    r = roles_real3x3("tt", 0, 8)
    add("castle", "Castle",
        ["castle", "fortress", "keep", "citadel", "rampart", "ramparts", "battlement", "battlements", "knight",
         "royal", "throne", "palace", "stronghold", "siege", "fort", "kingdom", "medieval", "bastion", "gatehouse"],
        "#15192b", "Knight", 0.5, r, (170, 180, 200), r["center"], "kenney-tiny-town")

    # cave: Tiny Town roof-scale texture tinted to dark grey-brown rock scree
    fill = tint(tile("tt", 1, 4), (28, 22, 24), (150, 128, 108))
    add("cave", "Cave",
        ["cave", "cavern", "caverns", "mine", "mines", "mineshaft", "tunnel", "tunnels", "stalactite",
         "stalagmite", "grotto", "rock", "rocky", "cavernous", "burrow", "quarry", "spelunking", "crystal"],
        "#100c0a", "Bat", 0.5,
        roles_synth(fill, (20, 14, 14), hi=(185, 160, 135)), (150, 150, 140), fill, "kenney-tiny-town")
    return themes


# ── sprites ──────────────────────────────────────────────────────────────────
# id, kind, tags, (src, col, row), default?

SPRITES = [
    ("player", "Player", ["player", "hero", "adventurer", "protagonist"], ("td", 1, 7), True),
    ("npc_wizard", "NPC", ["npc", "wizard", "mage", "villager", "elder", "merchant", "sage"], ("td", 0, 7), True),
    ("enemy_skeleton", "Enemy", ["skeleton", "undead", "walker", "bone", "warrior"], ("td", 2, 7), True),
    ("enemy_spider", "Enemy", ["spider", "jumper", "crawler", "arachnid", "jump", "hopper"], ("td", 2, 10), False),
    ("enemy_bat", "Enemy", ["bat", "flyer", "flying", "fly", "winged", "air"], ("td", 0, 10), False),
    ("enemy_turret", "Enemy", ["turret", "shooter", "gargoyle", "ranged", "stationary", "cannon"], ("td", 7, 1), False),
    ("enemy_boss", "Enemy", ["boss", "demon", "monster", "red", "miniboss", "guardian"], ("td", 2, 9), False),
    ("pickup_coin", "Pickup", ["coin", "gold", "money", "treasure", "currency"], ("tt", 9, 7), True),
    ("pickup_gem", "Pickup", ["gem", "jewel", "crystal", "diamond", "sapphire"], ("1b", 23, 4), False),
    ("pickup_health", "Pickup", ["health", "heal", "potion", "red", "life", "medkit", "hp"], ("td", 7, 9), False),
    ("pickup_powerup", "Pickup", ["powerup", "power", "mana", "boost", "blue", "potion", "buff"], ("td", 8, 9), False),
    ("pickup_ammo", "Pickup", ["ammo", "arrow", "arrows", "bolt", "ammunition", "quiver"], ("tt", 11, 9), False),
    ("weapon_sword", "Weapon", ["sword", "blade", "melee", "steel", "slash"], ("td", 10, 8), True),
    ("weapon_bow", "Weapon", ["bow", "ranged", "archer", "arrow"], ("tt", 10, 9), False),
    ("weapon_gun", "Weapon", ["gun", "pistol", "firearm", "blaster", "rifle", "shoot"], ("1b", 37, 9), False),
    ("weapon_staff", "Weapon", ["staff", "wand", "magic", "spell", "mage", "rod"], ("td", 9, 10), False),
    ("interactive_chest", "Interactive", ["chest", "treasure", "loot", "box", "container"], ("td", 5, 7), True),
    ("interactive_door", "Interactive", ["door", "gate", "entrance", "locked", "doorway"], ("td", 9, 3), False),
    ("interactive_lever", "Interactive", ["lever", "pull", "mechanism", "trigger", "pedestal"], ("td", 8, 2), False),
    ("interactive_switch", "Interactive", ["switch", "button", "console", "panel", "plate", "pressure"], ("td", 7, 4), False),
    ("interactive_sign", "Interactive", ["sign", "signpost", "hint", "tutorial", "notice", "board"], ("tt", 11, 6), False),
    ("checkpoint_flag", "Checkpoint", ["checkpoint", "flag", "save", "respawn", "banner"], ("1b", 17, 7), True),
    ("key_silver", "Key", ["key", "silver", "unlock", "lock", "keycard"], ("tt", 9, 9), True),
    ("start_gate", "Start", ["start", "spawn", "entrance", "begin", "doorway"], ("td", 9, 2), True),
    ("exit_door", "Exit", ["exit", "goal", "finish", "end", "door", "portal", "double"], ("td", 10, 3), True),
]

SOURCES = [
    SourceEntry(id="kenney-tiny-dungeon", name="Tiny Dungeon (1.0)", author="Kenney (www.kenney.nl)",
                url="https://kenney.nl/assets/tiny-dungeon", license="CC0-1.0"),
    SourceEntry(id="kenney-tiny-town", name="Tiny Town", author="Kenney (www.kenney.nl)",
                url="https://kenney.nl/assets/tiny-town", license="CC0-1.0"),
    SourceEntry(id="kenney-1-bit-pack", name="1-Bit Pack", author="Kenney (www.kenney.nl)",
                url="https://kenney.nl/assets/1-bit-pack", license="CC0-1.0"),
]
SRC_ID = {"td": "kenney-tiny-dungeon", "tt": "kenney-tiny-town", "1b": "kenney-1-bit-pack"}


def main() -> None:
    (ASSETS / "themes").mkdir(parents=True, exist_ok=True)
    (ASSETS / "sprites").mkdir(parents=True, exist_ok=True)
    theme_entries, sprite_entries = [], []
    for tid, name, kws, bg, enemy, rate, sheet, src in build_themes():
        sheet.save(ASSETS / "themes" / f"{tid}.png", optimize=True)
        theme_entries.append(ThemeEntry(
            id=tid, display_name=name, keywords=kws, background_color=bg,
            sheet=f"themes/{tid}.png",
            variants={"center": [(0, 3), (1, 3), (2, 3)]},
            source=src, generated=False, enemy_type=enemy, enemy_rate=rate))
    for sid, kind, tags, (src, c, r), default in SPRITES:
        tile(src, c, r).save(ASSETS / "sprites" / f"{sid}.png", optimize=True)
        sprite_entries.append(SpriteEntry(
            id=sid, kind=kind, tags=tags, file=f"sprites/{sid}.png", size=(16, 16),
            source=SRC_ID[src], generated=False, default=default))
    manifest = Manifest(version=1, tile_size=T, themes=theme_entries, sprites=sprite_entries, sources=SOURCES)
    (ASSETS / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"built {len(theme_entries)} themes, {len(sprite_entries)} sprites")


if __name__ == "__main__":
    main()
