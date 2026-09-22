"""Model shape matches the rulebook's recording requirements."""

import datetime as dt

from tidemark.data.models import ContextRecord, Swing


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
