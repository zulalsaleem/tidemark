"""ATR(14) with Wilder's smoothing, hand-computed against a small fixture."""

import pandas as pd

from tidemark.core.atr import atr


def _candles(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["high", "low", "close"])


def test_atr_none_before_period_candles_exist() -> None:
    # 13 candles is one short of the 14 required for a first ATR value.
    rows = [(10.0 + i, 9.0 + i, 9.5 + i) for i in range(13)]
    result = atr(_candles(rows), period=14)
    assert result.tolist() == [None] * 13


def test_atr_matches_hand_computed_value() -> None:
    # Simple fixture: flat true range of 1.0 on every candle (high - low == 1,
    # and close sits inside the range so the prev-close terms never win).
    # TR_i = 1.0 for all i since |high_i - prev_close| and |low_i - prev_close|
    # never exceed high_i - low_i here.
    rows = []
    price = 100.0
    for _ in range(16):
        rows.append((price + 1.0, price, price + 0.5))
        price += 0.5
    candles = _candles(rows)

    result = atr(candles, period=14)

    # First 13 values are None (candles 0..12).
    assert result.iloc[:13].isna().all()

    # ATR at index 13 (the 14th candle) = simple mean of the first 14 TRs.
    assert result.iloc[13] == 1.0

    # Wilder smoothing thereafter: constant TR of 1.0 keeps ATR at 1.0.
    assert result.iloc[14] == 1.0
    assert result.iloc[15] == 1.0


def test_atr_wilder_smoothing_with_varying_true_range() -> None:
    # Hand-computed fixture with a true-range spike after the initial window.
    rows = [
        (101, 99, 100),  # TR = 2 (no prev close)
        (102, 100, 101),  # TR = max(2, |102-100|, |100-100|) = 2
        (103, 101, 102),  # TR = 2
        (104, 102, 103),  # TR = 2
        (105, 103, 104),  # TR = 2
        (106, 104, 105),  # TR = 2
        (107, 105, 106),  # TR = 2
        (108, 106, 107),  # TR = 2
        (109, 107, 108),  # TR = 2
        (110, 108, 109),  # TR = 2
        (111, 109, 110),  # TR = 2
        (112, 110, 111),  # TR = 2
        (113, 111, 112),  # TR = 2
        (120, 112, 118),  # TR = max(8, |120-112|, |112-112|) = 8
    ]
    candles = _candles(rows)
    result = atr(candles, period=14)

    # First 13 true ranges are all 2.0 -> simple average at index 13.
    first_13_mean = sum([2.0] * 13 + [8.0]) / 14
    assert result.iloc[13] == first_13_mean
