"""Fractal swing detection (N=2): formed_at vs confirmed_at, strict equality."""

import datetime as dt

import pandas as pd

from tidemark.core.swings import HIGH, LOW, confirmed_swings_as_of, find_swings

START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _candles(highs: list[float], lows: list[float]) -> pd.DataFrame:
    close_times = [START + dt.timedelta(hours=4 * i) for i in range(len(highs))]
    return pd.DataFrame({"high": highs, "low": lows, "close_time": close_times})


def test_swing_high_detected_with_strict_fractal() -> None:
    # Index 2 is a clean pivot high: 10 > 8,9 on the left and 9,8 on the right.
    highs = [7, 8, 10, 9, 8]
    lows = [h - 5 for h in highs]
    candles = _candles(highs, lows)

    swings = find_swings(candles, fractal_n=2)

    high_swings = [s for s in swings if s.kind == HIGH]
    assert len(high_swings) == 1
    assert high_swings[0].price == 10
    assert high_swings[0].formed_at == candles["close_time"].iloc[2]
    assert high_swings[0].confirmed_at == candles["close_time"].iloc[4]


def test_swing_low_detected_with_strict_fractal() -> None:
    lows = [7, 6, 4, 5, 6]
    highs = [low + 5 for low in lows]
    candles = _candles(highs, lows)

    swings = find_swings(candles, fractal_n=2)

    low_swings = [s for s in swings if s.kind == LOW]
    assert len(low_swings) == 1
    assert low_swings[0].price == 4
    assert low_swings[0].formed_at == candles["close_time"].iloc[2]
    assert low_swings[0].confirmed_at == candles["close_time"].iloc[4]


def test_equal_highs_do_not_create_a_swing() -> None:
    # Pivot candidate at index 2 ties with its right-side neighbor at index 3.
    highs = [7, 8, 10, 10, 8]
    lows = [h - 5 for h in highs]
    candles = _candles(highs, lows)

    swings = find_swings(candles, fractal_n=2)

    assert [s for s in swings if s.kind == HIGH] == []


def test_equal_lows_do_not_create_a_swing() -> None:
    lows = [7, 6, 4, 4, 6]
    highs = [low + 5 for low in lows]
    candles = _candles(highs, lows)

    swings = find_swings(candles, fractal_n=2)

    assert [s for s in swings if s.kind == LOW] == []


def test_swing_not_returned_at_formed_at_and_is_returned_at_confirmed_at() -> None:
    highs = [7, 8, 10, 9, 8]
    lows = [h - 5 for h in highs]
    candles = _candles(highs, lows)
    swings = find_swings(candles, fractal_n=2)

    formed_at = candles["close_time"].iloc[2]
    confirmed_at = candles["close_time"].iloc[4]

    assert confirmed_swings_as_of(swings, formed_at) == []
    assert len(confirmed_swings_as_of(swings, confirmed_at)) == 1
    assert confirmed_swings_as_of(swings, confirmed_at)[0].price == 10
    # One tick before confirmation, still not usable.
    just_before = confirmed_at - dt.timedelta(seconds=1)
    assert confirmed_swings_as_of(swings, just_before) == []
