"""Coordinates the exchange client and store for backfill/update runs.

Data-layer orchestration only — no indicators, no rulebook evaluation.
Fetches closed candles from the exchange, validates and upserts them into
the store, and records a `Run` with per symbol/timeframe counts. One
symbol/timeframe failing never aborts the others.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from tidemark.data.exchange import ExchangeClient
from tidemark.data.store import CandleUpsertResult, TidemarkStore

logger = logging.getLogger(__name__)

DEFAULT_UPDATE_FALLBACK_DAYS = 180


@dataclass(frozen=True)
class SymbolTimeframeOutcome:
    """The result of fetching+storing one symbol/timeframe combination."""

    symbol: str
    timeframe: str
    result: CandleUpsertResult | None
    error: str | None


@dataclass(frozen=True)
class RunOutcome:
    """The result of one full backfill/update run."""

    run_id: str
    status: str
    outcomes: list[SymbolTimeframeOutcome]


def run_backfill(
    store: TidemarkStore,
    exchange: ExchangeClient,
    venue: str,
    symbols: list[str],
    timeframes: list[str],
    days: int,
    now: dt.datetime | None = None,
) -> RunOutcome:
    """Backfill `days` of closed candles for each symbol/timeframe."""
    now = now or dt.datetime.now(dt.UTC)
    since = now - dt.timedelta(days=days)
    return _execute(store, exchange, venue, symbols, timeframes, "backfill", lambda *_: since, now)


def run_update(
    store: TidemarkStore,
    exchange: ExchangeClient,
    venue: str,
    symbols: list[str],
    timeframes: list[str],
    fallback_days: int = DEFAULT_UPDATE_FALLBACK_DAYS,
    now: dt.datetime | None = None,
) -> RunOutcome:
    """Fetch from each symbol/timeframe's last stored candle up to now.

    If nothing is stored yet for a symbol/timeframe, falls back to
    `fallback_days` of history, matching a plain backfill.
    """
    now = now or dt.datetime.now(dt.UTC)

    def since_fn(symbol: str, timeframe: str) -> dt.datetime:
        latest = store.latest_candle(venue, symbol, timeframe)
        if latest is not None:
            return latest.close_time
        return now - dt.timedelta(days=fallback_days)

    return _execute(store, exchange, venue, symbols, timeframes, "update", since_fn, now)


def _execute(
    store: TidemarkStore,
    exchange: ExchangeClient,
    venue: str,
    symbols: list[str],
    timeframes: list[str],
    command: str,
    since_fn: Callable[[str, str], dt.datetime],
    now: dt.datetime,
) -> RunOutcome:
    run_id = uuid.uuid4().hex
    started_at = now
    store.start_run(run_id, command, started_at)

    # If an exception escapes below, `status` stays FAILED and `finally`
    # still records that — a crash mid-run must never leave a RUNNING row
    # with no final update, or no row at all.
    status = "FAILED"
    outcomes: list[SymbolTimeframeOutcome] = []
    stats: dict[tuple[str, str], CandleUpsertResult] = {}
    try:
        outcomes = [
            _fetch_and_store(
                store, exchange, venue, symbol, timeframe, since_fn(symbol, timeframe), now
            )
            for symbol in symbols
            for timeframe in timeframes
        ]

        fail_count = sum(1 for o in outcomes if o.error is not None)
        success_count = len(outcomes) - fail_count
        if fail_count == 0:
            status = "COMPLETED"
        elif success_count == 0:
            status = "FAILED"
        else:
            status = "PARTIAL"

        stats = {(o.symbol, o.timeframe): o.result for o in outcomes if o.result is not None}
    finally:
        store.finish_run(run_id, now, status, stats)

    return RunOutcome(run_id=run_id, status=status, outcomes=outcomes)


def _fetch_and_store(
    store: TidemarkStore,
    exchange: ExchangeClient,
    venue: str,
    symbol: str,
    timeframe: str,
    since: dt.datetime,
    until: dt.datetime,
) -> SymbolTimeframeOutcome:
    try:
        candles = exchange.fetch_closed_candles(symbol, timeframe, since, until)
        result = store.upsert_candles(venue, symbol, timeframe, candles, fetched_at=until)
        return SymbolTimeframeOutcome(symbol, timeframe, result, None)
    except Exception as exc:
        logger.error(
            "fetch/store failed: venue=%s symbol=%s timeframe=%s error=%s",
            venue,
            symbol,
            timeframe,
            exc,
        )
        return SymbolTimeframeOutcome(symbol, timeframe, None, str(exc))
