"""Storage: idempotent upserts, sanity checks, gap detection, run bookkeeping."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import inspect

from tidemark.data.exchange import RawCandle
from tidemark.data.models import JournalEntry, RejectedCandle
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
        "journal_entries",
    } <= tables
    # context_records was removed (Phase 8): its only writer never ran in
    # production, journal_entries is the single source of Section 1
    # results - see docs/adr/0011-market-intelligence-layer.md's
    # "Addendum: removing context_records".
    assert "context_records" not in tables


def test_double_upsert_inserts_zero_duplicates(store: TidemarkStore) -> None:
    base = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    candles = [_candle(base + dt.timedelta(hours=4 * i)) for i in range(5)]
    fetched_at = dt.datetime.now(dt.UTC)

    first = store.upsert_candles(VENUE, SYMBOL, TIMEFRAME, candles, fetched_at)
    assert first == CandleUpsertResult(fetched=5, inserted=5, duplicates_skipped=0, rejected=0)

    second = store.upsert_candles(VENUE, SYMBOL, TIMEFRAME, candles, fetched_at)
    assert second == CandleUpsertResult(fetched=5, inserted=0, duplicates_skipped=5, rejected=0)

    assert store.count_candles(VENUE, SYMBOL, TIMEFRAME) == 5


def test_large_batch_upsert_inserts_all_candles_without_variable_limit_error(
    store: TidemarkStore,
) -> None:
    """A single `upsert_candles` call with enough rows that one INSERT
    statement would exceed SQLite's bound-parameter limit must still
    insert every row - chunked transparently, not truncated or failed.
    """
    base = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    n = 1500  # well past one chunk at 11 columns/row (~90 rows/chunk at the 999 limit)
    candles = [_candle(base + dt.timedelta(hours=4 * i)) for i in range(n)]

    result = store.upsert_candles(VENUE, SYMBOL, TIMEFRAME, candles, dt.datetime.now(dt.UTC))

    assert result == CandleUpsertResult(fetched=n, inserted=n, duplicates_skipped=0, rejected=0)
    assert store.count_candles(VENUE, SYMBOL, TIMEFRAME) == n


def test_large_batch_upsert_rerun_inserts_zero_duplicates(store: TidemarkStore) -> None:
    base = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    n = 1500
    candles = [_candle(base + dt.timedelta(hours=4 * i)) for i in range(n)]
    fetched_at = dt.datetime.now(dt.UTC)

    store.upsert_candles(VENUE, SYMBOL, TIMEFRAME, candles, fetched_at)
    second = store.upsert_candles(VENUE, SYMBOL, TIMEFRAME, candles, fetched_at)

    assert second == CandleUpsertResult(fetched=n, inserted=0, duplicates_skipped=n, rejected=0)
    assert store.count_candles(VENUE, SYMBOL, TIMEFRAME) == n


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


# -- journal (Phase 3) --------------------------------------------------------
#
# context_records/ContextRecord persistence (save_context_record/
# latest_context_record/context_history) was removed - see
# docs/adr/0011-market-intelligence-layer.md's "Addendum: removing
# context_records". The idempotency, rule-version-keying, and UTC-aware
# properties those tests once proved for ContextRecord are still fully
# proven below for JournalEntry, the table that actually persists Section
# 1 results: test_save_journal_entry_is_idempotent_on_repeat,
# test_save_journal_entry_keys_on_asset_evaluated_at_and_rule_version,
# test_latest_journal_entry_scoped_to_rule_version. UTC-aware round-
# tripping for every model, including JournalEntry, is covered by
# tests/data/test_models.py::test_every_model_datetime_round_trips_utc_aware.


def _journal_entry(evaluated_at: dt.datetime, state: str = "BULLISH", **overrides) -> JournalEntry:
    defaults = dict(
        asset=SYMBOL,
        evaluated_at=evaluated_at,
        recorded_at=evaluated_at + dt.timedelta(seconds=5),
        rule_version="section-01-v1.1",
        state=state,
        watch="LONG_WATCH",
        grade="B",
        reason_code="MAJOR_SUPPORT",
        active_levels=[],
        fib={},
        swings_used=[],
        alert_sent=False,
        alert_reason=None,
    )
    defaults.update(overrides)
    return JournalEntry(**defaults)


def test_save_journal_entry_inserts_a_new_row(store: TidemarkStore) -> None:
    evaluated_at = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)

    result = store.save_journal_entry(_journal_entry(evaluated_at))

    assert result.inserted is True
    assert len(store.journal_history(SYMBOL)) == 1


def test_save_journal_entry_is_idempotent_on_repeat(store: TidemarkStore) -> None:
    evaluated_at = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)

    first = store.save_journal_entry(_journal_entry(evaluated_at, state="BULLISH"))
    second = store.save_journal_entry(_journal_entry(evaluated_at, state="BEARISH"))

    assert first.inserted is True
    assert second.inserted is False
    # Append-only: unlike ContextRecord, the repeat's differing state is
    # NOT written back - the original row is returned untouched.
    assert second.entry.state == "BULLISH"

    history = store.journal_history(SYMBOL)
    assert len(history) == 1
    assert history[0].state == "BULLISH"


def test_save_journal_entry_keys_on_asset_evaluated_at_and_rule_version(
    store: TidemarkStore,
) -> None:
    evaluated_at = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    store.save_journal_entry(_journal_entry(evaluated_at, rule_version="section-01-v1.0"))
    store.save_journal_entry(_journal_entry(evaluated_at, rule_version="section-01-v1.1"))

    assert len(store.journal_history(SYMBOL)) == 2


def test_latest_journal_entry_scoped_to_rule_version(store: TidemarkStore) -> None:
    base = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    store.save_journal_entry(_journal_entry(base, rule_version="section-01-v1.0"))
    store.save_journal_entry(
        _journal_entry(base + dt.timedelta(hours=4), rule_version="section-01-v1.1")
    )

    latest_v1_0 = store.latest_journal_entry(SYMBOL, "section-01-v1.0")
    latest_v1_1 = store.latest_journal_entry(SYMBOL, "section-01-v1.1")

    assert latest_v1_0 is not None
    assert latest_v1_0.evaluated_at == base
    assert latest_v1_1 is not None
    assert latest_v1_1.evaluated_at == base + dt.timedelta(hours=4)


def test_latest_journal_entry_none_when_missing(store: TidemarkStore) -> None:
    assert store.latest_journal_entry(SYMBOL, "section-01-v1.1") is None


def test_record_alert_outcome_updates_only_alert_fields(store: TidemarkStore) -> None:
    evaluated_at = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    result = store.save_journal_entry(_journal_entry(evaluated_at, state="BULLISH", grade="B"))

    store.record_alert_outcome(result.entry.id, alert_sent=True, alert_reason="WATCH_OPENED")

    [entry] = store.journal_history(SYMBOL)
    assert entry.alert_sent is True
    assert entry.alert_reason == "WATCH_OPENED"
    # Nothing else on the row moved.
    assert entry.state == "BULLISH"
    assert entry.grade == "B"


def test_a_telegram_failure_leaves_the_journal_row_intact(store: TidemarkStore) -> None:
    """PART E: a Telegram failure still leaves the journal row intact,
    with alert_sent=false and the attempted reason recorded."""
    evaluated_at = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    result = store.save_journal_entry(_journal_entry(evaluated_at, state="BULLISH"))

    # Simulate a failed send: alert_sent=False, but the reason the change
    # detector produced is still recorded.
    store.record_alert_outcome(result.entry.id, alert_sent=False, alert_reason="WATCH_OPENED")

    [entry] = store.journal_history(SYMBOL)
    assert entry.alert_sent is False
    assert entry.alert_reason == "WATCH_OPENED"
    assert entry.state == "BULLISH"  # the evaluation itself is untouched


def test_journal_alerts_only_returns_sent_rows(store: TidemarkStore) -> None:
    base = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    no_alert = store.save_journal_entry(_journal_entry(base))
    sent_alert = store.save_journal_entry(_journal_entry(base + dt.timedelta(hours=4)))
    store.record_alert_outcome(sent_alert.entry.id, alert_sent=True, alert_reason="WATCH_OPENED")
    store.record_alert_outcome(no_alert.entry.id, alert_sent=False, alert_reason=None)

    alerts = store.journal_alerts()

    assert len(alerts) == 1
    assert alerts[0].evaluated_at == base + dt.timedelta(hours=4)
    assert alerts[0].alert_reason == "WATCH_OPENED"
