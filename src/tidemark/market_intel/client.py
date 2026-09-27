"""Raw Coinalyze HTTP client.

Read-only: every method here is a GET against a public Coinalyze
endpoint and returns parsed JSON (a `list`/`dict`), never writes
anything, and never touches `tidemark.db`. See
docs/adr/0011-market-intelligence-layer.md for why this client exists as
its own isolated package.

Rate limiting: Coinalyze documents 40 calls/minute per API key, and each
symbol in a comma-separated `symbols` list consumes one of those 40 -
verified live during the Coinalyze inspection. This client tracks call
timestamps for observability, and on a 429 response respects the
`Retry-After` header with a bounded backoff - it never retries
indefinitely (`RateLimitedError` once the bound is exceeded).

The API key is held as `SecretStr` (never a plain `str` after
construction) and is passed only in the `api_key` request header, never
in a URL or log message - it must never appear in a repr, a str, or a
traceback.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from pydantic import SecretStr

from tidemark.market_intel.errors import (
    CoinalyzeConnectionError,
    CoinalyzeHttpError,
    MissingApiKeyError,
    RateLimitedError,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.coinalyze.net/v1"
RATE_LIMIT_PER_MINUTE = 40
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_RETRY_AFTER_SECONDS = 65.0
DEFAULT_TIMEOUT_SECONDS = 10.0


def _http_get(url: str, api_key: str, timeout: float) -> str:
    """Default transport: a plain GET with the key in a header, exactly
    like `notify.telegram`'s `_http_send` uses `urllib` directly rather
    than adding an HTTP-library dependency.
    """
    request = urllib.request.Request(url, headers={"api_key": api_key}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = _error_detail(exc)
        retry_after = _parse_retry_after(exc.headers.get("Retry-After") if exc.headers else None)
        raise CoinalyzeHttpError(exc.code, detail, retry_after=retry_after) from exc
    except Exception as exc:
        raise CoinalyzeConnectionError(str(exc)) from exc


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read())
        message = payload.get("message")
        if message:
            return str(message)
    except Exception:
        pass
    return exc.reason or str(exc)


def _parse_retry_after(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class CoinalyzeClient:
    """Thin wrapper over Coinalyze's public REST API.

    `transport`/`sleep_fn`/`clock` are injectable (same pattern as
    `notify.telegram.TelegramNotifier`'s `send_fn`/`sleep_fn`) so tests
    never touch a real network or a real clock.
    """

    def __init__(
        self,
        api_key: str | SecretStr | None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_retry_after_seconds: float = DEFAULT_MAX_RETRY_AFTER_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Callable[[str, str, float], str] = _http_get,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if api_key is None or (isinstance(api_key, str) and not api_key):
            raise MissingApiKeyError()
        if isinstance(api_key, SecretStr) and not api_key.get_secret_value():
            raise MissingApiKeyError()

        self._api_key = api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        self._max_retries = max_retries
        self._max_retry_after_seconds = max_retry_after_seconds
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._sleep_fn = sleep_fn
        self._clock = clock
        self._call_timestamps: list[float] = []

    def __repr__(self) -> str:
        return "CoinalyzeClient(api_key=SecretStr('**********'))"

    @property
    def calls_in_last_minute(self) -> int:
        """Rolling count of calls made in the last 60s, for observability
        only - this client does not preemptively block on it, since
        Coinalyze's own 429 response is the authoritative signal.
        """
        cutoff = self._clock() - 60.0
        self._call_timestamps = [t for t in self._call_timestamps if t >= cutoff]
        return len(self._call_timestamps)

    def _get(self, path: str, params: dict[str, str], *, call_units: int = 1) -> Any:
        """GET `path` with `params`, retrying a 429 up to `max_retries`
        times honoring `Retry-After` (capped at `max_retry_after_seconds`
        per attempt). `call_units` is how many of the 40/min budget this
        request spends (one per symbol in a `symbols` list) - tracked for
        observability, logged on throttling, never logged with the key.
        """
        query = urllib.parse.urlencode(params)
        url = f"{BASE_URL}{path}?{query}"
        api_key = self._api_key.get_secret_value()

        attempt = 0
        last_retry_after: float | None = None
        while True:
            for _ in range(call_units):
                self._call_timestamps.append(self._clock())
            if self.calls_in_last_minute > RATE_LIMIT_PER_MINUTE:
                logger.warning(
                    "Coinalyze call budget likely exceeded (%d calls tracked in the last "
                    "minute, limit is %d) - expect a 429",
                    self.calls_in_last_minute,
                    RATE_LIMIT_PER_MINUTE,
                )
            try:
                body = self._transport(url, api_key, self._timeout_seconds)
                return json.loads(body)
            except CoinalyzeHttpError as exc:
                if exc.status_code != 429:
                    raise
                attempt += 1
                last_retry_after = exc.retry_after
                if attempt > self._max_retries:
                    logger.error(
                        "Coinalyze rate limit exceeded after %d attempt(s), giving up "
                        "(path=%s, last Retry-After=%s)",
                        attempt - 1,
                        path,
                        last_retry_after,
                    )
                    raise RateLimitedError(attempt - 1, last_retry_after) from exc
                wait = min(last_retry_after or 1.0, self._max_retry_after_seconds)
                logger.warning(
                    "Coinalyze rate limited (attempt %d/%d), waiting %.1fs (path=%s)",
                    attempt,
                    self._max_retries,
                    wait,
                    path,
                )
                self._sleep_fn(wait)

    # -- endpoints ------------------------------------------------------

    def future_markets(self) -> list[dict]:
        """`GET /future-markets` - the full listing, not scoped to any
        symbol, so this always costs exactly one call unit.
        """
        return self._get("/future-markets", {}, call_units=1)

    def open_interest(self, symbols: list[str], *, convert_to_usd: bool = True) -> list[dict]:
        return self._get(
            "/open-interest",
            {"symbols": ",".join(symbols), "convert_to_usd": _bool_param(convert_to_usd)},
            call_units=len(symbols),
        )

    def funding_rate(self, symbols: list[str]) -> list[dict]:
        return self._get("/funding-rate", {"symbols": ",".join(symbols)}, call_units=len(symbols))

    def predicted_funding_rate(self, symbols: list[str]) -> list[dict]:
        return self._get(
            "/predicted-funding-rate", {"symbols": ",".join(symbols)}, call_units=len(symbols)
        )

    def open_interest_history(
        self, symbol: str, interval: str, from_ts: int, to_ts: int, *, convert_to_usd: bool = True
    ) -> list[dict]:
        return self._get(
            "/open-interest-history",
            {
                "symbols": symbol,
                "interval": interval,
                "from": str(from_ts),
                "to": str(to_ts),
                "convert_to_usd": _bool_param(convert_to_usd),
            },
            call_units=1,
        )

    def long_short_ratio_history(
        self, symbol: str, interval: str, from_ts: int, to_ts: int
    ) -> list[dict]:
        return self._get(
            "/long-short-ratio-history",
            {"symbols": symbol, "interval": interval, "from": str(from_ts), "to": str(to_ts)},
            call_units=1,
        )

    def funding_rate_history(
        self, symbol: str, interval: str, from_ts: int, to_ts: int
    ) -> list[dict]:
        """Closed-period funding rate candles - distinct from `funding_rate`
        (the live point-in-time reading). Used by the Merge 3 derivatives-
        context classifier, which needs the CLOSED 1H funding sign and a
        comparison against the previous closed reading, neither of which
        the live endpoint can give.
        """
        return self._get(
            "/funding-rate-history",
            {"symbols": symbol, "interval": interval, "from": str(from_ts), "to": str(to_ts)},
            call_units=1,
        )

    def liquidation_history(
        self, symbol: str, interval: str, from_ts: int, to_ts: int, *, convert_to_usd: bool = True
    ) -> list[dict]:
        return self._get(
            "/liquidation-history",
            {
                "symbols": symbol,
                "interval": interval,
                "from": str(from_ts),
                "to": str(to_ts),
                "convert_to_usd": _bool_param(convert_to_usd),
            },
            call_units=1,
        )

    def ohlcv_history(self, symbol: str, interval: str, from_ts: int, to_ts: int) -> list[dict]:
        return self._get(
            "/ohlcv-history",
            {"symbols": symbol, "interval": interval, "from": str(from_ts), "to": str(to_ts)},
            call_units=1,
        )


def _bool_param(value: bool) -> str:
    return "true" if value else "false"
