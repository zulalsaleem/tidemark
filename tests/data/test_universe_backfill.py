"""Daily candle backfill for ACTIVE market_registry symbols (Phase 6,
Merge 2A, PART B). No network - reuses data/ingest.py's fake-exchange
pattern (see tests/data/test_ingest.py).
"""

from __future__ import annotations

import datetime as dt

import ccxt
import pytest

from tidemark.data.exchange import ExchangeClient
from tidemark.data.models import MarketRegistry
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.data.universe_backfill import active_symbols, run_universe_backfill

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


def _registry_row(symbol: str, status: str = "ACTIVE") -> MarketRegistry:
    base = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    return MarketRegistry(
        venue=VENUE,
        symbol=symbol,
        contract_type="perpetual",
        quote_currency="USDT",
        first_candle_seen_at=None,
        last_candle_seen_at=None,
        first_seen_in_venue_list_at=base,
        last_seen_in_venue_list_at=base,
        status=status,
        section1_first_usable_at=None,
        section1_eligibility_checked_at=None,
    )


@pytest.fixture
def store() -> TidemarkStore:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    return TidemarkStore(engine)


def test_active_symbols_excludes_non_active_rows(store: TidemarkStore) -> None:
    store.record_market_listing(
        VENUE, "BTC/USDT:USDT", "perpetual", "USDT", dt.datetime.now(dt.UTC)
    )
    store.upsert_market_registry_row(_registry_row("ETH/USDT:USDT", status="ABSENT_FROM_VENUE"))

    assert active_symbols(store, VENUE) == ["BTC/USDT:USDT"]


def test_backfill_covers_only_active_symbols(store: TidemarkStore) -> None:
    now = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    open_time = now - dt.timedelta(days=1)
    store.upsert_market_registry_row(_registry_row("BTC/USDT:USDT", status="ACTIVE"))
    store.upsert_market_registry_row(_registry_row("ETH/USDT:USDT", status="ABSENT_FROM_VENUE"))
    fake = _FakeExchange(
        rows_by_symbol={
            "BTC/USDT:USDT": [_row(open_time)],
            "ETH/USDT:USDT": [_row(open_time)],
        }
    )
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: now)

    outcome = run_universe_backfill(store, exchange, VENUE, days=5, now=now)

    assert outcome.symbols_attempted == 1
    symbols_touched = {o.symbol for o in outcome.run_outcome.outcomes}
    assert symbols_touched == {"BTC/USDT:USDT"}
    assert store.count_candles(VENUE, "BTC/USDT:USDT", "1d") == 1
    assert store.count_candles(VENUE, "ETH/USDT:USDT", "1d") == 0


def test_one_symbol_failing_gives_partial(store: TidemarkStore) -> None:
    now = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    open_time = now - dt.timedelta(days=1)
    store.upsert_market_registry_row(_registry_row("BTC/USDT:USDT"))
    store.upsert_market_registry_row(_registry_row("ETH/USDT:USDT"))
    fake = _FakeExchange(
        rows_by_symbol={"ETH/USDT:USDT": [_row(open_time)]},
        fail_symbols={"BTC/USDT:USDT"},
    )
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: now, max_retries=0)

    outcome = run_universe_backfill(store, exchange, VENUE, days=5, now=now)

    assert outcome.run_outcome.status == "PARTIAL"
    assert outcome.symbols_with_coverage_updated == 1  # only ETH got coverage recorded


def test_registry_coverage_reflects_candles_actually_stored(store: TidemarkStore) -> None:
    now = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    day1 = now - dt.timedelta(days=3)
    day2 = now - dt.timedelta(days=2)
    store.upsert_market_registry_row(_registry_row("BTC/USDT:USDT"))
    fake = _FakeExchange(rows_by_symbol={"BTC/USDT:USDT": [_row(day1), _row(day2)]})
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: now)

    run_universe_backfill(store, exchange, VENUE, days=10, now=now)

    row = store.market_registry_row(VENUE, "BTC/USDT:USDT")
    assert row is not None
    assert row.first_candle_seen_at == day1
    assert row.last_candle_seen_at == day2 + dt.timedelta(days=1)  # a 1D candle's close_time


def test_registry_coverage_untouched_for_a_failed_symbol(store: TidemarkStore) -> None:
    now = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    store.upsert_market_registry_row(_registry_row("BTC/USDT:USDT"))
    fake = _FakeExchange(rows_by_symbol={}, fail_symbols={"BTC/USDT:USDT"})
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: now, max_retries=0)

    run_universe_backfill(store, exchange, VENUE, days=5, now=now)

    row = store.market_registry_row(VENUE, "BTC/USDT:USDT")
    assert row is not None
    assert row.first_candle_seen_at is None
    assert row.last_candle_seen_at is None


def test_progress_callback_invoked_once_per_symbol(store: TidemarkStore) -> None:
    now = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    store.upsert_market_registry_row(_registry_row("BTC/USDT:USDT"))
    store.upsert_market_registry_row(_registry_row("ETH/USDT:USDT"))
    fake = _FakeExchange(rows_by_symbol={})
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: now)
    seen: list[str] = []

    run_universe_backfill(
        store, exchange, VENUE, days=5, now=now, on_outcome=lambda o: seen.append(o.symbol)
    )

    assert sorted(seen) == ["BTC/USDT:USDT", "ETH/USDT:USDT"]


def test_no_active_symbols_gives_completed_empty_run(store: TidemarkStore) -> None:
    fake = _FakeExchange(rows_by_symbol={})
    exchange = ExchangeClient(exchange=fake)

    outcome = run_universe_backfill(store, exchange, VENUE, days=5)

    assert outcome.symbols_attempted == 0
    assert outcome.run_outcome.status == "COMPLETED"
