"""Server hooks: record events around generate / edit / undo, and carry the nudge log.

``track(kind)`` wraps a route function ``fn(req)``:

1. before it runs, a hand edit of the file since GLDtk's last write is scanned;
2. while it runs, the densify budget is scaled by the learned density;
3. after a successful run, the event is recorded, the output copy refreshed, the nudge notes
   appended to ``response.warnings``, and an LLM summary run if one is due.

Every step is guarded: memory can never fail a request.
"""

from __future__ import annotations

import contextvars
import functools
import json
from pathlib import Path
from typing import Any, Callable, List, Optional

from memory import store

_notes: contextvars.ContextVar[Optional[List[str]]] = contextvars.ContextVar("memory_notes", default=None)
PROMPT_CHARS = 300


def note(msg: str) -> None:
    notes = _notes.get()
    if notes is not None:
        notes.append(msg)


def _level_facts(path: Any, level: Any = None) -> dict:
    """prompt, theme and section structure of a written file (compact)."""
    try:
        from layout.ldtk_reader import read_level

        lv = read_level(json.loads(Path(path).read_text(encoding="utf-8")), level if level else 0)
        secs = [{k: s[k] for k in ("name", "theme", "difficulty", "style", "gravity_scale") if s.get(k)}
                for s in lv.sections]
        return {"prompt": (lv.prompt or "")[:PROMPT_CHARS], "sections": secs,
                "theme": secs[0].get("theme") if secs else None}
    except Exception:
        return {}


def _safe(fn: Callable, *a: Any, **k: Any) -> Any:
    try:
        return fn(*a, **k)
    except Exception:
        return None


def before(path: Any, approve: bool = True) -> None:
    """Scan a hand edit; then a pending write that was not undone is approved (Part 26c).
    *approve* is False for the undo route, which decides about the pending write itself."""
    from memory import approve as approvals, handedit

    if path:
        _safe(handedit.scan, path)
        if approve and store.enabled():
            _safe(approvals.approve, path)


def track(kind: str) -> Callable:
    """Decorator for ``fn(req)`` routes; *kind* is ``"generate"`` or ``"edit"``."""

    def wrap(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def route(req: Any) -> Any:
            from layout import densify
            from memory import analyze, handedit, prefs
            from memory import approve as approvals

            path = getattr(req, "output_path", None) if kind == "generate" else getattr(req, "path", None)
            on = bool(_safe(store.enabled))
            if not on:
                return fn(req)
            before(path)
            token = _notes.set([])
            scale = _safe(prefs.density_factor) or 1.0
            dtoken = densify.DENSITY_SCALE.set(scale)
            try:
                resp = fn(req)
            finally:
                densify.DENSITY_SCALE.reset(dtoken)
                notes = _notes.get() or []
                _notes.reset(token)
            if getattr(resp, "success", False) and path:
                if hasattr(resp, "warnings") and resp.warnings is not None:
                    resp.warnings = list(resp.warnings) + notes
                level = getattr(req, "level", None)
                facts = _level_facts(path, level)
                if kind == "generate":
                    etype, mode = ("regenerated" if level else "generated"), "generate"
                else:
                    etype, mode = "edited", getattr(getattr(resp, "edit", None), "mode", None)
                _safe(store.record, etype, file=Path(path).name, mode=mode,
                      prompt=(getattr(req, "description", None) or getattr(req, "prompt", None) or "")
                      [:PROMPT_CHARS] or facts.get("prompt"),
                      sections=facts.get("sections"), theme=facts.get("theme"),
                      difficulty=getattr(req, "difficulty", None))
                _safe(handedit.remember_output, path)
                _safe(approvals.mark, path, level)
                if store.load_prefs().get("events_since_summary", 0) >= store.SUMMARY_EVERY:
                    _safe(analyze.maybe_summarize, _provider_of(req))
            return resp

        return route

    return wrap


def _provider_of(req: Any) -> Any:
    """The provider the route's request names (built the same way the route builds it)."""
    try:
        import server

        return server.provider_factory(req)
    except Exception:
        return None


def undone(path: Any, label_mode: Optional[str]) -> None:
    """Record a successful undo (the file now holds an older GLDtk output)."""
    if store.enabled():
        _safe(store.record, "undone", file=Path(path).name, mode=label_mode)
        from memory import approve as approvals, handedit

        _safe(approvals.discard, path)
        _safe(handedit.remember_output, path)


def profile_changed(path: Any, profile: Any) -> None:
    if store.enabled():
        _safe(store.record, "profile_changed", file=Path(str(path)).name,
              physics=profile.physics.model_dump(),
              abilities={k: bool(v["enabled"]) for k, v in profile.abilities.model_dump().items()
                         if isinstance(v, dict) and "enabled" in v},
              design=profile.design.model_dump())
