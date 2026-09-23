"""Change detector: every transition in PART B, exact reason codes.

Pure-function tests: no store, no network. `previous` is built directly
as a `JournalEntry` (never persisted) and `current` as a `ContextRecord`
(never persisted) — `detect_change` only reads state/watch/grade off
them.
"""

import datetime as dt

from tidemark.data.models import ContextRecord, JournalEntry
from tidemark.journal.changes import (
    GRADE_DOWNGRADED,
    GRADE_UPGRADED,
    STRUCTURE_BROKEN,
    STRUCTURE_RESOLVED,
    WATCH_CLOSED,
    WATCH_FLIPPED,
    WATCH_OPENED,
    detect_change,
)

EVALUATED_AT = dt.datetime(2026, 9, 23, 4, tzinfo=dt.UTC)
RULE_VERSION = "section-01-v1.1"


def _journal_entry(state: str, watch: str, grade: str | None, rule_version: str = RULE_VERSION):
    return JournalEntry(
        asset="BTC/USDT:USDT",
        evaluated_at=EVALUATED_AT,
        recorded_at=EVALUATED_AT,
        rule_version=rule_version,
        state=state,
        watch=watch,
        grade=grade,
        reason_code="X",
        active_levels=[],
        fib={},
        swings_used=[],
    )


def _context_record(
    state: str, watch: str, grade: str | None, rule_version: str = RULE_VERSION, **overrides
):
    defaults = dict(
        asset="BTC/USDT:USDT",
        evaluated_at=EVALUATED_AT + dt.timedelta(hours=4),
        rule_version=rule_version,
        state=state,
        watch=watch,
        grade=grade,
        reason_code="X",
        active_levels=[],
        fib={},
        swings_used=[],
    )
    defaults.update(overrides)
    return ContextRecord(**defaults)


def test_wait_to_long_watch_is_watch_opened() -> None:
    previous = _journal_entry("BULLISH", "WAIT", None)
    current = _context_record("BULLISH", "LONG_WATCH", "B")
    assert detect_change(previous, current) == WATCH_OPENED


def test_wait_to_short_watch_is_watch_opened() -> None:
    previous = _journal_entry("BEARISH", "WAIT", None)
    current = _context_record("BEARISH", "SHORT_WATCH", "B")
    assert detect_change(previous, current) == WATCH_OPENED


def test_grade_b_to_a_is_grade_upgraded() -> None:
    previous = _journal_entry("BULLISH", "LONG_WATCH", "B")
    current = _context_record("BULLISH", "LONG_WATCH", "A")
    assert detect_change(previous, current) == GRADE_UPGRADED


def test_grade_a_to_b_is_grade_downgraded() -> None:
    previous = _journal_entry("BULLISH", "LONG_WATCH", "A")
    current = _context_record("BULLISH", "LONG_WATCH", "B")
    assert detect_change(previous, current) == GRADE_DOWNGRADED


def test_long_watch_to_short_watch_is_watch_flipped() -> None:
    previous = _journal_entry("BULLISH", "LONG_WATCH", "B")
    current = _context_record("BEARISH", "SHORT_WATCH", "B")
    assert detect_change(previous, current) == WATCH_FLIPPED


def test_short_watch_to_long_watch_is_watch_flipped() -> None:
    previous = _journal_entry("BEARISH", "SHORT_WATCH", "A")
    current = _context_record("BULLISH", "LONG_WATCH", "A")
    assert detect_change(previous, current) == WATCH_FLIPPED


def test_long_watch_to_wait_is_watch_closed() -> None:
    previous = _journal_entry("BULLISH", "LONG_WATCH", "B")
    current = _context_record("BULLISH", "WAIT", None)
    assert detect_change(previous, current) == WATCH_CLOSED


def test_short_watch_to_wait_is_watch_closed() -> None:
    previous = _journal_entry("BEARISH", "SHORT_WATCH", "A")
    current = _context_record("BEARISH", "WAIT", None)
    assert detect_change(previous, current) == WATCH_CLOSED


def test_bullish_to_structure_broken_bull_is_structure_broken() -> None:
    previous = _journal_entry("BULLISH", "LONG_WATCH", "B")
    current = _context_record("STRUCTURE_BROKEN_BULL", "WAIT", None)
    assert detect_change(previous, current) == STRUCTURE_BROKEN


def test_bearish_to_structure_broken_bear_is_structure_broken() -> None:
    previous = _journal_entry("BEARISH", "SHORT_WATCH", "A")
    current = _context_record("STRUCTURE_BROKEN_BEAR", "WAIT", None)
    assert detect_change(previous, current) == STRUCTURE_BROKEN


def test_neutral_to_structure_broken_is_structure_broken() -> None:
    # "any state" -> broken, not just BULLISH/BEARISH.
    previous = _journal_entry("NEUTRAL", "WAIT", None)
    current = _context_record("STRUCTURE_BROKEN_BULL", "WAIT", None)
    assert detect_change(previous, current) == STRUCTURE_BROKEN


def test_structure_broken_bull_to_bearish_is_structure_resolved() -> None:
    previous = _journal_entry("STRUCTURE_BROKEN_BULL", "WAIT", None)
    current = _context_record("BEARISH", "WAIT", None, reason_code="NOT_IN_ZONE")
    assert detect_change(previous, current) == STRUCTURE_RESOLVED


def test_structure_broken_bear_to_neutral_is_structure_resolved() -> None:
    previous = _journal_entry("STRUCTURE_BROKEN_BEAR", "WAIT", None)
    current = _context_record("NEUTRAL", "WAIT", None, reason_code="NEUTRAL_STRUCTURE")
    assert detect_change(previous, current) == STRUCTURE_RESOLVED


def test_structure_broken_bull_to_structure_broken_bear_is_structure_broken() -> None:
    # A different broken state is still "-> STRUCTURE_BROKEN_*", not a
    # resolution: structure is still broken, just in the other direction.
    previous = _journal_entry("STRUCTURE_BROKEN_BULL", "WAIT", None)
    current = _context_record("STRUCTURE_BROKEN_BEAR", "WAIT", None)
    assert detect_change(previous, current) == STRUCTURE_BROKEN


# --- never alert -------------------------------------------------------------


def test_first_evaluation_produces_no_alert() -> None:
    current = _context_record("BULLISH", "LONG_WATCH", "A")
    assert detect_change(None, current) is None


def test_unchanged_repeat_produces_no_alert() -> None:
    previous = _journal_entry("BULLISH", "LONG_WATCH", "A")
    current = _context_record("BULLISH", "LONG_WATCH", "A")
    assert detect_change(previous, current) is None


def test_state_change_with_watch_and_grade_unchanged_produces_no_alert() -> None:
    # BULLISH -> NEUTRAL doesn't match any listed transition (both stay
    # WAIT/None throughout, since neither holds a major level) - not in
    # the "Alert when" list, so no alert.
    previous = _journal_entry("BULLISH", "WAIT", None)
    current = _context_record("NEUTRAL", "WAIT", None)
    assert detect_change(previous, current) is None


def test_level_price_moving_alone_produces_no_alert() -> None:
    previous = _journal_entry("BULLISH", "LONG_WATCH", "A")
    current = _context_record(
        "BULLISH",
        "LONG_WATCH",
        "A",
        active_levels=[{"role": "support", "price": 80000.0}],
    )
    assert detect_change(previous, current) is None


def test_fib_values_moving_alone_produces_no_alert() -> None:
    previous = _journal_entry("BULLISH", "LONG_WATCH", "A")
    current = _context_record(
        "BULLISH", "LONG_WATCH", "A", fib={"nearest_level": "0.786", "anchor_start": 1.0}
    )
    assert detect_change(previous, current) is None


def test_rule_version_change_alone_does_not_raise_an_alert() -> None:
    # Comparing across a rule_version change would be comparing apples to
    # oranges; the caller (journal/pipeline.py) is responsible for only
    # ever passing a `previous` at the same rule_version as `current` -
    # but detect_change itself must not treat a version bump as a
    # meaningful transition even if state/watch/grade happen to differ.
    previous = _journal_entry("BULLISH", "WAIT", None, rule_version="section-01-v1.0")
    current = _context_record("BULLISH", "WAIT", None, rule_version="section-01-v1.1")
    # State/watch/grade are unchanged here on purpose, isolating the
    # rule_version-alone case from a real transition.
    assert detect_change(previous, current) is None
