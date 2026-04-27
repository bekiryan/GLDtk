"""Request and response models for the GLDtk companion server."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    description: str = Field(..., description="Natural-language level description")
    theme: Optional[str] = Field(None, description="dungeon | forest | sky")
    enemy_types: Optional[List[str]] = Field(None, description="e.g. ['Goblin','Slime']")
    difficulty: Optional[str] = Field(None, description="easy | medium | hard")
    platform_count: Optional[int] = Field(None, ge=2, le=30)
    width_tiles: Optional[int] = Field(None, ge=20, le=300, description="Level width in tiles")
    height_tiles: Optional[int] = Field(None, ge=10, le=100, description="Level height in tiles")
    output_path: str = Field("level.ldtk", description="Path to write the .ldtk file")
    seed: int = Field(42)
    provider: str = Field("ollama", description="anthropic | openai | deepseek | ollama")
    model: Optional[str] = Field("llama3.1")
    max_retries: int = Field(3, ge=1, le=10)


class LevelStats(BaseModel):
    theme: str
    nodes: int
    edges: int
    entities: Dict[str, int]   # {"Coin": 5, "Enemy": 2, ...}


class GenerateResponse(BaseModel):
    success: bool
    preview_ascii: Optional[str] = None
    stats: Optional[LevelStats] = None
    output_path: Optional[str] = None
    error: Optional[str] = None
