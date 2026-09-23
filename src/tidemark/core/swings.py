"""Fractal swing detection.

Section 1 parameters: swing fractal N = 2, usable 8h after formation. Every
detected swing must record `formed_at` (when the fractal completed) and
`confirmed_at` (when it becomes usable) separately, per the standing rules.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from tidemark.data.models import Swing

HIGH = "high"
LOW = "low"


def find_swings(candles: pd.DataFrame, fractal_n: int) -> list[Swing]:
    """Detect fractal swing highs/lows over closed candles only.

    `candles` must contain only closed candles, ordered oldest to newest,
    with `high`, `low`, and `close_time` columns. `fractal_n` is the number
    of candles required on each side of a pivot for it to qualify as a
    swing point (Section 1: N = 2).

    A swing high at index i requires `high[i]` to be strictly greater than
    the highs of the `fractal_n` candles on each side (equal highs do not
    qualify). A swing low is the mirror, on lows.

    `formed_at` is the pivot candle's own close time. `confirmed_at` is the
    close time of the Nth candle to its right — the swing is not usable
    before that point. Only pivots with a full `fractal_n` candles on both
    sides can be detected; the most recent `fractal_n` candles can never
    yet have a confirmed (or even formed) swing centered on them.
    """
    swings: list[Swing] = []
    n = fractal_n
    last = len(candles) - 1

    for i in range(n, last - n + 1):
        pivot_high = candles["high"].iloc[i]
        left_highs = candles["high"].iloc[i - n : i]
        right_highs = candles["high"].iloc[i + 1 : i + 1 + n]
        if (pivot_high > left_highs).all() and (pivot_high > right_highs).all():
            swings.append(
                Swing(
                    kind=HIGH,
                    price=float(pivot_high),
                    formed_at=_to_datetime(candles["close_time"].iloc[i]),
                    confirmed_at=_to_datetime(candles["close_time"].iloc[i + n]),
                    fractal_n=n,
                )
            )

        pivot_low = candles["low"].iloc[i]
        left_lows = candles["low"].iloc[i - n : i]
        right_lows = candles["low"].iloc[i + 1 : i + 1 + n]
        if (pivot_low < left_lows).all() and (pivot_low < right_lows).all():
            swings.append(
                Swing(
                    kind=LOW,
                    price=float(pivot_low),
                    formed_at=_to_datetime(candles["close_time"].iloc[i]),
                    confirmed_at=_to_datetime(candles["close_time"].iloc[i + n]),
                    fractal_n=n,
                )
            )

    swings.sort(key=lambda s: s.formed_at)
    return swings


def confirmed_swings_as_of(swings: list[Swing], as_of: dt.datetime) -> list[Swing]:
    """Return only swings confirmed by `as_of` (`confirmed_at <= as_of`).

    Nothing downstream may use an unconfirmed swing.
    """
    return [s for s in swings if s.confirmed_at <= as_of]


def _to_datetime(value: object) -> dt.datetime:
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value  # type: ignore[return-value]
