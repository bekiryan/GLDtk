"""Mission graph and space graph (v4 Part 23, Dormans-style cyclic generation).

* :mod:`.graph`   — the mission graph (goals: keys, locks, abilities, gates,
  treasure, boss, shortcuts) and its solvability check;
* :mod:`.grammar` — the graph-rewriting rules and the seeded rewriter;
* :mod:`.space`   — the space graph: mission nodes onto sections and terrain
  anchors, as spec entities with their links;
* :mod:`.verify`  — Part 22's item-state graph on the built level.

``build_with_mission`` is the pipeline hook: it tries the full mission with up
to ``SPACE_ATTEMPTS`` space mappings, then fewer rules, and returns the first
level whose mission is realised (or the best one, with a warning).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from spec.level_spec import LevelSpec, MissionSpec

from .grammar import RULE_ORDER, Request, requests_from, rewrite
from .graph import MissionGraph
from .space import SpacePlan, map_space
from .verify import MissionCheck, verify_mission

SPACE_ATTEMPTS = 3

FEATURE_RULE = {"lock": "lock_key_cycle", "loop": "shortcut_loop", "gate": "ability_gate",
                "treasure": "treasure_branch", "boss": "boss_before_exit"}


def mission_of(spec: LevelSpec) -> Optional[MissionSpec]:
    """``spec.mission``, else one derived from the spec's ``mission`` requirements."""
    if spec.mission is not None:
        return spec.mission if spec.mission.rules or spec.mission.keys else None
    rules: List[str] = []
    for req in spec.requirements:
        chk = req.check
        if chk is None or chk.type != "mission" or chk.subkind not in FEATURE_RULE:
            continue
        rule = FEATURE_RULE[chk.subkind]
        if rule == "shortcut_loop" and "lock_key_cycle" not in rules:
            rules.append("lock_key_cycle")
        rules.append(rule)
    if not rules:
        return None
    locks = [{"key_section": r.check.section, "lock_section": r.check.target_section}
             for r in spec.requirements
             if r.check and r.check.type == "mission" and r.check.subkind == "lock"]
    return MissionSpec.model_validate({"rules": rules, "locks": locks or None})


def plan_graph(mission: MissionSpec, abilities: Any = (), seed: int = 0,
               requests: Optional[List[Request]] = None) -> Tuple[MissionGraph, List[str]]:
    warnings: List[str] = []
    if requests is None:
        requests, warnings = requests_from(mission, list(abilities))
    graph, more = rewrite(requests, seed=seed)
    return graph, warnings + more


@dataclass
class MissionOutcome:
    spec: LevelSpec  # the spec the level was built from (mission entities included)
    result: Any  # whatever ``run`` returned for it
    plan: Optional[SpacePlan]
    check: Optional[MissionCheck]
    warnings: List[str] = field(default_factory=list)
    tries: int = 0

    def to_dict(self) -> Optional[Dict[str, Any]]:
        if self.plan is None:
            return None
        return {**self.plan.graph.to_dict(), "rules": list(self.plan.graph.applied),
                "realised": bool(self.check and self.check.realised),
                "features": self.check.features if self.check else []}


def build_with_mission(
    spec: LevelSpec,
    mission: MissionSpec,
    abilities: Any,
    seed: int,
    run: Callable[[LevelSpec], Tuple[Any, Any, Any]],
) -> MissionOutcome:
    """Build *spec* with its mission; *run(spec)* returns ``(layout, report, extra)``."""
    requests, warnings = requests_from(mission, list(abilities))
    candidates: List[Tuple[List[Request], int]] = [(requests, a) for a in range(SPACE_ATTEMPTS)]
    for n in range(len(requests) - 1, 0, -1):
        candidates.append((requests[:n], 0))
    fallback: Optional[MissionOutcome] = None
    tries = 0
    for k, (reqs, attempt) in enumerate(candidates):
        graph, more = rewrite(reqs, seed=seed)
        if k == 0:
            warnings += more
        plan = map_space(spec, graph, attempt, seed)
        if plan is None:
            if k == 0:
                warnings.append("mission: the mission does not fit the level's sections")
            continue
        tries += 1
        result = run(plan.spec)
        layout, report = result[0], result[1]
        check = verify_mission(layout, report, plan)
        outcome = MissionOutcome(plan.spec, result, plan, check, list(warnings) + plan.warnings, tries)
        if check.realised:
            if reqs is not requests:
                dropped = [r for r, _p in requests[len(reqs):]]
                outcome.warnings.append(f"mission: fell back to fewer rules (left out {dropped})")
            elif attempt:
                outcome.warnings.append(f"mission: realised with space mapping {attempt + 1}")
            return outcome
        if report.ok and fallback is None:
            fallback = outcome
    if fallback is not None:
        fallback.warnings.append("mission: not fully realised: " + "; ".join(fallback.check.problems
                                                                             if fallback.check else []))
        fallback.tries = tries
        return fallback
    result = run(spec)
    return MissionOutcome(spec, result, None, None,
                          warnings + ["mission: could not be realised; built without it"], tries + 1)


__all__ = [
    "MissionCheck", "MissionGraph", "MissionOutcome", "RULE_ORDER", "SPACE_ATTEMPTS", "SpacePlan",
    "build_with_mission", "map_space", "mission_of", "plan_graph", "requests_from", "rewrite",
    "verify_mission",
]
