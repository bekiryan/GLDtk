"""Structured-output LLM loop for the edit calls (region, entities, locate).

The same generate → validate → patch → repair pattern as ``LLMController``:
the provider returns JSON for a schema; ``parse`` validates it and applies
deterministic patches; what it cannot fix goes back to the LLM as a
``VALIDATION FAILED`` turn, up to ``max_retries`` calls in total.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generic, List, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel, ValidationError as PydanticValidationError

from api import jobs
from llm.providers import LLMProvider
from llm.validator import _extract_json, llm_call_message
from spec.level_spec import _inline_refs, _normalise_schema

T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)

# parse(raw model) -> (patched value or None, unfixable errors, patch notes)
Parser = Callable[[Any], Tuple[Optional[T], List[str], List[str]]]


class EditLLMFailed(RuntimeError):
    """The LLM gave no usable answer within the retry budget."""


@dataclass
class LLMRun(Generic[T]):
    value: T
    raw: str
    messages: List[Dict[str, Any]]
    attempts: int
    notes: List[str] = field(default_factory=list)  # deterministic patches applied


def strict_schema(model: Type[BaseModel]) -> Dict[str, Any]:
    """The provider-portable schema of *model* (see ``LevelSpec.json_schema``)."""
    raw = model.model_json_schema(by_alias=True)
    defs = raw.pop("$defs", {})
    return _normalise_schema(_inline_refs(raw, defs))


def run_structured(
    provider: LLMProvider,
    system: str,
    messages: List[Dict[str, Any]],
    model: Type[M],
    parse: Parser,
    max_retries: int = 3,
    schema: Optional[Dict[str, Any]] = None,
    what: str = "an answer",
) -> LLMRun:
    """Call the provider until ``parse`` accepts the answer (see the module doc).

    *what* names the request in the job's progress lines.
    """
    schema = schema or strict_schema(model)
    messages = list(messages)
    errors: List[str] = []
    for attempt in range(1, max_retries + 1):
        jobs.report("llm", llm_call_message(provider, what, attempt, max_retries))
        raw = provider.complete_json(system, messages, schema)
        jobs.checkpoint()
        try:
            obj = model.model_validate(json.loads(_extract_json(raw)))
        except ValueError as exc:  # JSON and pydantic errors
            kind = "match the schema" if isinstance(exc, PydanticValidationError) else "parse as JSON"
            errors = [f"Your output does not {kind}: {exc}"]
        else:
            value, errors, notes = parse(obj)
            if value is not None and not errors:
                return LLMRun(value, raw, messages, attempt, notes)
        if attempt < max_retries:
            jobs.report("validate", f"The answer has {len(errors)} problem(s), asking again")
            numbered = "\n".join(f"  {i + 1}. {e}" for i, e in enumerate(errors))
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": (
                    f"VALIDATION FAILED. Your output had these errors:\n{numbered}\n\n"
                    "Output a corrected JSON object that fixes every listed error. "
                    "Output ONLY the JSON object."
                )},
            ]
    raise EditLLMFailed(
        f"The LLM gave no valid answer after {max_retries} attempt(s): " + " ".join(errors)
    )


__all__ = ["EditLLMFailed", "LLMRun", "run_structured", "strict_schema"]
