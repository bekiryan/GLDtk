"""Provider registry — build an ``LLMProvider`` from a name + config."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import os

from . import anthropic_provider, gemini, ollama, openai_compat
from .base import LLMProvider
from .fallback import FallbackProvider

_DEFAULT_PROVIDER_MODELS: Dict[str, str] = {
    "gemini": gemini.DEFAULT_MODEL,
    "anthropic": anthropic_provider.DEFAULT_MODEL,
    "openai": openai_compat.DEFAULT_MODELS["openai"],
    "deepseek": openai_compat.DEFAULT_MODELS["deepseek"],
    "ollama": ollama.DEFAULT_MODEL,
}

_ALIASES: Dict[str, str] = {"claude": "anthropic", "google": "gemini"}

PROVIDER_NAMES = tuple(_DEFAULT_PROVIDER_MODELS)

# A local model writes a full LevelSpec in minutes, not seconds.
_DEFAULT_TIMEOUTS: Dict[str, float] = {"ollama": 900.0}
DEFAULT_TIMEOUT = 120.0

# Used when Gemini cannot answer (quota, overload, retired model, timeout).
# Override with GLDTK_LOCAL_FALLBACK="provider:model"; "off" disables it.
DEFAULT_LOCAL_FALLBACK = "ollama:qwen2.5:14b"


@dataclass
class LLMProviderConfig:
    """Provider settings (name + connection details)."""

    provider: str = "gemini"
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    timeout_seconds: Optional[float] = None


def resolve_provider_name(name: str) -> str:
    p = _ALIASES.get(name.strip().lower(), name.strip().lower())
    if p not in _DEFAULT_PROVIDER_MODELS:
        raise ValueError(
            f"Unsupported provider: {name!r}. Expected one of {'|'.join(PROVIDER_NAMES)}."
        )
    return p


def make_provider(
    name: str,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
) -> LLMProvider:
    """Instantiate the provider *name*; ``model=None`` uses the registry default."""
    p = resolve_provider_name(name)
    explicit_model = model is not None
    if timeout is None:
        timeout = _DEFAULT_TIMEOUTS.get(p, DEFAULT_TIMEOUT)
    model = model or _DEFAULT_PROVIDER_MODELS[p]

    if p == "gemini":
        return gemini.GeminiProvider(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout,
            fallback_models=() if explicit_model else gemini.FALLBACK_MODELS,
        )
    if p == "anthropic":
        return anthropic_provider.AnthropicProvider(
            model=model, api_key=api_key, timeout_seconds=timeout
        )
    if p == "ollama":
        return ollama.OllamaProvider(model=model, base_url=base_url, timeout_seconds=timeout)
    return openai_compat.OpenAICompatProvider(
        flavor=p, model=model, api_key=api_key, base_url=base_url, timeout_seconds=timeout
    )


def make_provider_from_config(config: LLMProviderConfig) -> LLMProvider:
    return make_provider(
        config.provider,
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout_seconds,
    )


def parse_fallback(value: Optional[str]) -> Optional[tuple[str, Optional[str]]]:
    """``"ollama:qwen2.5:14b"`` → ``("ollama", "qwen2.5:14b")``; off/empty → None."""
    if value is None:
        value = DEFAULT_LOCAL_FALLBACK
    value = value.strip()
    if value.lower() in ("", "off", "none", "0", "false"):
        return None
    name, _, model = value.partition(":")
    return resolve_provider_name(name), (model or None)


def make_provider_with_fallback(
    name: str,
    model: Optional[str] = None,
    fallback: Optional[str] = None,
) -> LLMProvider:
    """Like ``make_provider``; Gemini with its default model also gets a local fallback.

    *fallback* defaults to the ``GLDTK_LOCAL_FALLBACK`` environment variable, then to
    ``DEFAULT_LOCAL_FALLBACK``.  An explicit model means the user wants that model, so
    no cross-provider fallback is added.
    """
    primary = make_provider(name, model=model)
    if resolve_provider_name(name) != "gemini" or model is not None:
        return primary
    target = parse_fallback(fallback if fallback is not None else os.getenv("GLDTK_LOCAL_FALLBACK"))
    if target is None or target[0] == "gemini":
        return primary
    return FallbackProvider([primary, make_provider(target[0], model=target[1])])
