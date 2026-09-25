"""The universe-selection volume metric (Phase 6, Merge 2B, PART A).

UNIV-02/07 (docs/adr/0009-universe-selection-architecture.md): the
metric is DERIVED from stored OHLCV, never exchange-reported. ccxt's
unified `fetch_ohlcv` discards Binance's quote-asset volume field
entirely (see the Phase 6 preflight report), and obtaining it directly
would require a venue-specific raw endpoint call that breaks ADR 0002's
venue-agnostic contract. `derived_quote_volume = base_volume * close` is
computable from candles already stored today, with zero changes to
`data/exchange.py`.

Changing this formula, or the window/metric name below, is a methodology
version change (a new `UniverseSnapshot.methodology_version`), never a
silent redefinition of what an existing `metric_name` means.
"""

from __future__ import annotations

import datetime as dt
import statistics

from tidemark.data.models import Candle

METRIC_NAME = "MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D"
METRIC_WINDOW_DAYS = 30


def derived_quote_volume(candle: Candle) -> float:
    """One closed daily candle's derived quote volume: base_volume * close."""
    return candle.volume * candle.close


def median_daily_derived_quote_volume_30d(
    daily_candles: list[Candle], as_of: dt.datetime
) -> float | None:
    """Median derived quote volume over the trailing 30 CLOSED daily
    candles as of `as_of`.

    `daily_candles` must be closed 1D candles ordered oldest to newest
    (as `TidemarkStore.get_candles` already returns them). Truncates to
    `close_time <= as_of` first — the same look-ahead guard every other
    as-of computation in this codebase relies on — then requires at
    least `METRIC_WINDOW_DAYS` candles in that truncated history. Fewer
    than that returns `None`: the metric is never computed on a partial
    window: a symbol with, say, 12 days of history has no "trailing
    30-day median," not a median over 12 days pretending to be one.
    """
    closed = [c for c in daily_candles if c.close_time <= as_of]
    if len(closed) < METRIC_WINDOW_DAYS:
        return None
    trailing = closed[-METRIC_WINDOW_DAYS:]
    return statistics.median(derived_quote_volume(c) for c in trailing)
