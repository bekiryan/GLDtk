"""Request and response models for the GLDtk companion server."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    description: str = Field(..., description="Natural-language level description")
    theme: Optional[str] = Field(None, description="dungeon | forest | sky")
    enemy_types: Optional[List[str]] = Field(
        None, description="e.g. ['Goblin','Slime']"
    )
    difficulty: Optional[str] = Field(None, description="easy | medium | hard")
    complexity: Optional[float] = Field(
        None, ge=0.0, le=1.0,
        description="Complexity target 0..1 (shown to users as 1-10); None = off (v4 Part 24)",
    )
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
    densify: bool = Field(
        True,
        description="Fill large empty regions with extra platforms (fill_N nodes) after layout",
    )
    job_id: Optional[str] = Field(
        None, description="Client-made id for GET /progress and POST /cancel (v3 Part 14)"
    )
    levels: Optional[int] = Field(
        None, ge=1, le=8,
        description="Number of levels (v3 Part 17): 1 = one level, no outline call; 2-8 = a world "
                    "of exactly that many levels; None = decided by the outline call when the "
                    "prompt mentions several levels, else one level",
    )
    level: Optional[str] = Field(
        None,
        description="Regenerate only this level (iid or identifier) of the existing world at "
                    "output_path, keeping the other levels and the links (v3 Part 17)",
    )


class LevelSummary(BaseModel):
    """One level of the written file (v3 Part 17), for the sidebar's level list."""

    index: int  # position in the file (= the world order)
    id: Optional[str] = None  # outline level id ("forest"); None outside a generated world
    identifier: str  # LDtk identifier ("Forest_1")
    iid: Optional[str] = None  # LDtk level iid
    title: str = ""
    score: Optional[float] = None  # fidelity score (None when unknown, e.g. levels an edit skipped)
    verified: Optional[bool] = None  # None when not checked by this request
    current: bool = False  # the level this response's preview / fidelity / route are about


class LevelStats(BaseModel):
    theme: str
    nodes: int
    edges: int
    entities: Dict[str, int]  # spec kinds: {"Enemy": 2, "Pickup": 5, ...}
    layout_retries: int = 0  # LLM re-prompts after grid verification failed
    grid_repairs: int = 0  # deterministic grid repairs applied
    completeness_reprompts: int = 0  # LLM re-prompts for unmet requirements (0 or 1)
    entities_filled: int = 0  # entities the deterministic completeness fill added
    densify_added: int = 0  # fill platforms the densify step added
    comfort_added: int = 0  # platforms the comfort pass added for short hops (v3 Part 20)


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


class RouteStepModel(BaseModel):
    """One move of the golden route (``layout/route.py``)."""

    model_config = {"populate_by_name": True}

    kind: str  # walk | jump | fall | double_jump | dash | wall_jump
    from_cell: List[int] = Field(default_factory=list, alias="from")  # [col, row]
    to: List[int] = Field(default_factory=list)  # [col, row]
    points: List[List[float]] = Field(default_factory=list)  # [[x, y]…] level px, y down
    ability: Optional[str] = None


class SpanModel(BaseModel):
    col: int
    row: int
    w: int


class RouteModel(BaseModel):
    """The cheapest Start → Exit route on the final grid (v3 Part 13).

    ``success`` is False (with ``error``) when there is no path; the reachable
    spans and the unreachable required entities are filled in either way.
    """

    success: bool
    error: Optional[str] = None
    steps: List[RouteStepModel] = Field(default_factory=list)
    length_px: float = 0.0
    reachable_spans: List[SpanModel] = Field(default_factory=list)
    unreachable_required: List[str] = Field(default_factory=list)  # entity iids
    level_iid: Optional[str] = None


class RouteRequest(BaseModel):
    """POST /route: the project as it is open in LDtk (unsaved hand edits count)."""

    path: str = Field(..., description="Path of the .ldtk file the project belongs to")
    project: Dict[str, Any] = Field(..., description="The LDtk project JSON")
    level: Optional[str] = Field(None, description="Level of a multi-level world: its iid or identifier; None = the first level (v3 Part 17)")


class SectionDifficultyModel(BaseModel):
    """One section's measured difficulty (``fidelity/difficulty.py``, v3 Part 15a)."""

    id: str
    name: str
    label: str  # easy | medium | hard (what the section was asked to be)
    score: float  # 0..10
    metrics: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)  # non-empty = the score does not fit the label


class DifficultyModel(BaseModel):
    """Difficulty per section, in level order; no LLM call (GET /difficulty, and a field of
    GenerateResponse / EditResponse / UndoResponse)."""

    success: bool = True
    error: Optional[str] = None
    sections: List[SectionDifficultyModel] = Field(default_factory=list)
    overall: Optional[float] = None  # mean score, weighted by section length
    warnings: List[str] = Field(default_factory=list)  # level-wide (the curve)


class WorldFidelityModel(FidelityModel):
    """The world fidelity report (v3 Part 17): ``items`` are the world requirements
    (W1 …); ``score`` is the mean of their score and every level's score;
    ``verified`` is True when every level verified."""

    title: str = ""
    requirements_score: Optional[float] = None  # the world requirements alone
    levels: List[LevelSummary] = Field(default_factory=list)


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
    route: Optional[RouteModel] = None  # golden route of the written file (v3 Part 13)
    cancelled: bool = False  # the job was cancelled; nothing was written (v3 Part 14)
    difficulty: Optional[DifficultyModel] = None  # per-section difficulty (v3 Part 15a)
    # v3 Part 17: every level of the file (one entry for a single-level file).  For a world
    # the fields above describe the ``current`` level (the first one after a world generate).
    levels: List[LevelSummary] = Field(default_factory=list)
    world: Optional["WorldFidelityModel"] = None  # world report (a multi-level generate only)


# ─────────────────────────────────────────────────────────────────────────────
# Regional editing + history (Part 8)
# ─────────────────────────────────────────────────────────────────────────────


class RectModel(BaseModel):
    """A tile rectangle; row 0 is the top of the level."""

    col: int = Field(..., ge=0)
    row: int = Field(..., ge=0)
    w: int = Field(..., ge=1)
    h: int = Field(..., ge=1)


class EditRequestBase(BaseModel):
    path: str = Field(..., description="Absolute path of the .ldtk file")
    provider: str = Field("gemini", description="gemini | anthropic | ollama | openai | deepseek")
    model: Optional[str] = Field(None, description="None = provider default model")
    seed: int = Field(42)
    densify: bool = Field(True, description="Region/section edits: fill the rect with extra "
                                            "platforms; ignored by entity edits")
    max_retries: int = Field(3, ge=1, le=10)
    job_id: Optional[str] = Field(
        None, description="Client-made id for GET /progress and POST /cancel (v3 Part 14)"
    )
    level: Optional[str] = Field(None, description="Level of a multi-level world: its iid or identifier; None = the first level (v3 Part 17)")


class RegionEditRequest(EditRequestBase):
    rect: RectModel
    prompt: str


class SectionEditRequest(EditRequestBase):
    section_id: str
    prompt: str
    complexity: Optional[float] = Field(None, ge=0.0, le=1.0)  # v4 Part 24: this section's target
    # v3 Part 21: carve the section again as this structure (corridor, parkour,
    # labyrinth, arena, shaft, hub, open); None = from the prompt, else a region edit
    structure: Optional[str] = None


class EntityEditRequest(EditRequestBase):
    prompt: str
    rect: Optional[RectModel] = None
    entity_iids: Optional[List[str]] = None  # wins over rect when both are given


class LocateRequest(BaseModel):
    path: str
    text: str
    provider: str = Field("gemini")
    model: Optional[str] = None
    max_retries: int = Field(3, ge=1, le=10)
    job_id: Optional[str] = Field(
        None, description="Client-made id for GET /progress and POST /cancel (v3 Part 14)"
    )
    level: Optional[str] = Field(None, description="Level of a multi-level world: its iid or identifier; None = the first level (v3 Part 17)")


class EditInfo(BaseModel):
    mode: str  # region | section | entities
    region: Optional[RectModel] = None  # the edited rect (None for a whole-level entity edit)
    snapshot: Optional[str] = None  # history snapshot taken before the write


class EditResponse(GenerateResponse):
    edit: Optional[EditInfo] = None


class LocateResponse(BaseModel):
    success: bool
    rect: Optional[RectModel] = None
    explanation: str = ""
    error: Optional[str] = None
    cancelled: bool = False


class PathRequest(BaseModel):
    path: str
    level: Optional[str] = Field(
        None, description="Level whose route / difficulty the answer carries (iid or identifier; "
                          "None = the first); the undo itself restores the whole file")


class UndoResponse(BaseModel):
    success: bool
    restored_from: Optional[str] = None
    remaining: int = 0
    error: Optional[str] = None
    route: Optional[RouteModel] = None  # golden route of the restored file
    difficulty: Optional[DifficultyModel] = None  # difficulty of the restored file
    levels: Optional[List[LevelSummary]] = None  # the restored file's levels (v3 Part 17)


class HistoryEntry(BaseModel):
    file: str
    timestamp: str  # ISO 8601 UTC, sorts chronologically
    label: str


class HistoryResponse(BaseModel):
    snapshots: List[HistoryEntry] = Field(default_factory=list)  # newest first


# ─────────────────────────────────────────────────────────────────────────────
# Job progress + cancel (v3 Part 14)
# ─────────────────────────────────────────────────────────────────────────────


class ProgressEvent(BaseModel):
    t: float  # seconds since the job started
    stage: str
    message: str  # short, user-facing


class ProgressResponse(BaseModel):
    stage: Optional[str] = None  # None for an unknown job
    events: List[ProgressEvent] = Field(default_factory=list)  # oldest first, the last ~50
    elapsed_s: float = 0.0
    done: bool = True  # unknown ids are done
    cancelled: bool = False


class CancelRequest(BaseModel):
    job_id: str


class CancelResponse(BaseModel):
    job_id: str
    running: bool  # False: the job had finished or was not known yet


GenerateResponse.model_rebuild()
EditResponse.model_rebuild()
