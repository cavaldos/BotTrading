"""Provider-neutral request/response types."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class LLMError(Exception):
    """Any failure talking to a model. status_code is the upstream HTTP status when there is one."""

    def __init__(self, message: str, *, status_code: int | None = None, provider: str | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.provider = provider


class BadInput(LLMError):
    """The caller sent something unusable (empty prompt, wrong image type, image too big)."""


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    text: str = ""
    images: list[Any] = field(default_factory=list)  # anything media.to_image_url accepts


@dataclass
class ChatRequest:
    messages: list[Message]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # passed through to the provider body


@dataclass
class ChatResult:
    text: str
    model: str  # the model that answered (openrouter/free reports the real one)
    provider: str
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    reasoning: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def __str__(self) -> str:
        return self.text

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not include_raw:
            d.pop("raw")
        return d
