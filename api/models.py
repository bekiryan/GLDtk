"""Request and response models for the GLDtk companion server."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    description: str = Field(..., description="Natural-language level description")
    theme: Optional[str] = Field(None, description="dungeon | forest | sky")
    enemy_types: Optional[List[str]] = Field(
        None, description="e.g. ['Goblin','Slime']"
    )
    difficulty: Optional[str] = Field(None, description="easy | medium | hard")
    platform_count: Optional[int] = Field(None, ge=2, le=30)
    width_tiles: Optional[int] = Field(
        None, ge=30, le=300, description="Level width in tiles"
    )
    height_tiles: Optional[int] = Field(
        None, ge=15, le=100, description="Level height in tiles"
    )
    output_path: str = Field("level.ldtk", description="Path to write the .ldtk file")
    seed: int = Field(42)
    provider: str = Field(
        "gemini", description="gemini | anthropic | ollama | openai | deepseek"
    )
    model: Optional[str] = Field(None, description="None = provider default model")
    max_retries: int = Field(3, ge=1, le=10)
    game_profile_path: Optional[str] = Field(
        None,
        description="Path to gldtk.game.json; None = next to output_path (defaults if missing)",
    )


class LevelStats(BaseModel):
    theme: str
    nodes: int
    edges: int
    entities: Dict[str, int]  # {"Coin": 5, "Enemy": 2, ...}


class GenerateResponse(BaseModel):
    success: bool
    preview_ascii: Optional[str] = None
    stats: Optional[LevelStats] = None
    output_path: Optional[str] = None
    error: Optional[str] = None
    requirements: List[str] = Field(default_factory=list)  # requirement texts
    sections: List[str] = Field(default_factory=list)  # section names, in order
    profile_path: Optional[str] = None  # the Game Profile path that was used
