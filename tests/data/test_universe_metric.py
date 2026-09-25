"""The universe-selection volume metric (Phase 6, Merge 2B, PART A).

Pure functions, no store/network - see docs/adr/0009's UNIV-02/07.
"""

from __future__ import annotations

import datetime as dt

from tidemark.data.models import Candle
from tidemark.data.universe_metric import (
    METRIC_NAME,
    METRIC_WINDOW_DAYS,
    derived_quote_volume,
    median_daily_derived_quote_volume_30d,
)

BASE = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)


def _daily(days_ago: int, close: float, volume: float) -> Candle:
    """A closed daily candle whose `close_time` is `days_ago` days before
    `BASE` — so `days_ago=0` closes exactly at BASE, `days_ago=1` closed
    one day before it, and so on.
    """
    close_time = BASE - dt.timedelta(days=days_ago)
    return Candle(
        venue="binanceusdm",
        symbol="BTC/USDT:USDT",
        timeframe="1d",
        open_time=close_time - dt.timedelta(days=1),
        close_time=close_time,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        fetched_at=BASE,
    )


def test_metric_name_and_window_are_the_documented_constants() -> None:
    assert METRIC_NAME == "MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D"
    assert METRIC_WINDOW_DAYS == 30


def test_derived_quote_volume_is_base_volume_times_close() -> None:
    candle = _daily(0, close=10.0, volume=5.0)
    assert derived_quote_volume(candle) == 50.0


def test_median_over_exactly_30_closed_daily_candles() -> None:
    as_of = BASE
    # 30 candles with derived quote volume 1..30 (close=1, volume=i) - median
    # of 1..30 is 15.5.
    candles = [_daily(days_ago=i, close=1.0, volume=float(i + 1)) for i in range(30)][::-1]
    result = median_daily_derived_quote_volume_30d(candles, as_of)
    assert result == 15.5


def test_fewer_than_30_closed_candles_returns_none() -> None:
    candles = [_daily(days_ago=i, close=1.0, volume=100.0) for i in range(29)]
    assert median_daily_derived_quote_volume_30d(candles, BASE) is None


def test_never_computes_on_a_partial_window_even_with_extra_history() -> None:
    """31 candles exist, but only 29 have closed as of `as_of` - the
    metric must never silently compute a median over those 29; it must
    return None, not a median over a short window."""
    candles = [_daily(days_ago=i, close=1.0, volume=100.0) for i in range(31)]
    as_of = BASE - dt.timedelta(days=3)  # only the oldest 29 have closed by then

    result = median_daily_derived_quote_volume_30d(candles, as_of)

    assert result is None


def test_uses_only_the_trailing_30_not_older_history() -> None:
    # 40 candles: the oldest 10 have an extreme value that would skew the
    # median if included; only the trailing 30 (volume=10) must count.
    old = [_daily(days_ago=39 - i, close=1.0, volume=100_000.0) for i in range(10)]
    recent = [_daily(days_ago=29 - i, close=1.0, volume=10.0) for i in range(30)]
    candles = old + recent

    result = median_daily_derived_quote_volume_30d(candles, BASE)

    assert result == 10.0


def test_future_candles_beyond_as_of_never_influence_the_metric() -> None:
    as_of = BASE - dt.timedelta(days=10)
    closed = [_daily(days_ago=10 + i, close=1.0, volume=10.0) for i in range(30)]
    future = [_daily(days_ago=i, close=1.0, volume=999_999.0) for i in range(10)]  # after as_of
    candles = closed + future

    result = median_daily_derived_quote_volume_30d(candles, as_of)

    assert result == 10.0
