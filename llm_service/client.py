"""LLMClient plus the module-level shortcuts (ask / chat / achat) the bot calls."""

from __future__ import annotations

import threading
from typing import Any, Iterable

from .config import Settings
from .media import to_image_url
from .providers import LLMProvider, available, create_provider
from .schema import BadInput, ChatRequest, ChatResult, Message


class LLMClient:
    """Holds settings and open connections. Thread-safe; reuse one instance.

    LLMClient()                                   -> settings from llm_service/.env
    LLMClient(default_model="google/gemma-3-27b-it:free", timeout=60)   -> override any Settings field
    """

    def __init__(self, settings: Settings | None = None, providers: dict[str, LLMProvider] | None = None, **overrides):
        self.settings = settings or Settings.from_env(**overrides)
        self._providers: dict[str, LLMProvider] = dict(providers or {})
        self._lock = threading.Lock()

    def provider(self, name: str | None = None) -> LLMProvider:
        name = name or self.settings.default_provider
        with self._lock:
            if name not in self._providers:
                self._providers[name] = create_provider(name, self.settings)
            return self._providers[name]

    @staticmethod
    def providers() -> list[str]:
        return available()

    # ---- chat -------------------------------------------------------------

    def build_request(
        self,
        prompt: str = "",
        *,
        images: Iterable[Any] = (),
        system: str | None = None,
        history: Iterable[Message] = (),
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ChatRequest:
        limit = self.settings.max_image_bytes
        if isinstance(images, (str, bytes)):  # a single image passed without a list
            images = [images]
        urls = [to_image_url(i, max_bytes=limit) for i in images]
        if not (prompt or "").strip() and not urls:
            raise BadInput("send a prompt, an image, or both")

        messages: list[Message] = []
        if system:
            messages.append(Message("system", system))
        for m in history:
            messages.append(Message(m.role, m.text, [to_image_url(i, max_bytes=limit) for i in m.images]))
        messages.append(Message("user", prompt or "", urls))
        return ChatRequest(messages, model=model, temperature=temperature, max_tokens=max_tokens, extra=extra or {})

    def chat(self, prompt: str = "", *, provider: str | None = None, **kw) -> ChatResult:
        """Blocking call. kw: images, system, history, model, temperature, max_tokens, extra."""
        return self.provider(provider).chat(self.build_request(prompt, **kw))

    async def achat(self, prompt: str = "", *, provider: str | None = None, **kw) -> ChatResult:
        """Async call, same arguments as chat()."""
        return await self.provider(provider).achat(self.build_request(prompt, **kw))

    def ask(self, prompt: str = "", **kw) -> str:
        return self.chat(prompt, **kw).text

    async def aask(self, prompt: str = "", **kw) -> str:
        return (await self.achat(prompt, **kw)).text

    def list_models(self, provider: str | None = None, *, free: bool = False, vision: bool = False) -> list[dict]:
        items = self.provider(provider).list_models()
        if free:
            items = [m for m in items if _is_free(m)]
        if vision:
            items = [m for m in items if "image" in ((m.get("architecture") or {}).get("input_modalities") or [])]
        return items

    # ---- lifecycle --------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            for p in self._providers.values():
                p.close()
            self._providers.clear()

    async def aclose(self) -> None:
        for p in list(self._providers.values()):
            await p.aclose()
        self._providers.clear()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()


def _is_free(m: dict) -> bool:
    pricing = m.get("pricing") or {}
    try:
        return float(pricing.get("prompt", 1)) == 0 and float(pricing.get("completion", 1)) == 0
    except (TypeError, ValueError):
        return False


# ---- module-level shortcuts (shared default client) -----------------------

_default: LLMClient | None = None
_default_lock = threading.Lock()


def get_client() -> LLMClient:
    global _default
    with _default_lock:
        if _default is None:
            _default = LLMClient()
        return _default


def set_client(client: LLMClient | None) -> None:
    """Replace the shared client (e.g. different settings, or a fake in tests)."""
    global _default
    with _default_lock:
        _default = client


def ask(prompt: str = "", **kw) -> str:
    """Simplest call, returns only the answer text: ask("...", images=["chart.png"])."""
    return get_client().ask(prompt, **kw)


async def aask(prompt: str = "", **kw) -> str:
    return await get_client().aask(prompt, **kw)


def chat(prompt: str = "", **kw) -> ChatResult:
    """Blocking call returning the full ChatResult (text, model, usage...)."""
    return get_client().chat(prompt, **kw)


async def achat(prompt: str = "", **kw) -> ChatResult:
    return await get_client().achat(prompt, **kw)


def list_models(**kw) -> list[dict]:
    return get_client().list_models(**kw)
