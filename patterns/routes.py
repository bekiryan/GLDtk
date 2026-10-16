"""HTTP API of the pattern library: ``GET /patterns``, ``POST /patterns/add``, ``GET /patterns/stats``,
``GET /patterns/levels``, ``GET /patterns/attribution``, ``GET /patterns/notes`` and
``POST /patterns/notes/review`` (Part 26d)."""

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


@router.get("/patterns/attribution")
def pattern_attribution() -> Dict[str, Any]:
    """Credits for the open-licensed game levels in the index (Part 26d): ``text`` (plain-text
    ATTRIBUTION) and ``sources`` (per game: URL, commit, levels with authors and licence)."""
    from patterns.sources import attribution_text

    return attribution_text(PatternStore())


@router.get("/patterns/notes")
def list_notes(query: str = Query("", description="optional: rank by relevance"),
               k: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    """Design notes (Part 26d) with their review state."""
    from patterns.notes import excluded_notes

    store = PatternStore()
    excluded = excluded_notes()
    if query:
        recs = [h.pattern for h in retrieve(query, k, store=store, kind="note")]
    else:
        recs = [r for r in store if r.get("kind") == "note"][:k]
    return {"notes": [{"id": r["id"], **(r.get("note") or {}), "reviewed": bool(r.get("reviewed")),
                       "excluded": r["id"] in excluded} for r in recs]}


class ReviewNoteRequest(BaseModel):
    id: str
    reviewed: Optional[bool] = None
    exclude: Optional[bool] = None  # True: never offer this note again


@router.post("/patterns/notes/review")
def review_note_route(req: ReviewNoteRequest) -> Dict[str, Any]:
    from fastapi import HTTPException

    from patterns.notes import excluded_notes, review_note

    rec = review_note(PatternStore(), req.id, reviewed=req.reviewed, exclude=req.exclude)
    if rec is None:
        raise HTTPException(404, f"no note {req.id!r}")
    return {"id": req.id, "reviewed": bool(rec.get("reviewed")), "excluded": req.id in excluded_notes()}
