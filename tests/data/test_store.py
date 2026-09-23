"""SQLite persistence: candle upserts, as-of filtering, idempotent context records."""

import datetime as dt

from sqlalchemy import inspect

from tidemark.data.models import Candle, ContextRecord
from tidemark.data.store import TidemarkStore, create_store_engine, init_db

START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _store() -> TidemarkStore:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    return TidemarkStore(engine)


def _candle(i: int, close: float = 100.0) -> Candle:
    close_time = START + dt.timedelta(hours=4 * i)
    return Candle(
        asset="BTCUSDT",
        timeframe="4h",
        open_time=close_time - dt.timedelta(hours=4),
        close_time=close_time,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=10.0,
    )


def test_init_db_creates_expected_tables() -> None:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"candles", "swings", "levels", "context_records", "journal_entries"} <= tables


def test_save_and_get_candles_roundtrip_oldest_to_newest() -> None:
    store = _store()
    store.save_candles([_candle(0, 100.0), _candle(1, 101.0), _candle(2, 102.0)])

    df = store.get_candles("BTCUSDT", "4h")

    assert list(df["close"]) == [100.0, 101.0, 102.0]
    assert list(df["close_time"]) == [
        START,
        START + dt.timedelta(hours=4),
        START + dt.timedelta(hours=8),
    ]


def test_save_candles_upserts_on_close_time_conflict() -> None:
    store = _store()
    store.save_candles([_candle(0, 100.0)])
    store.save_candles([_candle(0, 999.0)])  # same asset/timeframe/close_time

    df = store.get_candles("BTCUSDT", "4h")

    assert len(df) == 1
    assert df["close"].iloc[0] == 999.0


def test_get_candles_as_of_excludes_later_candles() -> None:
    store = _store()
    store.save_candles([_candle(0), _candle(1), _candle(2)])

    df = store.get_candles("BTCUSDT", "4h", as_of=START + dt.timedelta(hours=4))

    assert list(df["close_time"]) == [START, START + dt.timedelta(hours=4)]


def test_get_candles_limit_keeps_most_recent() -> None:
    store = _store()
    store.save_candles([_candle(i) for i in range(5)])

    df = store.get_candles("BTCUSDT", "4h", limit=2)

    assert list(df["close_time"]) == [
        START + dt.timedelta(hours=12),
        START + dt.timedelta(hours=16),
    ]


def _record(evaluated_at: dt.datetime, state: str = "BULLISH") -> ContextRecord:
    return ContextRecord(
        asset="BTCUSDT",
        evaluated_at=evaluated_at,
        rule_version="section-01-v1.0",
        state=state,
        watch="WAIT",
        grade=None,
        reason_code="NOT_IN_ZONE",
        active_levels=[],
        fib={},
        swings_used=[],
    )


def test_save_context_record_is_idempotent() -> None:
    store = _store()
    store.save_context_record(_record(START))
    store.save_context_record(_record(START, state="BEARISH"))

    history = store.context_history("BTCUSDT")

    assert len(history) == 1
    assert history[0].state == "BEARISH"


def test_latest_context_record_returns_most_recent() -> None:
    store = _store()
    store.save_context_record(_record(START))
    store.save_context_record(_record(START + dt.timedelta(hours=4)))

    latest = store.latest_context_record("BTCUSDT")

    assert latest.evaluated_at == START + dt.timedelta(hours=4)


def test_latest_context_record_none_when_missing() -> None:
    store = _store()
    assert store.latest_context_record("BTCUSDT") is None
