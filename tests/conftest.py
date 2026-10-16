"""Shared pytest fixtures: repo root on sys.path, recorded LLM responses."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures" / "llm"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def legacy_profile():  # type: ignore[no-untyped-def]
    """The pre-Part-28 default physics (gravity 980, jump_v 600, run 200 and the
    old ability numbers), for tests whose hand-computed geometry pins them."""
    from game import GameProfile

    return GameProfile.model_validate({
        "physics": {"gravity": 980.0, "jump_v": 600.0, "run_speed": 200.0},
        "abilities": {
            "double_jump": {"jump_v": 500.0},
            "dash": {"distance_px": 64.0},
            "wall_jump": {"jump_v": 550.0, "max_climb_px": 160.0, "push_px": 48.0},
        },
    })


@pytest.fixture(autouse=True)
def _memory_dir(tmp_path_factory, monkeypatch):
    """Design memory never touches the real user folder in tests."""
    monkeypatch.setenv("GLDTK_MEMORY_DIR", str(tmp_path_factory.mktemp("gldtk_memory")))


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def load_fixture() -> Callable[[str], str]:
    return fixture_text


@pytest.fixture
def valid_spec_dict() -> Dict[str, Any]:
    return json.loads(fixture_text("valid_two_sections.json"))


@pytest.fixture
def minimal_spec_dict() -> Dict[str, Any]:
    return json.loads(fixture_text("valid_minimal.json"))


@pytest.fixture(autouse=True)
def _patterns_dir(tmp_path, monkeypatch):
    """Pattern library (Part 26a): never touch the user's index."""
    monkeypatch.setenv("GLDTK_PATTERNS_DIR", str(tmp_path / "patterns"))
