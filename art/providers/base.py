"""Image provider protocol.

A provider turns a text prompt (plus an optional reference image) into PNG
bytes. Post-processing into 16 px tiles is ``art.pipeline``'s job.
"""

from __future__ import annotations

from typing import Optional, Protocol, Tuple, runtime_checkable

from llm.providers.base import ProviderError  # shared error type (has .status)


@runtime_checkable
class ImageProvider(Protocol):
    name: str

    def generate(
        self,
        prompt: str,
        *,
        size_hint: Optional[Tuple[int, int]] = None,
        reference_png: Optional[bytes] = None,
    ) -> bytes:  # PNG bytes
        ...


class ImageProviderError(ProviderError):
    """An image provider call failed (HTTP, quota, no image in the response)."""


__all__ = ["ImageProvider", "ImageProviderError", "ProviderError"]
