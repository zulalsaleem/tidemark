"""build_journal_entry: turns an evaluated ContextRecord into a journal row."""

import datetime as dt

from tidemark.data.models import ContextRecord
from tidemark.journal.records import build_journal_entry

EVALUATED_AT = dt.datetime(2026, 9, 23, 4, tzinfo=dt.UTC)
RECORDED_AT = dt.datetime(2026, 9, 23, 4, 0, 5, tzinfo=dt.UTC)


def _record() -> ContextRecord:
    return ContextRecord(
        asset="BTC/USDT:USDT",
        evaluated_at=EVALUATED_AT,
        rule_version="section-01-v1.1",
        state="BULLISH",
        watch="LONG_WATCH",
        grade="A",
        reason_code="MAJOR_SUPPORT_FIB",
        active_levels=[{"role": "support", "price": 79918.9}],
        fib={"nearest_level": "0.618"},
        swings_used=[{"kind": "high", "price": 100.0}],
    )


def test_build_journal_entry_copies_evaluation_fields() -> None:
    entry = build_journal_entry(_record(), recorded_at=RECORDED_AT)

    assert entry.asset == "BTC/USDT:USDT"
    assert entry.evaluated_at == EVALUATED_AT
    assert entry.recorded_at == RECORDED_AT
    assert entry.rule_version == "section-01-v1.1"
    assert entry.state == "BULLISH"
    assert entry.watch == "LONG_WATCH"
    assert entry.grade == "A"
    assert entry.reason_code == "MAJOR_SUPPORT_FIB"
    assert entry.active_levels == [{"role": "support", "price": 79918.9}]
    assert entry.fib == {"nearest_level": "0.618"}
    assert entry.swings_used == [{"kind": "high", "price": 100.0}]


def test_build_journal_entry_starts_with_no_alert() -> None:
    entry = build_journal_entry(_record(), recorded_at=RECORDED_AT)

    assert entry.alert_sent is False
    assert entry.alert_reason is None


def test_build_journal_entry_for_a_wait_evaluation() -> None:
    wait_record = ContextRecord(
        asset="BTC/USDT:USDT",
        evaluated_at=EVALUATED_AT,
        rule_version="section-01-v1.1",
        state="NEUTRAL",
        watch="WAIT",
        grade=None,
        reason_code="NEUTRAL_STRUCTURE",
        active_levels=[],
        fib={},
        swings_used=[],
    )

    entry = build_journal_entry(wait_record, recorded_at=RECORDED_AT)

    assert entry.state == "NEUTRAL"
    assert entry.watch == "WAIT"
    assert entry.grade is None
