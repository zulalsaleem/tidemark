"""context_read.py: the one narrow, read-only exception to the
market_intel isolation boundary. Tests are free to use
`tidemark.data.store`/`tidemark.context`/`tidemark.journal` to set up
fixtures - only `market_intel`'s own production code is restricted (see
test_import_boundary.py).

Uses the REAL production write path (`journal.records.build_journal_entry`
+ `TidemarkStore.save_journal_entry`), not a bespoke row insert - a
bespoke insert into the wrong table is exactly what let context_read.py
read `context_records` for months while `tidemark run` only ever wrote
`journal_entries`, and no test caught it. See ADR 0011.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from tidemark.data.models import ContextRecord, JournalEntry
from tidemark.data.store import TidemarkStore, create_store_engine, get_session_factory, init_db
from tidemark.journal.records import build_journal_entry
from tidemark.market_intel.context_read import (
    SECTION1_STALE_AFTER,
    is_stale,
    read_latest_journal_entry,
)

ASSET = "BTC/USDT:USDT"


def _context_record(evaluated_at: dt.datetime, **overrides) -> ContextRecord:
    """A Section 1 evaluation output, exactly as `htf.evaluate` would
    return it in memory - never saved directly. Only `build_journal_entry`
    + `save_journal_entry` (the real `tidemark run` path) persist it.
    """
    defaults = dict(
        asset=ASSET,
        evaluated_at=evaluated_at,
        rule_version="section-01-v1.1",
        state="BULLISH",
        watch="LONG_WATCH",
        grade="A",
        reason_code="MAJOR_SUPPORT_FIB",
        active_levels=[],
        fib={},
        swings_used=[],
    )
    defaults.update(overrides)
    return ContextRecord(**defaults)


def _journal(store: TidemarkStore, evaluated_at: dt.datetime, **overrides) -> None:
    record = _context_record(evaluated_at, **overrides)
    entry = build_journal_entry(record, recorded_at=evaluated_at)
    store.save_journal_entry(entry)


def test_returns_none_for_a_fresh_database_with_no_journal_entries_table(tmp_path) -> None:
    db_path = (tmp_path / "empty.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    # Deliberately never call init_db - the table genuinely doesn't exist.

    assert read_latest_journal_entry(database_url, ASSET) is None


def test_returns_none_when_no_entry_exists_for_the_asset(tmp_path) -> None:
    db_path = (tmp_path / "tidemark.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    engine = create_store_engine(database_url)
    init_db(engine)
    store = TidemarkStore(engine)
    _journal(store, dt.datetime(2026, 1, 1, tzinfo=dt.UTC), asset="ETH/USDT:USDT")

    assert read_latest_journal_entry(database_url, ASSET) is None


def test_returns_the_latest_entry_for_the_asset(tmp_path) -> None:
    db_path = (tmp_path / "tidemark.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    engine = create_store_engine(database_url)
    init_db(engine)
    store = TidemarkStore(engine)
    older = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    newer = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)
    _journal(store, older, state="NEUTRAL")
    _journal(store, newer, state="BEARISH")

    result = read_latest_journal_entry(database_url, ASSET)

    assert result is not None
    assert result.state == "BEARISH"
    assert result.evaluated_at == newer


def test_reads_what_tidemark_run_actually_writes_not_context_records(tmp_path) -> None:
    """The regression this module exists to fix: `tidemark run` writes
    only `journal_entries` (never `context_records` - see ADR 0011's
    Merge 3 addendum). A row in `context_records` alone must NOT be
    visible here.
    """
    db_path = (tmp_path / "tidemark.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    engine = create_store_engine(database_url)
    init_db(engine)
    store = TidemarkStore(engine)

    # Only the standalone `tidemark context evaluate` path writes this -
    # simulated directly here since that's the only real writer.
    store.save_context_record(_context_record(dt.datetime(2026, 1, 1, tzinfo=dt.UTC)))

    assert read_latest_journal_entry(database_url, ASSET) is None

    # Once `tidemark run`'s real write path adds a journal_entries row,
    # it becomes visible.
    _journal(store, dt.datetime(2026, 1, 2, tzinfo=dt.UTC), state="BEARISH")

    result = read_latest_journal_entry(database_url, ASSET)
    assert result is not None
    assert result.state == "BEARISH"


def test_never_writes_anything(tmp_path) -> None:
    db_path = (tmp_path / "tidemark.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    engine = create_store_engine(database_url)
    init_db(engine)
    store = TidemarkStore(engine)
    _journal(store, dt.datetime(2026, 1, 1, tzinfo=dt.UTC))

    read_latest_journal_entry(database_url, ASSET)
    read_latest_journal_entry(database_url, ASSET)

    # Still exactly one row - a read never inserts, updates, or duplicates.
    with get_session_factory(engine)() as session:
        count = session.scalar(select(func.count()).select_from(JournalEntry))
    assert count == 1


# -- staleness ------------------------------------------------------------


def test_is_stale_false_within_the_grace_window(tmp_path) -> None:
    engine = create_store_engine(f"sqlite:///{(tmp_path / 'tidemark.db').as_posix()}")
    init_db(engine)
    store = TidemarkStore(engine)
    evaluated_at = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    _journal(store, evaluated_at)
    entry = store.latest_journal_entry(ASSET, "section-01-v1.1")

    now = evaluated_at + SECTION1_STALE_AFTER - dt.timedelta(minutes=1)
    assert is_stale(entry, now) is False


def test_is_stale_true_beyond_the_grace_window(tmp_path) -> None:
    engine = create_store_engine(f"sqlite:///{(tmp_path / 'tidemark.db').as_posix()}")
    init_db(engine)
    store = TidemarkStore(engine)
    evaluated_at = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC)
    _journal(store, evaluated_at)
    entry = store.latest_journal_entry(ASSET, "section-01-v1.1")

    now = evaluated_at + SECTION1_STALE_AFTER + dt.timedelta(minutes=1)
    assert is_stale(entry, now) is True
