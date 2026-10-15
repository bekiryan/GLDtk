"""Real Gemini call (opt-in): ``pytest -m e2e tests/e2e/test_gemini.py``.

Reads ``GEMINI_API_KEY`` from the environment or the repo's ``.env``.  The key
is never printed.  Run with ``-s`` to see the model, attempts and token usage.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from llm import LLMController, MaxRetriesExceeded, ProviderError, validate_spec
from llm.providers import make_provider
from game import GameProfile
from server import load_env_file
from spec import LevelSpec

pytestmark = pytest.mark.e2e

load_env_file(Path(__file__).resolve().parents[2] / ".env")

PROMPT = (
    "A short forest level with two sections: a calm meadow with three coins, then a "
    "dark cave with one bat enemy and a treasure chest. The player starts with a bow."
)


@pytest.mark.skipif(not os.getenv("GEMINI_API_KEY"), reason="GEMINI_API_KEY not set")
def test_gemini_produces_valid_level_spec():
    # Unset GLDTK_GEMINI_MODEL = the server's default path (with model fallback).
    provider = make_provider("gemini", model=os.getenv("GLDTK_GEMINI_MODEL"), timeout=180.0)
    model = provider.model
    controller = LLMController(provider=provider, max_retries=3)

    try:
        spec = controller.generate_spec(PROMPT)
    except ProviderError as exc:  # quota / overload / auth — surface, don't hide
        pytest.fail(f"Gemini provider error (model={model}): {exc}")
    except MaxRetriesExceeded as exc:
        pytest.fail(f"No valid spec after retries (model={model}): {exc}")

    stats = controller.stats
    print(
        f"\n[e2e] model={provider.model_used} attempts={stats.attempts} patched={stats.patched} "
        f"patches={stats.patches_applied} codes/attempt={stats.error_codes_per_attempt} "
        f"usage={provider.last_usage}"
    )
    print(f"[e2e] sections={[s.name for s in spec.sections]}")
    print(f"[e2e] entities={[(e.kind, e.description) for e in spec.entities]}")
    print(f"[e2e] requirements={[r.text for r in spec.requirements]}")

    assert isinstance(spec, LevelSpec)
    assert validate_spec(spec, GameProfile()).valid
    assert spec.requirements
    assert any(e.kind == "Pickup" for e in spec.entities)
