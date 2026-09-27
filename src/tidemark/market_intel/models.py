"""Normalized market-intelligence data shapes.

Every metric carries its own status (`OK`/`NO_DATA`/`MARKET_NOT_FOUND`)
and, when `OK`, the exact window it describes: a period's `(start,
close)` for a closed-period metric, or an `updated_at` timestamp plus
`is_point_in_time=True` for a live reading (open interest, funding rate,
predicted funding rate). A caller can never mistake which window a value
describes, and a missing value is never rendered as zero - it is a
non-`OK` status with `value=None` and a human-readable `reason`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

OK = "OK"
NO_DATA = "NO_DATA"
MARKET_NOT_FOUND = "MARKET_NOT_FOUND"


@dataclass(frozen=True)
class PointInTimeMetric:
    """A live reading with no concept of a closed period - open
    interest, current funding rate, predicted funding rate.
    """

    status: str
    value: float | None
    unit: str
    updated_at: dt.datetime | None
    reason: str | None = None
    is_point_in_time: bool = True

    @classmethod
    def unavailable(cls, status: str, unit: str, reason: str) -> PointInTimeMetric:
        return cls(status=status, value=None, unit=unit, updated_at=None, reason=reason)


@dataclass(frozen=True)
class ClosedPeriodMetric:
    """A single value tied to one fully-closed period - OI change,
    futures volume, buy volume, sell volume.
    """

    status: str
    value: float | None
    unit: str
    period_start: dt.datetime | None
    period_close: dt.datetime | None
    reason: str | None = None
    is_point_in_time: bool = False

    @classmethod
    def unavailable(cls, status: str, unit: str, reason: str) -> ClosedPeriodMetric:
        return cls(
            status=status,
            value=None,
            unit=unit,
            period_start=None,
            period_close=None,
            reason=reason,
        )


@dataclass(frozen=True)
class LongShortRatioMetric:
    """Long/short ratio has no "current" endpoint on Coinalyze - only
    history - so this is always the latest CLOSED bucket, never the
    in-progress one.
    """

    status: str
    ratio: float | None
    long_pct: float | None
    short_pct: float | None
    percent_unit: str
    period_start: dt.datetime | None
    period_close: dt.datetime | None
    reason: str | None = None
    is_point_in_time: bool = False

    @classmethod
    def unavailable(cls, status: str, reason: str) -> LongShortRatioMetric:
        return cls(
            status=status,
            ratio=None,
            long_pct=None,
            short_pct=None,
            percent_unit="%",
            period_start=None,
            period_close=None,
            reason=reason,
        )


@dataclass(frozen=True)
class LiquidationsMetric:
    """Liquidations, like long/short ratio, have no "current" endpoint -
    always the latest CLOSED bucket.
    """

    status: str
    long_usd: float | None
    short_usd: float | None
    unit: str
    period_start: dt.datetime | None
    period_close: dt.datetime | None
    reason: str | None = None
    is_point_in_time: bool = False

    @classmethod
    def unavailable(cls, status: str, reason: str) -> LiquidationsMetric:
        return cls(
            status=status,
            long_usd=None,
            short_usd=None,
            unit="USD",
            period_start=None,
            period_close=None,
            reason=reason,
        )


@dataclass(frozen=True)
class MarketIntelSnapshot:
    """Everything `tidemark intel market` prints for one symbol.

    Raw data only - no interpretation, no bias, no recommendation. When
    `market_status` is `MARKET_NOT_FOUND`, every metric field carries
    that same status and a reason, and no network call beyond
    `/future-markets` was made.
    """

    symbol: str
    coinalyze_symbol: str
    market_status: str
    generated_at: dt.datetime
    open_interest: PointInTimeMetric
    open_interest_change: ClosedPeriodMetric
    funding_rate: PointInTimeMetric
    predicted_funding_rate: PointInTimeMetric
    long_short_ratio: LongShortRatioMetric
    liquidations: LiquidationsMetric
    futures_volume: ClosedPeriodMetric
    buy_volume: ClosedPeriodMetric
    sell_volume: ClosedPeriodMetric
