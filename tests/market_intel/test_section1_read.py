"""`read_section1`: the stored Section 1 result for any asset, read read-only,
with the 8h staleness guard. Seeded through the production write path.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from tidemark.data.models import ContextRecord, JournalEntry
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.journal.records import build_journal_entry
from tidemark.market_intel.context_read import SECTION1_CANDLE, read_section1

NOW = dt.datetime(2026, 9, 28, 8, 30, tzinfo=dt.UTC)
FRESH_EVALUATED_AT = dt.datetime(2026, 9, 28, 8, 0, tzinfo=dt.UTC)
STALE_EVALUATED_AT = dt.datetime(2026, 9, 27, 8, 0, tzinfo=dt.UTC)
ETH = "ETH/USDT:USDT"
SOL = "SOL/USDT:USDT"


def _database(tmp_path) -> str:
    url = f"sqlite:///{(tmp_path / 'tidemark.db').as_posix()}"
    engine = create_store_engine(url)
    init_db(engine)
    engine.dispose()
    return url


def seed_section1(database_url: str, asset: str, state: str, watch: str, evaluated_at) -> None:
    """Written the way `tidemark run` writes it: ContextRecord, then
    build_journal_entry, then save_journal_entry.
    """
    record = ContextRecord(
        asset=asset,
        evaluated_at=evaluated_at,
        rule_version="section-01-v1.1",
        state=state,
        watch=watch,
        grade="A",
        reason_code="X",
        active_levels=[],
        fib={},
        swings_used=[],
    )
    engine = create_store_engine(database_url)
    TidemarkStore(engine).save_journal_entry(build_journal_entry(record, recorded_at=evaluated_at))
    engine.dispose()


def test_fresh_record_is_read_with_its_state_watch_and_levels(tmp_path) -> None:
    database_url = _database(tmp_path)
    seed_section1(database_url, ETH, "BEARISH", "SHORT_WATCH", FRESH_EVALUATED_AT)

    read = read_section1(database_url, ETH, NOW)

    assert read.available
    assert read.state == "BEARISH"
    assert read.watch == "SHORT_WATCH"
    assert read.rule_version == "section-01-v1.1"
    assert read.evaluated_at == FRESH_EVALUATED_AT


def test_the_4h_window_is_the_four_hours_ending_at_evaluated_at(tmp_path) -> None:
    database_url = _database(tmp_path)
    seed_section1(database_url, ETH, "BEARISH", "SHORT_WATCH", FRESH_EVALUATED_AT)

    read = read_section1(database_url, ETH, NOW)

    assert read.window_start == FRESH_EVALUATED_AT - SECTION1_CANDLE
    assert read.window_start == dt.datetime(2026, 9, 28, 4, 0, tzinfo=dt.UTC)


def test_stale_record_is_unavailable_with_the_reason_and_never_a_state(tmp_path) -> None:
    database_url = _database(tmp_path)
    seed_section1(database_url, ETH, "BEARISH", "SHORT_WATCH", STALE_EVALUATED_AT)

    read = read_section1(database_url, ETH, NOW)

    assert not read.available
    assert read.state is None
    assert read.reason.startswith("stored Section 1 record is stale")
    assert "2026-09-27 08:00 UTC" in read.reason


def test_missing_record_is_unavailable_never_computed(tmp_path) -> None:
    database_url = _database(tmp_path)

    read = read_section1(database_url, SOL, NOW)

    assert not read.available
    assert read.reason == "no stored Section 1 record for this asset"


def test_reading_writes_nothing(tmp_path) -> None:
    database_url = _database(tmp_path)
    seed_section1(database_url, ETH, "BEARISH", "SHORT_WATCH", FRESH_EVALUATED_AT)

    def journal_rows() -> int:
        engine = create_store_engine(database_url)
        try:
            with engine.connect() as conn:
                return conn.execute(select(func.count()).select_from(JournalEntry)).scalar_one()
        finally:
            engine.dispose()

    before = journal_rows()
    read_section1(database_url, ETH, NOW)
    read_section1(database_url, SOL, NOW)
    assert journal_rows() == before == 1
