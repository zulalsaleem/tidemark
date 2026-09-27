"""render_snapshot: raw data only, no fabricated zeros, and none of the
forbidden trading-instruction words - except the two metric labels this
merge's own spec requires ("Buy volume"/"Sell volume" are Merge 1 raw
data fields, not trading instructions; see the docstring on
`_assert_no_forbidden_language` for how this is verified precisely
rather than by a blunt substring ban that would contradict the METRICS
requirement to render them).
"""

from __future__ import annotations

import datetime as dt
import re

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
from tidemark.market_intel.telegram_render import FOOTER, render_snapshot

NOW = dt.datetime(2026, 9, 27, 10, 0, tzinfo=dt.UTC)
PERIOD_START = dt.datetime(2026, 9, 27, 9, 0, tzinfo=dt.UTC)
PERIOD_CLOSE = dt.datetime(2026, 9, 27, 10, 0, tzinfo=dt.UTC)

# Every word/phrase this must never contain, EXCEPT as part of the exact
# "Buy volume:"/"Sell volume:" metric labels Merge 1's own spec requires.
_FORBIDDEN = [
    "entry",
    "stop",
    " sl ",
    " tp ",
    "target",
    "r:r",
    "long setup",
    "short setup",
]


def _assert_no_forbidden_language(message: str) -> None:
    lowered = f" {message.lower()} "
    for word in _FORBIDDEN:
        assert word not in lowered, f"unexpected {word!r} in rendered message"

    # "buy"/"sell" are allowed ONLY inside the two volume metric labels
    # this merge's METRICS section requires - verified precisely instead
    # of banning the substring outright, which would make it impossible
    # to ever render "Buy volume"/"Sell volume" at all.
    without_allowed_labels = re.sub(r"buy volume|sell volume", "", lowered)
    assert "buy" not in without_allowed_labels
    assert "sell" not in without_allowed_labels


def _ok_snapshot(**metric_overrides) -> MarketIntelSnapshot:
    defaults = dict(
        symbol="BTC/USDT:USDT",
        coinalyze_symbol="BTCUSDT_PERP.A",
        market_status=OK,
        generated_at=NOW,
        open_interest=PointInTimeMetric(status=OK, value=100.0, unit="USD", updated_at=NOW),
        open_interest_change=ClosedPeriodMetric(
            status=OK, value=5.0, unit="USD", period_start=PERIOD_START, period_close=PERIOD_CLOSE
        ),
        funding_rate=PointInTimeMetric(status=OK, value=0.001, unit="%", updated_at=NOW),
        predicted_funding_rate=PointInTimeMetric(status=OK, value=0.002, unit="%", updated_at=NOW),
        long_short_ratio=LongShortRatioMetric(
            status=OK,
            ratio=1.2,
            long_pct=54.0,
            short_pct=46.0,
            percent_unit="%",
            period_start=PERIOD_START,
            period_close=PERIOD_CLOSE,
        ),
        liquidations=LiquidationsMetric(
            status=OK,
            long_usd=10.0,
            short_usd=20.0,
            unit="USD",
            period_start=PERIOD_START,
            period_close=PERIOD_CLOSE,
        ),
        futures_volume=ClosedPeriodMetric(
            status=OK,
            value=1000.0,
            unit="base asset units",
            period_start=PERIOD_START,
            period_close=PERIOD_CLOSE,
        ),
        buy_volume=ClosedPeriodMetric(
            status=OK,
            value=600.0,
            unit="base asset units",
            period_start=PERIOD_START,
            period_close=PERIOD_CLOSE,
        ),
        sell_volume=ClosedPeriodMetric(
            status=OK,
            value=400.0,
            unit="base asset units",
            period_start=PERIOD_START,
            period_close=PERIOD_CLOSE,
        ),
    )
    defaults.update(metric_overrides)
    return MarketIntelSnapshot(**defaults)


def _not_found_snapshot(ccxt_symbol: str, coinalyze_symbol: str) -> MarketIntelSnapshot:
    reason = f"{coinalyze_symbol!r} is not listed in Coinalyze's /future-markets"
    return MarketIntelSnapshot(
        symbol=ccxt_symbol,
        coinalyze_symbol=coinalyze_symbol,
        market_status=MARKET_NOT_FOUND,
        generated_at=NOW,
        open_interest=PointInTimeMetric.unavailable(MARKET_NOT_FOUND, "USD", reason),
        open_interest_change=ClosedPeriodMetric.unavailable(MARKET_NOT_FOUND, "USD", reason),
        funding_rate=PointInTimeMetric.unavailable(MARKET_NOT_FOUND, "%", reason),
        predicted_funding_rate=PointInTimeMetric.unavailable(MARKET_NOT_FOUND, "%", reason),
        long_short_ratio=LongShortRatioMetric.unavailable(MARKET_NOT_FOUND, reason),
        liquidations=LiquidationsMetric.unavailable(MARKET_NOT_FOUND, reason),
        futures_volume=ClosedPeriodMetric.unavailable(MARKET_NOT_FOUND, "-", reason),
        buy_volume=ClosedPeriodMetric.unavailable(MARKET_NOT_FOUND, "-", reason),
        sell_volume=ClosedPeriodMetric.unavailable(MARKET_NOT_FOUND, "-", reason),
    )


def test_ok_snapshot_renders_every_metric_with_value_unit_and_window() -> None:
    message = render_snapshot(_ok_snapshot())

    assert "BTC/USDT:USDT (BTCUSDT_PERP.A)" in message
    assert "Open interest: 100.0 USD" in message
    assert "(live, updated" in message  # point-in-time labeled distinctly
    assert "Open interest change: 5.0 USD" in message
    assert f"(period {PERIOD_START.isoformat()} to {PERIOD_CLOSE.isoformat()})" in message
    assert "Long/short ratio: 1.2" in message
    assert "Liquidations: long=10.0 USD / short=20.0 USD" in message
    assert "Buy volume: 600.0 base asset units" in message
    assert "Sell volume: 400.0 base asset units" in message
    assert FOOTER in message


def test_market_not_found_renders_a_single_clean_line() -> None:
    snapshot = _not_found_snapshot("NONSENSE/USDT:USDT", "NONSENSEUSDT_PERP.A")

    message = render_snapshot(snapshot)

    assert "MARKET_NOT_FOUND" in message
    assert "not a Binance USDT-M perpetual" in message
    assert FOOTER in message
    # No metric line, and no fabricated value.
    assert "0" not in message.replace(FOOTER, "")


def test_no_data_metric_reports_unavailable_never_zero() -> None:
    snapshot = _ok_snapshot(
        open_interest=PointInTimeMetric.unavailable(NO_DATA, "USD", "Coinalyze returned no data")
    )

    message = render_snapshot(snapshot)

    assert "Open interest: UNAVAILABLE (NO_DATA: Coinalyze returned no data)" in message
    assert "Open interest: 0" not in message
    assert "Open interest: None" not in message


def test_long_short_ratio_no_data_names_itself() -> None:
    snapshot = _ok_snapshot(
        long_short_ratio=LongShortRatioMetric.unavailable(
            NO_DATA, "not available for this market (has_long_short_ratio_data=False)"
        )
    )

    message = render_snapshot(snapshot)

    assert "Long/short ratio: UNAVAILABLE (NO_DATA:" in message
    assert "has_long_short_ratio_data" in message


def test_liquidations_no_data_names_itself() -> None:
    snapshot = _ok_snapshot(
        liquidations=LiquidationsMetric.unavailable(NO_DATA, "Coinalyze returned no data")
    )

    message = render_snapshot(snapshot)

    assert "Liquidations: UNAVAILABLE (NO_DATA:" in message


def test_rendered_message_contains_no_forbidden_trading_language() -> None:
    _assert_no_forbidden_language(render_snapshot(_ok_snapshot()))


def test_market_not_found_message_contains_no_forbidden_trading_language() -> None:
    snapshot = _not_found_snapshot("NONSENSE/USDT:USDT", "NONSENSEUSDT_PERP.A")
    _assert_no_forbidden_language(render_snapshot(snapshot))


def test_footer_states_data_source_and_not_a_trade_signal() -> None:
    assert "Coinalyze" in FOOTER
    assert "not a trade signal" in FOOTER.lower()
