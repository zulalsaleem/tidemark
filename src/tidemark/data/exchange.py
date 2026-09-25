"""Read-only market-data client, backed by ccxt.

Fetches closed OHLCV candles from a venue's public market-data endpoints
only. The underlying ccxt exchange instance is always constructed with no
credentials — this client never places orders, never holds private keys,
and never has exchange trading permissions. See CLAUDE.md and
docs/adr/0002-canonical-market-data-venue.md.

Venue is configuration, not code: any ccxt exchange id that exposes
`fetch_ohlcv` for unified perpetual symbols (e.g. "binanceusdm", "bitget",
"mexc") works without changes here.

`list_perpetual_symbols` (Phase 6, Merge 2A) is a second, separate
read-only call — ccxt's unified `load_markets`, for venue symbol
discovery — added alongside candle fetching without changing it.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import ccxt

from tidemark.data.timeframes import TIMEFRAME_DURATIONS

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 5
DEFAULT_BASE_BACKOFF_SECONDS = 1.0


@dataclass(frozen=True)
class RawCandle:
    """A closed OHLCV candle as returned by the exchange client."""

    open_time: dt.datetime
    close_time: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class MarketListing:
    """One symbol's entry in the venue's current market catalog, as
    filtered by `ExchangeClient.list_perpetual_symbols` (Phase 6, Merge
    2A, PART A: venue discovery).
    """

    symbol: str
    quote_currency: str
    contract_type: str


def build_exchange(venue: str) -> Any:
    """Construct a ccxt exchange instance for `venue` with no credentials."""
    try:
        exchange_class = getattr(ccxt, venue)
    except AttributeError as exc:
        raise ValueError(f"unknown ccxt venue: {venue!r}") from exc
    exchange = exchange_class({"enableRateLimit": True})
    assert not exchange.apiKey, "exchange must be constructed with no API key"
    assert not exchange.secret, "exchange must be constructed with no API secret"
    return exchange


class ExchangeClient:
    """Fetches closed OHLCV candles from a configured public venue.

    Holds no credentials — `apiKey`/`secret` on the underlying ccxt
    instance are asserted empty at construction time.
    """

    def __init__(
        self,
        venue: str = "binanceusdm",
        exchange: Any | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_backoff_seconds: float = DEFAULT_BASE_BACKOFF_SECONDS,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._venue = venue
        self._exchange = exchange if exchange is not None else build_exchange(venue)
        assert not self._exchange.apiKey, "exchange must hold no API key"
        assert not self._exchange.secret, "exchange must hold no API secret"
        self._max_retries = max_retries
        self._base_backoff_seconds = base_backoff_seconds
        self._sleep_fn = sleep_fn
        self._now_fn = now_fn or (lambda: dt.datetime.now(dt.UTC))

    def fetch_closed_candles(
        self,
        symbol: str,
        timeframe: str,
        since: dt.datetime,
        until: dt.datetime,
    ) -> list[RawCandle]:
        """Fetch closed candles for `symbol`/`timeframe` in `[since, until)`.

        Paginates through the exchange's OHLCV endpoint. A candle is
        closed iff `open_time + timeframe_duration <= now_utc` — the
        currently forming candle is never returned. `now_utc` comes from
        the client's clock (real by default, injectable for tests).
        """
        if timeframe not in TIMEFRAME_DURATIONS:
            raise ValueError(f"unsupported timeframe: {timeframe!r}")
        duration = TIMEFRAME_DURATIONS[timeframe]
        now = self._now_fn()

        candles: list[RawCandle] = []
        since_ms = _to_ms(since)
        until_ms = _to_ms(until)

        while since_ms < until_ms:
            batch = self._fetch_with_retry(symbol, timeframe, since_ms)
            if not batch:
                break

            for row in batch:
                open_ms = int(row[0])
                if open_ms < since_ms or open_ms >= until_ms:
                    continue
                open_time = dt.datetime.fromtimestamp(open_ms / 1000, tz=dt.UTC)
                close_time = open_time + duration
                if close_time > now:
                    continue  # forming candle — never return it
                candles.append(
                    RawCandle(
                        open_time=open_time,
                        close_time=close_time,
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                    )
                )

            last_open_ms = int(batch[-1][0])
            next_since_ms = last_open_ms + int(duration.total_seconds() * 1000)
            progressed = next_since_ms > since_ms
            if not progressed:
                break  # exchange made no forward progress; avoid looping forever
            since_ms = next_since_ms

        return candles

    def list_perpetual_symbols(self, quote_currency: str = "USDT") -> list[MarketListing]:
        """List active perpetual ("swap") contracts quoted in `quote_currency`.

        A separate, additive call from `fetch_closed_candles` (Phase 6,
        Merge 2A, PART A) — uses ccxt's unified `load_markets`, never a
        venue-specific raw endpoint, so this stays exactly as
        venue-agnostic as candle fetching already is (ADR 0002). Never
        touches the candle-fetch path above.

        Filters to ccxt's unified `type == "swap"` (a perpetual contract,
        as opposed to a dated `"future"`), `quote == quote_currency`, and
        `active` per the venue's own flag — an inactive/delisted listing
        is simply excluded here, not returned with a flag. The caller
        (`data/discover.py`) is what turns "no longer in this list" into
        an `ABSENT_FROM_VENUE` registry status.
        """
        markets = self._exchange.load_markets()
        listings: list[MarketListing] = []
        for market in markets.values():
            if market.get("type") != "swap":
                continue
            if market.get("quote") != quote_currency:
                continue
            if not market.get("active"):
                continue
            listings.append(
                MarketListing(
                    symbol=market["symbol"],
                    quote_currency=market["quote"],
                    contract_type="perpetual",
                )
            )
        return listings

    def _fetch_with_retry(self, symbol: str, timeframe: str, since_ms: int) -> list:
        attempt = 0
        while True:
            try:
                return self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms)
            except ccxt.NetworkError as exc:
                attempt += 1
                if attempt > self._max_retries:
                    logger.error(
                        "exchange fetch failed after %d retries: venue=%s symbol=%s "
                        "timeframe=%s error=%s",
                        self._max_retries,
                        self._venue,
                        symbol,
                        timeframe,
                        exc,
                    )
                    raise
                backoff = self._base_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "transient exchange error (attempt %d/%d), retrying in %.1fs: "
                    "venue=%s symbol=%s timeframe=%s error=%s",
                    attempt,
                    self._max_retries,
                    backoff,
                    self._venue,
                    symbol,
                    timeframe,
                    exc,
                )
                self._sleep_fn(backoff)


def _to_ms(moment: dt.datetime) -> int:
    return int(moment.timestamp() * 1000)
