"""LDtk export (``layout.ldtk_adapter``) and read-back (``layout.ldtk_reader``).

Schema conformance uses LDtk's own ``docs/JSON_SCHEMA.json`` (draft-07).  It
lives in ``ldtk-src/`` which is not part of this repo; the test looks for it
next to the repo root (and in parent directories, so git worktrees under
``.claude/worktrees`` find the main checkout's copy), or at
``$GLDTK_LDTK_SCHEMA``.  The schema validates as-is with
``jsonschema.Draft7Validator``: its root ``$ref: "#/LdtkJsonRoot"`` and the
``#/otherTypes/...`` refs are plain JSON pointers, so no adaptation is needed.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from layout import IREntity, IRLevel, TileValue
from layout.ldtk_adapter import (
    AestheticData,
    LDtkConfig,
    LevelMeta,
    TileEntry,
    to_ldtk_dict,
    to_ldtk_dict_with_warnings,
)
from layout.ldtk_reader import read_level
from spec import LevelSpec
from spec.catalog import ENUMS, KINDS, LEVEL_FIELDS, enum_value_id

ROOT = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────


def _find_schema() -> Optional[Path]:
    env = os.environ.get("GLDTK_LDTK_SCHEMA")
    if env and Path(env).is_file():
        return Path(env)
    for base in [ROOT, *ROOT.parents]:
        p = base / "ldtk-src" / "docs" / "JSON_SCHEMA.json"
        if p.is_file():
            return p
    return None


@pytest.fixture(scope="module")
def validator():
    jsonschema = pytest.importorskip("jsonschema")
    path = _find_schema()
    if path is None:
        pytest.skip("ldtk-src/docs/JSON_SCHEMA.json not found (set GLDTK_LDTK_SCHEMA)")
    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def assert_valid(validator, doc: Dict[str, Any]) -> None:
    # Round-trip through JSON text: what LDtk reads is the file, not the dict.
    doc = json.loads(json.dumps(doc))
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    assert not errors, "\n".join(
        f"{list(e.absolute_path)}: {e.message[:200]}" for e in errors[:20]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

SECTIONS = [
    {"id": "meadow", "name": "Meadow", "col_start": 0, "col_end": 19,
     "gravity_scale": 1.0, "theme": "forest", "difficulty": "easy"},
    {"id": "moon", "name": "Moon Base", "col_start": 20, "col_end": 39,
     "gravity_scale": 0.5, "theme": "sky", "difficulty": "hard"},
]


def full_ir() -> IRLevel:
    """Every kind, every field type, refs (good and missing), legacy + unknown names."""
    ir = IRLevel.empty("Level_0", 40, 20)
    ir.fill_rect(0, 18, 40, 2, TileValue.SOLID)
    ir.fill_rect(10, 17, 3, 1, TileValue.HAZARD)
    ir.metadata["sections"] = copy.deepcopy(SECTIONS)

    def e(kind: str, col: int, sid: str, **fields: Any) -> IREntity:
        meta: Dict[str, Any] = {"spec_id": sid, "section": "meadow" if col < 20 else "moon",
                                "anchor": "ground", "description": f"the {sid}", "sprite": ""}
        meta.update(fields)
        return IREntity(kind, col, 17, meta)

    ir.entities = [
        e("Start", 1, "player", facing="right", starting_items=["torch", "rope"],
          starting_weapon="sword", sprite="knight"),
        e("Exit", 38, "door_exit", requires_key=True),
        e("Enemy", 5, "skeleton", archetype="walker", health=3, damage=1, patrol_range=4,
          facing="left", attack="melee"),
        e("Enemy", 30, "dragon", archetype="boss", health=20, damage=3, attack="ranged"),
        e("Pickup", 7, "gem1", pickup_kind="gem", value=5),
        e("Weapon", 8, "bow", weapon_kind="bow", damage=2, range=6),
        e("Interactive", 14, "lever1", interactive_kind="lever", linked_to="gate"),
        e("Interactive", 16, "gate", interactive_kind="door", contents=""),
        e("Interactive", 18, "chest", interactive_kind="chest", contents="gold \"coins\"",
          linked_to="nowhere"),
        e("NPC", 22, "sage", name="Old Sage", dialogue='Beware!\nThe moon "bites".'),
        e("Checkpoint", 25, "flag"),
        e("Key", 27, "key1", opens="door_exit"),
    ]
    return ir


def full_meta(spec: Optional[LevelSpec] = None) -> LevelMeta:
    return LevelMeta(
        prompt="A meadow, then a moon base.\nBring a key — \"carefully\".",
        requirements=["There is a meadow.", "The moon has low gravity."],
        game_profile_relpath="gldtk.game.json",
        spec_json=spec,
    )


@pytest.fixture
def valid_spec(valid_spec_dict) -> LevelSpec:
    return LevelSpec.model_validate(valid_spec_dict)


def _all_instances(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        ei
        for li in doc["levels"][0]["layerInstances"]
        for ei in li["entityInstances"]
    ]


def _strip(meta: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in meta.items() if k != "iid"}


# ─────────────────────────────────────────────────────────────────────────────
# Schema conformance
# ─────────────────────────────────────────────────────────────────────────────


def test_full_export_validates(validator, valid_spec):
    doc, warnings = to_ldtk_dict_with_warnings(
        full_ir(), aesthetic=AestheticData(tile_entries=[TileEntry(0, 18, 0, 0)]),
        level_meta=full_meta(valid_spec),
    )
    assert_valid(validator, doc)
    assert any("nowhere" in w for w in warnings)


def test_export_without_meta_or_aesthetic_validates(validator):
    assert_valid(validator, to_ldtk_dict(full_ir()))


def test_legacy_v1_ir_exports_and_validates(validator):
    """Start/Exit markers plus Coin/Key/Enemy/Checkpoint from the aesthetic pass."""
    ir = IRLevel.empty("Level_0", 30, 15)
    ir.fill_rect(0, 14, 30, 1, TileValue.SOLID)
    ir.entities = [
        IREntity("Start", 1, 13),
        IREntity("Exit", 28, 13),
        IREntity("Coin", 5, 13, {"value": 1, "difficulty": 0.25}),
        IREntity("Key", 8, 13, {"unlocks": "exit"}),
        IREntity("Enemy", 12, 13, {"difficulty": 0.5, "patrol": True}),
        IREntity("Checkpoint", 15, 13, {"difficulty": 0.5}),
        IREntity("Skeleton", 18, 13),
    ]
    doc, warnings = to_ldtk_dict_with_warnings(ir, aesthetic=AestheticData())
    assert_valid(validator, doc)
    ids = [ei["__identifier"] for ei in _all_instances(doc)]
    assert ids == ["Start", "Exit", "Pickup", "Key", "Enemy", "Checkpoint", "Enemy"]
    coin = _all_instances(doc)[2]
    fields = {f["__identifier"]: f for f in coin["fieldInstances"]}
    assert fields["pickup_kind"]["__value"] == "Coin"
    assert fields["value"]["__value"] == 1
    assert any("Skeleton" in w for w in warnings)

    back = read_level(doc)
    assert back.version == "2" and back.prompt is None and back.spec is None
    assert back.entities[2].metadata["difficulty"] == 0.25
    assert back.entities[3].metadata["unlocks"] == "exit"
    assert back.entities[6].metadata["original_type"] == "Skeleton"


def test_v1_positional_signature_still_works():
    ir = full_ir()
    doc = to_ldtk_dict(ir, LDtkConfig(), AestheticData(bg_color="#123456"))
    assert doc["bgColor"] == "#123456"
    assert doc["defs"]["tilesets"][0]["uid"] == 100


# ─────────────────────────────────────────────────────────────────────────────
# Field types
# ─────────────────────────────────────────────────────────────────────────────


def test_defs():
    doc = to_ldtk_dict(full_ir())
    defs = doc["defs"]
    assert [d["identifier"] for d in defs["entities"]] == list(KINDS)
    assert {d["identifier"] for d in defs["enums"]} == set(ENUMS)
    assert [f["identifier"] for f in defs["levelFields"]] == [fs.name for fs, _ in LEVEL_FIELDS]
    start = next(d for d in defs["entities"] if d["identifier"] == "Start")
    assert (start["width"], start["height"]) == (16, 32)
    types = {f["identifier"]: (f["type"], f["__type"]) for f in start["fieldDefs"]}
    assert types["facing"] == (f"F_Enum({ENUMS['Facing'].uid})", "LocalEnum.Facing")
    assert types["starting_items"] == ("F_String", "Array<String>")
    assert types["description"] == ("F_Text", "String")
    key = next(d for d in defs["entities"] if d["identifier"] == "Key")
    assert {f["identifier"]: f["type"] for f in key["fieldDefs"]}["opens"] == "F_EntityRef"
    all_uids = [d["uid"] for d in defs["entities"] + defs["enums"] + defs["levelFields"] + defs["layers"]]
    all_uids += [f["uid"] for d in defs["entities"] for f in d["fieldDefs"]]
    assert len(all_uids) == len(set(all_uids))
    assert doc["nextUid"] > max(all_uids)


def test_every_instance_has_every_field_def():
    doc = to_ldtk_dict(full_ir())
    defs = {d["uid"]: d for d in doc["defs"]["entities"]}
    enum_values = {
        f"LocalEnum.{e['identifier']}": {v["id"] for v in e["values"]} for e in doc["defs"]["enums"]
    }
    for ei in _all_instances(doc):
        d = defs[ei["defUid"]]
        assert ei["__identifier"] == d["identifier"]
        assert ei["__tags"] == d["tags"]
        assert ei["__smartColor"] == d["color"]
        assert [f["defUid"] for f in ei["fieldInstances"]] == [f["uid"] for f in d["fieldDefs"]]
        for fi, fd in zip(ei["fieldInstances"], d["fieldDefs"]):
            assert fi["__identifier"] == fd["identifier"]
            assert fi["__type"] == fd["__type"]
            assert isinstance(fi["realEditorValues"], list)
            if fi["__type"] in enum_values:
                assert fi["__value"] in enum_values[fi["__type"]]
            if fd["isArray"]:
                assert isinstance(fi["__value"], list)
                assert len(fi["realEditorValues"]) == len(fi["__value"])


def test_values_and_defaults():
    doc = to_ldtk_dict(full_ir())
    inst = {
        next(f["__value"] for f in ei["fieldInstances"] if f["__identifier"] == "spec_id"): ei
        for ei in _all_instances(doc)
    }

    def fi(sid: str, name: str) -> Dict[str, Any]:
        return next(f for f in inst[sid]["fieldInstances"] if f["__identifier"] == name)

    assert fi("skeleton", "health")["__value"] == 3
    assert fi("skeleton", "health")["realEditorValues"] == [{"id": "V_Int", "params": [3]}]
    assert fi("skeleton", "archetype")["realEditorValues"] == [{"id": "V_String", "params": ["Walker"]}]
    # dragon: patrol_range absent → default value, null editor value
    assert fi("dragon", "patrol_range")["__value"] == 0
    assert fi("dragon", "patrol_range")["realEditorValues"] == [None]
    assert fi("dragon", "facing")["__value"] == "Left"
    assert (inst["dragon"]["width"], inst["dragon"]["height"]) == (32, 32)
    assert fi("door_exit", "requires_key")["realEditorValues"] == [{"id": "V_Bool", "params": [True]}]
    assert fi("player", "starting_items")["__value"] == ["torch", "rope"]
    assert fi("player", "starting_items")["realEditorValues"] == [
        {"id": "V_String", "params": ["torch"]}, {"id": "V_String", "params": ["rope"]}]
    # F_Text: newline escaped the way LDtk escapes it
    assert fi("sage", "dialogue")["realEditorValues"][0]["params"][0] == 'Beware!\\nThe moon "bites".'


def test_entity_refs_point_at_real_iids():
    doc, warnings = to_ldtk_dict_with_warnings(full_ir())
    level = doc["levels"][0]
    ent_layer = next(li for li in level["layerInstances"] if li["__type"] == "Entities")
    by_iid = {ei["iid"]: ei for ei in ent_layer["entityInstances"]}
    assert len(by_iid) == len(ent_layer["entityInstances"])
    refs = []
    for ei in ent_layer["entityInstances"]:
        for f in ei["fieldInstances"]:
            if f["__type"] == "EntityRef" and f["__value"] is not None:
                v = f["__value"]
                assert set(v) == {"entityIid", "layerIid", "levelIid", "worldIid"}
                assert v["entityIid"] in by_iid
                assert v["layerIid"] == ent_layer["iid"]
                assert v["levelIid"] == level["iid"]
                assert v["worldIid"] == doc["dummyWorldIid"]
                assert f["realEditorValues"] == [{"id": "V_String", "params": [v["entityIid"]]}]
                target = by_iid[v["entityIid"]]
                tsid = next(x["__value"] for x in target["fieldInstances"] if x["__identifier"] == "spec_id")
                refs.append((f["__identifier"], tsid))
    assert sorted(refs) == [("linked_to", "gate"), ("opens", "door_exit")]
    # the chest's ref to a missing entity is null, with a warning
    assert [w for w in warnings if "nowhere" in w]


def test_level_fields(valid_spec):
    doc = to_ldtk_dict(full_ir(), level_meta=full_meta(valid_spec))
    fields = {f["__identifier"]: f for f in doc["levels"][0]["fieldInstances"]}
    assert set(fields) == {fs.name for fs, _ in LEVEL_FIELDS}
    assert fields["gldtk_version"]["__value"] == "2"
    assert fields["gldtk_game_profile"]["__value"] == "gldtk.game.json"
    assert fields["gldtk_requirements"]["__type"] == "Array<String>"
    assert fields["gldtk_requirements"]["__value"] == ["There is a meadow.", "The moon has low gravity."]
    assert json.loads(fields["gldtk_sections"]["__value"]) == SECTIONS
    assert LevelSpec.model_validate_json(fields["gldtk_spec"]["__value"]) == valid_spec


# ─────────────────────────────────────────────────────────────────────────────
# Round trip
# ─────────────────────────────────────────────────────────────────────────────


def test_round_trip(tmp_path, valid_spec):
    ir = full_ir()
    doc = to_ldtk_dict(ir, aesthetic=AestheticData(), level_meta=full_meta(valid_spec))
    path = tmp_path / "level.ldtk"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    back = read_level(path)
    assert back.grid == ir.grid
    assert (back.width_tiles, back.height_tiles, back.tile_size) == (40, 20, 16)
    assert back.version == "2"
    assert back.prompt == full_meta().prompt
    assert back.requirements == full_meta().requirements
    assert back.game_profile_relpath == "gldtk.game.json"
    assert back.sections == SECTIONS
    assert back.spec == valid_spec

    assert len(back.entities) == len(ir.entities)
    for orig, got in zip(ir.entities, back.entities):
        assert got.entity_type == orig.entity_type
        assert (got.col, got.row) == (orig.col, orig.row)
        expected = dict(orig.metadata)
        if expected["spec_id"] == "chest":
            del expected["linked_to"]  # unresolved ref was written as null
        assert _strip(got.metadata) == expected, orig.metadata["spec_id"]
        assert got.metadata["iid"]


def test_reexport_keeps_iids():
    doc1 = to_ldtk_dict(full_ir())
    back = read_level(doc1)
    doc2 = to_ldtk_dict(back.to_ir())
    iids1 = [ei["iid"] for ei in _all_instances(doc1)]
    iids2 = [ei["iid"] for ei in _all_instances(doc2)]
    assert iids1 == iids2
    assert read_level(doc2).entities[6].metadata["linked_to"] == "gate"


def test_include_defaults():
    back = read_level(to_ldtk_dict(full_ir()), include_defaults=True)
    dragon = next(e for e in back.entities if e.metadata.get("spec_id") == "dragon")
    assert dragon.metadata["patrol_range"] == 0
    assert dragon.metadata["facing"] == "left"


def test_round_trip_of_real_pipeline_output(validator, valid_spec_dict):
    """Part 3 output (layout_spec + place_spec_entities) exports, validates and reads back."""
    from layout.spec_entities import place_spec_entities
    from layout.spec_layout import layout_spec

    valid_spec_dict["entities"] += [
        {"id": "key1", "kind": "Key", "anchor": "p2", "section": "meadow",
         "description": "a key", "sprite": "", "opens": "door"},
        {"id": "lever", "kind": "Interactive", "anchor": "p3", "section": "dungeon",
         "description": "a lever", "sprite": "", "interactive_kind": "lever",
         "linked_to": "chest"},
    ]
    valid_spec = LevelSpec.model_validate(valid_spec_dict)
    sl = layout_spec(valid_spec)
    place_spec_entities(valid_spec, sl)
    ir = sl.ir
    doc, warnings = to_ldtk_dict_with_warnings(ir, level_meta=LevelMeta(prompt="p", spec_json=valid_spec))
    assert_valid(validator, doc)
    back = read_level(doc)
    assert back.grid == ir.grid
    assert back.sections == ir.metadata["sections"]
    assert back.spec == valid_spec
    assert [e.entity_type for e in back.entities] == [e.entity_type for e in ir.entities]
    for orig, got in zip(ir.entities, back.entities):
        assert _strip(got.metadata) == orig.metadata
    assert not warnings
    refs = {e.metadata["spec_id"]: e.metadata for e in back.entities}
    assert refs["key1"]["opens"] == "door"
    assert refs["lever"]["linked_to"] == "chest"


# ─────────────────────────────────────────────────────────────────────────────
# Hand-edit tolerance
# ─────────────────────────────────────────────────────────────────────────────


def test_hand_edited_file(valid_spec):
    doc = to_ldtk_dict(full_ir(), level_meta=full_meta(valid_spec))
    level = doc["levels"][0]
    # user deleted some GLDtk level fields
    level["fieldInstances"] = [
        f for f in level["fieldInstances"] if f["__identifier"] not in ("gldtk_spec", "gldtk_sections")
    ]
    ent_layer = next(li for li in level["layerInstances"] if li["__type"] == "Entities")
    # user deleted fields from an entity, and edited a value in the editor
    skel = ent_layer["entityInstances"][2]
    skel["fieldInstances"] = [f for f in skel["fieldInstances"]
                              if f["__identifier"] not in ("spec_id", "gldtk_meta", "damage")]
    hp = next(f for f in skel["fieldInstances"] if f["__identifier"] == "health")
    hp["__value"], hp["realEditorValues"] = 9, [{"id": "V_Int", "params": [9]}]
    # user added a custom entity def and instance (with a ref to the gate)
    gate_iid = ent_layer["entityInstances"][7]["iid"]
    doc["defs"]["entities"].append(
        dict(doc["defs"]["entities"][0], identifier="Spring", uid=4000, fieldDefs=[]))
    ent_layer["entityInstances"].append({
        "__identifier": "Spring", "__grid": [3, 17], "__pivot": [0.5, 1], "__tags": [],
        "__tile": None, "__smartColor": "#ffffff", "iid": "spring-iid", "width": 16,
        "height": 16, "defUid": 4000, "px": [56, 288],
        "fieldInstances": [
            {"__identifier": "power", "__type": "Int", "__value": 7, "__tile": None,
             "defUid": 4001, "realEditorValues": [{"id": "V_Int", "params": [7]}]},
            {"__identifier": "target", "__type": "EntityRef", "__tile": None, "defUid": 4002,
             "__value": {"entityIid": gate_iid, "layerIid": "x", "levelIid": "y", "worldIid": "z"},
             "realEditorValues": [{"id": "V_String", "params": [gate_iid]}]},
        ],
    })
    # a hand-placed entity of a known kind with no GLDtk fields at all
    ent_layer["entityInstances"].append({
        "__identifier": "Pickup", "__grid": [4, 17], "__pivot": [0.5, 1], "__tags": [],
        "__tile": None, "__smartColor": "#ffffff", "iid": "hand-coin", "width": 16,
        "height": 16, "defUid": 13, "px": [72, 288], "fieldInstances": [],
    })

    back = read_level(json.loads(json.dumps(doc)))
    assert back.spec is None and back.sections == []
    assert back.prompt == full_meta().prompt
    skel_back = back.entities[2]
    assert skel_back.entity_type == "Enemy"
    assert skel_back.metadata["health"] == 9
    assert "spec_id" not in skel_back.metadata and "damage" not in skel_back.metadata
    spring = back.entities[-2]
    assert spring.entity_type == "Spring"
    assert spring.metadata == {"power": 7, "target": "gate", "iid": "spring-iid"}
    coin = back.entities[-1]
    assert coin.entity_type == "Pickup" and (coin.col, coin.row) == (4, 17)
    assert coin.metadata == {"description": "", "sprite": "", "iid": "hand-coin"}


def test_v1_file_without_gldtk_fields():
    """A v1 export: no level fields, no entity fields, legacy Coin def."""
    ir = IRLevel.empty("Level_0", 30, 15)
    ir.fill_rect(0, 14, 30, 1, TileValue.SOLID)
    ir.entities = [IREntity("Start", 1, 13), IREntity("Exit", 28, 13)]
    doc = to_ldtk_dict(ir)
    level = doc["levels"][0]
    level["fieldInstances"] = []
    doc["defs"]["levelFields"] = []
    for li in level["layerInstances"]:
        for ei in li["entityInstances"]:
            ei["fieldInstances"] = []
        if li["__type"] == "Entities":
            li["entityInstances"].append({
                "__identifier": "Coin", "__grid": [5, 13], "__pivot": [0.5, 1], "__tags": [],
                "__tile": None, "__smartColor": "#ffd700", "iid": "c1", "width": 16,
                "height": 16, "defUid": 20, "px": [88, 224], "fieldInstances": [],
            })
    back = read_level(doc)
    assert back.version is None and back.prompt is None and back.spec is None
    assert back.requirements == [] and back.sections == [] and back.game_profile_relpath is None
    assert [e.entity_type for e in back.entities] == ["Start", "Exit", "Coin"]
    assert back.grid[14][0] == TileValue.SOLID


def test_reader_survives_ldtk_resave_escaping(valid_spec):
    """LDtk re-saves strings escaped twice; JSON text fields still parse."""
    doc = to_ldtk_dict(full_ir(), level_meta=full_meta(valid_spec))
    for f in doc["levels"][0]["fieldInstances"]:
        if f["__identifier"] in ("gldtk_spec", "gldtk_sections"):
            v = f["realEditorValues"][0]["params"][0]
            f["realEditorValues"][0]["params"][0] = v.replace("\\", "\\\\")
    back = read_level(doc)
    assert back.spec == valid_spec
    assert back.sections == SECTIONS


def test_enum_value_ids_use_ldtk_identifier_style():
    """Enum values are capitalised (LDtk's default identifier style)."""
    doc = to_ldtk_dict(full_ir())
    for e in doc["defs"]["enums"]:
        for v in e["values"]:
            assert v["id"] == enum_value_id(v["id"].lower())
            assert v["id"][0].isupper()
