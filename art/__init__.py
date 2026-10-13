"""AI theme and sprite generation (v2 Part 6).

``art.providers`` holds the image-provider interface (Gemini by default, plus a
fake for tests). ``art.pipeline`` turns a generated image into a 16 px theme
sheet or sprite and saves it into the asset library. ``art.derive`` holds the
role-tile derivation shared with ``scripts/build_assets.py``.

Pillow is an optional dependency (``pip install -e ".[art]"``). This package
imports it lazily so the rest of the server runs without it; modules that need
it raise ``PillowMissing`` with an install hint.
"""

from __future__ import annotations


class PillowMissing(ImportError):
    """Pillow is not installed but an art feature needs it."""


def require_pillow():
    """Return the ``PIL.Image`` module, or raise ``PillowMissing`` with a hint."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PillowMissing(
            "AI art generation needs Pillow. Install it with: pip install -e \".[art]\""
        ) from exc
    return Image
