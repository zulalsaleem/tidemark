"""context_read.py: the one narrow, read-only exception to the
market_intel isolation boundary. Tests are free to use
`tidemark.data.store`/`tidemark.context` to set up fixtures - only
`market_intel`'s own production code is restricted (see
test_import_boundary.py).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from tidemark.data.models import ContextRecord
from tidemark.data.store import TidemarkStore, create_store_engine, get_session_factory, init_db
from tidemark.market_intel.context_read import (
    CONTEXT_RECORD_STALE_AFTER,
    is_stale,
    read_latest_context_record,
)

ASSET = "BTC/USDT:USDT"


def _record(evaluated_at: dt.datetime, **overrides) -> ContextRecord:
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


def test_returns_none_for_a_fresh_database_with_no_context_records_table(tmp_path) -> None:
    db_path = (tmp_path / "empty.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    # Deliberately never call init_db - the table genuinely doesn't exist.

    assert read_latest_context_record(database_url, ASSET) is None


def test_returns_none_when_no_record_exists_for_the_asset(tmp_path) -> None:
    db_path = (tmp_path / "tidemark.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    engine = create_store_engine(database_url)
    init_db(engine)
    store = TidemarkStore(engine)
    store.save_context_record(
        _record(dt.datetime(2026, 1, 1, tzinfo=dt.UTC), asset="ETH/USDT:USDT")
    )

    assert read_latest_context_record(database_url, ASSET) is None


def test_returns_the_latest_record_for_the_asset(tmp_path) -> None:
    db_path = (tmp_path / "tidemark.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    engine = create_store_engine(database_url)
    init_db(engine)
    store = TidemarkStore(engine)
    older = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    newer = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)
    store.save_context_record(_record(older, state="NEUTRAL"))
    store.save_context_record(_record(newer, state="BEARISH"))

    result = read_latest_context_record(database_url, ASSET)

    assert result is not None
    assert result.state == "BEARISH"
    assert result.evaluated_at == newer


def test_never_writes_anything(tmp_path) -> None:
    db_path = (tmp_path / "tidemark.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    engine = create_store_engine(database_url)
    init_db(engine)
    store = TidemarkStore(engine)
    store.save_context_record(_record(dt.datetime(2026, 1, 1, tzinfo=dt.UTC)))

    read_latest_context_record(database_url, ASSET)
    read_latest_context_record(database_url, ASSET)

    # Still exactly one row - a read never inserts, updates, or duplicates.
    with get_session_factory(engine)() as session:
        count = session.scalar(select(func.count()).select_from(ContextRecord))
    assert count == 1


# -- staleness ------------------------------------------------------------


def test_is_stale_false_within_the_grace_window() -> None:
    record = _record(dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC))
    now = record.evaluated_at + CONTEXT_RECORD_STALE_AFTER - dt.timedelta(minutes=1)
    assert is_stale(record, now) is False


def test_is_stale_true_beyond_the_grace_window() -> None:
    record = _record(dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.UTC))
    now = record.evaluated_at + CONTEXT_RECORD_STALE_AFTER + dt.timedelta(minutes=1)
    assert is_stale(record, now) is True
