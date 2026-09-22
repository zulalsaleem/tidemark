"""Exchange client: closed-candle filtering, retry, no credentials.

No network access — every test uses a fake ccxt-like exchange stub.
"""

from __future__ import annotations

import datetime as dt

import ccxt
import pytest

from tidemark.data.exchange import ExchangeClient


class FakeExchange:
    """Minimal ccxt-like stub: only `fetch_ohlcv`, no network."""

    apiKey = ""
    secret = ""

    def __init__(self, rows: list[list[float]] | None = None, fail_first_n: int = 0) -> None:
        self._rows = sorted(rows or [], key=lambda r: r[0])
        self._fail_first_n = fail_first_n
        self.calls = 0

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        self.calls += 1
        if self.calls <= self._fail_first_n:
            raise ccxt.NetworkError("simulated transient failure")
        since = since or 0
        rows = [r for r in self._rows if r[0] >= since]
        return rows[:limit] if limit is not None else rows


class _FakeExchangeWithCreds(FakeExchange):
    apiKey = "leaked-key"


def _row(open_time: dt.datetime, o=100.0, h=110.0, low=90.0, c=105.0, v=10.0) -> list:
    return [int(open_time.timestamp() * 1000), o, h, low, c, v]


def test_forming_candle_is_excluded() -> None:
    open_time = dt.datetime(2026, 9, 22, 8, tzinfo=dt.UTC)  # 4h candle: 08:00-12:00
    now = open_time + dt.timedelta(hours=2)  # mid-candle
    fake = FakeExchange(rows=[_row(open_time)])
    client = ExchangeClient(exchange=fake, now_fn=lambda: now)

    candles = client.fetch_closed_candles(
        "BTC/USDT:USDT", "4h", since=open_time, until=now + dt.timedelta(hours=1)
    )

    assert candles == []


def test_candle_closing_exactly_at_now_is_included() -> None:
    open_time = dt.datetime(2026, 9, 22, 8, tzinfo=dt.UTC)
    now = open_time + dt.timedelta(hours=4)  # closes exactly now
    fake = FakeExchange(rows=[_row(open_time)])
    client = ExchangeClient(exchange=fake, now_fn=lambda: now)

    candles = client.fetch_closed_candles(
        "BTC/USDT:USDT", "4h", since=open_time, until=now + dt.timedelta(seconds=1)
    )

    assert len(candles) == 1
    assert candles[0].open_time == open_time
    assert candles[0].close_time == now


def test_constructor_rejects_credentials_on_exchange_instance() -> None:
    fake = _FakeExchangeWithCreds(rows=[])
    with pytest.raises(AssertionError):
        ExchangeClient(exchange=fake)


def test_client_has_no_order_placement_methods() -> None:
    forbidden = {"place_order", "create_order", "cancel_order", "submit_order"}
    assert forbidden.isdisjoint(dir(ExchangeClient))


def test_unsupported_timeframe_raises() -> None:
    fake = FakeExchange(rows=[])
    client = ExchangeClient(exchange=fake)
    now = dt.datetime.now(dt.UTC)
    with pytest.raises(ValueError, match="unsupported timeframe"):
        client.fetch_closed_candles("BTC/USDT:USDT", "3m", since=now, until=now)


def test_retries_transient_network_errors_then_succeeds() -> None:
    open_time = dt.datetime(2026, 9, 22, 8, tzinfo=dt.UTC)
    now = open_time + dt.timedelta(hours=8)
    fake = FakeExchange(rows=[_row(open_time)], fail_first_n=2)
    sleeps: list[float] = []
    client = ExchangeClient(
        exchange=fake,
        now_fn=lambda: now,
        max_retries=3,
        base_backoff_seconds=0.01,
        sleep_fn=sleeps.append,
    )

    candles = client.fetch_closed_candles("BTC/USDT:USDT", "4h", since=open_time, until=now)

    assert len(candles) == 1
    assert len(sleeps) == 2
    assert sleeps == [0.01, 0.02]  # bounded exponential backoff


def test_gives_up_after_max_retries_without_retrying_forever() -> None:
    now = dt.datetime(2026, 9, 22, 12, tzinfo=dt.UTC)
    since = now - dt.timedelta(hours=4)
    fake = FakeExchange(rows=[], fail_first_n=100)
    client = ExchangeClient(
        exchange=fake,
        now_fn=lambda: now,
        max_retries=2,
        base_backoff_seconds=0.01,
        sleep_fn=lambda _seconds: None,
    )

    with pytest.raises(ccxt.NetworkError):
        client.fetch_closed_candles("BTC/USDT:USDT", "4h", since=since, until=now)

    assert fake.calls == 3  # 1 initial attempt + 2 retries, then it gives up
