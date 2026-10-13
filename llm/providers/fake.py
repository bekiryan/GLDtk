"""FakeProvider — recorded responses for offline, repeatable tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence, Union


@dataclass
class FakeCall:
    system: str
    messages: List[Dict[str, Any]]
    schema: Dict[str, Any]


class FakeProviderExhausted(RuntimeError):
    pass


class FakeProvider:
    """Return canned responses in order and record every call.

    ``responses`` is either a list of raw strings or a path to a directory,
    whose files are returned in sorted filename order.
    """

    name = "fake"

    def __init__(self, responses: Union[Sequence[str], str, Path]) -> None:
        if isinstance(responses, (str, Path)) and Path(responses).is_dir():
            files = sorted(p for p in Path(responses).iterdir() if p.is_file())
            self._responses = [p.read_text(encoding="utf-8") for p in files]
        elif isinstance(responses, (str, Path)):
            raise ValueError(f"FakeProvider: {responses!r} is not a directory")
        else:
            self._responses = list(responses)
        self.calls: List[FakeCall] = []

    def complete_json(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
    ) -> str:
        self.calls.append(FakeCall(system, [dict(m) for m in messages], schema))
        index = len(self.calls) - 1
        if index >= len(self._responses):
            raise FakeProviderExhausted(
                f"FakeProvider exhausted after {len(self._responses)} response(s)."
            )
        return self._responses[index]
