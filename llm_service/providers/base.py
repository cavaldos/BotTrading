from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..schema import ChatRequest, ChatResult


class LLMProvider(ABC):
    """One backend (OpenRouter, OpenAI, a local model...). Images in requests are already data/http URLs."""

    name: str = ""

    @abstractmethod
    def chat(self, req: ChatRequest) -> ChatResult: ...

    @abstractmethod
    async def achat(self, req: ChatRequest) -> ChatResult: ...

    def list_models(self) -> list[dict[str, Any]]:
        return []

    def close(self) -> None:
        pass

    async def aclose(self) -> None:
        self.close()
