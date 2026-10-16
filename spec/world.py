"""WorldOutline — the structured output of the world outline call (v3 Part 17).

A prompt such as *"a 3-level campaign: a forest, then a cave, then a castle
with the final boss"* is first turned into an outline: the world title, one
entry per level (a self-contained ``prompt`` the level pipeline runs on as if
the user had typed it) and the world-level requirements.  The levels share the
project's Game Profile; the outline never changes it.

World requirement checks (flat, like ``RequirementCheck``):

* ``entity_in_level`` — level ``level`` (an outline level id) has at least
  ``min_count`` (null = 1) entities of ``entity_kind`` (of ``subkind`` when
  set: archetype / pickup_kind / interactive_kind / weapon_kind), e.g. "the
  final level has the boss" or "the key is found in level 2";
* ``level_count`` — the world has ``min_count`` levels;
* ``none`` — not checkable (reported as unchecked).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from .level_spec import Difficulty, EntityKind, _inline_refs, _normalise_schema, _SpecModel

MAX_LEVELS = 8

SizeHint = Literal["small", "medium", "large"]
WorldCheckType = Literal["entity_in_level", "level_count", "none"]

# size_hint → the level width (tiles) suggested to the level call
SIZE_HINT_WIDTH: Dict[str, int] = {"small": 60, "medium": 100, "large": 160}


class OutlineLevel(_SpecModel):
    id: str = Field(..., description="Unique level id, lowercase, no spaces (e.g. \"forest\").")
    title: str = Field(..., description="Short level name (e.g. \"Whispering Forest\").")
    prompt: str = Field(
        ...,
        description="A self-contained description of this level, as a user would write it: "
        "its theme, layout, entities and everything the world asks of this level.",
    )
    size_hint: Optional[SizeHint] = Field(None, description="Rough level length; null = your choice.")
    difficulty: Difficulty
    theme: str = Field(..., description='Free-text visual theme, e.g. "forest", "ice cave".')


class WorldCheck(_SpecModel):
    type: WorldCheckType
    level: Optional[str] = Field(None, description="OutlineLevel.id the check applies to.")
    entity_kind: Optional[EntityKind] = None
    subkind: Optional[str] = Field(
        None, description="archetype / pickup_kind / interactive_kind / weapon_kind (e.g. \"boss\")."
    )
    min_count: Optional[int] = Field(None, ge=0, description="Minimum count (null = 1).")


class WorldRequirement(_SpecModel):
    id: str = Field(..., description='"W1", "W2", ...')
    text: str = Field(..., description="One atomic, checkable statement about the world.")
    check: Optional[WorldCheck] = Field(None, description="Structured check; null = not checkable.")


class WorldOutline(_SpecModel):
    title: str = Field(..., description="The world (campaign) title.")
    levels: List[OutlineLevel] = Field(..., min_length=1, max_length=MAX_LEVELS)
    requirements: List[WorldRequirement] = Field(default_factory=list)

    @classmethod
    def json_schema(cls) -> Dict[str, Any]:
        """Provider-portable schema (see ``LevelSpec.json_schema``)."""
        raw = cls.model_json_schema(by_alias=True)
        defs = raw.pop("$defs", {})
        return _normalise_schema(_inline_refs(raw, defs))

    def level_ids(self) -> List[str]:
        return [lv.id for lv in self.levels]


__all__ = [
    "MAX_LEVELS",
    "OutlineLevel",
    "SIZE_HINT_WIDTH",
    "WorldCheck",
    "WorldOutline",
    "WorldRequirement",
]
