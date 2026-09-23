"""Environment-based configuration.

All configuration comes from environment variables (optionally loaded from a
local `.env` file that is never committed). Secret values are typed as
`SecretStr` so they never appear in logs, tracebacks, or `repr()` output.
See `.env.example` for the full list of supported keys.
"""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for Tidemark.

    Standing rule: secrets come from environment variables and are never
    logged. Do not add fields here that hold non-secret copies of secret
    values, and do not log `Settings` instances directly.
    """

    model_config = SettingsConfigDict(
        env_prefix="TIDEMARK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram (read-only alert delivery; bot needs no trading permissions)
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    # Market data (read-only; no exchange trading permissions)
    exchange_api_key: SecretStr | None = None
    exchange_base_url: str | None = None
    exchange_id: str = "binanceusdm"

    # Storage
    database_url: str = "sqlite:///tidemark.db"

    # Rulebook
    rulebook_dir: str = "docs/rulebook"

    # Logging
    log_level: str = "INFO"


def get_settings() -> Settings:
    """Load settings from the environment (and `.env` if present)."""
    return Settings()
