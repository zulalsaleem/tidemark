"""Average True Range.

Section 1 of the rulebook fixes the distance unit as ATR(14) on the 4H
timeframe. All tolerance/threshold parameters (level tolerance, minimum Fib
leg, swing cluster distance, reaction test) are expressed in units of this
ATR.
"""

from __future__ import annotations

import pandas as pd


def atr(candles: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Average True Range over closed candles only.

    `candles` must contain only closed candles, ordered oldest to newest,
    with `high`, `low`, and `close` columns. ATR at index i uses only
    candles 0..i (no look-ahead).

    True range at i: max(high_i - low_i, |high_i - prev_close|,
    |low_i - prev_close|). The first candle has no previous close, so its
    true range is just high_0 - low_0.

    ATR uses Wilder's smoothing: the value at index `period - 1` (the
    period-th candle) is the simple average of the first `period` true
    ranges; each subsequent value is
    `(prev_atr * (period - 1) + true_range) / period`.

    Returns a `pd.Series` aligned with `candles.index`, holding `None` for
    every index before `period` candles are available.
    """
    highs = candles["high"]
    lows = candles["low"]
    closes = candles["close"]
    prev_closes = closes.shift(1)

    true_range = pd.concat(
        [
            highs - lows,
            (highs - prev_closes).abs(),
            (lows - prev_closes).abs(),
        ],
        axis=1,
    ).max(axis=1)
    true_range.iloc[0] = highs.iloc[0] - lows.iloc[0]

    values: list[float | None] = [None] * len(candles)
    running: float | None = None
    for i in range(len(candles)):
        if i < period - 1:
            continue
        if i == period - 1:
            running = true_range.iloc[:period].mean()
        else:
            running = (running * (period - 1) + true_range.iloc[i]) / period
        values[i] = running

    return pd.Series(values, index=candles.index, name="atr")
