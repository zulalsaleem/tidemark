"""fetch_classifier_inputs: BTC's closed-1H price/OI % change and
current+previous funding, independent from service.py's MarketIntelSnapshot.
"""

from __future__ import annotations

import datetime as dt

from tidemark.market_intel.briefing_data import BTC_CCXT_SYMBOL, fetch_classifier_inputs
from tidemark.market_intel.future_markets import FutureMarketsCache
from tidemark.market_intel.models import MARKET_NOT_FOUND, NO_DATA, OK

VENUE = "binanceusdm"
COINALYZE_SYMBOL = "BTCUSDT_PERP.A"
NOW = dt.datetime(2026, 9, 27, 19, 30, tzinfo=dt.UTC)
PERIOD_START = dt.datetime(2026, 9, 27, 18, 0, tzinfo=dt.UTC)
PERIOD_CLOSE = dt.datetime(2026, 9, 27, 19, 0, tzinfo=dt.UTC)
PERIOD_START_EPOCH = int(PERIOD_START.timestamp())
PREVIOUS_START_EPOCH = int((PERIOD_START - dt.timedelta(hours=1)).timestamp())


def _market_row(**overrides) -> dict:
    base = {
        "symbol": COINALYZE_SYMBOL,
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
    def __init__(
        self,
        future_markets_rows,
        ohlcv_history=None,
        oi_history=None,
        funding_history=None,
    ) -> None:
        self._rows = future_markets_rows
        self._ohlcv_history = ohlcv_history if ohlcv_history is not None else []
        self._oi_history = oi_history if oi_history is not None else []
        self._funding_history = funding_history if funding_history is not None else []
        self.calls: list[tuple] = []

    def future_markets(self):
        return self._rows

    def ohlcv_history(self, symbol, interval, from_ts, to_ts):
        self.calls.append(("ohlcv_history", symbol, interval, from_ts, to_ts))
        return self._ohlcv_history

    def open_interest_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        self.calls.append(("open_interest_history", symbol, interval, from_ts, to_ts))
        return self._oi_history

    def funding_rate_history(self, symbol, interval, from_ts, to_ts):
        self.calls.append(("funding_rate_history", symbol, interval, from_ts, to_ts))
        return self._funding_history


def _cache(client: _FakeClient) -> FutureMarketsCache:
    return FutureMarketsCache(client)


def test_unlisted_btc_gives_market_not_found_for_all_three_inputs() -> None:
    client = _FakeClient(future_markets_rows=[])
    price, oi, funding = fetch_classifier_inputs(client, _cache(client), VENUE, NOW)

    assert price.status == MARKET_NOT_FOUND
    assert oi.status == MARKET_NOT_FOUND
    assert funding.status == MARKET_NOT_FOUND
    assert client.calls == []  # short-circuited, no history calls at all


def test_ok_case_computes_percentage_changes_and_funding_trend_inputs() -> None:
    client = _FakeClient(
        future_markets_rows=[_market_row()],
        ohlcv_history=[
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [
                    {"t": PERIOD_START_EPOCH, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5}
                ],
            }
        ],
        oi_history=[
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [
                    {"t": PERIOD_START_EPOCH, "o": 1000.0, "h": 1010.0, "l": 995.0, "c": 1010.0}
                ],
            }
        ],
        funding_history=[
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [
                    {"t": PREVIOUS_START_EPOCH, "o": 0.005, "h": 0.006, "l": 0.004, "c": 0.005},
                    {"t": PERIOD_START_EPOCH, "o": 0.005, "h": 0.011, "l": 0.005, "c": 0.01},
                ],
            }
        ],
    )

    price, oi, funding = fetch_classifier_inputs(client, _cache(client), VENUE, NOW)

    assert price.status == OK
    assert price.change_pct == 0.5  # (100.5-100)/100*100
    assert price.period_start == PERIOD_START
    assert price.period_close == PERIOD_CLOSE

    assert oi.status == OK
    assert oi.change_pct == 1.0  # (1010-1000)/1000*100

    assert funding.status == OK
    assert funding.value == 0.01
    assert funding.previous_value == 0.005
    assert funding.period_start == PERIOD_START
    assert funding.period_close == PERIOD_CLOSE


def test_empty_ohlcv_bucket_is_no_data() -> None:
    client = _FakeClient(future_markets_rows=[_market_row()], ohlcv_history=[])
    price, _oi, _funding = fetch_classifier_inputs(client, _cache(client), VENUE, NOW)

    assert price.status == NO_DATA
    assert price.change_pct is None


def test_zero_opening_price_is_no_data_not_a_division_error() -> None:
    client = _FakeClient(
        future_markets_rows=[_market_row()],
        ohlcv_history=[
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [{"t": PERIOD_START_EPOCH, "o": 0.0, "h": 1.0, "l": 0.0, "c": 0.5}],
            }
        ],
    )
    price, _oi, _funding = fetch_classifier_inputs(client, _cache(client), VENUE, NOW)

    assert price.status == NO_DATA
    assert "opening value was 0" in price.reason


def test_missing_previous_funding_bucket_leaves_previous_value_none() -> None:
    client = _FakeClient(
        future_markets_rows=[_market_row()],
        funding_history=[
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [
                    {"t": PERIOD_START_EPOCH, "o": 0.005, "h": 0.011, "l": 0.005, "c": 0.01}
                ],
            }
        ],
    )
    _price, _oi, funding = fetch_classifier_inputs(client, _cache(client), VENUE, NOW)

    assert funding.status == OK
    assert funding.value == 0.01
    assert funding.previous_value is None


def test_empty_funding_history_is_no_data() -> None:
    client = _FakeClient(future_markets_rows=[_market_row()], funding_history=[])
    _price, _oi, funding = fetch_classifier_inputs(client, _cache(client), VENUE, NOW)

    assert funding.status == NO_DATA


def test_history_calls_use_the_clamped_closed_window() -> None:
    client = _FakeClient(future_markets_rows=[_market_row()])
    fetch_classifier_inputs(client, _cache(client), VENUE, NOW)

    expected_to = int(PERIOD_CLOSE.timestamp()) - 1
    calls_by_endpoint = {call[0]: call for call in client.calls}

    assert calls_by_endpoint["ohlcv_history"][3:] == (PERIOD_START_EPOCH, expected_to)
    assert calls_by_endpoint["open_interest_history"][3:] == (PERIOD_START_EPOCH, expected_to)
    assert calls_by_endpoint["funding_rate_history"][3:] == (PREVIOUS_START_EPOCH, expected_to)


def test_btc_symbol_constant_maps_correctly() -> None:
    assert BTC_CCXT_SYMBOL == "BTC/USDT:USDT"
