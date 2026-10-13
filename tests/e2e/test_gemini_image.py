"""Real Gemini image generation (opt-in): ``pytest -m e2e tests/e2e/test_gemini_image.py -s``.

One theme generation against a *temp copy* of the library (``assets/`` is never
touched). The provider is capped at 3 HTTP calls (free-tier image quota is
tiny). Reads ``GEMINI_API_KEY`` from the environment, the repo's ``.env``, or
the file named by ``GLDTK_ENV_FILE``; the key is never printed.

A 429 (quota, including "limit: 0" = no free image tier) skips with the reason,
because that is an answer about the free tier, not a code failure.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from aesthetic.library import DEFAULT_ROOT, clear_cache, load_library
from server import load_env_file

pytestmark = pytest.mark.e2e

load_env_file(Path(__file__).resolve().parents[2] / ".env")
if os.getenv("GLDTK_ENV_FILE"):
    load_env_file(Path(os.environ["GLDTK_ENV_FILE"]))

DESCRIPTION = "crystal cavern with glowing purple moss"


class _Recording:
    """Wraps a provider and keeps the raw image for inspection."""

    def __init__(self, inner, out: Path):
        self.inner, self.out, self.name = inner, out, inner.name

    def generate(self, prompt, **kw):
        data = self.inner.generate(prompt, **kw)
        self.out.write_bytes(data)
        return data


@pytest.mark.skipif(not os.getenv("GEMINI_API_KEY"), reason="GEMINI_API_KEY not set")
def test_gemini_generates_theme(tmp_path):
    from art.pipeline import generate_theme
    from art.providers import ImageProviderError
    from art.providers.gemini_image import GeminiImageProvider

    lib_root = tmp_path / "assets"
    shutil.copytree(DEFAULT_ROOT, lib_root, ignore=shutil.ignore_patterns("sources", "preview.png"))
    clear_cache()
    provider = GeminiImageProvider(
        model=os.getenv("GLDTK_GEMINI_IMAGE_MODEL"), max_calls=3, transient_retries=0
    )
    raw_path = tmp_path / "raw.png"
    try:
        entry = generate_theme(DESCRIPTION, None, _Recording(provider, raw_path), root=lib_root, reload=False)
    except ImageProviderError as exc:
        print(f"\n[e2e-image] calls={provider.calls} error={exc}")
        if exc.status == 429:
            pytest.skip(f"Gemini image quota (free tier): {exc}")
        pytest.fail(f"Gemini image error: {exc}")
    finally:
        clear_cache()

    sheet = load_library(lib_root).theme_sheet_path(entry)
    print(
        f"\n[e2e-image] model={provider.model_used} calls={provider.calls} usage={provider.last_usage}"
        f"\n[e2e-image] raw={raw_path} sheet={sheet}\n[e2e-image] quality={entry.quality}"
    )
    preview = tmp_path / "sheet_x8.png"
    from PIL import Image

    Image.open(sheet).resize((640, 512), Image.NEAREST).save(preview)
    print(f"[e2e-image] preview={preview}")
    assert sheet.is_file() and entry.generated
    assert entry.quality["colors"] <= 16
    clear_cache()
