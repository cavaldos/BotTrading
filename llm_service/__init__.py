"""LLM library for the trade bot (no server, no port). Kept in its own folder, with its own .env.

    from llm_service import ask, chat, achat

    ask("Hello")                                                  # -> str
    ask("What is the trend on this chart?", images=["chart.png"]) # text + image
    r = chat("...", images=[png_bytes], system="Answer briefly")  # -> ChatResult (text, model, usage)
    r = await achat("...", images=[buf])                  # inside async code (telegram bot)
"""

from .client import LLMClient, aask, achat, ask, chat, get_client, list_models, set_client
from .config import Settings
from .schema import BadInput, ChatRequest, ChatResult, LLMError, Message

__all__ = [
    "BadInput",
    "ChatRequest",
    "ChatResult",
    "LLMClient",
    "LLMError",
    "Message",
    "Settings",
    "aask",
    "achat",
    "ask",
    "chat",
    "get_client",
    "list_models",
    "set_client",
]
