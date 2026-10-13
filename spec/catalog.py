"""Entity catalog — the single source of truth for every entity kind.

Each :class:`KindSpec` says how one kind looks in LDtk: its identifier, colour,
size, stable def uid and typed field definitions.  ``layout.ldtk_adapter``
turns the catalog into ``defs.entities`` / ``defs.enums`` and
``layout.ldtk_reader`` uses it to decode ``fieldInstances`` again.

Field names follow ``spec.level_spec.SpecEntity`` (``pickup_kind``,
``weapon_kind`` …).  Every kind also carries:

* ``description`` (F_Text) and ``sprite`` (F_String) — user-facing;
* ``spec_id``, ``section``, ``anchor`` (F_String) — GLDtk bookkeeping that
  lets Part 8 map LDtk entities back to the spec;
* ``gldtk_meta`` (F_Text) — a JSON object with every other metadata key the
  IR entity carried, so nothing is lost in the round trip;
* ``sprite_tile`` (F_Tile, shown as the entity's tile) — the library sprite
  picked for this instance; derived on export, never read back.

Uids
----
Entity defs: 10–49 (Start = 10, Exit = 11; 19–49 are free for new kinds).
Enum defs: 500+.  Level fields: 900+.  Entity fields: ``def_uid * 100 + i``
(Start → 1000…, Key → 1800…).  Layers use 1–9 (Entities 2, Collisions 1,
Tiles 3 and 4–9 for further per-theme Tiles layers), the entity sprite atlas
tileset 90 and the theme tilesets 100+.

Enum values are capitalised in LDtk (``walker`` → ``Walker``) because the
editor's default identifier style (``Capitalize``) looks enum values up in
that form; the IR / spec side keeps the lowercase spelling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EnumSpec:
    identifier: str
    uid: int
    values: Tuple[str, ...]  # IR spelling (lowercase)

    def to_ldtk(self, value: Any) -> Optional[str]:
        """IR value → LDtk enum value id, or None when not a member."""
        if value is None:
            return None
        v = str(value).strip().lower()
        return enum_value_id(v) if v in self.values else None

    def from_ldtk(self, value: Any) -> Optional[str]:
        """LDtk enum value id → IR value (case-insensitive), or None."""
        if value is None:
            return None
        v = str(value).strip().lower()
        return v if v in self.values else None


def enum_value_id(value: str) -> str:
    """LDtk spelling of an enum value: first letter upper-cased."""
    return value[:1].upper() + value[1:]


ENUMS: Dict[str, EnumSpec] = {
    e.identifier: e
    for e in (
        EnumSpec("Facing", 500, ("left", "right")),
        EnumSpec("EnemyArchetype", 501, ("walker", "flyer", "jumper", "turret", "boss")),
        EnumSpec("EnemyAttack", 502, ("melee", "ranged", "none")),
        EnumSpec("PickupKind", 503, ("coin", "gem", "health", "ammo", "powerup")),
        EnumSpec("InteractiveKind", 504, ("chest", "door", "lever", "switch", "sign")),
    )
}

# ─────────────────────────────────────────────────────────────────────────────
# Fields
# ─────────────────────────────────────────────────────────────────────────────

F_STRING = "F_String"
F_TEXT = "F_Text"
F_INT = "F_Int"
F_BOOL = "F_Bool"
F_ENUM = "F_Enum"
F_ENTITY_REF = "F_EntityRef"
F_TILE = "F_Tile"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    ftype: str  # one of the F_* constants above
    default: Any = None
    enum: Optional[str] = None  # EnumSpec identifier, for F_ENUM
    is_array: bool = False
    display: str = "Hidden"  # LDtk editorDisplayMode
    doc: Optional[str] = None

    @property
    def enum_spec(self) -> Optional[EnumSpec]:
        return ENUMS[self.enum] if self.enum else None


# Fields every kind has, after its own fields.
COMMON_FIELDS: Tuple[FieldSpec, ...] = (
    FieldSpec("description", F_TEXT, "", doc="Original wording from the prompt."),
    FieldSpec("sprite", F_STRING, "", doc="Free-text sprite hint."),
    FieldSpec("spec_id", F_STRING, None, doc="GLDtk: id of this entity in the level spec."),
    FieldSpec("section", F_STRING, None, doc="GLDtk: section id."),
    FieldSpec("anchor", F_STRING, None, doc="GLDtk: platform node the entity stands on."),
    FieldSpec("gldtk_meta", F_TEXT, None, doc="GLDtk: extra metadata (JSON object)."),
    FieldSpec("sprite_tile", F_TILE, None, display="EntityTile",
              doc="GLDtk: library sprite shown for this entity (a tile of the sprite atlas)."),
)

INTERNAL_FIELDS = frozenset({"spec_id", "section", "anchor", "gldtk_meta", "sprite_tile"})
REF_FIELDS = frozenset({"linked_to", "opens"})


# ─────────────────────────────────────────────────────────────────────────────
# Kinds
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KindSpec:
    kind: str  # spec kind == LDtk identifier
    uid: int
    color: str
    width: int = 16
    height: int = 16
    fields: Tuple[FieldSpec, ...] = ()
    max_count: int = 0  # 0 = unlimited
    resizable: bool = False
    tags: Tuple[str, ...] = ("gldtk",)

    @property
    def identifier(self) -> str:
        return self.kind

    @property
    def all_fields(self) -> Tuple[FieldSpec, ...]:
        return self.fields + COMMON_FIELDS

    def field(self, name: str) -> Optional[FieldSpec]:
        for f in self.all_fields:
            if f.name == name:
                return f
        return None

    def field_uid(self, name: str) -> int:
        for i, f in enumerate(self.all_fields):
            if f.name == name:
                return self.uid * 100 + i
        raise KeyError(f"{self.kind} has no field {name!r}")

    def size_for(self, metadata: Dict[str, object]) -> Tuple[int, int]:
        """Instance size in px (a ``boss`` Enemy is 32×32)."""
        if self.kind == "Enemy" and str(metadata.get("archetype", "")).lower() == "boss":
            return 32, 32
        return self.width, self.height


_FACING = FieldSpec("facing", F_ENUM, "right", enum="Facing", display="ValueOnly")

KINDS: Dict[str, KindSpec] = {
    k.kind: k
    for k in (
        KindSpec(
            "Start", 10, "#00cc44", 16, 32, max_count=1,
            fields=(
                _FACING,
                FieldSpec("starting_items", F_STRING, [], is_array=True),
                FieldSpec("starting_weapon", F_STRING, ""),
            ),
        ),
        KindSpec(
            "Exit", 11, "#ffcc00",
            fields=(FieldSpec("requires_key", F_BOOL, False),),
        ),
        KindSpec(
            "Enemy", 12, "#ff2244", resizable=True,
            fields=(
                FieldSpec("archetype", F_ENUM, "walker", enum="EnemyArchetype", display="ValueOnly"),
                FieldSpec("health", F_INT, 1),
                FieldSpec("damage", F_INT, 1),
                FieldSpec("patrol_range", F_INT, 0, doc="Patrol range in tiles.", display="RadiusGrid"),
                FieldSpec("facing", F_ENUM, "left", enum="Facing"),
                FieldSpec("attack", F_ENUM, "melee", enum="EnemyAttack"),
            ),
        ),
        KindSpec(
            "Pickup", 13, "#ffd700",
            fields=(
                FieldSpec("pickup_kind", F_ENUM, "coin", enum="PickupKind", display="ValueOnly"),
                FieldSpec("value", F_INT, 1),
            ),
        ),
        KindSpec(
            "Weapon", 14, "#ff8800",
            fields=(
                FieldSpec("weapon_kind", F_STRING, "sword", display="ValueOnly"),
                FieldSpec("damage", F_INT, 1),
                FieldSpec("range", F_INT, 1, doc="Range in tiles."),
            ),
        ),
        KindSpec(
            "Interactive", 15, "#a0703c",
            fields=(
                FieldSpec("interactive_kind", F_ENUM, "chest", enum="InteractiveKind", display="ValueOnly"),
                FieldSpec("linked_to", F_ENTITY_REF, None, display="RefLinkBetweenCenters"),
                FieldSpec("contents", F_STRING, ""),
            ),
        ),
        KindSpec(
            "NPC", 16, "#44ddcc",
            fields=(
                FieldSpec("name", F_STRING, "", display="ValueOnly"),
                FieldSpec("dialogue", F_TEXT, ""),
            ),
        ),
        KindSpec("Checkpoint", 17, "#44aaff"),
        KindSpec(
            "Key", 18, "#cc44ff",
            fields=(FieldSpec("opens", F_ENTITY_REF, None, display="RefLinkBetweenCenters"),),
        ),
    )
}

KIND_BY_UID: Dict[int, KindSpec] = {k.uid: k for k in KINDS.values()}

# ─────────────────────────────────────────────────────────────────────────────
# Level fields
# ─────────────────────────────────────────────────────────────────────────────

LEVEL_FIELDS: Tuple[Tuple[FieldSpec, int], ...] = (
    (FieldSpec("gldtk_prompt", F_TEXT, None, doc="GLDtk: the original prompt."), 900),
    (FieldSpec("gldtk_sections", F_TEXT, None, doc="GLDtk: sections (JSON list)."), 901),
    (FieldSpec("gldtk_requirements", F_STRING, [], is_array=True, doc="GLDtk: requirements."), 902),
    (FieldSpec("gldtk_game_profile", F_STRING, None, doc="GLDtk: Game Profile path, relative to the .ldtk file."), 903),
    (FieldSpec("gldtk_version", F_STRING, None, doc="GLDtk: export format version."), 904),
    (FieldSpec("gldtk_spec", F_TEXT, None, doc="GLDtk: the full LevelSpec (JSON)."), 905),
)

GLDTK_VERSION = "2"

# ─────────────────────────────────────────────────────────────────────────────
# Mapping arbitrary entity types to a kind
# ─────────────────────────────────────────────────────────────────────────────

# Legacy aesthetic names → (kind, implied fields)
LEGACY: Dict[str, Tuple[str, Dict[str, object]]] = {
    "coin": ("Pickup", {"pickup_kind": "coin"}),
    "key": ("Key", {}),
    "enemy": ("Enemy", {}),
    "checkpoint": ("Checkpoint", {}),
}

# Keyword → (kind, implied fields); first match wins, checked as substrings.
_KEYWORDS: List[Tuple[Tuple[str, ...], str, Dict[str, object]]] = [
    (("player", "spawn", "hero"), "Start", {}),
    (("exit", "goal", "finish", "portal", "flag"), "Exit", {}),
    (("save", "bonfire", "respawn"), "Checkpoint", {}),
    (("chest",), "Interactive", {"interactive_kind": "chest"}),
    (("door", "gate"), "Interactive", {"interactive_kind": "door"}),
    (("lever",), "Interactive", {"interactive_kind": "lever"}),
    (("switch", "button"), "Interactive", {"interactive_kind": "switch"}),
    (("sign",), "Interactive", {"interactive_kind": "sign"}),
    (("gem", "diamond", "crystal", "jewel"), "Pickup", {"pickup_kind": "gem"}),
    (("heart", "potion", "health", "medkit"), "Pickup", {"pickup_kind": "health"}),
    (("ammo", "arrows", "bullet"), "Pickup", {"pickup_kind": "ammo"}),
    (("powerup", "power-up", "mushroom", "star"), "Pickup", {"pickup_kind": "powerup"}),
    (("coin", "gold", "money"), "Pickup", {"pickup_kind": "coin"}),
    (("sword", "bow", "gun", "staff", "axe", "spear", "dagger", "weapon"), "Weapon", {}),
    (("boss",), "Enemy", {"archetype": "boss"}),
    (("bat", "bird", "harpy", "ghost", "flyer"), "Enemy", {"archetype": "flyer"}),
    (("turret", "cannon"), "Enemy", {"archetype": "turret"}),
    (
        ("enemy", "skeleton", "slime", "goblin", "zombie", "orc", "spider", "monster", "guard", "troll"),
        "Enemy",
        {},
    ),
    (("npc", "villager", "merchant", "shopkeeper", "wizard", "sage", "elder"), "NPC", {}),
]

DEFAULT_KIND = "Interactive"


@dataclass
class KindResolution:
    kind: KindSpec
    implied: Dict[str, object] = field(default_factory=dict)
    exact: bool = True  # False → the original name was approximated
    legacy: bool = False


def resolve_kind(entity_type: str) -> KindResolution:
    """Map any IR ``entity_type`` to a catalog kind.

    Exact kinds map to themselves, the legacy aesthetic names map as documented
    in the module docstring, anything else goes to the closest kind by keyword
    (``Interactive`` when nothing matches) with ``exact=False``.
    """
    name = str(entity_type or "").strip()
    for k in KINDS:
        if k.lower() == name.lower():
            return KindResolution(KINDS[k])
    key = name.lower()
    if key in LEGACY:
        kind, implied = LEGACY[key]
        return KindResolution(KINDS[kind], dict(implied), exact=True, legacy=True)
    for words, kind, implied in _KEYWORDS:
        if any(w in key for w in words):
            return KindResolution(KINDS[kind], dict(implied), exact=False)
    return KindResolution(KINDS[DEFAULT_KIND], {}, exact=False)


def all_uids() -> List[int]:
    """Every uid the catalog uses (entity defs, their fields, enums, level fields)."""
    uids: List[int] = []
    for k in KINDS.values():
        uids.append(k.uid)
        uids += [k.field_uid(f.name) for f in k.all_fields]
    uids += [e.uid for e in ENUMS.values()]
    uids += [uid for _, uid in LEVEL_FIELDS]
    return uids


__all__ = [
    "COMMON_FIELDS",
    "DEFAULT_KIND",
    "ENUMS",
    "EnumSpec",
    "FieldSpec",
    "F_TILE",
    "GLDTK_VERSION",
    "INTERNAL_FIELDS",
    "KIND_BY_UID",
    "KINDS",
    "KindResolution",
    "KindSpec",
    "LEGACY",
    "LEVEL_FIELDS",
    "REF_FIELDS",
    "all_uids",
    "enum_value_id",
    "resolve_kind",
]
