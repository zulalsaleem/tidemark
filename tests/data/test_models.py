"""Model shape matches the rulebook's recording requirements."""

import datetime as dt

from tidemark.data.models import (
    Candle,
    ContextRecord,
    JournalEntry,
    Level,
    RejectedCandle,
    Run,
    Swing,
)
from tidemark.data.store import create_store_engine, get_session_factory, init_db


def test_swing_stores_formed_and_confirmed_separately() -> None:
    formed = dt.datetime(2026, 9, 16, 4, tzinfo=dt.UTC)
    confirmed = dt.datetime(2026, 9, 16, 12, tzinfo=dt.UTC)
    swing = Swing(
        asset="BTCUSDT",
        timeframe="4h",
        kind="HL",
        price=60000.0,
        formed_at=formed,
        confirmed_at=confirmed,
        fractal_n=2,
    )
    assert swing.formed_at == formed
    assert swing.confirmed_at == confirmed
    assert swing.formed_at != swing.confirmed_at


def test_context_record_carries_rule_version() -> None:
    record = ContextRecord(
        asset="BTCUSDT",
        evaluated_at=dt.datetime(2026, 9, 16, tzinfo=dt.UTC),
        rule_version="1.0",
        state="BULLISH",
        watch="LONG_WATCH",
        grade="A",
        reason_code="OK",
    )
    assert record.rule_version == "1.0"


def test_every_model_datetime_round_trips_utc_aware() -> None:
    """SQLite has no native tz-aware datetime type — every datetime column
    must come back UTC-aware via UTCDateTime, not just the candle tables.
    """
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = get_session_factory(engine)
    now = dt.datetime(2026, 9, 22, 12, tzinfo=dt.UTC)

    with session_factory() as session:
        candle = Candle(
            venue="binanceusdm",
            symbol="BTC/USDT:USDT",
            timeframe="4h",
            open_time=now,
            close_time=now + dt.timedelta(hours=4),
            open=100.0,
            high=110.0,
            low=90.0,
            close=105.0,
            volume=10.0,
            fetched_at=now,
        )
        rejected = RejectedCandle(
            venue="binanceusdm",
            symbol="BTC/USDT:USDT",
            timeframe="4h",
            open_time=now,
            close_time=now + dt.timedelta(hours=4),
            open=100.0,
            high=95.0,
            low=90.0,
            close=105.0,
            volume=10.0,
            reason="high < max(open, close)",
            rejected_at=now,
        )
        run = Run(
            run_id="run-1", command="backfill", started_at=now, finished_at=now, status="COMPLETED"
        )
        swing = Swing(
            asset="BTCUSDT",
            timeframe="4h",
            kind="HL",
            price=60000.0,
            formed_at=now,
            confirmed_at=now,
            fractal_n=2,
        )
        level = Level(
            asset="BTCUSDT",
            timeframe="4h",
            kind="support",
            price=59000.0,
            zone_low=58500.0,
            zone_high=59500.0,
            touches=2,
            is_major=True,
            source="swing_cluster",
            formed_at=now,
        )
        context_record = ContextRecord(
            asset="BTCUSDT",
            evaluated_at=now,
            rule_version="1.0",
            state="BULLISH",
            watch="LONG_WATCH",
            grade="A",
            reason_code="OK",
        )
        session.add_all([candle, rejected, run, swing, level, context_record])
        session.commit()
        session.refresh(context_record)

        journal_entry = JournalEntry(
            context_record_id=context_record.id,
            recorded_at=now,
            rule_version="1.0",
            note="no setups found",
        )
        session.add(journal_entry)
        session.commit()

    with session_factory() as session:
        stored_candle = session.query(Candle).one()
        stored_rejected = session.query(RejectedCandle).one()
        stored_run = session.query(Run).one()
        stored_swing = session.query(Swing).one()
        stored_level = session.query(Level).one()
        stored_context = session.query(ContextRecord).one()
        stored_journal = session.query(JournalEntry).one()

    stored_datetimes = [
        stored_candle.open_time,
        stored_candle.close_time,
        stored_candle.fetched_at,
        stored_rejected.open_time,
        stored_rejected.close_time,
        stored_rejected.rejected_at,
        stored_run.started_at,
        stored_run.finished_at,
        stored_swing.formed_at,
        stored_swing.confirmed_at,
        stored_level.formed_at,
        stored_context.evaluated_at,
        stored_journal.recorded_at,
    ]
    for value in stored_datetimes:
        assert value.tzinfo is not None
        assert value.utcoffset() == dt.timedelta(0)
    assert stored_run.started_at == now
    assert stored_swing.formed_at == now
