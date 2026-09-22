"""Swing clustering into levels, and previous day/week high/low."""

import datetime as dt

import pandas as pd
import pytest

from tidemark.core.levels import (
    RESISTANCE,
    SUPPORT,
    cluster_swings_into_levels,
    prev_period_high_low,
    prev_period_levels,
)
from tidemark.data.models import Swing

START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _swing(kind: str, price: float, i: int) -> Swing:
    return Swing(
        kind=kind,
        price=price,
        formed_at=START + dt.timedelta(hours=4 * i),
        confirmed_at=START + dt.timedelta(hours=4 * (i + 2)),
        fractal_n=2,
    )


def test_two_swings_within_half_atr_form_a_level() -> None:
    atr_value = 10.0  # threshold = 5.0
    swings = [_swing("high", 100.0, 0), _swing("high", 103.0, 1)]

    levels = cluster_swings_into_levels(swings, atr_value)

    assert len(levels) == 1
    level = levels[0]
    assert level.kind == RESISTANCE
    assert level.price == 101.5
    assert level.touches == 2
    assert level.is_major is True
    assert level.zone_low == pytest.approx(101.5 - 2.5)
    assert level.zone_high == pytest.approx(101.5 + 2.5)


def test_swings_beyond_half_atr_do_not_cluster() -> None:
    atr_value = 10.0  # threshold = 5.0
    swings = [_swing("high", 100.0, 0), _swing("high", 110.0, 1)]

    levels = cluster_swings_into_levels(swings, atr_value)

    assert levels == []


def test_single_swing_never_forms_a_level() -> None:
    levels = cluster_swings_into_levels([_swing("low", 100.0, 0)], atr_value=10.0)
    assert levels == []


def test_highs_and_lows_cluster_separately() -> None:
    atr_value = 10.0
    swings = [
        _swing("high", 100.0, 0),
        _swing("high", 101.0, 1),
        _swing("low", 100.5, 2),
        _swing("low", 101.5, 3),
    ]

    levels = cluster_swings_into_levels(swings, atr_value)

    kinds = {level.kind for level in levels}
    assert kinds == {RESISTANCE, SUPPORT}
    assert len(levels) == 2


def _period_candles() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "high": [105.0, 110.0],
            "low": [95.0, 90.0],
            "close_time": [START, START + dt.timedelta(days=1)],
        }
    )


def test_prev_period_high_low_returns_the_last_closed_row() -> None:
    high, low = prev_period_high_low(_period_candles(), period="day")
    assert (high, low) == (110.0, 90.0)


def test_prev_period_high_low_rejects_invalid_period() -> None:
    with pytest.raises(ValueError, match="period"):
        prev_period_high_low(_period_candles(), period="month")


def test_prev_day_levels_are_not_major() -> None:
    levels = prev_period_levels(_period_candles(), period="day", atr_value=10.0)
    assert all(not level.is_major for level in levels)
    assert all(level.touches == 1 for level in levels)


def test_prev_week_levels_are_always_major() -> None:
    levels = prev_period_levels(_period_candles(), period="week", atr_value=10.0)
    assert all(level.is_major for level in levels)


def test_prev_period_levels_kinds_and_prices() -> None:
    levels = prev_period_levels(_period_candles(), period="day", atr_value=10.0)
    by_kind = {level.kind: level for level in levels}
    assert by_kind[RESISTANCE].price == 110.0
    assert by_kind[SUPPORT].price == 90.0
