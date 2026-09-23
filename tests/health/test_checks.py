"""Health checks: OK/WARN/FAIL under the right conditions, with fixture
data and a frozen clock. No real network anywhere - these only touch a
SQLite store.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tidemark.data.exchange import RawCandle
from tidemark.data.models import JournalEntry, Run
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.health.checks import (
    EXIT_CODES,
    FAIL,
    GAPS_WARN_MAX,
    OK,
    WARN,
    check_candle_freshness,
    check_database,
    check_gaps,
    check_journal_activity,
    check_last_run,
    check_telegram_config,
    run_all_checks,
    worst_status,
)

NOW = dt.datetime(2026, 9, 23, 20, 0, tzinfo=dt.UTC)
VENUE = "binanceusdm"
SYMBOL = "BTC/USDT:USDT"


@pytest.fixture
def store() -> TidemarkStore:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    return TidemarkStore(engine)


def _candle(open_time: dt.datetime) -> RawCandle:
    return RawCandle(
        open_time=open_time,
        close_time=open_time + dt.timedelta(hours=4),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1.0,
    )


# --- worst_status -------------------------------------------------------


def test_worst_status_empty_is_ok() -> None:
    assert worst_status([]) == OK


def test_worst_status_is_the_worst_present() -> None:
    assert worst_status([OK, WARN, OK]) == WARN
    assert worst_status([OK, WARN, FAIL]) == FAIL
    assert worst_status([OK, OK]) == OK


# --- check_database -------------------------------------------------------


def test_check_database_ok_when_all_tables_present(store: TidemarkStore) -> None:
    result = check_database(store._engine)
    assert result.status == OK


def test_check_database_fail_when_a_table_is_missing() -> None:
    from tidemark.data.models import Base, Candle

    engine = create_store_engine("sqlite:///:memory:")
    # Create only a subset of the schema, simulating a partially
    # initialized / corrupted database.
    Candle.__table__.create(engine)
    result = check_database(engine)
    assert result.status == FAIL
    assert "missing table" in result.detail
    # Sanity: the full schema does have more than just "candles".
    assert len(Base.metadata.tables) > 1


def test_check_database_fail_when_unreachable() -> None:
    engine = create_store_engine("sqlite:///Z:/nonexistent/path/does/not/exist.db")
    result = check_database(engine)
    assert result.status == FAIL
    assert "unreachable" in result.detail


# --- check_candle_freshness -------------------------------------------------


def test_candle_freshness_ok_within_1_5x_interval(store: TidemarkStore) -> None:
    # 4h interval; 1.5x = 6h. A candle that closed 5h ago is OK.
    close_time = NOW - dt.timedelta(hours=5)
    store.upsert_candles(VENUE, SYMBOL, "4h", [_candle(close_time - dt.timedelta(hours=4))], NOW)

    result = check_candle_freshness(store, VENUE, SYMBOL, "4h", NOW)

    assert result.status == OK


def test_candle_freshness_warn_between_1_5x_and_3x_interval(store: TidemarkStore) -> None:
    # 4h interval; between 6h and 12h old -> WARN. Use 8h old.
    open_time = NOW - dt.timedelta(hours=12)
    store.upsert_candles(VENUE, SYMBOL, "4h", [_candle(open_time)], NOW)

    result = check_candle_freshness(store, VENUE, SYMBOL, "4h", NOW)

    assert result.status == WARN


def test_candle_freshness_fail_beyond_3x_interval(store: TidemarkStore) -> None:
    # 4h interval; beyond 12h old -> FAIL. Use 20h old.
    open_time = NOW - dt.timedelta(hours=24)
    store.upsert_candles(VENUE, SYMBOL, "4h", [_candle(open_time)], NOW)

    result = check_candle_freshness(store, VENUE, SYMBOL, "4h", NOW)

    assert result.status == FAIL


def test_candle_freshness_warn_when_no_candles_at_all(store: TidemarkStore) -> None:
    result = check_candle_freshness(store, VENUE, SYMBOL, "4h", NOW)

    assert result.status == WARN
    assert "no candles" in result.detail


# --- check_last_run -------------------------------------------------------


def _run(status: str, started_at: dt.datetime, finished_at: dt.datetime | None) -> Run:
    return Run(
        run_id="r1",
        command="run",
        started_at=started_at,
        finished_at=finished_at,
        status=status,
    )


def test_last_run_fail_when_failed() -> None:
    run = _run("FAILED", NOW - dt.timedelta(hours=1), NOW - dt.timedelta(minutes=30))
    result = check_last_run("run", run, NOW)
    assert result.status == FAIL


def test_last_run_warn_when_partial() -> None:
    run = _run("PARTIAL", NOW - dt.timedelta(hours=1), NOW - dt.timedelta(minutes=30))
    result = check_last_run("run", run, NOW)
    assert result.status == WARN


def test_last_run_ok_when_running_and_recent() -> None:
    run = _run("RUNNING", NOW - dt.timedelta(minutes=5), None)
    result = check_last_run("run", run, NOW)
    assert result.status == OK


def test_last_run_fail_when_running_older_than_2_hours() -> None:
    run = _run("RUNNING", NOW - dt.timedelta(hours=3), None)
    result = check_last_run("run", run, NOW)
    assert result.status == FAIL
    assert "crashed" in result.detail


def test_last_run_ok_when_completed_recently() -> None:
    run = _run("COMPLETED", NOW - dt.timedelta(hours=5), NOW - dt.timedelta(hours=4))
    result = check_last_run("run", run, NOW)
    assert result.status == OK


def test_last_run_fail_when_completed_over_8_hours_ago() -> None:
    run = _run("COMPLETED", NOW - dt.timedelta(hours=10), NOW - dt.timedelta(hours=9))
    result = check_last_run("run", run, NOW)
    assert result.status == FAIL
    assert "8" not in result.detail  # detail mentions age, not the threshold number


def test_last_run_fail_when_none_recorded() -> None:
    result = check_last_run("run", None, NOW)
    assert result.status == FAIL


# --- check_journal_activity ------------------------------------------------


def _journal_entry(recorded_at: dt.datetime) -> JournalEntry:
    return JournalEntry(
        asset=SYMBOL,
        evaluated_at=recorded_at,
        recorded_at=recorded_at,
        rule_version="section-01-v1.1",
        state="NEUTRAL",
        watch="WAIT",
        grade=None,
        reason_code="NEUTRAL_STRUCTURE",
        active_levels=[],
        fib={},
        swings_used=[],
        alert_sent=False,
        alert_reason=None,
    )


def test_journal_activity_ok_within_8_hours(store: TidemarkStore) -> None:
    store.save_journal_entry(_journal_entry(NOW - dt.timedelta(hours=1)))
    result = check_journal_activity(store, NOW)
    assert result.status == OK


def test_journal_activity_warn_between_8_and_24_hours(store: TidemarkStore) -> None:
    store.save_journal_entry(_journal_entry(NOW - dt.timedelta(hours=10)))
    result = check_journal_activity(store, NOW)
    assert result.status == WARN


def test_journal_activity_fail_beyond_24_hours(store: TidemarkStore) -> None:
    store.save_journal_entry(_journal_entry(NOW - dt.timedelta(hours=30)))
    result = check_journal_activity(store, NOW)
    assert result.status == FAIL


def test_journal_activity_fail_when_no_entries(store: TidemarkStore) -> None:
    result = check_journal_activity(store, NOW)
    assert result.status == FAIL
    assert "no journal entries" in result.detail


# --- check_gaps -------------------------------------------------------------


def test_gaps_ok_when_zero(store: TidemarkStore) -> None:
    candles = [_candle(NOW - dt.timedelta(hours=4 * i)) for i in range(5)]
    store.upsert_candles(VENUE, SYMBOL, "4h", candles, NOW)
    result = check_gaps(store, VENUE, SYMBOL, "4h")
    assert result.status == OK


def test_gaps_warn_between_1_and_5(store: TidemarkStore) -> None:
    base = NOW - dt.timedelta(hours=4 * 10)
    candles = [_candle(base + dt.timedelta(hours=4 * i)) for i in range(10)]
    del candles[3]  # exactly one gap
    store.upsert_candles(VENUE, SYMBOL, "4h", candles, NOW)
    result = check_gaps(store, VENUE, SYMBOL, "4h")
    assert result.status == WARN
    assert GAPS_WARN_MAX >= 1


def test_gaps_fail_above_5(store: TidemarkStore) -> None:
    base = NOW - dt.timedelta(hours=4 * 20)
    candles = [_candle(base + dt.timedelta(hours=4 * i)) for i in range(20)]
    # Remove 6 candles (indices spread out so each removal is its own gap).
    for i in sorted([2, 5, 8, 11, 14, 17], reverse=True):
        del candles[i]
    store.upsert_candles(VENUE, SYMBOL, "4h", candles, NOW)
    result = check_gaps(store, VENUE, SYMBOL, "4h")
    assert result.status == FAIL


# --- check_telegram_config --------------------------------------------------


def test_telegram_config_ok_when_configured() -> None:
    assert check_telegram_config(True).status == OK


def test_telegram_config_warn_when_missing() -> None:
    assert check_telegram_config(False).status == WARN


# --- run_all_checks (integration) ------------------------------------------


def test_run_all_checks_overall_status_is_worst_individual(store: TidemarkStore) -> None:
    # Fresh candles/runs/journal -> everything OK except telegram (not
    # configured) -> overall WARN.
    close_time = NOW - dt.timedelta(hours=1)
    store.upsert_candles(VENUE, SYMBOL, "4h", [_candle(close_time - dt.timedelta(hours=4))], NOW)
    store.start_run("r1", "run", NOW - dt.timedelta(minutes=10))
    store.finish_run("r1", NOW - dt.timedelta(minutes=5), "COMPLETED", {})
    store.save_journal_entry(_journal_entry(NOW - dt.timedelta(minutes=5)))

    report = run_all_checks(
        store=store,
        engine=store._engine,
        venue=VENUE,
        symbols=[SYMBOL],
        is_telegram_configured=False,
        rule_version="section-01-v1.1",
        now=NOW,
    )

    assert report.status == WARN
    assert any(c.name == "telegram_config" and c.status == WARN for c in report.checks)


def test_run_all_checks_empty_database_gives_fail_without_crashing(store: TidemarkStore) -> None:
    # Schema initialized (via the `store` fixture's init_db), but no
    # candles, no runs, no journal entries at all.
    report = run_all_checks(
        store=store,
        engine=store._engine,
        venue=VENUE,
        symbols=[SYMBOL],
        is_telegram_configured=True,
        rule_version="section-01-v1.1",
        now=NOW,
    )

    assert report.status == FAIL


def test_run_all_checks_unreachable_database_gives_fail_without_crashing() -> None:
    engine = create_store_engine("sqlite:///Z:/nonexistent/path/does/not/exist.db")
    store = TidemarkStore(engine)

    report = run_all_checks(
        store=store,
        engine=engine,
        venue=VENUE,
        symbols=[SYMBOL],
        is_telegram_configured=True,
        rule_version="section-01-v1.1",
        now=NOW,
    )

    assert report.status == FAIL
    assert len(report.checks) == 1
    assert report.checks[0].name == "database"


def test_run_all_checks_timeframe_with_no_candles_is_warn_not_fail(store: TidemarkStore) -> None:
    # No candles for any timeframe at all - each candle-freshness check
    # should be WARN, and none of them should be FAIL.
    report = run_all_checks(
        store=store,
        engine=store._engine,
        venue=VENUE,
        symbols=[SYMBOL],
        is_telegram_configured=True,
        rule_version="section-01-v1.1",
        now=NOW,
    )

    freshness_checks = [c for c in report.checks if c.name.startswith("candles:")]
    assert freshness_checks  # sanity: some were actually run
    assert all(c.status == WARN for c in freshness_checks)


def test_run_all_checks_a_stale_running_row_is_fail(store: TidemarkStore) -> None:
    store.start_run("stuck", "run", NOW - dt.timedelta(hours=3))
    # Give it recent-enough candles/journal so only the run check fails.
    store.upsert_candles(VENUE, SYMBOL, "4h", [_candle(NOW - dt.timedelta(hours=4))], NOW)
    store.save_journal_entry(_journal_entry(NOW - dt.timedelta(minutes=5)))

    report = run_all_checks(
        store=store,
        engine=store._engine,
        venue=VENUE,
        symbols=[SYMBOL],
        is_telegram_configured=True,
        rule_version="section-01-v1.1",
        now=NOW,
    )

    assert report.status == FAIL
    run_checks = [c for c in report.checks if c.name.startswith("last_run:")]
    assert any(c.status == FAIL and "crashed" in c.detail for c in run_checks)


# --- exit codes -------------------------------------------------------------


def test_exit_codes_map_correctly() -> None:
    assert EXIT_CODES[OK] == 0
    assert EXIT_CODES[WARN] == 1
    assert EXIT_CODES[FAIL] == 2


def test_health_report_exit_code_property() -> None:
    from tidemark.health.checks import HealthReport

    report = HealthReport(status=WARN, generated_at=NOW, rule_version="section-01-v1.1")
    assert report.exit_code == 1
