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

    Implementation note: the pivot test is vectorized (via `Series.shift`)
    rather than a per-index Python loop with repeated `.iloc[]` slicing.
    `Series.shift(k)` at a boundary position (fewer than `k` candles
    available on that side) produces `NaN`, and pandas' `>`/`<` against
    `NaN` is always `False` — so a boundary position never qualifies as a
    pivot, which is exactly the same "needs a full window on both sides"
    rule the original `range(n, last - n + 1)` bound enforced explicitly.
    This changes nothing about which candles qualify as swings or in what
    order they're returned — only how many redundant per-call pandas
    slices it takes to find out, which is what made replaying this at
    every closed candle (`replay.report.replay_section1`, and now Phase 6
    Merge 2B's eligibility assessment calling that once per candidate
    symbol) impractically slow at real candle counts.
    """
    n = fractal_n
    highs = candles["high"]
    lows = candles["low"]

    is_swing_high = pd.Series(True, index=candles.index)
    is_swing_low = pd.Series(True, index=candles.index)
    for k in range(1, n + 1):
        is_swing_high &= (highs > highs.shift(k)) & (highs > highs.shift(-k))
        is_swing_low &= (lows < lows.shift(k)) & (lows < lows.shift(-k))

    swings: list[Swing] = []
    for kind, mask, prices in ((HIGH, is_swing_high, highs), (LOW, is_swing_low, lows)):
        for i in mask.to_numpy().nonzero()[0]:
            swings.append(
                Swing(
                    kind=kind,
                    price=float(prices.iloc[i]),
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
