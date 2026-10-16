"""Fidelity: check a level against the prompt's requirements, fill what is missing."""

from .checker import check_level, check_requirement, check_spec
from .difficulty import DifficultyReport, SectionDifficulty, apply_to_fidelity, measure_difficulty
from .fill import FillResult, fill_missing
from .report import FidelityItem, FidelityReport, FidelityStatus

__all__ = [
    "DifficultyReport",
    "SectionDifficulty",
    "apply_to_fidelity",
    "measure_difficulty",
    "check_level",
    "check_requirement",
    "check_spec",
    "FillResult",
    "fill_missing",
    "FidelityItem",
    "FidelityReport",
    "FidelityStatus",
]
