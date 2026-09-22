"""Retracement leg detection, minimum leg size, zone, and invalidation."""

import datetime as dt

import pandas as pd
import pytest

from tidemark.core.fib import (
    BEARISH,
    BULLISH,
    find_valid_leg,
    in_fib_zone,
    nearest_fib_level,
)
from tidemark.data.models import Swing

START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _swing(kind: str, price: float, formed_i: int, confirmed_i: int) -> Swing:
    return Swing(
        kind=kind,
        price=price,
        formed_at=START + dt.timedelta(hours=4 * formed_i),
        confirmed_at=START + dt.timedelta(hours=4 * confirmed_i),
        fractal_n=2,
    )


def test_bullish_leg_anchors_on_preceding_low_and_most_recent_high() -> None:
    atr_value = 10.0  # min leg = 20
    swings = [
        _swing("low", 100.0, 0, 2),
        _swing("high", 150.0, 4, 6),
    ]

    leg = find_valid_leg(swings, atr_value, direction=BULLISH)

    assert leg is not None
    assert leg.anchor_start == 100.0
    assert leg.anchor_end == 150.0
    assert leg.valid_from == START + dt.timedelta(hours=24)  # confirmed_at of the high


def test_bearish_leg_is_the_mirror() -> None:
    atr_value = 10.0
    swings = [
        _swing("high", 150.0, 0, 2),
        _swing("low", 100.0, 4, 6),
    ]

    leg = find_valid_leg(swings, atr_value, direction=BEARISH)

    assert leg is not None
    assert leg.anchor_start == 150.0
    assert leg.anchor_end == 100.0


def test_leg_shorter_than_2x_atr_produces_no_fib() -> None:
    atr_value = 10.0  # min leg = 20
    swings = [
        _swing("low", 100.0, 0, 2),
        _swing("high", 115.0, 4, 6),  # range 15 < 20
    ]

    leg = find_valid_leg(swings, atr_value, direction=BULLISH)

    assert leg is None


def test_leg_at_least_2x_atr_produces_a_fib() -> None:
    atr_value = 10.0  # min leg = 20
    swings = [
        _swing("low", 100.0, 0, 2),
        _swing("high", 120.0, 4, 6),  # range exactly 20
    ]

    leg = find_valid_leg(swings, atr_value, direction=BULLISH)

    assert leg is not None


def test_bullish_zone_and_levels() -> None:
    atr_value = 10.0
    swings = [
        _swing("low", 100.0, 0, 2),
        _swing("high", 200.0, 4, 6),  # range 100
    ]
    leg = find_valid_leg(swings, atr_value, direction=BULLISH)

    assert leg.level_500 == 150.0
    assert leg.level_618 == pytest.approx(138.2)
    assert leg.level_786 == pytest.approx(121.4)
    # zone = 0.500..0.786 extended by 0.25 * ATR (2.5) on both edges
    assert leg.zone_low == pytest.approx(121.4 - 2.5)
    assert leg.zone_high == pytest.approx(150.0 + 2.5)

    assert in_fib_zone(135.0, leg) is True
    assert in_fib_zone(160.0, leg) is False
    assert nearest_fib_level(151.0, leg) == "0.500"
    assert nearest_fib_level(122.0, leg) == "0.786"


def test_missing_end_or_start_swing_produces_no_leg() -> None:
    assert find_valid_leg([], atr_value=10.0, direction=BULLISH) is None
    # A confirmed high with no preceding confirmed low: still no leg.
    swings = [_swing("high", 150.0, 4, 6)]
    assert find_valid_leg(swings, atr_value=10.0, direction=BULLISH) is None


def test_invalid_direction_raises() -> None:
    with pytest.raises(ValueError, match="direction"):
        find_valid_leg([], atr_value=10.0, direction="sideways")


def test_invalidated_at_set_on_close_beyond_leg_start() -> None:
    atr_value = 10.0
    swings = [
        _swing("low", 100.0, 0, 2),
        _swing("high", 200.0, 4, 6),
    ]
    valid_from = START + dt.timedelta(hours=24)  # confirmed_at of the high, index 6
    candles = pd.DataFrame(
        {
            "close_time": [valid_from + dt.timedelta(hours=4 * i) for i in range(3)],
            "close": [150.0, 105.0, 90.0],  # breaches anchor_start (100) at index 2
        }
    )

    leg = find_valid_leg(swings, atr_value, direction=BULLISH, candles_4h=candles)

    assert leg.invalidated_at == candles["close_time"].iloc[2]


def test_wick_beyond_leg_start_does_not_invalidate() -> None:
    # invalidation only checks `close`, so a low wick under anchor_start
    # that still closes above it must not invalidate the leg.
    atr_value = 10.0
    swings = [
        _swing("low", 100.0, 0, 2),
        _swing("high", 200.0, 4, 6),
    ]
    valid_from = START + dt.timedelta(hours=24)
    candles = pd.DataFrame(
        {
            "close_time": [valid_from + dt.timedelta(hours=4 * i) for i in range(2)],
            "close": [150.0, 105.0],
        }
    )

    leg = find_valid_leg(swings, atr_value, direction=BULLISH, candles_4h=candles)

    assert leg.invalidated_at is None
