"""Fake image provider for offline tests.

Returns canned PNGs (bytes or paths) in order, cycling, and records every call.
With no images given, it serves ``tests/fixtures/art/grid_theme.png`` for theme
prompts and ``tests/fixtures/art/sprite.png`` otherwise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "art"

ImageSource = Union[bytes, str, Path]


class FakeImageProvider:
    name = "fake"

    def __init__(self, images: Optional[Sequence[ImageSource]] = None) -> None:
        self._images: List[ImageSource] = list(images or [])
        self.calls: List[Dict[str, Any]] = []

    def generate(
        self,
        prompt: str,
        *,
        size_hint: Optional[Tuple[int, int]] = None,
        reference_png: Optional[bytes] = None,
    ) -> bytes:
        self.calls.append(
            {"prompt": prompt, "size_hint": size_hint, "has_reference": reference_png is not None}
        )
        if self._images:
            src = self._images[(len(self.calls) - 1) % len(self._images)]
        else:
            src = FIXTURES / ("grid_theme.png" if "tile sheet" in prompt.lower() else "sprite.png")
        return src if isinstance(src, bytes) else Path(src).read_bytes()
