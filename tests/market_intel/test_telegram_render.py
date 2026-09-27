"""render_snapshot: presentation-only formatting (abbreviated USD,
rounded volumes named by asset, three-decimal percentages, compact
timestamps, grouped output) - no value, period, or label differs from
what Merge 1 defines, and none of the forbidden trading-instruction
words appear except the two metric labels this project's own spec
requires ("Buy volume"/"Sell volume" are raw data fields, not trading
instructions; see `_assert_no_forbidden_language`).
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

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
from tidemark.market_intel.telegram_render import (
    FOOTER,
    _fmt_instant,
    _fmt_pct,
    _fmt_range,
    _fmt_ratio,
    _fmt_usd,
    _fmt_volume,
    render_snapshot,
)

TODAY = dt.datetime(2026, 9, 27, 19, 56, tzinfo=dt.UTC)
PERIOD_START = dt.datetime(2026, 9, 27, 18, 0, tzinfo=dt.UTC)
PERIOD_CLOSE = dt.datetime(2026, 9, 27, 19, 0, tzinfo=dt.UTC)

_FORBIDDEN = ["entry", "stop", " sl ", " tp ", "target", "r:r", "long setup", "short setup"]


def _assert_no_forbidden_language(message: str) -> None:
    lowered = f" {message.lower()} "
    for word in _FORBIDDEN:
        assert word not in lowered, f"unexpected {word!r} in rendered message"

    # "buy"/"sell" are allowed ONLY inside the two volume metric labels
    # this project's spec requires - verified precisely instead of
    # banning the substring outright, which would make it impossible to
    # ever render "Buy volume"/"Sell volume" at all.
    without_allowed_labels = re.sub(r"buy volume|sell volume", "", lowered)
    assert "buy" not in without_allowed_labels
    assert "sell" not in without_allowed_labels


# -- formatting primitives ----------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1_040_000_000.0, "$1.04B"),
        (-34_582_374.696, "-$34.6M"),
        (177_800.0, "$177.8K"),
        (495_606.608, "$495.6K"),
        (523.4, "$523.40"),
        (0.0, "$0.00"),
    ],
)
def test_fmt_usd(value: float, expected: str) -> None:
    assert _fmt_usd(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (597_404.22, "597,404"),
        (1_234.0, "1,234"),
        (12.345, "12.35"),
        (0.05, "0.0500"),
        (-250.0, "-250.00"),
    ],
)
def test_fmt_volume(value: float, expected: str) -> None:
    assert _fmt_volume(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-0.0032, "-0.003%"), (0.0022, "0.002%"), (54.69, "54.690%")],
)
def test_fmt_pct(value: float, expected: str) -> None:
    assert _fmt_pct(value) == expected


def test_fmt_ratio() -> None:
    assert _fmt_ratio(1.207) == "1.207"


def test_fmt_instant_same_day_omits_the_date() -> None:
    assert _fmt_instant(TODAY, reference=TODAY) == "19:56 UTC"


def test_fmt_instant_different_day_includes_the_date() -> None:
    yesterday_instant = TODAY - dt.timedelta(days=1)
    assert _fmt_instant(yesterday_instant, reference=TODAY) == "2026-09-26 19:56 UTC"


def test_fmt_range_same_day_omits_the_date() -> None:
    assert _fmt_range(PERIOD_START, PERIOD_CLOSE, reference=TODAY) == "18:00–19:00 UTC"


def test_fmt_range_different_day_includes_the_date() -> None:
    start = PERIOD_START - dt.timedelta(days=1)
    close = PERIOD_CLOSE - dt.timedelta(days=1)
    assert _fmt_range(start, close, reference=TODAY) == "2026-09-26 18:00–19:00 UTC"


# -- full snapshot rendering ---------------------------------------------------


def _ok_snapshot(**metric_overrides) -> MarketIntelSnapshot:
    defaults = dict(
        symbol="SOL/USDT:USDT",
        coinalyze_symbol="SOLUSDT_PERP.A",
        market_status=OK,
        generated_at=TODAY,
        open_interest=PointInTimeMetric(
            status=OK, value=1_040_000_000.0, unit="USD", updated_at=TODAY
        ),
        open_interest_change=ClosedPeriodMetric(
            status=OK,
            value=-34_582_374.696,
            unit="USD",
            period_start=PERIOD_START,
            period_close=PERIOD_CLOSE,
        ),
        funding_rate=PointInTimeMetric(status=OK, value=-0.0032, unit="%", updated_at=TODAY),
        predicted_funding_rate=PointInTimeMetric(
            status=OK, value=0.0022, unit="%", updated_at=TODAY
        ),
        long_short_ratio=LongShortRatioMetric(
            status=OK,
            ratio=1.207,
            long_pct=54.69,
            short_pct=45.31,
            percent_unit="%",
            period_start=PERIOD_START,
            period_close=PERIOD_CLOSE,
        ),
        liquidations=LiquidationsMetric(
            status=OK,
            long_usd=495_606.608,
            short_usd=158_077.048,
            unit="USD",
            period_start=PERIOD_START,
            period_close=PERIOD_CLOSE,
        ),
        futures_volume=ClosedPeriodMetric(
            status=OK,
            value=597_404.22,
            unit="base asset units",
            period_start=PERIOD_START,
            period_close=PERIOD_CLOSE,
        ),
        buy_volume=ClosedPeriodMetric(
            status=OK,
            value=250_123.4,
            unit="base asset units",
            period_start=PERIOD_START,
            period_close=PERIOD_CLOSE,
        ),
        sell_volume=ClosedPeriodMetric(
            status=OK,
            value=347_280.82,
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
        generated_at=TODAY,
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


def test_ok_snapshot_renders_every_metric_abbreviated_and_named() -> None:
    message = render_snapshot(_ok_snapshot())

    assert "SOL/USDT:USDT (SOLUSDT_PERP.A)" in message
    assert "Open interest: $1.04B (live, updated 19:56 UTC)" in message
    assert "Open interest change: -$34.6M (period 18:00–19:00 UTC)" in message
    assert "Funding rate: -0.003% (live, updated 19:56 UTC)" in message
    assert "Predicted funding rate: 0.002% (live, updated 19:56 UTC)" in message
    assert "Long/short ratio: 1.207 (long 54.690% / short 45.310%)" in message
    assert "Liquidations: long=$495.6K / short=$158.1K" in message
    assert "Futures volume: 597,404 SOL" in message
    assert "Buy volume: 250,123 SOL" in message
    assert "Sell volume: 347,281 SOL" in message
    assert "base asset units" not in message  # named the asset instead
    assert FOOTER in message


def test_quote_asset_denominated_volume_is_named_by_the_quote_ticker() -> None:
    snapshot = _ok_snapshot(
        futures_volume=ClosedPeriodMetric(
            status=OK,
            value=12_345.0,
            unit="quote asset units",
            period_start=PERIOD_START,
            period_close=PERIOD_CLOSE,
        )
    )

    message = render_snapshot(snapshot)

    assert "Futures volume: 12,345 USDT" in message


def test_contracts_denominated_volume_keeps_the_word_contracts() -> None:
    snapshot = _ok_snapshot(
        futures_volume=ClosedPeriodMetric(
            status=OK,
            value=42.0,
            unit="contracts",
            period_start=PERIOD_START,
            period_close=PERIOD_CLOSE,
        )
    )

    message = render_snapshot(snapshot)

    assert "Futures volume: 42.00 contracts" in message


def test_output_is_grouped_with_blank_lines_between_groups() -> None:
    message = render_snapshot(_ok_snapshot())
    blocks = message.split("\n\n")

    # header, OI group, funding group, positioning group, liq+volume group, footer
    assert len(blocks) == 6
    assert blocks[0] == "SOL/USDT:USDT (SOLUSDT_PERP.A)"
    assert blocks[1].startswith("Open interest:")
    assert "Open interest change:" in blocks[1]
    assert blocks[2].startswith("Funding rate:")
    assert "Predicted funding rate:" in blocks[2]
    assert blocks[3].startswith("Long/short ratio:")
    assert blocks[4].startswith("Liquidations:")
    assert "Futures volume:" in blocks[4]
    assert "Buy volume:" in blocks[4]
    assert "Sell volume:" in blocks[4]
    assert blocks[5] == FOOTER


def test_a_period_entirely_on_a_prior_day_shows_the_date() -> None:
    snapshot = _ok_snapshot(
        open_interest_change=ClosedPeriodMetric(
            status=OK,
            value=-1_000.0,
            unit="USD",
            period_start=dt.datetime(2026, 9, 26, 18, 0, tzinfo=dt.UTC),
            period_close=dt.datetime(2026, 9, 26, 19, 0, tzinfo=dt.UTC),
        )
    )

    message = render_snapshot(snapshot)

    assert "Open interest change: -$1.00K (period 2026-09-26 18:00–19:00 UTC)" in message


def test_a_live_value_from_a_prior_day_shows_the_date() -> None:
    snapshot = _ok_snapshot(
        funding_rate=PointInTimeMetric(
            status=OK,
            value=0.001,
            unit="%",
            updated_at=dt.datetime(2026, 9, 26, 12, 30, tzinfo=dt.UTC),
        )
    )

    message = render_snapshot(snapshot)

    assert "Funding rate: 0.001% (live, updated 2026-09-26 12:30 UTC)" in message


def test_market_not_found_renders_a_single_clean_line() -> None:
    snapshot = _not_found_snapshot("NONSENSE/USDT:USDT", "NONSENSEUSDT_PERP.A")

    message = render_snapshot(snapshot)

    assert "MARKET_NOT_FOUND" in message
    assert "not a Binance USDT-M perpetual" in message
    assert FOOTER in message


def test_no_data_metric_reports_unavailable_never_zero() -> None:
    snapshot = _ok_snapshot(
        open_interest=PointInTimeMetric.unavailable(NO_DATA, "USD", "Coinalyze returned no data")
    )

    message = render_snapshot(snapshot)

    assert "Open interest: UNAVAILABLE (NO_DATA: Coinalyze returned no data)" in message
    assert "Open interest: $0" not in message
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


def test_footer_is_unchanged_from_merge_2() -> None:
    assert (
        FOOTER
        == "Data: Coinalyze (Binance USDT-M perpetuals). Market info only — not a trade signal."
    )
