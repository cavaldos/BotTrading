"""OpenRouter (OpenAI-compatible chat completions). Default model: openrouter/free."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

import httpx

from ..config import Settings
from ..schema import ChatRequest, ChatResult, LLMError
from . import register
from .base import LLMProvider

log = logging.getLogger("llm_service")

RETRY_STATUS = {408, 429, 500, 502, 503, 504}


@register("openrouter")
class OpenRouterProvider(LLMProvider):
    name = "openrouter"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._transport = transport  # tests inject httpx.MockTransport (works for sync and async)
        self._lock = threading.Lock()
        self._sync: httpx.Client | None = None
        self._async: httpx.AsyncClient | None = None
        self._async_loop: asyncio.AbstractEventLoop | None = None

    # ---- public API -------------------------------------------------------

    def chat(self, req: ChatRequest) -> ChatResult:
        payload = self.build_payload(req, self.settings.default_model)
        return self._parse(self._request_sync("POST", "/chat/completions", json=payload), payload)

    async def achat(self, req: ChatRequest) -> ChatResult:
        payload = self.build_payload(req, self.settings.default_model)
        return self._parse(await self._request_async("POST", "/chat/completions", json=payload), payload)

    def list_models(self) -> list[dict[str, Any]]:
        return self._request_sync("GET", "/models").get("data") or []

    def close(self) -> None:
        if self._sync:
            self._sync.close()
            self._sync = None

    async def aclose(self) -> None:
        self.close()
        if self._async:
            await self._async.aclose()
            self._async = None

    # ---- request building / parsing --------------------------------------

    @staticmethod
    def build_payload(req: ChatRequest, default_model: str) -> dict[str, Any]:
        messages = []
        for m in req.messages:
            if m.images:
                content: Any = [{"type": "text", "text": m.text}] if m.text else []
                content += [{"type": "image_url", "image_url": {"url": u}} for u in m.images]
            else:
                content = m.text
            messages.append({"role": m.role, "content": content})

        payload: dict[str, Any] = {"model": req.model or default_model, "messages": messages}
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.max_tokens is not None:
            payload["max_tokens"] = req.max_tokens
        payload.update(req.extra)
        return payload

    def _parse(self, data: dict[str, Any], payload: dict[str, Any]) -> ChatResult:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("OpenRouter returned no choices", provider=self.name)
        choice = choices[0]
        if choice.get("error"):
            raise LLMError(f"OpenRouter: {_error_message(choice['error'])}", provider=self.name)

        msg = choice.get("message") or {}
        return ChatResult(
            text=_content_text(msg.get("content")),
            model=data.get("model") or payload["model"],
            provider=self.name,
            finish_reason=choice.get("finish_reason"),
            usage=data.get("usage") or {},
            id=data.get("id"),
            reasoning=msg.get("reasoning"),
            raw=data,
        )

    # ---- HTTP -------------------------------------------------------------

    def _client_kwargs(self) -> dict[str, Any]:
        if not self.settings.openrouter_api_key:
            raise LLMError("OPENROUTER_API_KEY is not set (llm_service/.env)", provider=self.name)
        headers = {"Authorization": f"Bearer {self.settings.openrouter_api_key}"}
        if self.settings.openrouter_site_url:
            headers["HTTP-Referer"] = self.settings.openrouter_site_url
        if self.settings.openrouter_app_name:
            headers["X-Title"] = self.settings.openrouter_app_name
        kw: dict[str, Any] = {
            "base_url": self.settings.openrouter_base_url.rstrip("/"),
            "headers": headers,
            "timeout": self.settings.timeout,
        }
        if self._transport is not None:
            kw["transport"] = self._transport
        return kw

    def _get_sync(self) -> httpx.Client:
        with self._lock:
            if self._sync is None:
                self._sync = httpx.Client(**self._client_kwargs())
            return self._sync

    def _get_async(self) -> httpx.AsyncClient:
        # An AsyncClient is tied to the event loop it first ran on; make a new one if the loop changed
        # (e.g. the caller uses asyncio.run() more than once).
        loop = asyncio.get_running_loop()
        if self._async is None or self._async_loop is not loop:
            self._async = httpx.AsyncClient(**self._client_kwargs())
            self._async_loop = loop
        return self._async

    def _request_sync(self, method: str, path: str, **kw) -> dict[str, Any]:
        client = self._get_sync()
        attempts = self.settings.max_retries + 1
        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                resp = client.request(method, path, **kw)
            except httpx.TransportError as e:
                if last:
                    raise LLMError(f"OpenRouter unreachable: {e}", provider=self.name) from e
                time.sleep(self._retry_wait(attempt, None, e))
                continue
            if resp.status_code in RETRY_STATUS and not last:
                time.sleep(self._retry_wait(attempt, resp))
                continue
            return self._check(resp)
        raise AssertionError("unreachable")

    async def _request_async(self, method: str, path: str, **kw) -> dict[str, Any]:
        client = self._get_async()
        attempts = self.settings.max_retries + 1
        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                resp = await client.request(method, path, **kw)
            except httpx.TransportError as e:
                if last:
                    raise LLMError(f"OpenRouter unreachable: {e}", provider=self.name) from e
                await asyncio.sleep(self._retry_wait(attempt, None, e))
                continue
            if resp.status_code in RETRY_STATUS and not last:
                await asyncio.sleep(self._retry_wait(attempt, resp))
                continue
            return self._check(resp)
        raise AssertionError("unreachable")

    def _retry_wait(self, attempt: int, resp: httpx.Response | None, exc: Exception | None = None) -> float:
        wait = min(2.0**attempt, 10.0)
        if resp is not None:
            try:
                wait = min(float(resp.headers.get("retry-after", "")), 30.0)
            except ValueError:
                pass
        reason = f"HTTP {resp.status_code}" if resp is not None else type(exc).__name__
        log.warning("OpenRouter %s, retry %d in %.1fs", reason, attempt + 1, wait)
        return wait

    def _check(self, resp: httpx.Response) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400 or (isinstance(data, dict) and data.get("error")):
            msg = _error_message(data["error"]) if data.get("error") else resp.text[:300]
            raise LLMError(f"OpenRouter {resp.status_code}: {msg}", status_code=resp.status_code, provider=self.name)
        return data


def _error_message(err: Any) -> str:
    if isinstance(err, dict):
        msg = err.get("message") or str(err)
        raw = (err.get("metadata") or {}).get("raw")
        return f"{msg} ({raw})" if isinstance(raw, str) and raw else msg
    return str(err)


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, list):  # some models return content parts
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)
