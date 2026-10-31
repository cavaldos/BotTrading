"""Offline tests: OpenRouter is replaced by an httpx MockTransport."""

from __future__ import annotations

import asyncio
import base64
import io
import json

import httpx
import pytest

import llm_service
from llm_service import BadInput, LLMClient, LLMError, Message, Settings
from llm_service.media import to_image_url
from llm_service.providers.openrouter import OpenRouterProvider

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PNG_B64 = base64.b64encode(PNG).decode()
PNG_URL = f"data:image/png;base64,{PNG_B64}"


def make_client(handler, **settings_kw) -> tuple[LLMClient, list[dict]]:
    sent: list[dict] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        sent.append({"path": request.url.path, "headers": dict(request.headers),
                     "body": json.loads(request.content) if request.content else None})
        return handler(request)

    settings = Settings(openrouter_api_key="sk-test", max_retries=1, **settings_kw)
    provider = OpenRouterProvider(settings, transport=httpx.MockTransport(wrapped))
    return LLMClient(settings, providers={"openrouter": provider}), sent


def ok_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "gen-1",
        "model": "meta-llama/llama-4-maverick:free",
        "choices": [{"message": {"role": "assistant", "content": "hello back"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    })


# ---- images --------------------------------------------------------------

def test_image_inputs(tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(PNG)
    for src in (str(f), f, PNG, bytearray(PNG), io.BytesIO(PNG), PNG_B64, PNG_URL):
        assert to_image_url(src) == PNG_URL, type(src)
    with open(f, "rb") as fh:
        assert to_image_url(fh) == PNG_URL
    assert to_image_url("https://x.com/a.png") == "https://x.com/a.png"


def test_image_rejects_bad_input():
    for bad, kw in [(b"not an image", {}), (PNG, {"max_bytes": 10}),
                    ("data:text/plain;base64,aGVsbG8=", {}), ("/no/such/file.png", {}), (123, {})]:
        with pytest.raises(BadInput):
            to_image_url(bad, **kw)


# ---- sync ----------------------------------------------------------------

def test_text_chat_payload_and_result():
    client, sent = make_client(ok_response)
    r = client.chat("hi", system="be short", temperature=0.2)

    req = sent[0]
    assert req["path"] == "/api/v1/chat/completions"
    assert req["headers"]["authorization"] == "Bearer sk-test"
    assert req["body"]["model"] == "openrouter/free"
    assert req["body"]["messages"] == [{"role": "system", "content": "be short"}, {"role": "user", "content": "hi"}]
    assert req["body"]["temperature"] == 0.2
    assert r.text == str(r) == "hello back"
    assert r.model == "meta-llama/llama-4-maverick:free"
    assert r.usage["total_tokens"] == 7


def test_image_chat_uses_content_parts():
    client, sent = make_client(ok_response)
    client.chat("what is this?", images=[PNG, "https://x.com/c.jpg"])
    content = sent[0]["body"]["messages"][-1]["content"]
    assert content == [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": PNG_URL}},
        {"type": "image_url", "image_url": {"url": "https://x.com/c.jpg"}},
    ]


def test_single_image_without_list_and_image_only():
    client, sent = make_client(ok_response)
    client.chat(images=PNG_B64)
    assert sent[0]["body"]["messages"][-1]["content"] == [{"type": "image_url", "image_url": {"url": PNG_URL}}]


def test_history_and_model_override():
    client, sent = make_client(ok_response)
    client.chat("and now?", model="x/y:free", history=[Message("user", "first"), Message("assistant", "ok")])
    body = sent[0]["body"]
    assert body["model"] == "x/y:free"
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]


def test_empty_request_rejected():
    client, sent = make_client(ok_response)
    with pytest.raises(BadInput):
        client.chat("   ")
    assert sent == []


def test_retry_then_success():
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": {"message": "slow down"}})
        return ok_response(request)

    client, _ = make_client(flaky)
    assert client.ask("hi") == "hello back"
    assert calls["n"] == 2


def test_errors():
    client, _ = make_client(lambda r: httpx.Response(200, json={"error": {"code": 400, "message": "no endpoints"}}))
    with pytest.raises(LLMError, match="no endpoints"):
        client.chat("hi")

    client, _ = make_client(lambda r: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(LLMError, match="bad key") as e:
        client.chat("hi")
    assert e.value.status_code == 401

    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        LLMClient(Settings(openrouter_api_key="")).chat("hi")


def test_module_shortcuts_use_shared_client():
    client, sent = make_client(ok_response)
    llm_service.set_client(client)
    try:
        assert llm_service.ask("hi", images=[PNG]) == "hello back"
        assert llm_service.chat("hi").model.endswith(":free")
        assert asyncio.run(llm_service.aask("hi")) == "hello back"
    finally:
        llm_service.set_client(None)
    assert len(sent) == 3


def test_settings_overrides(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=from-file\nLLM_DEFAULT_MODEL=file/model\nLLM_TIMEOUT=30\n")
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "env/model")
    s = Settings.from_env(env)
    assert (s.openrouter_api_key, s.default_model, s.timeout) == ("from-file", "env/model", 30.0)
    assert Settings.from_env(env, default_model="arg/model").default_model == "arg/model"
    import os
    assert "OPENROUTER_API_KEY" not in os.environ or os.environ["OPENROUTER_API_KEY"] != "from-file"


# ---- async ---------------------------------------------------------------

async def test_async_chat():
    client, sent = make_client(ok_response)
    r = await client.achat("hi", images=[io.BytesIO(PNG)])
    await client.aclose()
    assert r.text == "hello back"
    assert sent[0]["body"]["messages"][-1]["content"][1]["image_url"]["url"] == PNG_URL


def test_async_across_event_loops():
    client, _ = make_client(ok_response)
    assert asyncio.run(client.aask("one")) == "hello back"
    assert asyncio.run(client.aask("two")) == "hello back"  # new loop, new AsyncClient
