"""Run orchestration: status computation and per-symbol failure isolation."""

from __future__ import annotations

import datetime as dt

import ccxt
import pytest

from tidemark.data.exchange import ExchangeClient, RawCandle
from tidemark.data.ingest import run_backfill, run_update
from tidemark.data.store import TidemarkStore, create_store_engine, init_db

VENUE = "binanceusdm"


class _FakeExchange:
    apiKey = ""
    secret = ""

    def __init__(
        self, rows_by_symbol: dict[str, list] | None = None, fail_symbols: set[str] | None = None
    ) -> None:
        self._rows_by_symbol = rows_by_symbol or {}
        self._fail_symbols = fail_symbols or set()

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        if symbol in self._fail_symbols:
            raise ccxt.NetworkError(f"simulated failure for {symbol}")
        since = since or 0
        rows = [r for r in self._rows_by_symbol.get(symbol, []) if r[0] >= since]
        return rows[:limit] if limit is not None else rows


def _row(open_time: dt.datetime) -> list:
    return [int(open_time.timestamp() * 1000), 100.0, 110.0, 90.0, 105.0, 10.0]


@pytest.fixture
def store() -> TidemarkStore:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    return TidemarkStore(engine)


def test_up_to_date_run_is_completed_not_failed(store: TidemarkStore) -> None:
    now = dt.datetime(2026, 9, 22, 12, tzinfo=dt.UTC)
    fake = _FakeExchange(rows_by_symbol={})
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: now)

    outcome = run_backfill(store, exchange, VENUE, ["BTC/USDT:USDT"], ["4h"], days=1, now=now)

    assert outcome.status == "COMPLETED"
    assert outcome.outcomes[0].result.inserted == 0


def test_one_symbol_failing_gives_partial(store: TidemarkStore) -> None:
    now = dt.datetime(2026, 9, 22, 12, tzinfo=dt.UTC)
    open_time = now - dt.timedelta(hours=4)
    fake = _FakeExchange(
        rows_by_symbol={"ETH/USDT:USDT": [_row(open_time)]},
        fail_symbols={"BTC/USDT:USDT"},
    )
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: now, max_retries=0)

    outcome = run_backfill(
        store, exchange, VENUE, ["BTC/USDT:USDT", "ETH/USDT:USDT"], ["4h"], days=1, now=now
    )

    assert outcome.status == "PARTIAL"
    by_symbol = {o.symbol: o for o in outcome.outcomes}
    assert by_symbol["BTC/USDT:USDT"].error is not None
    assert by_symbol["ETH/USDT:USDT"].error is None
    assert store.count_candles(VENUE, "ETH/USDT:USDT", "4h") == 1
    assert store.count_candles(VENUE, "BTC/USDT:USDT", "4h") == 0


def test_all_symbols_failing_gives_failed(store: TidemarkStore) -> None:
    now = dt.datetime(2026, 9, 22, 12, tzinfo=dt.UTC)
    fake = _FakeExchange(rows_by_symbol={}, fail_symbols={"BTC/USDT:USDT"})
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: now, max_retries=0)

    outcome = run_backfill(store, exchange, VENUE, ["BTC/USDT:USDT"], ["4h"], days=1, now=now)

    assert outcome.status == "FAILED"


def test_run_update_fetches_since_last_stored_candle(store: TidemarkStore) -> None:
    now = dt.datetime(2026, 9, 22, 12, tzinfo=dt.UTC)
    earlier = now - dt.timedelta(hours=8)
    later = now - dt.timedelta(hours=4)
    fake = _FakeExchange(rows_by_symbol={"BTC/USDT:USDT": [_row(earlier), _row(later)]})
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: now)

    store.upsert_candles(
        VENUE,
        "BTC/USDT:USDT",
        "4h",
        [
            RawCandle(
                open_time=earlier,
                close_time=earlier + dt.timedelta(hours=4),
                open=100,
                high=110,
                low=90,
                close=105,
                volume=10,
            )
        ],
        now,
    )

    outcome = run_update(store, exchange, VENUE, ["BTC/USDT:USDT"], ["4h"], now=now)

    assert outcome.status == "COMPLETED"
    assert store.count_candles(VENUE, "BTC/USDT:USDT", "4h") == 2


def test_run_update_without_prior_data_uses_fallback_lookback(store: TidemarkStore) -> None:
    now = dt.datetime(2026, 9, 22, 12, tzinfo=dt.UTC)
    open_time = now - dt.timedelta(days=2)
    fake = _FakeExchange(rows_by_symbol={"BTC/USDT:USDT": [_row(open_time)]})
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: now)

    outcome = run_update(
        store, exchange, VENUE, ["BTC/USDT:USDT"], ["4h"], fallback_days=7, now=now
    )

    assert outcome.status == "COMPLETED"
    assert store.count_candles(VENUE, "BTC/USDT:USDT", "4h") == 1
