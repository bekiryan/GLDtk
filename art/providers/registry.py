"""Image provider factory."""

from __future__ import annotations

from typing import Any, Optional

from .base import ImageProvider


def make_image_provider(name: Optional[str] = "gemini", model: Optional[str] = None, **kwargs: Any) -> ImageProvider:
    """Build an image provider by name (``gemini`` default, ``fake`` for tests)."""
    key = (name or "gemini").lower()
    if key in ("gemini", "google", "gemini-image"):
        from .gemini_image import GeminiImageProvider

        return GeminiImageProvider(model=model, **kwargs)
    if key == "fake":
        from .fake import FakeImageProvider

        return FakeImageProvider(**kwargs)
    raise ValueError(f"unknown image provider {name!r} (known: gemini, fake)")
