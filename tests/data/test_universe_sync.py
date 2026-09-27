"""Full-timeframe candle sync for the currently selected universe (Phase
6, Merge 3). No network - a fake ccxt-like stub (see
tests/data/test_ingest.py's own pattern).
"""

from __future__ import annotations

import datetime as dt

import pytest

from tidemark.data.exchange import ExchangeClient
from tidemark.data.models import UniverseSnapshot, UniverseSnapshotRow
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.data.universe_sync import (
    SYNC_TIMEFRAMES,
    run_universe_sync,
    selected_symbols,
)

VENUE = "binanceusdm"
NOW = dt.datetime(2026, 9, 27, tzinfo=dt.UTC)


class _FakeExchange:
    apiKey = ""
    secret = ""

    def __init__(self, rows_by_symbol: dict[str, list] | None = None) -> None:
        self._rows_by_symbol = rows_by_symbol or {}

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
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


def _save_snapshot(
    store: TidemarkStore, selected: list[str], excluded: list[str] | None = None
) -> None:
    header = UniverseSnapshot(
        snapshot_id="snap-1",
        snapshot_at=NOW,
        methodology_version="universe-v2",
        venue=VENUE,
        metric_name="MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D",
        metric_window_days=30,
        k=50,
        n_selected=len(selected),
        provenance="FORWARD",
        candle_hash="deadbeef",
        counts_by_exclusion_reason={},
    )
    rows = [
        UniverseSnapshotRow(
            snapshot_id="snap-1",
            symbol=s,
            rank=i + 1,
            metric_value=1000.0 - i,
            eligible=True,
            selected=True,
            exclusion_reason=None,
        )
        for i, s in enumerate(selected)
    ]
    rows += [
        UniverseSnapshotRow(
            snapshot_id="snap-1",
            symbol=s,
            rank=len(selected) + i + 1,
            metric_value=1.0,
            eligible=False,
            selected=False,
            exclusion_reason="NON_CRYPTO_UNDERLYING",
        )
        for i, s in enumerate(excluded or [])
    ]
    store.save_universe_snapshot(header, rows)


def test_selected_symbols_returns_only_selected_rows_in_rank_order(store: TidemarkStore) -> None:
    _save_snapshot(store, selected=["BTC/USDT:USDT", "ETH/USDT:USDT"], excluded=["MSTR/USDT:USDT"])

    assert selected_symbols(store, VENUE) == ["BTC/USDT:USDT", "ETH/USDT:USDT"]


def test_selected_symbols_empty_when_no_snapshot_exists(store: TidemarkStore) -> None:
    assert selected_symbols(store, VENUE) == []


def test_sync_fetches_every_timeframe_for_selected_symbols_only(store: TidemarkStore) -> None:
    _save_snapshot(store, selected=["BTC/USDT:USDT"], excluded=["MSTR/USDT:USDT"])
    open_time = NOW - dt.timedelta(days=10)
    fake = _FakeExchange(
        rows_by_symbol={
            "BTC/USDT:USDT": [_row(open_time)],
            "MSTR/USDT:USDT": [_row(open_time)],
        }
    )
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: NOW)

    outcome = run_universe_sync(store, exchange, VENUE, days=15, now=NOW)

    assert outcome.status == "COMPLETED"
    touched_symbols = {o.symbol for o in outcome.outcomes}
    assert touched_symbols == {"BTC/USDT:USDT"}  # MSTR (excluded) is never touched
    for timeframe in SYNC_TIMEFRAMES:
        assert store.count_candles(VENUE, "BTC/USDT:USDT", timeframe) == 1
        assert store.count_candles(VENUE, "MSTR/USDT:USDT", timeframe) == 0


def test_sync_covers_1h_4h_1d_1w_exactly(store: TidemarkStore) -> None:
    _save_snapshot(store, selected=["BTC/USDT:USDT"])
    fake = _FakeExchange(rows_by_symbol={})
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: NOW)

    outcome = run_universe_sync(store, exchange, VENUE, days=15, now=NOW)

    timeframes_touched = {o.timeframe for o in outcome.outcomes}
    assert timeframes_touched == {"1h", "4h", "1d", "1w"}


def test_sync_is_idempotent(store: TidemarkStore) -> None:
    _save_snapshot(store, selected=["BTC/USDT:USDT"])
    open_time = NOW - dt.timedelta(days=10)
    fake = _FakeExchange(rows_by_symbol={"BTC/USDT:USDT": [_row(open_time)]})
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: NOW)

    run_universe_sync(store, exchange, VENUE, days=15, now=NOW)
    second = run_universe_sync(store, exchange, VENUE, days=15, now=NOW)

    assert second.status == "COMPLETED"
    for outcome in second.outcomes:
        assert outcome.result.inserted == 0  # nothing new the second time
    assert store.count_candles(VENUE, "BTC/USDT:USDT", "4h") == 1


def test_sync_with_no_snapshot_is_a_completed_no_op(store: TidemarkStore) -> None:
    fake = _FakeExchange(rows_by_symbol={})
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: NOW)

    outcome = run_universe_sync(store, exchange, VENUE, days=15, now=NOW)

    assert outcome.status == "COMPLETED"
    assert outcome.outcomes == []


def test_sync_one_symbol_failing_gives_partial(store: TidemarkStore) -> None:
    import ccxt

    class _FailingExchange(_FakeExchange):
        def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
            if symbol == "BAD/USDT:USDT":
                raise ccxt.NetworkError("simulated failure")
            return super().fetch_ohlcv(symbol, timeframe, since, limit)

    _save_snapshot(store, selected=["BTC/USDT:USDT", "BAD/USDT:USDT"])
    open_time = NOW - dt.timedelta(days=10)
    fake = _FailingExchange(rows_by_symbol={"BTC/USDT:USDT": [_row(open_time)]})
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: NOW, max_retries=0)

    outcome = run_universe_sync(store, exchange, VENUE, days=15, now=NOW)

    assert outcome.status == "PARTIAL"
