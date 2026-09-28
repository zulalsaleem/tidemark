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

    # Coinalyze (read-only derivatives data for the market_intel layer -
    # see docs/adr/0011). Strictly separate from the research engine's
    # market data below; never used for price structure.
    coinalyze_api_key: SecretStr | None = None

    # The /coin Telegram bot (Merge 2, see docs/adr/0011). Reuses
    # telegram_bot_token above; authorization is by this numeric chat id
    # ONLY - never username or display name. Any other chat is silently
    # ignored, never replied to. The offset file is a flat JSON file with
    # no relationship to tidemark.db - see market_intel/bot_state.py.
    telegram_allowed_chat_id: int | None = None
    telegram_bot_offset_file: str = "tidemark_telegram_offset.json"

    # Market data (read-only, public endpoints only; ccxt venue id, e.g.
    # "binanceusdm", "bitget", "mexc" — see docs/adr/0002)
    venue: str = "binanceusdm"

    # Comma-separated ccxt unified perpetual symbols, e.g. "BTC/USDT:USDT".
    # The rule for selecting a larger universe than this default is
    # NOT_DEFINED — see docs/architecture.md.
    symbols: str = "BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT,XRP/USDT:USDT,DOGE/USDT:USDT"

    # Default backfill depth in days.
    backfill_days: int = 180

    # Phase 6, Merge 3: how old the latest universe snapshot may be
    # before `tidemark run`/`tidemark observe run` stop trusting it and
    # fall back to TIDEMARK_SYMBOLS instead (see data/symbol_source.py).
    universe_staleness_hours: int = 48

    # /coin universe context: how old the latest `universe_context_cache`
    # row may be before /coin still shows it but states its age plainly.
    # Default 12h covers one missed run of a 4x-daily refresh-context
    # schedule without /coin ever falling silent about staleness - see
    # docs/adr/0011-market-intelligence-layer.md.
    universe_context_stale_after_hours: int = 12

    # Storage
    database_url: str = "sqlite:///tidemark.db"

    # Rulebook
    rulebook_dir: str = "docs/rulebook"

    # Logging
    log_level: str = "INFO"

    def symbol_list(self) -> list[str]:
        """Parse `symbols` into a trimmed, non-empty list."""
        return [s.strip() for s in self.symbols.split(",") if s.strip()]


def get_settings() -> Settings:
    """Load settings from the environment (and `.env` if present)."""
    return Settings()
