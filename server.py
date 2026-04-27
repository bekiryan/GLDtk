"""GLDtk Companion Server — backend only.

Run:
    python server.py

The GLDtk panel inside LDtk sends requests to this server.
POST /generate  — run the pipeline and write a .ldtk file.
"""

from __future__ import annotations

import collections
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from aesthetic import build_aesthetic_layer
from aesthetic.themes import THEMES
from api.models import GenerateRequest, GenerateResponse, LevelStats
from layout import LayoutConfig, sugiyama_layout, to_ldtk_dict
from llm import LLMController, MaxRetriesExceeded, PhysicsParams
from llm.validator import LLMProviderConfig

app = FastAPI(title="GLDtk Companion", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Difficulty → pipeline parameter mapping
# ─────────────────────────────────────────────────────────────────────────────

_DIFFICULTY: Dict[str, Dict[str, Any]] = {
    "easy":   {"density": 0.5,  "v_step": 3},
    "medium": {"density": 1.0,  "v_step": 4},
    "hard":   {"density": 1.5,  "v_step": 5},
}


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/themes")
def list_themes() -> Dict[str, List[str]]:
    return {"themes": [k for k in THEMES if k != "default"]}


@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest) -> GenerateResponse:
    # ── difficulty params ─────────────────────────────────────────────────
    diff_cfg = _DIFFICULTY.get(req.difficulty or "medium", _DIFFICULTY["medium"])
    density: float = diff_cfg["density"]
    v_step: int    = diff_cfg["v_step"]

    # ── build constraints block for the LLM ───────────────────────────────
    constraints: Dict[str, Any] = {}
    if req.theme:
        constraints["theme"] = req.theme
    if req.enemy_types:
        constraints["enemy_types"] = req.enemy_types
    if req.platform_count:
        constraints["platform_count"] = req.platform_count
    if req.difficulty:
        constraints["difficulty"] = req.difficulty

    # ── LLM graph generation ──────────────────────────────────────────────
    try:
        controller = LLMController(
            physics=PhysicsParams(),
            provider=req.provider,
            model=req.model,
            max_retries=req.max_retries,
        )
        graph = controller.generate(req.description, constraints=constraints or None)
    except MaxRetriesExceeded as exc:
        return GenerateResponse(success=False, error=str(exc))
    except Exception as exc:
        return GenerateResponse(success=False, error=f"LLM error: {exc}")

    # ── layout ────────────────────────────────────────────────────────────
    try:
        layout_kwargs: dict = {"v_step": v_step}
        if req.width_tiles:
            layout_kwargs["width_tiles"] = req.width_tiles
        if req.height_tiles:
            layout_kwargs["height_tiles"] = req.height_tiles
            layout_kwargs["baseline_row"] = req.height_tiles - 5
        layout_cfg = LayoutConfig(**layout_kwargs)
        node_layouts, ir = sugiyama_layout(graph, layout_cfg)
    except Exception as exc:
        return GenerateResponse(success=False, error=f"Layout error: {exc}")

    # ── aesthetic ─────────────────────────────────────────────────────────
    try:
        result = build_aesthetic_layer(
            description=req.description,
            graph=graph,
            node_layouts=node_layouts,
            ir_level=ir,
            theme_override=req.theme,
            density=density,
            seed=req.seed,
            custom_enemy_types=req.enemy_types or None,
        )
    except Exception as exc:
        return GenerateResponse(success=False, error=f"Aesthetic error: {exc}")

    # ── serialise ─────────────────────────────────────────────────────────
    try:
        doc = to_ldtk_dict(ir, aesthetic=result.aesthetic)
        out = Path(req.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    except Exception as exc:
        return GenerateResponse(success=False, error=f"Serialisation error: {exc}")

    # ── stats ─────────────────────────────────────────────────────────────
    entity_counts: Dict[str, int] = collections.Counter(
        e.entity_type for e in ir.entities
    )
    stats = LevelStats(
        theme=result.theme.display_name,
        nodes=len(graph.nodes()),
        edges=len(graph.edges()),
        entities=dict(entity_counts),
    )
    preview = ir.ascii_render({"Start": "S", "Exit": "E"})

    return GenerateResponse(
        success=True,
        preview_ascii=preview,
        stats=stats,
        output_path=str(out.resolve()),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.getenv("GLDTK_HOST", "127.0.0.1")
    port = int(os.getenv("GLDTK_PORT", "8765"))
    print(f"GLDtk companion → http://{host}:{port}")
    uvicorn.run("server:app", host=host, port=port, reload=False)
