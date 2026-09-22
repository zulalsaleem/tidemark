"""Storage: idempotent upserts, sanity checks, gap detection, run bookkeeping."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import inspect

from tidemark.data.exchange import RawCandle
from tidemark.data.models import RejectedCandle
from tidemark.data.store import CandleUpsertResult, TidemarkStore, create_store_engine, init_db

VENUE = "binanceusdm"
SYMBOL = "BTC/USDT:USDT"
TIMEFRAME = "4h"


def _candle(open_time: dt.datetime, o=100.0, h=110.0, low=90.0, c=105.0, v=10.0) -> RawCandle:
    return RawCandle(
        open_time=open_time,
        close_time=open_time + dt.timedelta(hours=4),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
    )


@pytest.fixture
def store() -> TidemarkStore:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    return TidemarkStore(engine)


def test_init_db_creates_expected_tables() -> None:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    tables = set(inspect(engine).get_table_names())
    assert {
        "candles",
        "rejected_candles",
        "runs",
        "run_symbol_stats",
        "swings",
        "levels",
        "context_records",
        "journal_entries",
    } <= tables


def test_double_upsert_inserts_zero_duplicates(store: TidemarkStore) -> None:
    base = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    candles = [_candle(base + dt.timedelta(hours=4 * i)) for i in range(5)]
    fetched_at = dt.datetime.now(dt.UTC)

    first = store.upsert_candles(VENUE, SYMBOL, TIMEFRAME, candles, fetched_at)
    assert first == CandleUpsertResult(fetched=5, inserted=5, duplicates_skipped=0, rejected=0)

    second = store.upsert_candles(VENUE, SYMBOL, TIMEFRAME, candles, fetched_at)
    assert second == CandleUpsertResult(fetched=5, inserted=0, duplicates_skipped=5, rejected=0)

    assert store.count_candles(VENUE, SYMBOL, TIMEFRAME) == 5


def test_invalid_ohlc_rows_are_rejected_and_recorded(store: TidemarkStore) -> None:
    base = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    good = _candle(base)
    bad_high = _candle(base + dt.timedelta(hours=4), o=100, h=95, low=90, c=105)
    bad_low = _candle(base + dt.timedelta(hours=8), o=100, h=110, low=101, c=105)
    negative_volume = _candle(base + dt.timedelta(hours=12), v=-1)
    zero_price = _candle(base + dt.timedelta(hours=16), o=0)

    result = store.upsert_candles(
        VENUE,
        SYMBOL,
        TIMEFRAME,
        [good, bad_high, bad_low, negative_volume, zero_price],
        dt.datetime.now(dt.UTC),
    )

    assert result.inserted == 1
    assert result.rejected == 4
    assert store.count_candles(VENUE, SYMBOL, TIMEFRAME) == 1

    with store._session_factory() as session:
        rejected_rows = list(session.query(RejectedCandle))
    assert len(rejected_rows) == 4
    assert all(row.reason for row in rejected_rows)


def test_gap_detection_finds_removed_candle(store: TidemarkStore) -> None:
    base = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    candles = [_candle(base + dt.timedelta(hours=4 * i)) for i in range(5)]
    del candles[2]  # remove base+8h -> a deliberate gap

    store.upsert_candles(VENUE, SYMBOL, TIMEFRAME, candles, dt.datetime.now(dt.UTC))

    gaps = store.find_gaps(VENUE, SYMBOL, TIMEFRAME)
    assert gaps == [base + dt.timedelta(hours=8)]


def test_gap_detection_reports_nothing_for_contiguous_candles(store: TidemarkStore) -> None:
    base = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    candles = [_candle(base + dt.timedelta(hours=4 * i)) for i in range(5)]
    store.upsert_candles(VENUE, SYMBOL, TIMEFRAME, candles, dt.datetime.now(dt.UTC))

    assert store.find_gaps(VENUE, SYMBOL, TIMEFRAME) == []


def test_stored_timestamps_are_utc_aware(store: TidemarkStore) -> None:
    base = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    store.upsert_candles(VENUE, SYMBOL, TIMEFRAME, [_candle(base)], dt.datetime.now(dt.UTC))

    [candle] = store.get_candles(VENUE, SYMBOL, TIMEFRAME)
    assert candle.open_time.tzinfo is not None
    assert candle.open_time.utcoffset() == dt.timedelta(0)
    assert candle.close_time.tzinfo is not None
    assert candle.fetched_at.tzinfo is not None
    assert candle.open_time == base


def test_start_run_inserts_running_row(store: TidemarkStore) -> None:
    started = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)

    store.start_run("run-1", "backfill", started)

    run = store.latest_run()
    assert run is not None
    assert run.run_id == "run-1"
    assert run.status == "RUNNING"
    assert run.finished_at is None
    assert run.started_at.tzinfo is not None

    [running] = store.running_runs()
    assert running.run_id == "run-1"


def test_finish_run_rejects_invalid_status(store: TidemarkStore) -> None:
    now = dt.datetime.now(dt.UTC)
    store.start_run("run-bad", "backfill", now)
    with pytest.raises(ValueError, match="invalid run status"):
        store.finish_run(run_id="run-bad", finished_at=now, status="BOGUS", stats={})


def test_finish_run_rejects_running_as_a_final_status(store: TidemarkStore) -> None:
    now = dt.datetime.now(dt.UTC)
    store.start_run("run-bad", "backfill", now)
    with pytest.raises(ValueError, match="invalid run status"):
        store.finish_run(run_id="run-bad", finished_at=now, status="RUNNING", stats={})


def test_start_then_finish_run_and_latest_run(store: TidemarkStore) -> None:
    started = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    finished = started + dt.timedelta(minutes=1)
    stats = {
        (SYMBOL, TIMEFRAME): CandleUpsertResult(
            fetched=5, inserted=5, duplicates_skipped=0, rejected=0
        ),
    }

    store.start_run("run-1", "backfill", started)
    store.finish_run("run-1", finished, "COMPLETED", stats)

    run = store.latest_run()
    assert run is not None
    assert run.run_id == "run-1"
    assert run.status == "COMPLETED"
    assert run.started_at.tzinfo is not None
    assert run.finished_at is not None
    assert run.finished_at.tzinfo is not None
    assert store.running_runs() == []

    [stat] = store.run_symbol_stats("run-1")
    assert stat.symbol == SYMBOL
    assert stat.inserted == 5


def test_running_runs_excludes_finished_ones(store: TidemarkStore) -> None:
    now = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    store.start_run("run-done", "backfill", now)
    store.finish_run("run-done", now, "COMPLETED", {})
    store.start_run("run-stuck", "update", now)

    [running] = store.running_runs()
    assert running.run_id == "run-stuck"
