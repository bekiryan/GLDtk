"""Never write into LDtk's bundled samples.

LDtk ships its samples in ``<install>/extraFiles/samples/`` (inside the ``.app``
bundle on a Mac); a file saved there is lost on the next app update.  The sidebar
asks for a save location first; :func:`writes_outside_samples` is the server-side
safety net on every route that writes the ``.ldtk`` (or files beside it).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import HTTPException


def is_bundled_sample(path: Optional[str | Path]) -> bool:
    """True when *path* lies in an ``extraFiles/samples`` folder or inside a ``.app`` bundle."""
    if not path:
        return False
    parts = Path(path).expanduser().resolve().parts
    for a, b in zip(parts, parts[1:]):
        if (a == "extraFiles" and b == "samples") or (a.endswith(".app") and b == "Contents"):
            return True
    return False


def refuse_sample_write(path: Optional[str | Path]) -> None:
    """Raise HTTP 400 when *path* is a bundled sample."""
    if is_bundled_sample(path):
        raise HTTPException(
            status_code=400,
            detail=f"{Path(path).name} is a bundled LDtk sample: save a copy elsewhere first "
                   "(GLDtk never writes into the LDtk app folder)",
        )


def writes_outside_samples(fn: Callable) -> Callable:
    """Route decorator: refuse a request whose ``output_path`` or ``path`` is a bundled sample."""

    @functools.wraps(fn)
    def route(req: Any) -> Any:
        for attr in ("output_path", "path"):
            refuse_sample_write(getattr(req, attr, None))
        return fn(req)

    return route
