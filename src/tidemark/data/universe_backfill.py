"""Daily candle backfill for ACTIVE market_registry symbols (Phase 6,
Merge 2A, PART B).

Reuses the existing `data/ingest.py` backfill path unchanged — same
chunked upsert, same per-symbol/timeframe failure isolation, same
COMPLETED/PARTIAL/FAILED run status — for exactly one timeframe (1D).
This module's own job is narrow: pick the symbol set (every ACTIVE
`market_registry` row for the venue) and, once ingestion finishes, record
each symbol's actual candle coverage back onto its registry row. It
computes no metric and no eligibility — that is Merge 2B.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from tidemark.data.exchange import ExchangeClient
from tidemark.data.ingest import RunOutcome, SymbolTimeframeOutcome, run_backfill
from tidemark.data.store import TidemarkStore

DAILY_TIMEFRAME = "1d"

# 30-day median window (UNIV-02/07) plus margin - not itself a rulebook
# parameter, just enough daily history for that window to always be full.
DEFAULT_BACKFILL_DAYS = 120


@dataclass(frozen=True)
class UniverseBackfillOutcome:
    """The result of one `tidemark universe backfill` run."""

    run_outcome: RunOutcome
    symbols_attempted: int
    symbols_with_coverage_updated: int


def active_symbols(store: TidemarkStore, venue: str) -> list[str]:
    """Every ACTIVE `market_registry` symbol for `venue`."""
    return [row.symbol for row in store.market_registry(venue) if row.status == "ACTIVE"]


def run_universe_backfill(
    store: TidemarkStore,
    exchange: ExchangeClient,
    venue: str,
    days: int = DEFAULT_BACKFILL_DAYS,
    now: dt.datetime | None = None,
    on_outcome: Callable[[SymbolTimeframeOutcome], None] | None = None,
) -> UniverseBackfillOutcome:
    """Backfill `days` of closed 1D candles for every ACTIVE registry
    symbol, via the unmodified `data/ingest.py` path, then update each
    successfully-fetched symbol's `first_candle_seen_at`/
    `last_candle_seen_at` from what is actually stored.

    `on_outcome`, if given, is called once per symbol as it finishes —
    see `run_backfill`; useful for progress reporting across the several
    hundred symbols a full venue listing can contain.
    """
    symbols = active_symbols(store, venue)
    run_outcome = run_backfill(
        store, exchange, venue, symbols, [DAILY_TIMEFRAME], days, now=now, on_outcome=on_outcome
    )

    updated = 0
    for outcome in run_outcome.outcomes:
        if outcome.error is not None:
            continue
        candles = store.get_candles(venue, outcome.symbol, DAILY_TIMEFRAME)
        if not candles:
            continue
        store.record_candle_coverage(
            venue, outcome.symbol, candles[0].open_time, candles[-1].close_time
        )
        updated += 1

    return UniverseBackfillOutcome(
        run_outcome=run_outcome,
        symbols_attempted=len(symbols),
        symbols_with_coverage_updated=updated,
    )
