"""Exchange client is read-only: no order endpoints, no in-progress candles."""

import datetime as dt

import ccxt
import pytest

from tidemark.data.exchange import ExchangeClient

START_MS = int(dt.datetime(2026, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)
FOUR_HOURS_MS = 4 * 60 * 60 * 1000


class _FakeExchange:
    """Stand-in for a ccxt exchange: no network access, deterministic clock."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.urls = {"api": "https://example.invalid"}
        self._now_ms = START_MS + 3 * FOUR_HOURS_MS  # "now" sits mid-way through candle 3

    def parse_timeframe(self, timeframe: str) -> int:
        assert timeframe == "4h"
        return 4 * 60 * 60

    def milliseconds(self) -> int:
        return self._now_ms

    def fetch_ohlcv(self, asset, timeframe, limit, since):
        rows = []
        for i in range(5):
            open_ms = START_MS + i * FOUR_HOURS_MS
            rows.append([open_ms, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0])
        if since is not None:
            rows = [r for r in rows if r[0] >= since]
        return rows[:limit]


def test_fetch_closed_candles_excludes_in_progress_candle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ccxt, "binanceusdm", _FakeExchange, raising=False)
    client = ExchangeClient()

    candles = client.fetch_closed_candles("BTC/USDT:USDT", "4h", limit=10)

    # "now" is mid-way through candle index 3 (open at +3*4h), so candle 3's
    # close (+4*4h) is still in the future and must be excluded, along with
    # candle 4 entirely. Only candles 0-2 have fully closed.
    assert len(candles) == 3
    assert [c.close for c in candles] == [100.5, 101.5, 102.5]
    for candle in candles:
        assert candle.close_time <= dt.datetime.fromtimestamp(
            START_MS / 1000 + 12 * 3600, tz=dt.UTC
        )


def test_fetch_closed_candles_sets_asset_and_timeframe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ccxt, "binanceusdm", _FakeExchange, raising=False)
    client = ExchangeClient()

    candles = client.fetch_closed_candles("BTC/USDT:USDT", "4h", limit=10)

    assert all(c.asset == "BTC/USDT:USDT" for c in candles)
    assert all(c.timeframe == "4h" for c in candles)


def test_fetch_closed_candles_passes_since_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ccxt, "binanceusdm", _FakeExchange, raising=False)
    client = ExchangeClient()

    since = dt.datetime.fromtimestamp((START_MS + FOUR_HOURS_MS) / 1000, tz=dt.UTC)
    candles = client.fetch_closed_candles("BTC/USDT:USDT", "4h", limit=10, since=since)

    assert [c.close for c in candles] == [101.5, 102.5]


def test_client_has_no_order_placement_methods() -> None:
    forbidden = {"place_order", "create_order", "cancel_order", "submit_order"}
    assert forbidden.isdisjoint(dir(ExchangeClient))
