"""Closed-period boundary computation at awkward moments.

Coinalyze does not truncate to closed periods on its own (verified
live), so this pure function is what keeps every market_intel metric
honoring CLAUDE.md's "closed candles only" rule.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tidemark.market_intel.clamping import closed_period


def _utc(y, m, d, h=0, minute=0, s=0) -> dt.datetime:
    return dt.datetime(y, m, d, h, minute, s, tzinfo=dt.UTC)


def test_1037_utc_1h_period_is_0900_to_1000() -> None:
    period = closed_period(_utc(2026, 9, 27, 10, 37), "1hour")
    assert period.start == _utc(2026, 9, 27, 9, 0)
    assert period.close == _utc(2026, 9, 27, 10, 0)


def test_1037_utc_4h_period_is_0400_to_0800() -> None:
    period = closed_period(_utc(2026, 9, 27, 10, 37), "4hour")
    assert period.start == _utc(2026, 9, 27, 4, 0)
    assert period.close == _utc(2026, 9, 27, 8, 0)


def test_exactly_on_the_hour_uses_the_period_that_just_closed() -> None:
    period = closed_period(_utc(2026, 9, 27, 10, 0, 0), "1hour")
    assert period.start == _utc(2026, 9, 27, 9, 0)
    assert period.close == _utc(2026, 9, 27, 10, 0)


def test_one_second_before_the_boundary_uses_the_prior_period() -> None:
    period = closed_period(_utc(2026, 9, 27, 9, 59, 59), "1hour")
    assert period.start == _utc(2026, 9, 27, 8, 0)
    assert period.close == _utc(2026, 9, 27, 9, 0)


def test_exactly_on_a_4h_boundary_uses_the_period_that_just_closed() -> None:
    period = closed_period(_utc(2026, 9, 27, 8, 0, 0), "4hour")
    assert period.start == _utc(2026, 9, 27, 4, 0)
    assert period.close == _utc(2026, 9, 27, 8, 0)


def test_midnight_daily_boundary() -> None:
    period = closed_period(_utc(2026, 9, 27, 0, 0, 1), "daily")
    assert period.start == _utc(2026, 9, 26, 0, 0)
    assert period.close == _utc(2026, 9, 27, 0, 0)


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        closed_period(dt.datetime(2026, 9, 27, 10, 37), "1hour")  # noqa: DTZ001


def test_unknown_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown interval"):
        closed_period(_utc(2026, 9, 27, 10, 37), "7hour")
