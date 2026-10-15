"""Entity catalog: kinds, fields, uids, legacy / unknown name mapping."""

from __future__ import annotations

import typing

from spec.catalog import (
    COMMON_FIELDS,
    ENUMS,
    F_ENTITY_REF,
    F_ENUM,
    KINDS,
    all_uids,
    enum_value_id,
    resolve_kind,
)
from spec.level_spec import EntityKind, SpecEntity

_BASE = {"id", "kind", "anchor", "section", "description", "sprite",
         "place", "position"}  # placement inputs (v3 Part 21), not kind fields


def test_catalog_covers_every_spec_kind():
    assert set(KINDS) == set(typing.get_args(EntityKind))


def test_start_and_exit_keep_their_uids():
    assert KINDS["Start"].uid == 10
    assert KINDS["Exit"].uid == 11


def test_uids_are_unique():
    uids = all_uids()
    assert len(uids) == len(set(uids))
    # layers (1–3) and the default tileset (100) stay free
    assert not {1, 2, 3, 100} & set(uids)


def test_every_spec_entity_field_has_a_home():
    """Each SpecEntity kind field belongs to at least one catalog kind."""
    spec_fields = set(SpecEntity.model_fields) - _BASE
    catalog_fields = {f.name for k in KINDS.values() for f in k.fields}
    assert spec_fields == catalog_fields


def test_common_fields_on_every_kind():
    for kind in KINDS.values():
        names = [f.name for f in kind.all_fields]
        assert len(names) == len(set(names)), kind.kind
        for f in COMMON_FIELDS:
            assert f.name in names
        assert kind.field("description").ftype == "F_Text"
        assert kind.field("sprite").ftype == "F_String"


def test_enum_fields_match_spec_literals():
    for kind in KINDS.values():
        for f in kind.fields:
            if f.ftype == F_ENUM:
                literal = SpecEntity.model_fields[f.name].annotation
                inner = [a for a in typing.get_args(literal) if a is not type(None)][0]
                assert set(ENUMS[f.enum].values) == set(typing.get_args(inner)), f.name
                assert f.default in ENUMS[f.enum].values


def test_field_types():
    assert KINDS["Start"].field("starting_items").is_array
    assert KINDS["Interactive"].field("linked_to").ftype == F_ENTITY_REF
    assert KINDS["Key"].field("opens").ftype == F_ENTITY_REF
    assert KINDS["NPC"].field("dialogue").ftype == "F_Text"
    assert KINDS["Exit"].field("requires_key").ftype == "F_Bool"


def test_sizes():
    assert (KINDS["Start"].width, KINDS["Start"].height) == (16, 32)
    assert KINDS["Enemy"].size_for({"archetype": "boss"}) == (32, 32)
    assert KINDS["Enemy"].size_for({"archetype": "walker"}) == (16, 16)
    assert KINDS["Pickup"].size_for({}) == (16, 16)


def test_enum_value_spelling():
    assert enum_value_id("walker") == "Walker"
    assert ENUMS["EnemyArchetype"].to_ldtk("BOSS") == "Boss"
    assert ENUMS["EnemyArchetype"].from_ldtk("Boss") == "boss"
    assert ENUMS["EnemyArchetype"].to_ldtk("swimmer") is None


def test_exact_kinds_case_insensitive():
    for name in KINDS:
        r = resolve_kind(name.lower())
        assert r.kind.kind == name and r.exact


def test_legacy_names():
    r = resolve_kind("Coin")
    assert r.kind.kind == "Pickup" and r.implied == {"pickup_kind": "coin"} and r.exact
    for name in ("Key", "Enemy", "Checkpoint"):
        assert resolve_kind(name).kind.kind == name


def test_unknown_names_map_to_closest_kind():
    assert resolve_kind("Skeleton").kind.kind == "Enemy"
    assert not resolve_kind("Skeleton").exact
    assert resolve_kind("Bat").implied == {"archetype": "flyer"}
    assert resolve_kind("TreasureChest").implied == {"interactive_kind": "chest"}
    assert resolve_kind("Shopkeeper").kind.kind == "NPC"
    assert resolve_kind("RubyGem").implied == {"pickup_kind": "gem"}
    assert resolve_kind("Bow").kind.kind == "Weapon"
    # nothing matches → Interactive, never silently Enemy
    r = resolve_kind("Spring")
    assert r.kind.kind == "Interactive" and not r.exact


def test_sprite_tile_is_an_internal_tile_field_on_every_kind():
    from spec.catalog import F_TILE, INTERNAL_FIELDS

    assert "sprite_tile" in INTERNAL_FIELDS
    for kind in KINDS.values():
        fs = kind.field("sprite_tile")
        assert fs is not None and fs.ftype == F_TILE and fs.display == "EntityTile"
    # appended after the existing common fields, so older field uids do not shift
    assert [f.name for f in COMMON_FIELDS][-2:] == ["gldtk_meta", "sprite_tile"]
