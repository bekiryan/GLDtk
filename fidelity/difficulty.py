"""Difficulty metrics: does each section's built difficulty match its label? (v3 Part 15a)

Sections carry a label (easy, medium, hard) that the LLM sets from the prompt.
This module measures the *built* level, per section, with no LLM call, and
turns the measurements into a score from 0 to 10.

Measures (per section; a section is a column band, or a row band in a
vertical level; ``length`` is its size along that axis in tiles)
-----------------------------------------------------------------
* **Jumps**: the non-walk moves of the golden route (``layout.route``) that
  start in the section.  ``mean_gap`` / ``max_gap`` are horizontal tiles,
  ``max_rise`` is the highest climb in tiles, and ``hard_move_share`` the share
  of moves that need an ability or sit near the jump limit (the gap is more
  than 85% of the plain-jump reach for that rise, section gravity included).
* **Hazards**: ``hazard_per_100`` = hazard tiles per 100 columns (rows);
  ``hazard_near_landing`` = hazard tiles within 1 tile of a route landing.
* **Enemies**: ``enemies``, ``enemy_threat`` = sum of ``health * damage``
  (1 for an unset value; a boss counts 4x), ``bosses``.
* **Recovery**: ``checkpoints`` and ``health_pickups``.
* **Platforms**: ``mean_platform_width`` (each ledge capped at 8 tiles) and
  ``narrow_share``, the share of ledges at most 3 tiles wide.

Score
-----
Each pressure term is normalised to 0..1 and multiplied by its weight; the
weights add up to 10 and recovery subtracts up to ``RECOVERY_RELIEF``::

    jumps     (3.0) = 0.35 * span(mean_gap) + 0.25 * span(max_gap)
                      + 0.15 * max_rise/jump_height + 0.25 * hard_move_share
                      (span(x) = 0 at 40% of the reach, 1 at the full reach)
    hazards   (2.5) = min(1, (hazard_per_100 + 0.4 * 100 * hazard_near_landing / length) / 20)
    enemies   (3.0) = min(1, threat_per_100 / 15 + 0.3 per boss); one enemy adds <= 12 threat
    platforms (1.5) = narrow_share
    relief   (-1.0) = min(1, 0.5 * checkpoints + 0.25 * health_pickups)
    raw   = clamp(sum of the weighted terms - relief, 0, 10)
    score = 10 * (raw / 10) ** 0.75

(``reach`` is the plain jump's flat reach in tiles, ``jump_height`` its apex.)
The weights were calibrated on the test fixtures and the recorded Gemini
evaluation (``GAMMA`` stretches the gentle end) so that easy sections score about 2 to 3,
medium 4 to 6 and hard 7 to 9 when the content matches the label.

Checks
------
* a section whose score does not fit its label (easy above 5, hard below 4)
  gets a warning (``SectionDifficulty.warnings``);
  :func:`apply_to_fidelity` turns it into an ``approximated`` fidelity item
  when the prompt stated that difficulty;
* the curve going down where the prompt implied "gets harder" is a warning
  only (``DifficultyReport.warnings``).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from game.profile import GameProfile
from layout.ir import IREntity, TileValue
from layout.route import Route, level_route
from symbolic.physics import flat_jump_reach, gravity_for, max_jump_height

# ── weights (sum 10) ────────────────────────────────────────────────────────
W_JUMPS = 3.0
W_HAZARDS = 2.5
W_ENEMIES = 3.0
W_PLATFORMS = 1.5
RECOVERY_RELIEF = 1.0
GAMMA = 0.75  # score = 10 * (raw/10) ** GAMMA: spreads the gentle end (LLM levels are mostly gentle)

NEAR_LIMIT = 0.85  # a jump over this share of the plain reach is "near the limit"
HAZARD_SATURATION = 20.0  # hazard tiles per 100 columns that count as full pressure
THREAT_SATURATION = 15.0
ENEMY_CAP = 12.0  # one enemy adds at most this much threat (a 20 HP boss would swamp the rest)
GAP_FLOOR = 0.4  # a jump shorter than this share of the plain reach adds no pressure  # health*damage per 100 columns that count as full pressure
BOSS_THREAT = 4
BOSS_PRESSURE = 0.3
NARROW_WIDTH = 3
PLATFORM_CAP = 8

MAX_EASY = 5.0  # an "easy" section scoring above this is a mismatch
MIN_HARD = 4.0  # a "hard" section scoring below this is a mismatch
CURVE_DROP = 1.0  # a drop of more than this between neighbours contradicts "gets harder"

LABEL_WORDS: Dict[str, Sequence[str]] = {
    "easy": ("easy", "gentle", "simple", "beginner", "relaxed"),
    "medium": ("medium", "moderate"),
    "hard": ("hard", "difficult", "challenging", "brutal", "tough", "punishing"),
}
HARDER_RE = re.compile(
    r"(gets?|getting|grows?|growing|becomes?|increas\w*|ramps?|rising|progressive\w*|escalat\w*)"
    r"\W+(?:\w+\W+){0,3}?(harder|more difficult|tougher|more challenging|difficulty|in difficulty)"
    r"|(harder|tougher|more difficult)\W+(?:\w+\W+){0,2}?(toward|towards|near|at) the (end|exit)"
    r"|difficulty\W+(?:\w+\W+){0,2}?(increas|ramp|rise|grow|climb)",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SectionDifficulty:
    id: str
    name: str
    label: str
    score: float
    metrics: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    @property
    def mismatch(self) -> bool:
        return bool(self.warnings)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "label": self.label,
            "score": self.score,
            "metrics": dict(self.metrics),
            "warnings": list(self.warnings),
        }


@dataclass
class DifficultyReport:
    sections: List[SectionDifficulty] = field(default_factory=list)
    overall: Optional[float] = None  # mean section score, weighted by section length
    warnings: List[str] = field(default_factory=list)  # level-wide (the curve)
    route_ok: bool = True

    def by_label(self) -> Dict[str, List[float]]:
        out: Dict[str, List[float]] = {}
        for s in self.sections:
            out.setdefault(s.label, []).append(s.score)
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sections": [s.to_dict() for s in self.sections],
            "overall": self.overall,
            "warnings": list(self.warnings),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _section_dicts(sections: Sequence[Any], width: int, height: int) -> List[Dict[str, Any]]:
    """Sections as dicts with defaults; accepts dicts or Band-like objects."""
    out: List[Dict[str, Any]] = []
    for i, s in enumerate(sections or []):
        get = (lambda k, d=None, s=s: s.get(k, d)) if isinstance(s, dict) else (
            lambda k, d=None, s=s: getattr(s, k, d))
        try:
            out.append({
                "id": str(get("id") or f"section{i}"),
                "name": str(get("name") or get("id") or f"Section {i + 1}"),
                "difficulty": str(get("difficulty") or "medium"),
                "orientation": str(get("orientation") or "horizontal"),
                "gravity_scale": float(get("gravity_scale", 1.0) or 1.0),
                "col_start": int(get("col_start", 0)),
                "col_end": int(get("col_end", width - 1)),
                "row_start": int(get("row_start", 0)),
                "row_end": int(get("row_end", height - 1)),
                "structure": (get("structure") or None) if isinstance(s, dict)
                else getattr(s, "structure", None),
            })
        except (TypeError, ValueError):
            continue
    return out or [{
        "id": "main", "name": "Level", "difficulty": "medium", "orientation": "horizontal",
        "gravity_scale": 1.0, "col_start": 0, "col_end": width - 1, "row_start": 0,
        "row_end": height - 1,
    }]


def _contains(sec: Dict[str, Any], col: int, row: int) -> bool:
    return sec["col_start"] <= col <= sec["col_end"] and sec["row_start"] <= row <= sec["row_end"]


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _first(secs: List[Dict[str, Any]], col: int, row: int) -> Optional[int]:
    for i, s in enumerate(secs):
        if _contains(s, col, row):
            return i
    return None


def _surface_runs(grid: Sequence[Sequence[Any]]) -> List[tuple]:
    """Ledges: maximal horizontal runs of solid cells with an empty cell above, as (row, c0, c1)."""
    runs = []
    h = len(grid)
    for r in range(1, h):
        c, w = 0, len(grid[r])
        while c < w:
            if int(grid[r][c]) == TileValue.SOLID and int(grid[r - 1][c]) == TileValue.EMPTY:
                c0 = c
                while c < w and int(grid[r][c]) == TileValue.SOLID and int(grid[r - 1][c]) == TileValue.EMPTY:
                    c += 1
                runs.append((r, c0, c - 1))
            else:
                c += 1
    return runs


# ─────────────────────────────────────────────────────────────────────────────
# Measure
# ─────────────────────────────────────────────────────────────────────────────


def measure_difficulty(
    grid: Sequence[Sequence[Any]],
    entities: Sequence[IREntity],
    sections: Sequence[Any],
    profile: Optional[GameProfile] = None,
    route: Optional[Route] = None,
    prompt: Optional[str] = None,
) -> DifficultyReport:
    """Per-section difficulty of the level made of *grid* and *entities*.

    *sections* are dicts (``id, name, difficulty, orientation, gravity_scale,
    col_start/col_end/row_start/row_end``) or objects with those attributes
    (``edit.context.Band``).  *route* is the golden route (computed here when
    None).  *prompt* is only used for the "gets harder" curve check.
    """
    profile = profile or GameProfile()
    height = len(grid)
    width = len(grid[0]) if height else 0
    secs = _section_dicts(sections, width, height)
    ts = float(profile.tile_size)
    if route is None:
        scale_at = lambda c, r: secs[_first(secs, c, r) or 0]["gravity_scale"]  # noqa: E731
        step_at = None
        if any(sec.get("structure") in WALK_STRUCTURES + ("parkour", "shaft") for sec in secs):
            step_at = lambda c, r: secs[_first(secs, c, r) or 0].get("structure") not in (  # noqa: E731
                None, "open")
        route = level_route(grid, entities, profile, scale_at, step_at=step_at)

    phys = profile.physics
    n = len(secs)
    moves: List[List[Dict[str, float]]] = [[] for _ in range(n)]
    landings: List[List[tuple]] = [[] for _ in range(n)]
    for step in route.steps:
        if step.kind == "walk" or len(step.points) < 2:
            continue
        i = _first(secs, *step.from_cell)
        if i is None:
            continue
        (x0, y0), (x1, y1) = step.points[0], step.points[-1]
        gap = abs(x1 - x0) / ts
        rise = max(0.0, (y0 - y1) / ts)
        g = gravity_for(profile, secs[i]["gravity_scale"])
        reach = phys.run_speed * _air_time(g, phys.jump_v, (y0 - y1)) / ts
        near = step.kind == "jump" and reach > 0 and gap > NEAR_LIMIT * reach
        moves[i].append({
            "gap": gap, "rise": rise,
            "hard": 1.0 if (step.ability or near or step.kind not in ("jump", "fall")) else 0.0,
        })
        landings[i].append(step.to_cell)

    runs = _surface_runs(grid)
    hazards = [(c, r) for r in range(height) for c in range(width)
               if int(grid[r][c]) == TileValue.HAZARD]

    out = DifficultyReport(route_ok=route.ok)
    total_len = 0
    weighted = 0.0
    for i, sec in enumerate(secs):
        vertical = sec["orientation"] == "vertical"
        length = (sec["row_end"] - sec["row_start"] + 1) if vertical else (sec["col_end"] - sec["col_start"] + 1)
        length = max(1, length)
        in_sec = lambda c, r, sec=sec: _contains(sec, c, r)  # noqa: E731

        mv = moves[i]
        gaps = [m["gap"] for m in mv]
        m_jump: Dict[str, Any] = {
            "moves": len(mv),
            "mean_gap": round(sum(gaps) / len(gaps), 2) if gaps else 0.0,
            "max_gap": round(max(gaps), 2) if gaps else 0.0,
            "max_rise": round(max((m["rise"] for m in mv), default=0.0), 2),
            "hard_move_share": round(sum(m["hard"] for m in mv) / len(mv), 2) if mv else 0.0,
        }
        sec_hz = [(c, r) for c, r in hazards if in_sec(c, r)]
        near_land = sum(
            1 for c, r in sec_hz
            if any(abs(c - lc) <= 1 and abs(r - lr - 1) <= 1 for lc, lr in landings[i])
        )
        ents = [e for e in entities if in_sec(e.col, e.row)]
        threat, bosses, enemies = 0, 0, 0
        for e in ents:
            if e.entity_type != "Enemy":
                continue
            enemies += 1
            boss = str(e.metadata.get("archetype") or "").lower() == "boss"
            bosses += boss
            health = _num(e.metadata.get("health"))
            damage = _num(e.metadata.get("damage"))
            threat += min(ENEMY_CAP, health * damage * (BOSS_THREAT if boss else 1))
        sec_runs = [(r, c0, c1) for r, c0, c1 in runs if in_sec(c0, r) or in_sec(c1, r)]
        widths = [min(PLATFORM_CAP, c1 - c0 + 1) for _, c0, c1 in sec_runs]

        metrics: Dict[str, Any] = dict(m_jump)
        metrics.update({
            "length": length,
            "hazard_tiles": len(sec_hz),
            "hazard_per_100": round(100.0 * len(sec_hz) / length, 1),
            "hazard_near_landing": near_land,
            "enemies": enemies,
            "enemy_threat": threat,
            "bosses": bosses,
            "checkpoints": sum(1 for e in ents if e.entity_type == "Checkpoint"),
            "health_pickups": sum(
                1 for e in ents
                if e.entity_type == "Pickup" and str(e.metadata.get("pickup_kind") or "") == "health"),
            "mean_platform_width": round(sum(widths) / len(widths), 2) if widths else 0.0,
            "narrow_share": round(sum(1 for w in widths if w <= NARROW_WIDTH) / len(widths), 2) if widths else 0.0,
        })
        score = _score(metrics, profile, secs[i]["gravity_scale"], secs[i].get("structure"))
        label = sec["difficulty"] if sec["difficulty"] in LABEL_WORDS else "medium"
        item = SectionDifficulty(sec["id"], sec["name"], label, score, metrics)
        if label == "easy" and score > MAX_EASY:
            item.warnings.append(
                f"{sec['name']} is labelled easy but scores {score:.1f}/10 (easy is at most {MAX_EASY:g}).")
        elif label == "hard" and score < MIN_HARD:
            item.warnings.append(
                f"{sec['name']} is labelled hard but scores only {score:.1f}/10 (hard is at least {MIN_HARD:g}).")
        out.sections.append(item)
        total_len += length
        weighted += score * length
    out.overall = round(weighted / total_len, 1) if total_len else None

    if prompt and HARDER_RE.search(prompt) and len(out.sections) > 1:
        scores = [s.score for s in out.sections]
        drops = [(a, b) for a, b in zip(out.sections, out.sections[1:]) if b.score < a.score - CURVE_DROP]
        if drops:
            a, b = drops[0]
            out.warnings.append(
                f"The prompt asks for a level that gets harder, but {b.name} ({b.score:.1f}) is "
                f"easier than {a.name} ({a.score:.1f}).")
        elif scores[-1] < scores[0]:
            out.warnings.append("The prompt asks for a level that gets harder, but it ends easier than it starts.")
    return out


def _num(v: Any) -> float:
    try:
        return float(v) if v is not None and float(v) > 0 else 1.0
    except (TypeError, ValueError):
        return 1.0


def _air_time(g: float, jump_v: float, rise_px: float) -> float:
    """Time of a plain jump from launch to a landing *rise_px* higher (descending root)."""
    disc = jump_v * jump_v - 2.0 * g * rise_px
    if disc < 0:
        return 0.0
    return (jump_v + math.sqrt(disc)) / g


WALK_STRUCTURES = ("corridor", "labyrinth", "arena", "hub")
"""Walk-heavy structures (v3 Part 21): enemies weigh more, jumps less."""
WALK_ENEMY_BOOST = 1.6
WALK_JUMP_DAMP = 0.6


def _score(m: Dict[str, Any], profile: GameProfile, gravity_scale: float,
           structure: Optional[str] = None) -> float:
    ts = float(profile.tile_size)
    phys = profile.physics
    g = gravity_for(profile, gravity_scale)
    reach = flat_jump_reach(g, phys.jump_v, phys.run_speed) / ts
    height = max_jump_height(g, phys.jump_v) / ts
    length = m["length"]

    jumps = 0.0
    if m["moves"]:
        def span(x: float) -> float:  # 0 below GAP_FLOOR of the reach, 1 at the reach
            return _clamp((x / reach - GAP_FLOOR) / (1.0 - GAP_FLOOR))

        jumps = _clamp(
            0.35 * span(m["mean_gap"]) + 0.25 * span(m["max_gap"])
            + 0.15 * m["max_rise"] / height + 0.25 * m["hard_move_share"])
    hazards = _clamp((m["hazard_per_100"] + 4.0 * m["hazard_near_landing"] * 100.0 / length / 10.0)
                     / HAZARD_SATURATION)
    enemies = _clamp(100.0 * m["enemy_threat"] / length / THREAT_SATURATION + BOSS_PRESSURE * m["bosses"])
    platforms = m["narrow_share"]
    relief = _clamp(0.5 * m["checkpoints"] + 0.25 * m["health_pickups"]) * RECOVERY_RELIEF
    w_jumps, w_enemies = W_JUMPS, W_ENEMIES
    if structure in WALK_STRUCTURES:  # walking and fighting: the enemies make it hard
        w_jumps, w_enemies = W_JUMPS * WALK_JUMP_DAMP, W_ENEMIES * WALK_ENEMY_BOOST
    raw = w_jumps * jumps + W_HAZARDS * hazards + w_enemies * enemies + W_PLATFORMS * platforms - relief
    return round(10.0 * (_clamp(raw, 0.0, 10.0) / 10.0) ** GAMMA, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Fidelity
# ─────────────────────────────────────────────────────────────────────────────


def _states(text: str, label: str) -> bool:
    low = text.lower()
    return any(re.search(rf"\b{w}\b", low) for w in LABEL_WORDS[label])


def apply_to_fidelity(report: DifficultyReport, fidelity: Any, prompt: Optional[str] = None) -> int:
    """Mark the mismatched sections in a ``FidelityReport`` (in place); returns how many.

    A section counts when its label (or a synonym) is stated by a requirement
    that names the section, or by the *prompt* next to the section's name or
    id.  A satisfied requirement becomes ``approximated``; without a matching
    requirement an ``approximated`` item ``D<n>`` (category ``layout``) is added.
    """
    from .report import FidelityItem

    if fidelity is None:
        return 0
    changed = 0
    for sec in report.sections:
        if not sec.mismatch:
            continue
        names = [x.lower() for x in (sec.id, sec.name) if x]
        reason = (f"The section is labelled {sec.label} but the built level scores "
                  f"{sec.score:.1f}/10 for difficulty.")

        def names_section(text: str) -> bool:
            low = text.lower()
            return any(nm in low for nm in names)

        hit = next((i for i in fidelity.items
                    if i.status == "satisfied" and names_section(i.text) and _states(i.text, sec.label)), None)
        if hit is not None:
            hit.status, hit.reason = "approximated", reason
            changed += 1
        elif prompt and names_section(prompt) and _states(prompt, sec.label) and \
                not any(i.id == f"D-{sec.id}" for i in fidelity.items):
            fidelity.items.append(FidelityItem(
                id=f"D-{sec.id}", text=f"{sec.name} is {sec.label}.", category="layout",
                status="approximated", reason=reason))
            changed += 1
    return changed


def level_scores(grid: Sequence[Sequence[Any]], entities: Sequence[IREntity], sections: Sequence[Any],
                 profile: Optional[GameProfile] = None) -> Dict[str, float]:
    """Measured score per section id, and ``"*"`` for the whole level (for the ``complexity`` check)."""
    rep = measure_difficulty(grid, entities, sections, profile)
    out = {s.id: s.score for s in rep.sections}
    if rep.overall is not None:
        out["*"] = rep.overall
    return out


def difficulty_of_file(path: Any, level: Optional[str] = None) -> DifficultyReport:
    """The difficulty of an ``.ldtk`` file, read like an edit reads it (hand edits count).

    *level* (an iid or identifier; None = the first level) picks the level of a world.

    Raises :class:`edit.context.EditError` when the file cannot be read.
    """
    from edit.context import load_context
    from layout.route import level_route

    ctx = load_context(path, level)
    spans = ctx.extract(ctx.grid)
    route = level_route(ctx.grid, ctx.entities, ctx.profile, ctx.gravity_scale_at, spans.graph,
                        step_at=ctx.step_at())
    return measure_difficulty(ctx.grid, ctx.entities, ctx.bands, ctx.profile, route, ctx.level.prompt)


__all__ = [
    "DifficultyReport",
    "SectionDifficulty",
    "apply_to_fidelity",
    "difficulty_of_file",
    "level_scores",
    "measure_difficulty",
]
