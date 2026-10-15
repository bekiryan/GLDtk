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
