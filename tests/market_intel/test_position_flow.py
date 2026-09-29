"""classify_snapshot: maps an already-fetched MarketIntelSnapshot's
price_change/open_interest_change_pct fields onto
position_flow_classifier's inputs - no fetching of its own.
"""

from __future__ import annotations

import datetime as dt

from tidemark.market_intel.models import (
    MARKET_NOT_FOUND,
    NO_DATA,
    OK,
    ClosedPeriodMetric,
    LiquidationsMetric,
    LongShortRatioMetric,
    MarketIntelSnapshot,
    PointInTimeMetric,
)
from tidemark.market_intel.position_flow import classify_snapshot

PERIOD_START = dt.datetime(2026, 9, 27, 18, 0, tzinfo=dt.UTC)
PERIOD_CLOSE = dt.datetime(2026, 9, 27, 19, 0, tzinfo=dt.UTC)
TODAY = dt.datetime(2026, 9, 27, 19, 30, tzinfo=dt.UTC)


def _metric(**overrides) -> ClosedPeriodMetric:
    defaults = dict(
        status=OK, value=1.0, unit="%", period_start=PERIOD_START, period_close=PERIOD_CLOSE
    )
    defaults.update(overrides)
    return ClosedPeriodMetric(**defaults)


def _snapshot(**overrides) -> MarketIntelSnapshot:
    defaults = dict(
        symbol="SOL/USDT:USDT",
        coinalyze_symbol="SOLUSDT_PERP.A",
        market_status=OK,
        generated_at=TODAY,
        open_interest=PointInTimeMetric(status=OK, value=1.0, unit="USD", updated_at=TODAY),
        open_interest_change=_metric(unit="USD"),
        funding_rate=PointInTimeMetric(status=OK, value=0.01, unit="%", updated_at=TODAY),
        predicted_funding_rate=PointInTimeMetric(status=OK, value=0.01, unit="%", updated_at=TODAY),
        long_short_ratio=LongShortRatioMetric(
            status=OK,
            ratio=1.2,
            long_pct=55.0,
            short_pct=45.0,
            percent_unit="%",
            period_start=PERIOD_START,
            period_close=PERIOD_CLOSE,
        ),
        liquidations=LiquidationsMetric(
            status=OK,
            long_usd=1.0,
            short_usd=1.0,
            unit="USD",
            period_start=PERIOD_START,
            period_close=PERIOD_CLOSE,
        ),
        futures_volume=_metric(unit="base asset units"),
        buy_volume=_metric(unit="base asset units"),
        sell_volume=_metric(unit="base asset units"),
        price_change=_metric(value=0.3),
        open_interest_change_pct=_metric(value=0.3),
    )
    defaults.update(overrides)
    return MarketIntelSnapshot(**defaults)


def test_classify_snapshot_reads_price_change_and_oi_change_pct() -> None:
    snapshot = _snapshot(
        price_change=_metric(value=0.3), open_interest_change_pct=_metric(value=0.3)
    )

    result = classify_snapshot(snapshot)

    assert result.result == "LONG_BUILDUP"
    assert result.price.change_pct == 0.3
    assert result.open_interest.change_pct == 0.3


def test_classify_snapshot_never_reads_the_absolute_usd_oi_change() -> None:
    """open_interest_change (USD) must never leak into the classifier -
    only open_interest_change_pct."""
    snapshot = _snapshot(
        open_interest_change=_metric(value=999_999.0, unit="USD"),
        open_interest_change_pct=_metric(value=0.0),
    )

    result = classify_snapshot(snapshot)

    assert result.open_interest.change_pct == 0.0


def test_classify_snapshot_propagates_unavailable_status_and_reason() -> None:
    snapshot = _snapshot(
        price_change=ClosedPeriodMetric.unavailable(NO_DATA, "%", "Coinalyze returned no data")
    )

    result = classify_snapshot(snapshot)

    assert result.result == "NO_MATCH"
    assert "price" in result.reason
    assert result.price.status == NO_DATA
    assert result.price.reason == "Coinalyze returned no data"


def test_classify_snapshot_for_a_market_not_found_symbol() -> None:
    reason = "'NONSENSEUSDT_PERP.A' is not listed in Coinalyze's /future-markets"
    snapshot = _snapshot(
        market_status=MARKET_NOT_FOUND,
        price_change=ClosedPeriodMetric.unavailable(MARKET_NOT_FOUND, "%", reason),
        open_interest_change_pct=ClosedPeriodMetric.unavailable(MARKET_NOT_FOUND, "%", reason),
    )

    result = classify_snapshot(snapshot)

    assert result.result == "NO_MATCH"
    assert result.price.status == MARKET_NOT_FOUND
    assert result.open_interest.status == MARKET_NOT_FOUND
