"""Exceptions for the Coinalyze market-intelligence client.

All of these are plain, typed exceptions with no secret material in their
message or attributes - the API key must never appear in a traceback. See
docs/adr/0011-market-intelligence-layer.md.
"""

from __future__ import annotations


class CoinalyzeError(Exception):
    """Base class for every error this package raises."""


class MissingApiKeyError(CoinalyzeError):
    """`TIDEMARK_COINALYZE_API_KEY` is unset or empty.

    Raised eagerly (client construction), not on first call, so a caller
    can report this cleanly before attempting any network I/O.
    """

    def __init__(self) -> None:
        super().__init__("TIDEMARK_COINALYZE_API_KEY is not set")


class UnsupportedVenueError(CoinalyzeError):
    """No known Coinalyze exchange code for this ccxt venue id.

    Symbol mapping (see `symbols.py`) is mechanical only for venues we
    have an explicit exchange-code mapping for. An unmapped venue must
    fail closed rather than guess a code.
    """

    def __init__(self, venue: str) -> None:
        self.venue = venue
        super().__init__(f"no Coinalyze exchange-code mapping for venue {venue!r}")


class CoinalyzeConnectionError(CoinalyzeError):
    """The request never reached Coinalyze (timeout, DNS, TLS, refused, ...).

    Mirrors `notify.telegram.TelegramSendError(kind="connection")`: this
    says nothing about whether the API key or symbol is valid.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class CoinalyzeHttpError(CoinalyzeError):
    """Coinalyze answered with a non-2xx HTTP status.

    `retry_after` is the parsed `Retry-After` header (seconds), if the
    response carried one - only ever populated for 429s in practice.
    """

    def __init__(self, status_code: int, detail: str, retry_after: float | None = None) -> None:
        self.status_code = status_code
        self.detail = detail
        self.retry_after = retry_after
        super().__init__(f"HTTP {status_code}: {detail}")


class RateLimitedError(CoinalyzeError):
    """Every bounded retry against a 429 was exhausted.

    Coinalyze's documented limit is 40 calls/minute per key, and each
    symbol in a comma-separated request consumes one call - see
    docs/adr/0011. This is a deliberate give-up, never an infinite retry.
    """

    def __init__(self, attempts: int, last_retry_after: float | None) -> None:
        self.attempts = attempts
        self.last_retry_after = last_retry_after
        super().__init__(f"rate limited after {attempts} attempt(s)")


class TelegramError(Exception):
    """Base class for the /coin bot's own Telegram-client errors.

    Deliberately its own hierarchy, not shared with `notify.telegram`'s
    `TelegramSendError` - see `telegram_client.py` for why this bot never
    imports `notify.telegram` at all.
    """


class MissingBotTokenError(TelegramError):
    """`TIDEMARK_TELEGRAM_BOT_TOKEN` is unset or empty."""

    def __init__(self) -> None:
        super().__init__("TIDEMARK_TELEGRAM_BOT_TOKEN is not set")


class TelegramConnectionError(TelegramError):
    """A getUpdates/sendMessage request never reached Telegram at all."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class TelegramHttpError(TelegramError):
    """Telegram answered with a non-2xx HTTP status."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")
