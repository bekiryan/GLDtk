"""HTTP API of the pattern library: ``GET /patterns``, ``POST /patterns/add``, ``GET /patterns/stats``."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from patterns.embed import make_embedder, sync_vectors
from patterns.ingest import add_path
from patterns.prompt import crop
from patterns.retrieve import Filters, retrieve
from patterns.store import PatternStore

router = APIRouter()


class AddPatternRequest(BaseModel):
    path: str
    level: Optional[str] = None  # iid / identifier; None = every level of the file
    embedder: str = "hash"  # the embedder of the existing index wins when there is one


@router.get("/patterns")
def preview_patterns(
    query: str = Query("", description="what to look for"),
    k: int = Query(3, ge=1, le=20),
    structure_type: Optional[str] = None,
    difficulty_min: Optional[int] = Query(None, ge=1, le=10),
    difficulty_max: Optional[int] = Query(None, ge=1, le=10),
    tags: Optional[str] = Query(None, description="comma-separated"),
) -> Dict[str, Any]:
    flt = Filters(structure_type, difficulty_min, difficulty_max,
                  [t.strip() for t in (tags or "").split(",") if t.strip()])
    hits = retrieve(query, k, flt, store=PatternStore())
    return {"query": query, "patterns": [
        {"id": h.id, "title": h.title, "score": round(h.score, 4), "label": h.pattern.get("label"),
         "features": h.pattern["features"], "ascii": crop(h.pattern["ascii"]),
         "source": h.pattern["source"], "license": h.pattern["license"],
         "origin_path": h.pattern["origin_path"]} for h in hits]}


@router.post("/patterns/add")
def add_pattern_file(req: AddPatternRequest) -> Dict[str, Any]:
    store = PatternStore()
    rep = add_path(store, req.path, req.level, source="gldtk-user", license="project-owned (user-approved)")
    embedder = make_embedder((store.embedder or {}).get("name") or req.embedder)
    embedded = sync_vectors(store, embedder) if rep.added else 0
    if rep.added:
        store.save()
    return {"added": rep.added, "duplicates": rep.duplicates, "rejected": rep.rejected,
            "embedded": embedded, "skipped": rep.skipped_files, "notes": rep.notes}


@router.get("/patterns/stats")
def pattern_stats() -> Dict[str, Any]:
    return PatternStore().stats()


@router.get("/patterns/levels")
def similar_levels(query: str = Query("", description="what to look for"),
                   k: int = Query(3, ge=1, le=20)) -> Dict[str, Any]:
    """Whole levels (kind "level") similar to *query*, without their full ASCII."""
    hits = retrieve(query, k, store=PatternStore(), kind="level")
    return {"query": query, "levels": [
        {"id": h.id, "title": h.title, "source": h.pattern["source"],
         "origin_path": h.pattern["origin_path"], "level": h.pattern["level"],
         "score": round(h.score, 4)} for h in hits]}
