"""evaluation_store.py: market_intel's own table, never a research one.

Idempotent on repeat like JournalEntry, with the same narrow exception
(sent/send_reason only) - and never touches journal_entries/
observations/context_records, verified against a database that already
has real rows in those tables.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import inspect
from sqlalchemy.orm import sessionmaker

from tidemark.data.models import ContextRecord, JournalEntry
from tidemark.data.store import TidemarkStore, create_store_engine, get_session_factory, init_db
from tidemark.journal.records import build_journal_entry
from tidemark.market_intel.evaluation_store import (
    EvaluationRecord,
    MarketIntelEvaluation,
    init_evaluation_store,
    latest_evaluation_before,
    latest_sent_evaluation_before,
    make_engine,
    record_evaluation,
)
from tidemark.market_intel.models import NO_DATA, OK

ASSET = "BTC/USDT:USDT"
T1 = dt.datetime(2026, 9, 27, 18, 0, tzinfo=dt.UTC)
T2 = dt.datetime(2026, 9, 27, 19, 0, tzinfo=dt.UTC)
T3 = dt.datetime(2026, 9, 27, 20, 0, tzinfo=dt.UTC)


def _record(evaluated_at: dt.datetime, **overrides) -> EvaluationRecord:
    defaults = dict(
        asset=ASSET,
        evaluated_at=evaluated_at,
        recorded_at=evaluated_at,
        rulebook_version="derivatives-context-v0.1",
        classification="D1",
        classification_reason=None,
        price_status=OK,
        price_change_pct=1.0,
        price_period_start=evaluated_at - dt.timedelta(hours=1),
        price_period_close=evaluated_at,
        oi_status=OK,
        oi_change_pct=1.0,
        oi_period_start=evaluated_at - dt.timedelta(hours=1),
        oi_period_close=evaluated_at,
        funding_status=OK,
        funding_value=0.01,
        funding_previous_value=0.005,
        funding_period_start=evaluated_at - dt.timedelta(hours=1),
        funding_period_close=evaluated_at,
        structure_available=True,
        structure_state="BULLISH",
        structure_watch="LONG_WATCH",
        structure_grade="A",
        structure_rule_version="section-01-v1.1",
        structure_evaluated_at=evaluated_at,
        sent=False,
        send_reason=None,
    )
    defaults.update(overrides)
    return EvaluationRecord(**defaults)


def _engine(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'tidemark.db').as_posix()}")
    init_evaluation_store(engine)
    return engine


# -- table identity: never a research table -----------------------------


def test_the_table_is_named_distinctly_from_every_research_table(tmp_path) -> None:
    engine = _engine(tmp_path)
    tables = set(inspect(engine).get_table_names())

    assert "market_intel_evaluations" in tables
    for research_table in ("journal_entries", "observations", "context_records", "candles"):
        assert research_table not in tables  # this engine has never seen the research schema


def test_writing_an_evaluation_never_touches_the_research_tables(tmp_path) -> None:
    """Same physical database file, both schemas present - proves
    record_evaluation only ever inserts into its own table."""
    db_path = (tmp_path / "shared.db").as_posix()
    database_url = f"sqlite:///{db_path}"

    research_engine = create_store_engine(database_url)
    init_db(research_engine)
    store = TidemarkStore(research_engine)
    context_record = ContextRecord(
        asset=ASSET,
        evaluated_at=T1,
        rule_version="section-01-v1.1",
        state="BULLISH",
        watch="LONG_WATCH",
        grade="A",
        reason_code="X",
        active_levels=[],
        fib={},
        swings_used=[],
    )
    store.save_journal_entry(build_journal_entry(context_record, recorded_at=T1))

    market_intel_engine = make_engine(database_url)
    init_evaluation_store(market_intel_engine)
    record_evaluation(market_intel_engine, _record(T2))

    # The one journal_entries row from before is still exactly one row.
    with get_session_factory(research_engine)() as session:
        remaining = session.query(JournalEntry).count()
    assert remaining == 1


# -- insert / idempotency ------------------------------------------------


def test_record_evaluation_persists_every_field(tmp_path) -> None:
    engine = _engine(tmp_path)
    record_evaluation(engine, _record(T1, classification="D5"))

    row = latest_evaluation_before(engine, ASSET, T2)

    assert row is not None
    assert row.classification == "D5"
    assert row.price_change_pct == 1.0
    assert row.structure_state == "BULLISH"
    assert row.sent is False


def test_repeat_write_for_the_same_hour_never_changes_classification_fields(tmp_path) -> None:
    engine = _engine(tmp_path)
    record_evaluation(engine, _record(T1, classification="D1", price_change_pct=1.0))
    # A second write for the SAME hour with different classification data
    # must be a no-op for those fields - mirrors JournalEntry.
    record_evaluation(engine, _record(T1, classification="D4", price_change_pct=99.0))

    row = latest_evaluation_before(engine, ASSET, T2)

    assert row.classification == "D1"
    assert row.price_change_pct == 1.0


def test_repeat_write_may_update_sent_and_send_reason(tmp_path) -> None:
    engine = _engine(tmp_path)
    record_evaluation(engine, _record(T1, sent=False, send_reason=None))
    record_evaluation(engine, _record(T1, sent=True, send_reason="classification_changed"))

    row = latest_evaluation_before(engine, ASSET, T2)

    assert row.sent is True
    assert row.send_reason == "classification_changed"


def test_no_duplicate_rows_are_created_for_the_same_asset_and_hour(tmp_path) -> None:
    engine = _engine(tmp_path)
    record_evaluation(engine, _record(T1))
    record_evaluation(engine, _record(T1))
    record_evaluation(engine, _record(T1))

    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        count = session.query(MarketIntelEvaluation).count()
    assert count == 1


# -- previous-evaluation / previous-sent lookups -------------------------


def test_latest_evaluation_before_returns_the_most_recent_prior_row_regardless_of_sent(
    tmp_path,
) -> None:
    engine = _engine(tmp_path)
    record_evaluation(engine, _record(T1, classification="D1", sent=True))
    record_evaluation(engine, _record(T2, classification="NO_MATCH", sent=False))

    result = latest_evaluation_before(engine, ASSET, T3)

    assert result.classification == "NO_MATCH"  # the immediately-prior one, sent or not


def test_latest_evaluation_before_excludes_the_current_hour_itself(tmp_path) -> None:
    engine = _engine(tmp_path)
    record_evaluation(engine, _record(T1, classification="D1"))

    assert latest_evaluation_before(engine, ASSET, T1) is None
    assert latest_evaluation_before(engine, ASSET, T2) is not None


def test_latest_sent_evaluation_before_skips_unsent_rows(tmp_path) -> None:
    engine = _engine(tmp_path)
    record_evaluation(
        engine, _record(T1, structure_state="BULLISH", structure_watch="LONG_WATCH", sent=True)
    )
    record_evaluation(
        engine, _record(T2, structure_state="BEARISH", structure_watch="SHORT_WATCH", sent=False)
    )

    result = latest_sent_evaluation_before(engine, ASSET, T3)

    assert result.structure_state == "BULLISH"  # T2 was never sent, so it's skipped


def test_latest_sent_evaluation_before_returns_none_when_nothing_was_ever_sent(tmp_path) -> None:
    engine = _engine(tmp_path)
    record_evaluation(engine, _record(T1, sent=False))

    assert latest_sent_evaluation_before(engine, ASSET, T2) is None


def test_lookups_return_none_on_a_fresh_table(tmp_path) -> None:
    engine = _engine(tmp_path)
    assert latest_evaluation_before(engine, ASSET, T1) is None
    assert latest_sent_evaluation_before(engine, ASSET, T1) is None


# -- unavailable inputs are stored as such, never a fabricated number ----


def test_unavailable_inputs_store_their_status_and_reason_never_a_number(tmp_path) -> None:
    engine = _engine(tmp_path)
    record_evaluation(
        engine,
        _record(
            T1,
            classification="NO_MATCH",
            classification_reason="missing/unavailable input(s): price",
            price_status=NO_DATA,
            price_change_pct=None,
            price_period_start=None,
            price_period_close=None,
        ),
    )

    row = latest_evaluation_before(engine, ASSET, T2)

    assert row.price_status == NO_DATA
    assert row.price_change_pct is None


def test_structure_unavailable_stores_no_structure_fields(tmp_path) -> None:
    engine = _engine(tmp_path)
    record_evaluation(
        engine,
        _record(
            T1,
            structure_available=False,
            structure_state=None,
            structure_watch=None,
            structure_grade=None,
            structure_rule_version=None,
            structure_evaluated_at=None,
        ),
    )

    row = latest_evaluation_before(engine, ASSET, T2)

    assert row.structure_available is False
    assert row.structure_state is None
