"""`tidemark evidence`: read-only archive queries over persisted
`observations`/`journal_entries` rows. See
docs/adr/0010-evidence-command.md.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tidemark.context import htf, mtf
from tidemark.data import evidence
from tidemark.data.models import JournalEntry, Observation
from tidemark.data.store import TidemarkStore, create_store_engine, init_db

VENUE = "binanceusdm"
SYMBOL = "BTC/USDT:USDT"
SYMBOL_2 = "ETH/USDT:USDT"
BASE = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _hours(n: int) -> dt.datetime:
    return BASE + dt.timedelta(hours=n)


@pytest.fixture
def store() -> TidemarkStore:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    return TidemarkStore(engine)


def _obs(
    evaluated_at: dt.datetime,
    state: str,
    *,
    symbol: str = SYMBOL,
    session_started_at: dt.datetime | None = None,
    rule_version: str = mtf.RULE_VERSION_V2,
    **overrides,
) -> Observation:
    defaults = dict(
        asset=symbol,
        evaluated_at=evaluated_at,
        rule_version=rule_version,
        section_1_state=htf.BULLISH,
        section_1_watch=htf.LONG_WATCH,
        section_1_grade="B",
        section_1_level_price=100.0,
        session_started_at=session_started_at or evaluated_at,
        grade_at_start="B",
        grade_history=[],
        interaction_detected=False,
        reaction_tier=None,
        reaction_condition_matched=None,
        reaction_started_at=None,
        structure_reference_price=None,
        structure_reference_confirmed_at=None,
        structure_change=None,
        failure=None,
        expiry=False,
        state=state,
        reason_code=state,
        swings_used=[],
    )
    defaults.update(overrides)
    return Observation(**defaults)


def _journal(
    evaluated_at: dt.datetime,
    state: str,
    *,
    symbol: str = SYMBOL,
    watch: str = htf.WAIT,
    active_levels: list | None = None,
    **overrides,
) -> JournalEntry:
    defaults = dict(
        asset=symbol,
        evaluated_at=evaluated_at,
        recorded_at=evaluated_at,
        rule_version=htf.RULE_VERSION,
        state=state,
        watch=watch,
        grade=None,
        reason_code=state,
        active_levels=active_levels or [],
        fib={},
        swings_used=[],
        alert_sent=False,
        alert_reason=None,
    )
    defaults.update(overrides)
    return JournalEntry(**defaults)


def _save_all(store: TidemarkStore, rows: list[Observation]) -> None:
    for row in rows:
        store.save_observation(row)


def _build(store: TidemarkStore, **kwargs) -> evidence.EvidenceReport:
    return evidence.build_evidence_report(
        store, "tidemark evidence", dt.datetime.now(dt.UTC), **kwargs
    )


# -- empty archive -------------------------------------------------------------


def test_empty_archive_reports_zero_everything(store: TidemarkStore) -> None:
    report = _build(store)
    assert report.coverage.total_observation_count == 0
    assert report.coverage.symbols_observed == ()
    assert report.sessions.total_sessions == 0
    assert report.is_young_archive is True
    assert all(v.verdict == evidence.INSUFFICIENT for v in report.sufficiency)


# -- one session / multiple sessions -------------------------------------------


def test_one_session_is_counted_once(store: TidemarkStore) -> None:
    session = [
        _obs(_hours(0), mtf.NO_INTERACTION, session_started_at=_hours(0)),
        _obs(_hours(1), mtf.NO_INTERACTION, session_started_at=_hours(0)),
        _obs(_hours(2), mtf.HTF_CONTEXT_INVALIDATED, session_started_at=_hours(0)),
    ]
    _save_all(store, session)
    report = _build(store)
    assert report.sessions.total_sessions == 1
    assert report.sessions.outcome_counts[evidence.OUTCOME_HTF_CONTEXT_INVALIDATED] == 1
    assert report.coverage.total_observation_count == 3


def test_multiple_sessions_across_symbols_are_all_counted(store: TidemarkStore) -> None:
    session_a = [
        _obs(_hours(0), mtf.NO_INTERACTION, symbol=SYMBOL, session_started_at=_hours(0)),
        _obs(_hours(1), mtf.HTF_CONTEXT_INVALIDATED, symbol=SYMBOL, session_started_at=_hours(0)),
    ]
    session_b = [
        _obs(_hours(10), mtf.NO_INTERACTION, symbol=SYMBOL_2, session_started_at=_hours(10)),
        _obs(
            _hours(11), mtf.HTF_CONTEXT_INVALIDATED, symbol=SYMBOL_2, session_started_at=_hours(10)
        ),
    ]
    _save_all(store, [*session_a, *session_b])
    report = _build(store)
    assert report.sessions.total_sessions == 2
    assert set(report.coverage.symbols_observed) == {SYMBOL, SYMBOL_2}
    assert report.sessions.sessions_by_symbol == {SYMBOL: 1, SYMBOL_2: 1}


# -- an active session ----------------------------------------------------------


def test_active_session_is_not_a_termination(store: TidemarkStore) -> None:
    session = [
        _obs(_hours(0), mtf.NO_INTERACTION, session_started_at=_hours(0)),
        _obs(_hours(1), mtf.CONTINUATION_CANDIDATE_NOT_EVALUATED, session_started_at=_hours(0)),
    ]
    _save_all(store, session)
    report = _build(store)
    assert report.sessions.total_sessions == 1
    assert report.sessions.active_sessions == 1
    assert report.sessions.outcome_counts[evidence.OUTCOME_STILL_ACTIVE] == 1
    # An active session hasn't terminated, so it must not appear in
    # SEC2-03's termination-reason counts.
    assert sum(report.sec2_03.terminations_by_reason.values()) == 0


# -- an R1 occurrence / repeated R1 ---------------------------------------------


def test_r1_occurrence_is_counted_as_first_qualifying_event(store: TidemarkStore) -> None:
    session = [
        _obs(
            _hours(0),
            mtf.REACTION_DETECTED,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(0),
            session_started_at=_hours(0),
        ),
        _obs(_hours(1), mtf.HTF_CONTEXT_INVALIDATED, session_started_at=_hours(0)),
    ]
    _save_all(store, session)
    report = _build(store)
    assert report.sec2_01.sessions_with_r1 == 1
    assert report.sec2_01.r1_as_first_qualifying_event == 1
    assert report.sec2_01.r1_followed_by_invalidation == 1


def test_repeated_r1_across_separate_sessions_counts_each_session_once(
    store: TidemarkStore,
) -> None:
    session_1 = [
        _obs(
            _hours(0),
            mtf.REACTION_DETECTED,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(0),
            session_started_at=_hours(0),
        ),
        _obs(_hours(1), mtf.HTF_CONTEXT_INVALIDATED, session_started_at=_hours(0)),
    ]
    session_2 = [
        _obs(
            _hours(20),
            mtf.REACTION_DETECTED,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(20),
            session_started_at=_hours(20),
        ),
        _obs(_hours(21), mtf.HTF_CONTEXT_INVALIDATED, session_started_at=_hours(20)),
    ]
    _save_all(store, [*session_1, *session_2])
    report = _build(store)
    assert report.sec2_01.sessions_with_r1 == 2


def test_r1_followed_by_resolution_is_distinguished_from_r1_followed_by_invalidation(
    store: TidemarkStore,
) -> None:
    session = [
        _obs(
            _hours(0),
            mtf.REACTION_DETECTED,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(0),
            session_started_at=_hours(0),
        ),
        _obs(
            _hours(1),
            mtf.BULLISH_STRUCTURE_CHANGE,
            structure_change=mtf.BULLISH_STRUCTURE_CHANGE,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(0),
            session_started_at=_hours(0),
        ),
        _obs(_hours(2), mtf.HANDOFF_TO_15M, session_started_at=_hours(0)),
    ]
    _save_all(store, session)
    report = _build(store)
    assert report.sec2_01.r1_followed_by_resolution == 1
    assert report.sec2_01.r1_followed_by_invalidation == 0
    assert report.sessions.outcome_counts[evidence.OUTCOME_STRUCTURE_CHANGE_LONG] == 1


def test_unresolved_open_r1_session_is_flagged(store: TidemarkStore) -> None:
    session = [
        _obs(_hours(0), mtf.REACTION_DETECTED, reaction_tier=mtf.R1, reaction_started_at=_hours(0)),
    ]
    _save_all(store, session)
    report = _build(store)
    assert report.sec2_01.unresolved_open_r1_sessions == 1
    assert report.sessions.active_sessions == 1


# -- CONTINUATION_CANDIDATE_NOT_EVALUATED: observed, never reinterpreted -------


def test_continuation_candidate_is_reported_as_observed_only(store: TidemarkStore) -> None:
    session = [
        _obs(_hours(0), mtf.NO_INTERACTION, session_started_at=_hours(0)),
        _obs(_hours(1), mtf.CONTINUATION_CANDIDATE_NOT_EVALUATED, session_started_at=_hours(0)),
        _obs(_hours(2), mtf.CONTINUATION_CANDIDATE_NOT_EVALUATED, session_started_at=_hours(0)),
    ]
    _save_all(store, session)
    report = _build(store)
    assert report.sec2_02.sessions_with_continuation_candidate == 1
    assert report.sec2_02.total_occurrences == 2
    assert len(report.sec2_02.events) == 2
    # Raw events preserve the state exactly as recorded, with no derived
    # judgment field anywhere on the event.
    assert not hasattr(report.sec2_02.events[0], "classification")
    assert report.sec2_02.events[0].preceding_state == mtf.NO_INTERACTION
    assert report.sec2_02.events[1].preceding_state == mtf.CONTINUATION_CANDIDATE_NOT_EVALUATED


# -- the 12-candle boundary ------------------------------------------------------


def _reaction_session(expiry_length: int) -> list[Observation]:
    """A session whose single reaction cycle runs for `expiry_length`
    candles: REACTION_DETECTED rows, then either REACTION_EXPIRED (when
    `expiry_length` == 12, matching the rulebook's fixed expiry) or a
    failure cutting it short.
    """
    rows = [
        _obs(
            _hours(i),
            mtf.REACTION_DETECTED if i < expiry_length - 1 else mtf.REACTION_EXPIRED,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(0),
            session_started_at=_hours(0),
        )
        for i in range(expiry_length)
    ]
    return rows


def test_reaction_reaching_exactly_12_candles_is_bucketed_exactly_12(
    store: TidemarkStore,
) -> None:
    _save_all(store, _reaction_session(mtf.REACTION_EXPIRY_CANDLES))
    report = _build(store)
    assert report.sec2_04.cycle_counts[evidence.EXACTLY_12] == 1
    assert report.sec2_04.cycle_counts[evidence.BEFORE_12] == 0
    assert report.sec2_04.cycle_counts[evidence.BEYOND_12] == 0


def test_session_terminating_before_12_candles_is_bucketed_before_12(
    store: TidemarkStore,
) -> None:
    session = [
        _obs(
            _hours(0),
            mtf.REACTION_DETECTED,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(0),
            session_started_at=_hours(0),
        ),
        _obs(
            _hours(1),
            mtf.REACTION_DETECTED,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(0),
            session_started_at=_hours(0),
        ),
        _obs(
            _hours(2),
            mtf.SUPPORT_FAILURE,
            failure=mtf.SUPPORT_FAILURE,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(0),
            session_started_at=_hours(0),
        ),
        _obs(_hours(3), mtf.STAND_DOWN, session_started_at=_hours(0)),
    ]
    _save_all(store, session)
    report = _build(store)
    assert report.sec2_04.cycle_counts[evidence.BEFORE_12] == 1
    assert report.sec2_04.cycle_counts[evidence.EXACTLY_12] == 0


def test_session_continuing_beyond_12_candles_is_flagged_as_a_data_integrity_signal(
    store: TidemarkStore,
) -> None:
    """The rulebook fixes reaction expiry at exactly 12 candles - a
    reaction that somehow reaches REACTION_EXPIRED at a length other than
    12 should never happen, but if the archive ever contains one, it must
    be surfaced (BEYOND_12), not silently folded into EXACTLY_12.
    """
    _save_all(store, _reaction_session(13))
    report = _build(store)
    assert report.sec2_04.cycle_counts[evidence.BEYOND_12] == 1
    assert report.sec2_04.cycle_counts[evidence.EXACTLY_12] == 0


# -- weekly and monthly grouping -------------------------------------------------


def test_sessions_are_grouped_by_week_and_month(store: TidemarkStore) -> None:
    week1 = dt.datetime(2026, 1, 5, tzinfo=dt.UTC)  # 2026-W02
    week2 = dt.datetime(2026, 1, 20, tzinfo=dt.UTC)  # 2026-W04, still January
    _save_all(
        store,
        [
            _obs(week1, mtf.HTF_CONTEXT_INVALIDATED, session_started_at=week1),
            _obs(week2, mtf.HTF_CONTEXT_INVALIDATED, session_started_at=week2),
        ],
    )
    report = _build(store)
    assert report.sessions.sessions_by_week == {"2026-W02": 1, "2026-W04": 1}
    assert report.sessions.sessions_by_month == {"2026-01": 2}


# -- UTC boundary ------------------------------------------------------------


def test_week_label_is_computed_from_utc_not_local_time() -> None:
    # 2025-12-29 is a Monday - the first day of ISO week 2026-W01.
    monday_utc = dt.datetime(2025, 12, 29, 0, 0, tzinfo=dt.UTC)
    assert evidence._week_label(monday_utc) == "2026-W01"
    sunday_utc = dt.datetime(2025, 12, 28, 23, 0, tzinfo=dt.UTC)
    assert evidence._week_label(sunday_utc) == "2025-W52"


# -- deterministic ordering ------------------------------------------------------


def test_report_is_deterministic_across_two_builds(store: TidemarkStore) -> None:
    session = [
        _obs(_hours(0), mtf.NO_INTERACTION, symbol=SYMBOL_2),
        _obs(_hours(0), mtf.NO_INTERACTION, symbol=SYMBOL),
        _obs(_hours(1), mtf.HTF_CONTEXT_INVALIDATED, symbol=SYMBOL),
        _obs(_hours(1), mtf.HTF_CONTEXT_INVALIDATED, symbol=SYMBOL_2),
    ]
    _save_all(store, session)
    first = _build(store)
    second = _build(store)
    assert first.coverage.symbols_observed == second.coverage.symbols_observed
    assert first.sessions.outcome_counts == second.sessions.outcome_counts
    assert first.coverage.per_symbol == second.coverage.per_symbol


def test_per_symbol_coverage_is_sorted_by_symbol(store: TidemarkStore) -> None:
    _save_all(
        store,
        [
            _obs(_hours(0), mtf.NO_INTERACTION, symbol="ZZZ/USDT:USDT"),
            _obs(_hours(0), mtf.NO_INTERACTION, symbol="AAA/USDT:USDT"),
        ],
    )
    report = _build(store)
    assert [s.symbol for s in report.coverage.per_symbol] == ["AAA/USDT:USDT", "ZZZ/USDT:USDT"]


# -- time-range filtering ---------------------------------------------------------


def test_from_to_filters_restrict_the_archive(store: TidemarkStore) -> None:
    _save_all(
        store,
        [
            _obs(_hours(0), mtf.NO_INTERACTION),
            _obs(_hours(100), mtf.NO_INTERACTION),
        ],
    )
    report = _build(store, since=_hours(50))
    assert report.coverage.total_observation_count == 1
    assert report.coverage.earliest_evaluated_at == _hours(100)


def test_symbol_filter_restricts_to_the_named_symbols(store: TidemarkStore) -> None:
    _save_all(
        store,
        [
            _obs(_hours(0), mtf.NO_INTERACTION, symbol=SYMBOL),
            _obs(_hours(0), mtf.NO_INTERACTION, symbol=SYMBOL_2),
        ],
    )
    report = _build(store, symbols=[SYMBOL])
    assert report.coverage.symbols_observed == (SYMBOL,)


# -- no lookahead: an --to filter reproduces what was known at that time ---------


def test_until_filter_reproduces_an_earlier_view_of_the_archive(store: TidemarkStore) -> None:
    session = [
        _obs(
            _hours(0),
            mtf.REACTION_DETECTED,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(0),
            session_started_at=_hours(0),
        ),
        _obs(
            _hours(1),
            mtf.BULLISH_STRUCTURE_CHANGE,
            structure_change=mtf.BULLISH_STRUCTURE_CHANGE,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(0),
            session_started_at=_hours(0),
        ),
    ]
    _save_all(store, session)

    early_view = _build(store, until=_hours(0))
    assert early_view.sec2_01.r1_followed_by_resolution == 0
    assert early_view.sessions.outcome_counts[evidence.OUTCOME_STILL_ACTIVE] == 1

    full_view = _build(store, until=_hours(1))
    assert full_view.sec2_01.r1_followed_by_resolution == 1
    assert full_view.sessions.outcome_counts[evidence.OUTCOME_STRUCTURE_CHANGE_LONG] == 1


# -- young-archive refusal / --allow-insufficient --------------------------------


def test_archive_under_14_days_is_marked_young(store: TidemarkStore) -> None:
    _save_all(store, [_obs(_hours(0), mtf.NO_INTERACTION), _obs(_hours(10), mtf.NO_INTERACTION)])
    report = _build(store)
    assert report.is_young_archive is True


def test_archive_spanning_14_or_more_days_is_not_young(store: TidemarkStore) -> None:
    fifteen_days = dt.timedelta(days=15)
    rows = []
    t = BASE
    while t < BASE + fifteen_days:
        rows.append(_obs(t, mtf.NO_INTERACTION, session_started_at=t))
        t += dt.timedelta(hours=1)
    _save_all(store, rows)
    report = _build(store)
    assert report.is_young_archive is False
    assert report.coverage.span_days >= evidence.MIN_ARCHIVE_SPAN_DAYS


# -- SEC2-03: level identity raw counts ------------------------------------------


def test_repeated_level_encounters_are_counted_as_one_group(store: TidemarkStore) -> None:
    session_1 = [_obs(_hours(0), mtf.HTF_CONTEXT_INVALIDATED, section_1_level_price=100.0)]
    session_2 = [
        _obs(
            _hours(20),
            mtf.HTF_CONTEXT_INVALIDATED,
            section_1_level_price=100.0,
            session_started_at=_hours(20),
        )
    ]
    _save_all(store, [*session_1, *session_2])
    report = _build(store)
    assert report.sec2_03.repeated_level_encounter_groups == 1
    assert report.sec2_03.repeated_level_encounter_sessions == 2


def test_level_failure_termination_is_counted_as_level_related(store: TidemarkStore) -> None:
    session = [_obs(_hours(0), mtf.SUPPORT_FAILURE, failure=mtf.SUPPORT_FAILURE)]
    _save_all(store, session)
    report = _build(store)
    assert report.sec2_03.level_related_terminations == 1
    assert report.sec2_03.terminations_by_reason[evidence.OUTCOME_LEVEL_FAILURE_SUPPORT] == 1


# -- sufficiency verdicts ---------------------------------------------------------


def test_sufficiency_verdict_is_about_investigability_not_the_answer(
    store: TidemarkStore,
) -> None:
    report = _build(store)
    for v in report.sufficiency:
        assert v.verdict in evidence.SUFFICIENCY_VERDICTS
        # The verdict object never carries a field describing what the
        # eventual SEC2-0x answer "looks like" - only whether it can be
        # investigated.
        assert not hasattr(v, "answer")
