# llm_service

A Python library the trade bot uses to call LLMs (text + images). There is no server and no open
port: the bot imports it and calls functions.

It is kept separate from the bot:
- It lives in its own folder, has its own `.env`, and imports nothing from `src/`.
- `llm_service/.env` is read only by this library and is **not** loaded into the bot's `os.environ`.

The first provider is **OpenRouter**, and the default model is **`openrouter/free`**. That router
picks a suitable free model for each request, and a vision-capable one when images are attached.
`result.model` tells you which model answered.

## Setup

```bash
cp llm_service/.env.example llm_service/.env   # fill in OPENROUTER_API_KEY
```

The library only needs `httpx` and `python-dotenv`. Both are already in the bot's
`requirements.txt`, so the bot's venv works as is.

## Using it from the bot

The bot runs from the repo root (`python -m src.live.run`), so it can import the package directly:

```python
from llm_service import ask, chat, achat, LLMError

# 1. Simplest: get just the answer text (str)
text = ask("Summarize this news: ...")

# 2. Text + images. An image can be: a file path, bytes, BytesIO, an open file, base64, a data URL or an http URL
text = ask("What is the trend on this chart?", images=["res/chart.png"])
text = ask("Compare these two timeframes", images=[png_bytes_1h, png_bytes_4h])

# 3. Get the full result
r = chat("...", images=[buf], system="You are a trader, answer briefly",
         model="google/gemma-4-31b-it:free", temperature=0.3, max_tokens=500)
r.text, r.model, r.usage, r.finish_reason

# 4. In async code (telegram bot, websocket loop...)
r = await achat("...", images=[photo_bytes])
text = await aask("...")

# Error handling (missing key, rate limit, bad image format...)
try:
    text = ask("...")
except LLMError as e:
    log.warning("LLM error: %s", e)
```

For a different configuration, create a separate client (it is thread-safe, so reuse it):

```python
from llm_service import LLMClient

vision = LLMClient(default_model="google/gemma-4-31b-it:free", timeout=60)
vision.ask("...", images=["a.png"])
```

Multi-turn conversation:

```python
from llm_service import chat, Message
chat("What about the 4h chart?", history=[Message("user", "Analyze BTC 1h"), Message("assistant", "...")])
```

## Quick test from the terminal (run from the repo root)

```bash
.venv/bin/python -m llm_service chat "Hello"
.venv/bin/python -m llm_service chat "What is in this chart?" -i chart.png
.venv/bin/python -m llm_service models --free --vision    # free models that accept images
```

## Configuration (`llm_service/.env`)

| Variable | Default | |
|---|---|---|
| `OPENROUTER_API_KEY` | | required |
| `LLM_DEFAULT_MODEL` | `openrouter/free` | |
| `LLM_DEFAULT_PROVIDER` | `openrouter` | |
| `LLM_TIMEOUT` | `120` | seconds |
| `LLM_MAX_RETRIES` | `2` | retries on 429 / 5xx / network errors |
| `LLM_MAX_IMAGE_MB` | `10` | |

Priority: arguments passed in code > environment variables > `llm_service/.env`.

## Layout

```
llm_service/
  __init__.py        # ask, aask, chat, achat, LLMClient, ...
  client.py          # LLMClient + the shared default client
  config.py          # Settings
  schema.py          # Message, ChatRequest, ChatResult, LLMError
  media.py           # normalizes images to data URLs, checks type and size
  providers/
    base.py          # LLMProvider interface (sync chat + async achat)
    openrouter.py
  tests/             # run offline, OpenRouter is mocked
```

Adding a provider (OpenAI, Gemini, Ollama...):
1. Create `providers/<name>.py`, subclass `LLMProvider` and decorate it with `@register("<name>")`.
2. Import it at the bottom of `providers/__init__.py`.
3. Call it with `chat(..., provider="<name>")`.

## Tests

```bash
cd llm_service
python -m venv .venv && .venv/bin/pip install httpx python-dotenv pytest pytest-asyncio
.venv/bin/python -m pytest -q
```

## Notes

- Free models have low rate limits. The library retries 429 with backoff, then raises `LLMError`.
- `ask` / `chat` block until the answer arrives (can take a few seconds). In async code use
  `aask` / `achat` so the event loop is not blocked.
- The bot's Dockerfile only copies `src/`. To use this library in Docker, add
  `COPY llm_service/ /app/llm_service/`.
