"""Swing clustering into levels, and previous day/week high/low."""

import datetime as dt

import pandas as pd
import pytest

from tidemark.core.levels import (
    RESISTANCE,
    SUPPORT,
    SWING_HIGH_CLUSTER,
    SWING_LOW_CLUSTER,
    cluster_swings_into_levels,
    level_role,
    prev_period_high_low,
    prev_period_levels,
)
from tidemark.data.models import Level, Swing

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
    assert level.source == SWING_HIGH_CLUSTER
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

    sources = {level.source for level in levels}
    assert sources == {SWING_HIGH_CLUSTER, SWING_LOW_CLUSTER}


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


# --- level_role (Section 1 v1.1, RULE 1.7a) --------------------------------


def _level_at(price: float, kind: str = RESISTANCE, source: str = SWING_HIGH_CLUSTER) -> Level:
    return Level(
        kind=kind,
        price=price,
        zone_low=price - 1,
        zone_high=price + 1,
        touches=2,
        is_major=True,
        source=source,
        formed_at=START,
    )


def test_level_role_support_when_close_above_price() -> None:
    level = _level_at(100.0)
    assert level_role(level, close=101.0) == SUPPORT


def test_level_role_resistance_when_close_below_price() -> None:
    level = _level_at(100.0)
    assert level_role(level, close=99.0) == RESISTANCE


def test_level_role_tie_break_is_support() -> None:
    level = _level_at(100.0)
    assert level_role(level, close=100.0) == SUPPORT


def test_level_role_ignores_static_kind_and_source() -> None:
    # A level whose ORIGIN is a swing high (kind=RESISTANCE at formation)
    # must still read as SUPPORT once price has moved above it — role is
    # never derived from kind/source, only from (level.price, close).
    swing_high_origin_level = _level_at(100.0, kind=RESISTANCE, source=SWING_HIGH_CLUSTER)
    assert level_role(swing_high_origin_level, close=150.0) == SUPPORT

    swing_low_origin_level = _level_at(100.0, kind=SUPPORT, source=SWING_LOW_CLUSTER)
    assert level_role(swing_low_origin_level, close=50.0) == RESISTANCE
