"""fetch_market_intel: MARKET_NOT_FOUND vs NO_DATA vs OK, missing values
never rendered as zero, every returned metric carries its period/close
time (or its point-in-time update timestamp), and the CJK symbol flows
through the whole pipeline correctly.
"""

from __future__ import annotations

import datetime as dt

from tidemark.market_intel.future_markets import FutureMarketsCache
from tidemark.market_intel.models import MARKET_NOT_FOUND, NO_DATA, OK
from tidemark.market_intel.service import fetch_market_intel

VENUE = "binanceusdm"
SYMBOL = "BTC/USDT:USDT"
COINALYZE_SYMBOL = "BTCUSDT_PERP.A"
NOW = dt.datetime(2026, 9, 27, 10, 37, tzinfo=dt.UTC)
PERIOD_START = dt.datetime(2026, 9, 27, 9, 0, tzinfo=dt.UTC)
PERIOD_CLOSE = dt.datetime(2026, 9, 27, 10, 0, tzinfo=dt.UTC)
PERIOD_START_EPOCH = int(PERIOD_START.timestamp())


def _market_row(symbol: str = COINALYZE_SYMBOL, **overrides) -> dict:
    base = {
        "symbol": symbol,
        "exchange": "A",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "is_perpetual": True,
        "has_long_short_ratio_data": True,
        "has_ohlcv_data": True,
        "has_buy_sell_data": True,
        "oi_lq_vol_denominated_in": "BASE_ASSET",
    }
    base.update(overrides)
    return base


class _FakeClient:
    """Implements exactly the surface `service.py` calls on
    `CoinalyzeClient`, recording every call for assertion.
    """

    def __init__(
        self,
        future_markets_rows: list[dict],
        oi: list[dict] | None = None,
        funding: list[dict] | None = None,
        predicted: list[dict] | None = None,
        oi_history: list[dict] | None = None,
        ls_history: list[dict] | None = None,
        liq_history: list[dict] | None = None,
        ohlcv_history: list[dict] | None = None,
    ) -> None:
        self._future_markets_rows = future_markets_rows
        self._oi = oi if oi is not None else []
        self._funding = funding if funding is not None else []
        self._predicted = predicted if predicted is not None else []
        self._oi_history = (
            oi_history if oi_history is not None else [{"symbol": COINALYZE_SYMBOL, "history": []}]
        )
        self._ls_history = (
            ls_history if ls_history is not None else [{"symbol": COINALYZE_SYMBOL, "history": []}]
        )
        self._liq_history = (
            liq_history
            if liq_history is not None
            else [{"symbol": COINALYZE_SYMBOL, "history": []}]
        )
        self._ohlcv_history = (
            ohlcv_history
            if ohlcv_history is not None
            else [{"symbol": COINALYZE_SYMBOL, "history": []}]
        )
        self.calls: dict[str, int] = {
            "future_markets": 0,
            "open_interest": 0,
            "funding_rate": 0,
            "predicted_funding_rate": 0,
            "open_interest_history": 0,
            "long_short_ratio_history": 0,
            "liquidation_history": 0,
            "ohlcv_history": 0,
        }
        self.history_calls: list[tuple] = []

    def future_markets(self) -> list[dict]:
        self.calls["future_markets"] += 1
        return self._future_markets_rows

    def open_interest(self, symbols, convert_to_usd=True):
        self.calls["open_interest"] += 1
        return self._oi

    def funding_rate(self, symbols):
        self.calls["funding_rate"] += 1
        return self._funding

    def predicted_funding_rate(self, symbols):
        self.calls["predicted_funding_rate"] += 1
        return self._predicted

    def open_interest_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        self.calls["open_interest_history"] += 1
        self.history_calls.append(("open_interest_history", symbol, interval, from_ts, to_ts))
        return self._oi_history

    def long_short_ratio_history(self, symbol, interval, from_ts, to_ts):
        self.calls["long_short_ratio_history"] += 1
        self.history_calls.append(("long_short_ratio_history", symbol, interval, from_ts, to_ts))
        return self._ls_history

    def liquidation_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        self.calls["liquidation_history"] += 1
        self.history_calls.append(("liquidation_history", symbol, interval, from_ts, to_ts))
        return self._liq_history

    def ohlcv_history(self, symbol, interval, from_ts, to_ts):
        self.calls["ohlcv_history"] += 1
        self.history_calls.append(("ohlcv_history", symbol, interval, from_ts, to_ts))
        return self._ohlcv_history


def _cache(client: _FakeClient) -> FutureMarketsCache:
    return FutureMarketsCache(client)


# -- MARKET_NOT_FOUND ---------------------------------------------------------


def test_unlisted_symbol_short_circuits_to_market_not_found() -> None:
    client = _FakeClient(future_markets_rows=[])  # nothing listed at all
    snapshot = fetch_market_intel(client, _cache(client), SYMBOL, VENUE, NOW)

    assert snapshot.market_status == MARKET_NOT_FOUND
    for metric in (
        snapshot.open_interest,
        snapshot.open_interest_change,
        snapshot.funding_rate,
        snapshot.predicted_funding_rate,
        snapshot.long_short_ratio,
        snapshot.liquidations,
        snapshot.futures_volume,
        snapshot.buy_volume,
        snapshot.sell_volume,
    ):
        assert metric.status == MARKET_NOT_FOUND
        assert metric.reason is not None

    # No missing value is ever a silent zero.
    assert snapshot.open_interest.value is None
    assert snapshot.long_short_ratio.ratio is None
    assert snapshot.liquidations.long_usd is None

    # Short-circuited: never called any metric endpoint.
    assert client.calls["open_interest"] == 0
    assert client.calls["ohlcv_history"] == 0


# -- NO_DATA for point-in-time metrics (listed, but empty response) ----------


def test_listed_symbol_with_empty_current_endpoints_is_no_data() -> None:
    """Mirrors the real CJK-coin behavior observed live: listed in
    /future-markets, but open-interest/funding-rate/predicted-funding-
    rate all return `200 []`."""
    client = _FakeClient(
        future_markets_rows=[_market_row()],
        oi=[],
        funding=[],
        predicted=[],
    )
    snapshot = fetch_market_intel(client, _cache(client), SYMBOL, VENUE, NOW)

    assert snapshot.market_status == OK  # the market itself is known
    assert snapshot.open_interest.status == NO_DATA
    assert snapshot.open_interest.value is None
    assert snapshot.funding_rate.status == NO_DATA
    assert snapshot.predicted_funding_rate.status == NO_DATA


# -- OK path: every metric, exact values, period/close time carried ----------


def _full_ok_client() -> _FakeClient:
    return _FakeClient(
        future_markets_rows=[_market_row()],
        oi=[{"symbol": COINALYZE_SYMBOL, "value": 8_050_839_906.58, "update": 1790519073306}],
        funding=[{"symbol": COINALYZE_SYMBOL, "value": 0.001449, "update": 1790519072319}],
        predicted=[{"symbol": COINALYZE_SYMBOL, "value": 0.003938, "update": 1790519076308}],
        oi_history=[
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [
                    {"t": PERIOD_START_EPOCH, "o": 100.0, "h": 110.0, "l": 95.0, "c": 105.0}
                ],
            }
        ],
        ls_history=[
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [{"t": PERIOD_START_EPOCH, "r": 1.2, "l": 54.5, "s": 45.5}],
            }
        ],
        liq_history=[
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [{"t": PERIOD_START_EPOCH, "l": 1000.0, "s": 2000.0}],
            }
        ],
        ohlcv_history=[
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [
                    {
                        "t": PERIOD_START_EPOCH,
                        "o": 100.0,
                        "h": 110.0,
                        "l": 95.0,
                        "c": 105.0,
                        "v": 1000.0,
                        "bv": 600.0,
                        "tx": 500,
                        "btx": 300,
                    }
                ],
            }
        ],
    )


def test_ok_path_reports_every_metric_with_correct_value_and_window() -> None:
    client = _full_ok_client()
    snapshot = fetch_market_intel(client, _cache(client), SYMBOL, VENUE, NOW)

    assert snapshot.market_status == OK
    assert snapshot.coinalyze_symbol == COINALYZE_SYMBOL

    oi = snapshot.open_interest
    assert oi.status == OK
    assert oi.value == 8_050_839_906.58
    assert oi.unit == "USD"
    assert oi.is_point_in_time is True
    assert oi.updated_at == dt.datetime.fromtimestamp(1790519073306 / 1000, tz=dt.UTC)

    oi_change = snapshot.open_interest_change
    assert oi_change.status == OK
    assert oi_change.value == 5.0  # c - o = 105 - 100
    assert oi_change.period_start == PERIOD_START
    assert oi_change.period_close == PERIOD_CLOSE

    assert snapshot.funding_rate.value == 0.001449
    assert snapshot.funding_rate.unit == "%"
    assert snapshot.predicted_funding_rate.value == 0.003938

    ls = snapshot.long_short_ratio
    assert ls.status == OK
    assert ls.ratio == 1.2
    assert ls.long_pct == 54.5
    assert ls.short_pct == 45.5
    assert ls.period_start == PERIOD_START
    assert ls.period_close == PERIOD_CLOSE

    liq = snapshot.liquidations
    assert liq.status == OK
    assert liq.long_usd == 1000.0
    assert liq.short_usd == 2000.0
    assert liq.unit == "USD"
    assert liq.period_start == PERIOD_START
    assert liq.period_close == PERIOD_CLOSE

    assert snapshot.futures_volume.value == 1000.0
    assert snapshot.buy_volume.value == 600.0
    assert snapshot.sell_volume.value == 400.0  # v - bv
    for metric in (snapshot.futures_volume, snapshot.buy_volume, snapshot.sell_volume):
        assert metric.period_start == PERIOD_START
        assert metric.period_close == PERIOD_CLOSE
        assert metric.unit == "base asset units"


def test_history_endpoints_are_queried_with_the_clamped_closed_window() -> None:
    client = _full_ok_client()
    fetch_market_intel(client, _cache(client), SYMBOL, VENUE, NOW)

    expected_to = int(PERIOD_CLOSE.timestamp()) - 1
    for call in client.history_calls:
        _, symbol, interval, from_ts, to_ts = call
        assert symbol == COINALYZE_SYMBOL
        assert interval == "1hour"
        assert from_ts == PERIOD_START_EPOCH
        assert to_ts == expected_to


# -- per-metric NO_DATA independence, pre-flight skips via future_market_info -


def test_missing_long_short_ratio_data_flag_skips_the_call_and_reports_no_data() -> None:
    client = _full_ok_client()
    client._future_markets_rows = [_market_row(has_long_short_ratio_data=False)]

    snapshot = fetch_market_intel(client, _cache(client), SYMBOL, VENUE, NOW)

    assert snapshot.long_short_ratio.status == NO_DATA
    assert snapshot.long_short_ratio.ratio is None
    assert "has_long_short_ratio_data" in snapshot.long_short_ratio.reason
    assert client.calls["long_short_ratio_history"] == 0  # never called at all

    # Everything else still resolves normally.
    assert snapshot.open_interest.status == OK


def test_missing_ohlcv_data_flag_skips_volume_entirely() -> None:
    client = _full_ok_client()
    client._future_markets_rows = [_market_row(has_ohlcv_data=False)]

    snapshot = fetch_market_intel(client, _cache(client), SYMBOL, VENUE, NOW)

    for metric in (snapshot.futures_volume, snapshot.buy_volume, snapshot.sell_volume):
        assert metric.status == NO_DATA
        assert metric.value is None
    assert client.calls["ohlcv_history"] == 0


def test_ohlcv_without_buy_sell_split_reports_volume_but_not_the_split() -> None:
    client = _full_ok_client()
    client._future_markets_rows = [_market_row(has_buy_sell_data=False)]

    snapshot = fetch_market_intel(client, _cache(client), SYMBOL, VENUE, NOW)

    assert snapshot.futures_volume.status == OK
    assert snapshot.futures_volume.value == 1000.0
    assert snapshot.buy_volume.status == NO_DATA
    assert snapshot.sell_volume.status == NO_DATA
    assert "has_buy_sell_data" in snapshot.buy_volume.reason


def test_empty_history_bucket_is_no_data_not_zero() -> None:
    client = _full_ok_client()
    client._oi_history = [{"symbol": COINALYZE_SYMBOL, "history": []}]

    snapshot = fetch_market_intel(client, _cache(client), SYMBOL, VENUE, NOW)

    assert snapshot.open_interest_change.status == NO_DATA
    assert snapshot.open_interest_change.value is None


# -- CJK symbol flows through the full pipeline -------------------------------


def test_cjk_symbol_flows_through_the_pipeline() -> None:
    cjk_symbol = "龙虾/USDT:USDT"
    coinalyze_symbol = "龙虾USDT_PERP.A"
    client = _FakeClient(
        future_markets_rows=[_market_row(symbol=coinalyze_symbol, base_asset="龙虾")],
        oi=[],
        funding=[],
        predicted=[],
    )

    snapshot = fetch_market_intel(client, _cache(client), cjk_symbol, VENUE, NOW)

    assert snapshot.symbol == cjk_symbol
    assert snapshot.coinalyze_symbol == coinalyze_symbol
    assert snapshot.market_status == OK  # listed, even though no data flows
    assert snapshot.open_interest.status == NO_DATA
