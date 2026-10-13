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
    entities: Dict[str, int]  # spec kinds: {"Enemy": 2, "Pickup": 5, ...}
    layout_retries: int = 0  # LLM re-prompts after grid verification failed
    grid_repairs: int = 0  # deterministic grid repairs applied
    completeness_reprompts: int = 0  # LLM re-prompts for unmet requirements (0 or 1)
    entities_filled: int = 0  # entities the deterministic completeness fill added


class FidelityItemModel(BaseModel):
    id: str  # requirement id ("R1")
    text: str
    category: str
    status: str  # satisfied | approximated | impossible | missing | unchecked
    reason: str
    auto_filled: bool = False  # the completeness fill added entities for it


class FidelityModel(BaseModel):
    """Mirror of ``fidelity.FidelityReport`` (see ``fidelity/report.py``).

    ``score`` = (satisfied + 0.5 · approximated) / checkable items (all but
    unchecked); None when nothing is checkable.
    """

    score: Optional[float] = None
    verified: Optional[bool] = None
    counts: Dict[str, int] = Field(default_factory=dict)  # status → item count
    items: List[FidelityItemModel] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    success: bool
    preview_ascii: Optional[str] = None
    stats: Optional[LevelStats] = None
    output_path: Optional[str] = None
    error: Optional[str] = None
    requirements: List[str] = Field(default_factory=list)  # requirement texts
    sections: List[str] = Field(default_factory=list)  # section names, in order
    profile_path: Optional[str] = None  # the Game Profile path that was used
    verified: bool = False  # the final tile grid was proven beatable
    warnings: List[str] = Field(default_factory=list)  # layout / entity / repair notes
    fidelity: Optional[FidelityModel] = None  # per-requirement report on the final level
