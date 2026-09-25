"""Section 2 (1H behaviour) evaluation - PART H of the Phase 5 spec.

Hand-written 1H fixtures, no network. `_candle`/`_frame`/`_level`/
`_journal_entry` build the minimum inputs `mtf.evaluate` needs: a pinned
Section 1 "as of" record (asset/watch/level, mirroring what
`context/htf.py` actually emits) and closed 1H candles.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from tidemark.context import htf, mtf
from tidemark.data.models import JournalEntry

ASSET = "TEST/USDT:USDT"
BASE = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
PRICE = 100.0
ZONE_TOL = 1.0  # zone = [99, 101]


def _candle(i: int, open_: float, high: float, low: float, close: float) -> dict:
    open_time = BASE + dt.timedelta(hours=i)
    return {
        "open_time": open_time,
        "close_time": open_time + dt.timedelta(hours=1),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1.0,
    }


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _level(
    price: float = PRICE,
    zone_tol: float = ZONE_TOL,
    *,
    role: str = "support",
    touches: int = 2,
    held: bool = True,
) -> dict:
    return {
        "role": role,
        "price": price,
        "zone_low": price - zone_tol,
        "zone_high": price + zone_tol,
        "touches": touches,
        "is_major": True,
        "held": held,
        "source": "swing_low_cluster",
        "formed_at": BASE.isoformat(),
    }


def _journal_entry(
    evaluated_at: dt.datetime,
    watch: str,
    level: dict,
    *,
    state: str = htf.BULLISH,
    grade: str = "B",
) -> JournalEntry:
    return JournalEntry(
        asset=ASSET,
        evaluated_at=evaluated_at,
        recorded_at=evaluated_at,
        rule_version=htf.RULE_VERSION,
        state=state,
        watch=watch,
        grade=grade,
        reason_code="MAJOR_SUPPORT" if watch == htf.LONG_WATCH else "MAJOR_RESISTANCE",
        active_levels=[level],
        fib={},
        swings_used=[],
        alert_sent=False,
        alert_reason=None,
    )


# -- activation / no-op ----------------------------------------------------


def test_no_row_when_section_1_never_watched() -> None:
    candles = _frame([_candle(0, 100.6, 100.8, 100.2, 100.6)])
    entry = _journal_entry(BASE, htf.WAIT, _level())

    assert mtf.evaluate(ASSET, [entry], candles) == []


# -- Stage A: reaction tiers, not confused with each other -----------------


def test_r1_basic_detected_alone() -> None:
    # Interacts with the zone, closes away from its own low, on the
    # correct (support) side of the level price - and stays above the
    # level price itself, so it can't also satisfy R3's reclaim test.
    candles = _frame([_candle(0, 100.6, 100.8, 100.2, 100.6)])
    entry = _journal_entry(BASE, htf.LONG_WATCH, _level())

    results = mtf.evaluate(ASSET, [entry], candles)

    assert len(results) == 1
    assert results[0].state == mtf.REACTION_DETECTED
    assert results[0].reaction_tier == mtf.R1
    assert results[0].reaction_condition_matched == "R1_BASIC"


def test_r2_strong_detected_via_engulfing_not_r1_or_r3() -> None:
    # Candle 0 touches the zone but closes at its own low (no reaction).
    # Candle 1 engulfs candle 0's full range and closes back on-side,
    # without either candle ever trading below the level price (so R3
    # never matches).
    candles = _frame(
        [
            _candle(0, 100.6, 100.6, 100.3, 100.3),
            _candle(1, 100.1, 100.9, 100.05, 100.8),
        ]
    )
    entry = _journal_entry(BASE, htf.LONG_WATCH, _level())

    results = mtf.evaluate(ASSET, [entry], candles)

    assert results[0].state == mtf.CONTINUATION_CANDIDATE_NOT_EVALUATED
    assert results[1].state == mtf.REACTION_DETECTED
    assert results[1].reaction_tier == mtf.R2
    assert results[1].reaction_condition_matched == "R2_ENGULFING"


def test_r3_reclaim_detected_without_r1s_zone_precondition() -> None:
    # Wicks clean through the level price and closes back on the
    # original (support) side - a failed breakdown.
    candles = _frame([_candle(0, 99.0, 100.6, 98.0, 100.5)])
    entry = _journal_entry(BASE, htf.LONG_WATCH, _level())

    results = mtf.evaluate(ASSET, [entry], candles)

    assert results[0].state == mtf.REACTION_DETECTED
    assert results[0].reaction_tier == mtf.R3
    assert results[0].reaction_condition_matched == "R3_RECLAIM"


def test_no_interaction_means_no_reaction() -> None:
    candles = _frame([_candle(0, 110, 111, 110, 110.5)])
    entry = _journal_entry(BASE, htf.LONG_WATCH, _level())

    results = mtf.evaluate(ASSET, [entry], candles)

    assert results[0].state == mtf.NO_INTERACTION
    assert results[0].interaction_detected is False
    assert results[0].reaction_tier is None


# -- Stage B: structural reference -----------------------------------------


def test_no_structural_reference_when_no_confirmed_swing_exists() -> None:
    # Only two 1H candles total - far too few for fractal N=2 to confirm
    # any swing, so a reaction stays open with nothing to reference.
    candles = _frame(
        [
            _candle(0, 100.6, 100.8, 100.2, 100.6),
            _candle(1, 100.7, 101.0, 100.4, 100.9),
        ]
    )
    entry = _journal_entry(BASE, htf.LONG_WATCH, _level())

    results = mtf.evaluate(ASSET, [entry], candles)

    assert results[0].state == mtf.REACTION_DETECTED
    assert results[1].state == mtf.NO_STRUCTURAL_REFERENCE


def _order_test_candles() -> list[dict]:
    rows = [
        # Baseline swing low (pivot @ idx2, price 96.0), confirmed idx4 -
        # kept well clear of the zone (< 99) so it registers no
        # interaction at all and can't accidentally trip a failure.
        _candle(0, 97.1, 97.2, 97.0, 97.1),
        _candle(1, 96.6, 96.7, 96.5, 96.6),
        _candle(2, 96.1, 96.2, 96.0, 96.1),
        _candle(3, 96.4, 96.5, 96.3, 96.4),
        _candle(4, 96.9, 97.0, 96.8, 96.9),
        _candle(5, 99.6, 99.8, 99.5, 99.6),
        # Reaction trigger (idx6): interacts, closes back above the level.
        _candle(6, 100.5, 100.6, 100.2, 100.5),
        # Reference swing HIGH pivot (@ idx9, price 101.6), confirmed idx11.
        _candle(7, 100.8, 100.9, 100.6, 100.8),
        _candle(8, 101.0, 101.2, 100.9, 101.0),
        _candle(9, 101.3, 101.6, 101.1, 101.3),
        _candle(10, 101.1, 101.4, 101.0, 101.1),
        _candle(11, 101.05, 101.1, 101.05, 101.05),
        # idx12: closes beyond the reference (101.8 > 101.6) *before* any
        # higher low has confirmed - must NOT count as confirmation. (This
        # candle's own high also becomes a new, more recent swing high
        # pivot once confirmed at idx14 - the reference simply becomes
        # 101.8 from then on; still > baseline, doesn't change the test.)
        _candle(12, 101.7, 101.8, 101.5, 101.8),
        # Higher-low swing LOW pivot (@ idx15, price 100.5, > baseline
        # 98.0), confirmed idx17.
        _candle(13, 101.1, 101.3, 101.0, 101.1),
        _candle(14, 100.9, 101.0, 100.8, 100.9),
        _candle(15, 100.7, 100.9, 100.5, 100.7),
        _candle(16, 100.9, 101.0, 100.7, 100.9),
        _candle(17, 101.0, 101.2, 100.9, 101.0),
        # idx18: higher low is now confirmed *and* this candle closes
        # beyond the reference again - confirms.
        _candle(18, 101.8, 102.0, 101.3, 101.9),
        _candle(19, 101.9, 102.0, 101.8, 101.9),
    ]
    assert [r["close_time"] for r in rows] == sorted(r["close_time"] for r in rows)
    return rows


def test_close_beyond_reference_before_higher_low_does_not_confirm() -> None:
    candles = _frame(_order_test_candles())
    entry = _journal_entry(BASE, htf.LONG_WATCH, _level())

    results = mtf.evaluate(ASSET, [entry], candles)

    assert results[6].state == mtf.REACTION_DETECTED
    # Reference known, close (101.8) beyond it, but no higher low yet.
    assert results[12].state == mtf.REACTION_DETECTED
    assert results[12].structure_change is None


def test_confirmation_requires_higher_low_before_the_close_beyond() -> None:
    candles = _frame(_order_test_candles())
    entry = _journal_entry(BASE, htf.LONG_WATCH, _level())

    results = mtf.evaluate(ASSET, [entry], candles)

    assert results[18].state == mtf.BULLISH_STRUCTURE_CHANGE
    assert results[18].structure_reference_price == 101.8
    assert results[19].state == mtf.HANDOFF_TO_15M


def test_wick_through_reference_does_not_confirm_only_a_close_does() -> None:
    # Same setup as the higher-low fixture, but split so the higher low
    # confirms *before* the reference is even tested, isolating the
    # wick-vs-close distinction on its own.
    rows = [
        _candle(0, 97.1, 97.2, 97.0, 97.1),
        _candle(1, 96.6, 96.7, 96.5, 96.6),
        _candle(2, 96.1, 96.2, 96.0, 96.1),
        _candle(3, 96.4, 96.5, 96.3, 96.4),
        _candle(4, 96.9, 97.0, 96.8, 96.9),
        _candle(5, 99.6, 99.8, 99.5, 99.6),
        _candle(6, 100.5, 100.6, 100.2, 100.5),  # trigger
        # Higher-low swing LOW pivot (@ idx8, price 100.1), confirmed idx10.
        _candle(7, 100.4, 100.5, 100.4, 100.45),
        _candle(8, 100.3, 100.5, 100.1, 100.3),
        _candle(9, 100.45, 100.6, 100.3, 100.45),
        _candle(10, 100.6, 100.7, 100.5, 100.6),
        # Reference swing HIGH pivot (@ idx13, price 101.6), confirmed idx15
        # - the wick/confirm candles below come *after* this, so they never
        # interfere with the pivot's own confirming neighbors.
        _candle(11, 100.8, 100.9, 100.6, 100.8),
        _candle(12, 101.0, 101.2, 100.9, 101.0),
        _candle(13, 101.3, 101.6, 101.1, 101.3),
        _candle(14, 101.1, 101.4, 101.0, 101.1),
        _candle(15, 101.2, 101.3, 101.0, 101.2),
        # idx16: reference known and higher low already confirmed; wicks
        # above the reference but closes back below - must not confirm.
        _candle(16, 101.6, 101.9, 101.4, 101.5),
        # idx17: closes beyond the reference - confirms.
        _candle(17, 101.6, 102.0, 101.4, 101.8),
    ]
    candles = _frame(rows)
    entry = _journal_entry(BASE, htf.LONG_WATCH, _level())

    results = mtf.evaluate(ASSET, [entry], candles)

    assert results[16].state == mtf.REACTION_DETECTED
    assert results[16].structure_change is None
    assert results[17].state == mtf.BULLISH_STRUCTURE_CHANGE


# -- terminal states ---------------------------------------------------------


def test_support_failure_on_confirmed_close_beyond_the_zone() -> None:
    candles = _frame(
        [
            _candle(0, 100.6, 100.8, 100.2, 100.6),  # interacts, triggers R1
            _candle(1, 99.2, 99.5, 98.0, 98.5),  # close (98.5) < zone_low (99)
            _candle(2, 98.0, 98.2, 97.8, 98.0),
        ]
    )
    entry = _journal_entry(BASE, htf.LONG_WATCH, _level())

    results = mtf.evaluate(ASSET, [entry], candles)

    assert results[0].state == mtf.REACTION_DETECTED
    assert results[1].state == mtf.SUPPORT_FAILURE
    assert results[1].failure == mtf.SUPPORT_FAILURE
    assert results[2].state == mtf.STAND_DOWN


def test_reaction_expires_at_exactly_12_candles_not_11_or_13() -> None:
    trigger = _candle(0, 100.6, 100.8, 100.2, 100.6)
    # Flat filler inside the zone: no confirmation, no failure, no new
    # swings (ties never form a fractal pivot).
    filler = [_candle(i, 100.6, 100.8, 100.4, 100.6) for i in range(1, 13)]
    candles = _frame([trigger, *filler])
    entry = _journal_entry(BASE, htf.LONG_WATCH, _level())

    results = mtf.evaluate(ASSET, [entry], candles)

    assert results[0].state == mtf.REACTION_DETECTED
    assert results[11].state == mtf.NO_STRUCTURAL_REFERENCE  # candle 11: not yet
    assert results[12].state == mtf.REACTION_EXPIRED  # candle 12: exactly 12
    assert results[12].expiry is True


def test_htf_context_invalidated_when_section_1_changes() -> None:
    level = _level()
    entry1 = _journal_entry(BASE, htf.LONG_WATCH, level, grade="B")
    entry2 = _journal_entry(BASE + dt.timedelta(hours=2), htf.LONG_WATCH, level, grade="A")

    candles = _frame(
        [
            _candle(0, 110, 111, 110, 110),  # as-of entry1, no interaction
            _candle(2, 110, 111, 110, 110),  # close_time BASE+3h -> as-of entry2
        ]
    )

    results = mtf.evaluate(ASSET, [entry1, entry2], candles)

    assert results[0].state == mtf.NO_INTERACTION
    assert results[1].state == mtf.HTF_CONTEXT_INVALIDATED


# -- look-ahead guard --------------------------------------------------------


def test_look_ahead_guard_truncation_reproduces_the_same_rows() -> None:
    candles = _frame(_order_test_candles())
    entry = _journal_entry(BASE, htf.LONG_WATCH, _level())

    full_results = mtf.evaluate(ASSET, [entry], candles)

    for i in range(len(candles)):
        truncated = candles.iloc[: i + 1].reset_index(drop=True)
        truncated_results = mtf.evaluate(ASSET, [entry], truncated)
        assert truncated_results == full_results[: len(truncated_results)], (
            f"mismatch at truncation index {i}"
        )


def test_v02_look_ahead_guard_truncation_reproduces_the_same_rows() -> None:
    candles = _frame(_order_test_candles())
    entry = _journal_entry(BASE, htf.LONG_WATCH, _level())

    full_results = mtf.evaluate(ASSET, [entry], candles, rule_version=mtf.RULE_VERSION_V2)

    for i in range(len(candles)):
        truncated = candles.iloc[: i + 1].reset_index(drop=True)
        truncated_results = mtf.evaluate(
            ASSET, [entry], truncated, rule_version=mtf.RULE_VERSION_V2
        )
        assert truncated_results == full_results[: len(truncated_results)], (
            f"mismatch at truncation index {i}"
        )


# -- section-02-v0.2: session termination (the one variable that changes) ---


def test_rule_version_constants() -> None:
    assert mtf.RULE_VERSION_V1 == "section-02-v0.1"
    assert mtf.RULE_VERSION_V2 == "section-02-v0.2"
    assert mtf.SUPPORTED_RULE_VERSIONS == (mtf.RULE_VERSION_V1, mtf.RULE_VERSION_V2)


def test_default_rule_version_is_v01() -> None:
    level = _level()
    entry1 = _journal_entry(BASE, htf.LONG_WATCH, level, grade="B")
    entry2 = _journal_entry(BASE + dt.timedelta(hours=2), htf.LONG_WATCH, level, grade="A")
    candles = _frame([_candle(0, 110, 111, 110, 110.5), _candle(2, 110, 111, 110, 110.5)])

    default_results = mtf.evaluate(ASSET, [entry1, entry2], candles)
    v1_results = mtf.evaluate(ASSET, [entry1, entry2], candles, rule_version=mtf.RULE_VERSION_V1)

    assert default_results == v1_results


def test_grade_only_change_ends_session_in_v01_but_not_in_v02() -> None:
    # Documents the one thing v0.2 reverses from v0.1 (section-02-v0.2-
    # justification.md, Part 1): the SAME two journal entries (state and
    # watch unchanged, grade B -> A) end a session under v0.1 and do not
    # under v0.2.
    level = _level()
    entry_b = _journal_entry(BASE, htf.LONG_WATCH, level, grade="B")
    entry_a = _journal_entry(BASE + dt.timedelta(hours=2), htf.LONG_WATCH, level, grade="A")
    candles = _frame([_candle(0, 110, 111, 110, 110.5), _candle(2, 110, 111, 110, 110.5)])

    v1_results = mtf.evaluate(ASSET, [entry_b, entry_a], candles, rule_version=mtf.RULE_VERSION_V1)
    v2_results = mtf.evaluate(ASSET, [entry_b, entry_a], candles, rule_version=mtf.RULE_VERSION_V2)

    assert v1_results[0].state == mtf.NO_INTERACTION
    assert v1_results[1].state == mtf.HTF_CONTEXT_INVALIDATED

    assert v2_results[0].state == mtf.NO_INTERACTION
    assert v2_results[1].state == mtf.NO_INTERACTION
    assert v2_results[1].section_1_grade == "A"
    assert v2_results[1].grade_at_start == "B"


def test_v02_ends_session_on_watch_direction_change() -> None:
    long_level = _level(role="support")
    short_level = _level(price=200.0, role="resistance")
    entry1 = _journal_entry(BASE, htf.LONG_WATCH, long_level)
    entry2 = _journal_entry(BASE + dt.timedelta(hours=2), htf.SHORT_WATCH, short_level)
    candles = _frame([_candle(0, 110, 111, 110, 110.5), _candle(2, 110, 111, 110, 110.5)])

    results = mtf.evaluate(ASSET, [entry1, entry2], candles, rule_version=mtf.RULE_VERSION_V2)

    assert results[0].state == mtf.NO_INTERACTION
    assert results[1].state == mtf.HTF_CONTEXT_INVALIDATED


def test_v02_ends_session_when_watch_disappears() -> None:
    level = _level()
    entry1 = _journal_entry(BASE, htf.LONG_WATCH, level)
    entry2 = _journal_entry(BASE + dt.timedelta(hours=2), htf.WAIT, level)
    candles = _frame([_candle(0, 110, 111, 110, 110.5), _candle(2, 110, 111, 110, 110.5)])

    results = mtf.evaluate(ASSET, [entry1, entry2], candles, rule_version=mtf.RULE_VERSION_V2)

    assert results[0].state == mtf.NO_INTERACTION
    assert results[1].state == mtf.HTF_CONTEXT_INVALIDATED


def test_v02_ends_session_when_held_level_moves_outside_original_zone() -> None:
    original = _level(price=100.0)  # zone [99, 101]
    moved_outside = _level(price=103.0)  # price 103 is outside [99, 101]
    entry1 = _journal_entry(BASE, htf.LONG_WATCH, original)
    entry2 = _journal_entry(BASE + dt.timedelta(hours=2), htf.LONG_WATCH, moved_outside)
    candles = _frame([_candle(0, 110, 111, 110, 110.5), _candle(2, 110, 111, 110, 110.5)])

    results = mtf.evaluate(ASSET, [entry1, entry2], candles, rule_version=mtf.RULE_VERSION_V2)

    assert results[0].state == mtf.NO_INTERACTION
    assert results[1].state == mtf.HTF_CONTEXT_INVALIDATED


def test_v02_continues_when_held_level_moves_but_stays_inside_original_zone() -> None:
    original = _level(price=100.0)  # zone [99, 101]
    moved_inside = _level(price=100.5)  # a different price, still within [99, 101]
    entry1 = _journal_entry(BASE, htf.LONG_WATCH, original)
    entry2 = _journal_entry(BASE + dt.timedelta(hours=2), htf.LONG_WATCH, moved_inside)
    candles = _frame([_candle(0, 110, 111, 110, 110.5), _candle(2, 110, 111, 110, 110.5)])

    results = mtf.evaluate(ASSET, [entry1, entry2], candles, rule_version=mtf.RULE_VERSION_V2)

    assert results[0].state == mtf.NO_INTERACTION
    assert results[1].state == mtf.NO_INTERACTION
    # Section 2's own pinned level never moves mid-session - only the
    # newest as-of active_levels are used for the containment check itself.
    assert results[1].section_1_level_price == 100.0


def test_v02_ends_session_when_no_level_of_that_role_holds_anymore() -> None:
    level = _level()
    entry1 = _journal_entry(BASE, htf.LONG_WATCH, level)
    entry2 = _journal_entry(BASE + dt.timedelta(hours=2), htf.LONG_WATCH, _level(held=False))
    candles = _frame([_candle(0, 110, 111, 110, 110.5), _candle(2, 110, 111, 110, 110.5)])

    results = mtf.evaluate(ASSET, [entry1, entry2], candles, rule_version=mtf.RULE_VERSION_V2)

    assert results[1].state == mtf.HTF_CONTEXT_INVALIDATED


def test_v02_records_grade_at_start_and_full_grade_history_across_changes() -> None:
    level = _level()
    entries = [
        _journal_entry(BASE, htf.LONG_WATCH, level, grade="B"),
        _journal_entry(BASE + dt.timedelta(hours=2), htf.LONG_WATCH, level, grade="A"),
        _journal_entry(BASE + dt.timedelta(hours=4), htf.LONG_WATCH, level, grade="B"),
    ]
    candles = _frame(
        [
            _candle(0, 110, 111, 110, 110.5),
            _candle(2, 110, 111, 110, 110.5),
            _candle(4, 110, 111, 110, 110.5),
        ]
    )

    results = mtf.evaluate(ASSET, entries, candles, rule_version=mtf.RULE_VERSION_V2)

    assert [r.state for r in results] == [mtf.NO_INTERACTION] * 3
    assert [r.section_1_grade for r in results] == ["B", "A", "B"]
    assert all(r.grade_at_start == "B" for r in results)
    assert results[0].grade_history == []
    assert results[1].grade_history == [
        {"evaluated_at": results[1].evaluated_at.isoformat(), "grade": "A"}
    ]
    assert results[2].grade_history == [
        {"evaluated_at": results[1].evaluated_at.isoformat(), "grade": "A"},
        {"evaluated_at": results[2].evaluated_at.isoformat(), "grade": "B"},
    ]
