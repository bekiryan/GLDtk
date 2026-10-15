"""Game Profile — physics and player abilities belong to the *game*.

A per-project ``gldtk.game.json`` (next to the ``.ldtk`` file) holds the
physics constants, the player's abilities and the player's look.  The level
generator only reads it; a level may only scale gravity per section
(``Section.gravity_scale``).

Derived limits (Y-up, g = gravity, v = jump_v, vmax = run_speed)
----------------------------------------------------------------
  max jump height      h  = v² / 2g
  flat jump reach      R  = vmax · 2v / g
  double jump height   h2 = v1²/2g + v2²/2g
  dash reach           R + distance_px
  wall jump envelope   Δy ≤ h + max_climb_px,  |Δx| ≤ R + push_px

Heights and reaches scale with 1 / gravity_scale (g_eff = g · gravity_scale).

Design comfort limits (``design``, v3 Part 20)
-----------------------------------------------
Physics says what the player *can* do; ``design`` says what a normal move
*should* be: a hop clears at most ``comfort_hop_tiles`` empty columns, rises
at most ``comfort_rise_tiles`` rows, and a fall drops at most
``comfort_drop_tiles`` rows.  The golden route prefers such moves
(``layout/route.py``) and the comfort pass (``layout/comfort.py``) adds
platforms until the golden path is made of them (outside parkour sections).
A profile without ``design`` gets the defaults.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import List, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError as PydanticValidationError

from symbolic.physics import (
    double_jump_max_time,
    flat_jump_reach,
    max_jump_height,
)

PROFILE_FILENAME = "gldtk.game.json"


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


class _ProfileModel(BaseModel):
    # Strict: a typo in a hand-edited profile should be an error, not ignored.
    model_config = ConfigDict(extra="forbid")


class Physics(_ProfileModel):
    gravity: float = Field(980.0, gt=0, description="Downward acceleration, px/s².")
    jump_v: float = Field(600.0, gt=0, description="Jump launch speed, px/s.")
    run_speed: float = Field(
        200.0, gt=0, description="Cap on |vx| for walking and air movement, px/s."
    )
    walk_step_px: float = Field(
        8.0, ge=0, description="Max |Δy| a Walk edge may cover, px."
    )


class DoubleJump(_ProfileModel):
    enabled: bool = False
    jump_v: float = Field(500.0, gt=0, description="Second (mid-air) jump speed, px/s.")


class Dash(_ProfileModel):
    enabled: bool = False
    distance_px: float = Field(64.0, ge=0, description="Horizontal dash distance, px.")
    air: bool = Field(True, description="The dash can be used mid-air.")


class WallJump(_ProfileModel):
    enabled: bool = False
    jump_v: float = Field(550.0, gt=0, description="Wall kick speed, px/s.")
    max_climb_px: float = Field(
        160.0, ge=0, description="Max extra height gained by wall jumping, px."
    )
    push_px: float = Field(48.0, ge=0, description="Extra horizontal reach, px.")


class Abilities(_ProfileModel):
    double_jump: DoubleJump = Field(default_factory=DoubleJump)
    dash: Dash = Field(default_factory=Dash)
    wall_jump: WallJump = Field(default_factory=WallJump)

    def enabled_names(self) -> List[str]:
        return [
            name
            for name in ("double_jump", "dash", "wall_jump")
            if getattr(self, name).enabled
        ]


class Player(_ProfileModel):
    description: str = ""
    sprite: str = ""
    width_px: float = Field(12.0, gt=0, description="Player hitbox width, px.")
    height_px: float = Field(24.0, gt=0, description="Player hitbox height, px.")


class Design(_ProfileModel):
    comfort_hop_tiles: int = Field(
        4, ge=1, le=30, description="Horizontal gap (empty columns) of a normal jump, tiles."
    )
    comfort_rise_tiles: int = Field(
        3, ge=1, le=30, description="How far a normal jump goes up, tiles."
    )
    comfort_drop_tiles: int = Field(
        6, ge=1, le=60, description="How far a normal fall goes down, tiles."
    )

    def is_comfortable(self, gap: float, rise: float, drop: float) -> bool:
        """Is a move clearing *gap* columns, rising *rise* and dropping *drop* rows normal?"""
        return (gap <= self.comfort_hop_tiles and rise <= self.comfort_rise_tiles
                and drop <= self.comfort_drop_tiles)


class GameProfile(_ProfileModel):
    version: int = 1
    name: str = "Untitled game"
    tile_size: int = Field(16, gt=0)
    physics: Physics = Field(default_factory=Physics)
    abilities: Abilities = Field(default_factory=Abilities)
    player: Player = Field(default_factory=Player)
    design: Design = Field(default_factory=Design)

    @field_validator("tile_size")
    @classmethod
    def _only_16px_tiles(cls, v: int) -> int:
        # The tilesets, the prompt and spec.level_spec.TILE_SIZE all assume 16 px.
        if v != 16:
            raise ValueError("only 16 px tiles are supported for now")
        return v

    def player_tiles(self) -> Tuple[int, int]:
        """Player hitbox size in whole tiles ``(width, height)``, rounded up."""
        ts = self.tile_size
        return (math.ceil(self.player.width_px / ts), math.ceil(self.player.height_px / ts))

    # ------------------------------------------------------------------
    # Prompt summary
    # ------------------------------------------------------------------

    def summary_for_prompt(self) -> str:
        """Compact GAME PROFILE block for the LLM user message.

        Lists the physics constants, the derived plain-jump limits and only
        the *enabled* abilities with their derived limits.
        """
        p = self.physics
        g, v, vmax = p.gravity, p.jump_v, p.run_speed
        h = max_jump_height(g, v)
        reach = flat_jump_reach(g, v, vmax)
        lines = [
            f'Game: "{self.name}"',
            f"Physics: gravity={g:g} px/s², jump_v={v:g} px/s, "
            f"run_speed={vmax:g} px/s (max |vx|), tile={self.tile_size} px.",
            f"Plain jump (ability null): rise up to {h:.0f} px; "
            f"cross up to {reach:.0f} px horizontally at equal height.",
            f"Walk: |Δy| ≤ {p.walk_step_px:g} px. "
            "Fall: target strictly lower, vy = 0.",
        ]

        ab = self.abilities
        ability_lines: List[str] = []
        if ab.dash.enabled:
            where = "mid-air" if ab.dash.air else "on the ground only (no gap bonus)"
            bonus = reach + ab.dash.distance_px if ab.dash.air else reach
            ability_lines.append(
                f'- "dash": {ab.dash.distance_px:g} px dash, {where}; '
                f"flat reach up to {bonus:.0f} px."
            )
        if ab.double_jump.enabled:
            v2 = ab.double_jump.jump_v
            h2 = h + max_jump_height(g, v2)
            t2 = double_jump_max_time(0.0, g, v, v2) or 0.0
            ability_lines.append(
                f'- "double_jump": second jump at {v2:g} px/s; rise up to {h2:.0f} px; '
                f"flat reach up to {vmax * t2:.0f} px."
            )
        if ab.wall_jump.enabled:
            wj = ab.wall_jump
            ability_lines.append(
                f'- "wall_jump": needs a wall; rise up to {h + wj.max_climb_px:.0f} px; '
                f"reach up to {reach + wj.push_px:.0f} px."
            )

        if ability_lines:
            lines.append("Abilities (set an edge's \"ability\" only to one of these):")
            lines += ability_lines
        else:
            lines.append('Abilities: none — every edge must have "ability": null.')
        lines.append(
            "Limits above are for gravity_scale 1.0; heights and reaches scale "
            "with 1 / gravity_scale."
        )
        d = self.design
        lines.append(
            f"Comfortable moves (design): a normal hop clears at most {d.comfort_hop_tiles} "
            f"empty tiles across and rises at most {d.comfort_rise_tiles} tiles; a normal fall "
            f"drops at most {d.comfort_drop_tiles} tiles. Build the golden path from walks and "
            "such hops between neighbouring platforms; longer jumps only in parkour sections."
        )
        if self.player.description:
            lines.append(f"Player: {self.player.description}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# File I/O
# ─────────────────────────────────────────────────────────────────────────────


def profile_path_for(ldtk_path: Union[str, Path]) -> Path:
    """The profile file that belongs to *ldtk_path* (same directory)."""
    return Path(ldtk_path).parent / PROFILE_FILENAME


def load_profile(path: Union[str, Path]) -> GameProfile:
    """Load a GameProfile.  A missing file yields the defaults.

    Raises ``ValueError`` (with the file path) if the file is not valid JSON
    or does not match the GameProfile schema.
    """
    path = Path(path)
    if not path.is_file():
        return GameProfile()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Game profile {path} is not valid JSON: {exc}") from exc
    try:
        return GameProfile.model_validate(data)
    except PydanticValidationError as exc:
        raise ValueError(f"Game profile {path} is invalid:\n{exc}") from exc


def save_profile(profile: GameProfile, path: Union[str, Path]) -> Path:
    """Write *profile* as pretty JSON; creates the parent directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
