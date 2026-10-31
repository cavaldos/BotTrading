"""Settings. Priority: arguments passed in code > environment variables > llm_service/.env.

The .env file is read without being loaded into os.environ, so the bot's environment stays untouched.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from pathlib import Path

from dotenv import dotenv_values

SERVICE_DIR = Path(__file__).resolve().parent

_ENV_NAMES = {
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "openrouter_base_url": "OPENROUTER_BASE_URL",
    "openrouter_app_name": "OPENROUTER_APP_NAME",
    "openrouter_site_url": "OPENROUTER_SITE_URL",
    "default_provider": "LLM_DEFAULT_PROVIDER",
    "default_model": "LLM_DEFAULT_MODEL",
    "timeout": "LLM_TIMEOUT",
    "max_retries": "LLM_MAX_RETRIES",
    "max_image_mb": "LLM_MAX_IMAGE_MB",
}


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_app_name: str = "bot-trade-llm"
    openrouter_site_url: str = ""

    default_provider: str = "openrouter"
    default_model: str = "openrouter/free"

    timeout: float = 120.0
    max_retries: int = 2
    max_image_mb: float = 10.0

    @property
    def max_image_bytes(self) -> int:
        return int(self.max_image_mb * 1024 * 1024)

    @classmethod
    def from_env(cls, env_file: str | Path | None = None, **overrides) -> "Settings":
        path = Path(env_file) if env_file else SERVICE_DIR / ".env"
        file_vals = dotenv_values(path) if path.is_file() else {}
        types = {f.name: f.type for f in fields(cls)}

        values = {}
        for attr, env_name in _ENV_NAMES.items():
            raw = os.environ.get(env_name) or file_vals.get(env_name) or ""
            raw = raw.strip()
            if not raw:
                continue
            kind = types[attr]
            values[attr] = float(raw) if kind == "float" else int(raw) if kind == "int" else raw

        settings = cls(**values)
        overrides = {k: v for k, v in overrides.items() if v is not None}
        return replace(settings, **overrides) if overrides else settings
