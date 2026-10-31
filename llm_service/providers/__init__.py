"""Provider registry. A new provider = one module that uses @register("name"), imported below."""

from __future__ import annotations

from typing import Callable

from ..config import Settings
from ..schema import LLMError
from .base import LLMProvider

_REGISTRY: dict[str, Callable[[Settings], LLMProvider]] = {}


def register(name: str):
    def deco(factory):
        _REGISTRY[name] = factory
        return factory

    return deco


def available() -> list[str]:
    return sorted(_REGISTRY)


def create_provider(name: str, settings: Settings) -> LLMProvider:
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise LLMError(f"unknown provider '{name}', available: {available()}") from None
    return factory(settings)


from . import openrouter  # noqa: E402,F401  (registers itself)

__all__ = ["LLMProvider", "available", "create_provider", "register"]
