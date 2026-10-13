"""Provider registry — build an ``LLMProvider`` from a name + config."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from . import anthropic_provider, gemini, ollama, openai_compat
from .base import LLMProvider

_DEFAULT_PROVIDER_MODELS: Dict[str, str] = {
    "gemini": gemini.DEFAULT_MODEL,
    "anthropic": anthropic_provider.DEFAULT_MODEL,
    "openai": openai_compat.DEFAULT_MODELS["openai"],
    "deepseek": openai_compat.DEFAULT_MODELS["deepseek"],
    "ollama": ollama.DEFAULT_MODEL,
}

_ALIASES: Dict[str, str] = {"claude": "anthropic", "google": "gemini"}

PROVIDER_NAMES = tuple(_DEFAULT_PROVIDER_MODELS)


@dataclass
class LLMProviderConfig:
    """Provider settings (name + connection details)."""

    provider: str = "gemini"
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    timeout_seconds: float = 120.0


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
    timeout: float = 120.0,
) -> LLMProvider:
    """Instantiate the provider *name*; ``model=None`` uses the registry default."""
    p = resolve_provider_name(name)
    explicit_model = model is not None
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
