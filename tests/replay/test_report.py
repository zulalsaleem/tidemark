"""Read-only Section 1/2 replay: session-outcome classification (Table 2's
"every session ends in exactly one outcome" invariant), determinism, the
look-ahead guard, that nothing gets written, and that per-evaluation and
per-session counts stay separate units. See
docs/adr/0008-replay-as-a-repo-command.md.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from tidemark.context import htf, mtf
from tidemark.data.exchange import RawCandle
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.replay import report as replay_report

VENUE = "binanceusdm"
SYMBOL = "BTC/USDT:USDT"
BASE = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _hours(n: int) -> dt.datetime:
    return BASE + dt.timedelta(hours=n)


def _obs(evaluated_at: dt.datetime, state: str, **overrides) -> mtf.ObservationResult:
    defaults = dict(
        asset=SYMBOL,
        evaluated_at=evaluated_at,
        rule_version=mtf.RULE_VERSION,
        section_1_state=htf.BULLISH,
        section_1_watch=htf.LONG_WATCH,
        section_1_grade="B",
        section_1_level_price=100.0,
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
    return mtf.ObservationResult(**defaults)


# -- Table 2: every session ends in exactly one outcome, counts sum ----------


def test_session_outcome_counts_sum_to_session_count() -> None:
    sessions = [
        # STRUCTURE_CHANGE_LONG - triggered, then echoed by HANDOFF_TO_15M.
        [
            _obs(
                _hours(0),
                mtf.REACTION_DETECTED,
                reaction_tier=mtf.R1,
                reaction_started_at=_hours(0),
                interaction_detected=True,
            ),
            _obs(
                _hours(1),
                mtf.BULLISH_STRUCTURE_CHANGE,
                structure_change=mtf.BULLISH_STRUCTURE_CHANGE,
                reaction_tier=mtf.R1,
                reaction_started_at=_hours(0),
                interaction_detected=True,
            ),
            _obs(_hours(2), mtf.HANDOFF_TO_15M),
        ],
        # STRUCTURE_CHANGE_SHORT - the triggering row is the session's last row.
        [
            _obs(
                _hours(3),
                mtf.BEARISH_STRUCTURE_CHANGE,
                structure_change=mtf.BEARISH_STRUCTURE_CHANGE,
                section_1_watch=htf.SHORT_WATCH,
            ),
        ],
        # LEVEL_FAILURE_SUPPORT - triggered, then echoed by STAND_DOWN.
        [
            _obs(_hours(4), mtf.SUPPORT_FAILURE, failure=mtf.SUPPORT_FAILURE),
            _obs(_hours(5), mtf.STAND_DOWN),
        ],
        # LEVEL_FAILURE_RESISTANCE.
        [
            _obs(
                _hours(6),
                mtf.RESISTANCE_FAILURE,
                failure=mtf.RESISTANCE_FAILURE,
                section_1_watch=htf.SHORT_WATCH,
            ),
        ],
        # REACTION_EXPIRED - data simply ends on the expiry candle.
        [
            _obs(
                _hours(7),
                mtf.REACTION_DETECTED,
                reaction_tier=mtf.R1,
                reaction_started_at=_hours(7),
            ),
            _obs(_hours(8), mtf.REACTION_EXPIRED, expiry=True),
        ],
        # HTF_CONTEXT_INVALIDATED.
        [
            _obs(_hours(9), mtf.NO_INTERACTION),
            _obs(_hours(10), mtf.HTF_CONTEXT_INVALIDATED),
        ],
        # STILL_OPEN_AT_END_OF_DATA - data just stops mid-session.
        [
            _obs(_hours(11), mtf.NO_INTERACTION),
            _obs(_hours(12), mtf.CONTINUATION_CANDIDATE_NOT_EVALUATED, interaction_detected=True),
        ],
    ]

    row = replay_report._build_table2_row(SYMBOL, sessions)

    assert row.session_count == len(sessions) == 7
    assert sum(row.outcome_counts.values()) == row.session_count
    assert row.outcome_counts[replay_report.STRUCTURE_CHANGE_LONG] == 1
    assert row.outcome_counts[replay_report.STRUCTURE_CHANGE_SHORT] == 1
    assert row.outcome_counts[replay_report.LEVEL_FAILURE_SUPPORT] == 1
    assert row.outcome_counts[replay_report.LEVEL_FAILURE_RESISTANCE] == 1
    assert row.outcome_counts[mtf.REACTION_EXPIRED] == 1
    assert row.outcome_counts[mtf.HTF_CONTEXT_INVALIDATED] == 1
    assert row.outcome_counts[replay_report.STILL_OPEN_AT_END_OF_DATA] == 1


def test_invalidation_reason_only_counts_sessions_whose_outcome_is_invalidated() -> None:
    resolved_then_invalidated = [
        _obs(
            _hours(0), mtf.BULLISH_STRUCTURE_CHANGE, structure_change=mtf.BULLISH_STRUCTURE_CHANGE
        ),
        _obs(_hours(1), mtf.HANDOFF_TO_15M),
        # Eventually invalidated too - must NOT count towards
        # invalidation_reason_counts, since this session's outcome is the
        # structure change, not the invalidation.
        _obs(
            _hours(2),
            mtf.HTF_CONTEXT_INVALIDATED,
            section_1_state=htf.NEUTRAL,
            section_1_watch=htf.WAIT,
            section_1_grade=None,
        ),
    ]
    genuinely_invalidated = [
        _obs(_hours(3), mtf.NO_INTERACTION),
        _obs(
            _hours(4),
            mtf.HTF_CONTEXT_INVALIDATED,
            section_1_state=htf.NEUTRAL,
            section_1_watch=htf.WAIT,
            section_1_grade=None,
        ),
    ]

    row = replay_report._build_table2_row(
        SYMBOL, [resolved_then_invalidated, genuinely_invalidated]
    )

    assert row.outcome_counts[replay_report.STRUCTURE_CHANGE_LONG] == 1
    assert row.outcome_counts[mtf.HTF_CONTEXT_INVALIDATED] == 1
    assert sum(row.invalidation_reason_counts.values()) == 1


def test_session_still_active_at_end_of_data_is_still_open() -> None:
    session = [
        _obs(_hours(0), mtf.NO_INTERACTION),
        _obs(_hours(1), mtf.REACTION_DETECTED, reaction_tier=mtf.R1, reaction_started_at=_hours(1)),
        _obs(
            _hours(2),
            mtf.NO_STRUCTURAL_REFERENCE,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(1),
        ),
    ]

    assert replay_report._classify_session(session) == replay_report.STILL_OPEN_AT_END_OF_DATA


def test_unrecognized_last_state_raises_rather_than_guessing() -> None:
    session = [_obs(_hours(0), "SOME_FUTURE_STATE")]
    with pytest.raises(ValueError, match="unclassified"):
        replay_report._classify_session(session)


# -- group_sessions itself: a session ended by invalidation must close ------
# -- the session it ends, not open a phantom one (Bug 1) --------------------


def test_group_sessions_closes_on_invalidation_as_one_session_not_two() -> None:
    # The invalidation row's own section_1_* fields deliberately differ
    # from the preceding row's - exactly like the real thing (mtf.py's
    # _invalidated_row populates them from the *new* post-change Section 1
    # record) - to prove group_sessions no longer splits on that alone.
    rows = [
        _obs(_hours(0), mtf.NO_INTERACTION),
        _obs(
            _hours(1),
            mtf.HTF_CONTEXT_INVALIDATED,
            section_1_state=htf.NEUTRAL,
            section_1_watch=htf.WAIT,
            section_1_grade=None,
        ),
        # A fresh session starting right after must still start fresh, not
        # get merged into the one that just closed.
        _obs(
            _hours(2),
            mtf.NO_INTERACTION,
            section_1_state=htf.BEARISH,
            section_1_watch=htf.SHORT_WATCH,
            section_1_grade="B",
        ),
    ]

    sessions = replay_report.group_sessions(rows)

    assert len(sessions) == 2
    assert [r.state for r in sessions[0]] == [mtf.NO_INTERACTION, mtf.HTF_CONTEXT_INVALIDATED]
    assert replay_report._classify_session(sessions[0]) == mtf.HTF_CONTEXT_INVALIDATED
    assert len(sessions[1]) == 1


def test_group_sessions_bearish_structure_change_classifies_as_bearish() -> None:
    rows = [
        _obs(
            _hours(0),
            mtf.REACTION_DETECTED,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(0),
            section_1_watch=htf.SHORT_WATCH,
        ),
        _obs(
            _hours(1),
            mtf.BEARISH_STRUCTURE_CHANGE,
            structure_change=mtf.BEARISH_STRUCTURE_CHANGE,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(0),
            section_1_watch=htf.SHORT_WATCH,
        ),
        _obs(_hours(2), mtf.HANDOFF_TO_15M, section_1_watch=htf.SHORT_WATCH),
        # Eventually invalidated too - the trailing invalidation row must
        # not override the earlier bearish resolution.
        _obs(
            _hours(3),
            mtf.HTF_CONTEXT_INVALIDATED,
            section_1_state=htf.NEUTRAL,
            section_1_watch=htf.WAIT,
            section_1_grade=None,
        ),
    ]

    sessions = replay_report.group_sessions(rows)

    assert len(sessions) == 1
    assert replay_report._classify_session(sessions[0]) == replay_report.STRUCTURE_CHANGE_SHORT


def test_group_sessions_still_open_when_data_ends_without_invalidation() -> None:
    rows = [
        _obs(_hours(0), mtf.NO_INTERACTION),
        _obs(_hours(1), mtf.REACTION_DETECTED, reaction_tier=mtf.R1, reaction_started_at=_hours(1)),
        _obs(
            _hours(2),
            mtf.NO_STRUCTURAL_REFERENCE,
            reaction_tier=mtf.R1,
            reaction_started_at=_hours(1),
        ),
    ]

    sessions = replay_report.group_sessions(rows)

    assert len(sessions) == 1
    assert replay_report._classify_session(sessions[0]) == replay_report.STILL_OPEN_AT_END_OF_DATA


# -- a small real store fixture for the full-report tests below --------------

_VALUES_4H = [
    120,
    115,
    110,
    105,
    100,
    110,
    120,
    130,
    120,
    115,
    112,
    110,
    120,
    130,
    140,
    150,
    140,
    130,
    120,
    105,  # break: close 105 < HL 110
    100,
    95,
    90,
    95,
    100,
]


def _candle_4h(open_time: dt.datetime, v: float) -> RawCandle:
    return RawCandle(
        open_time=open_time,
        close_time=open_time + dt.timedelta(hours=4),
        open=v,
        high=v,
        low=v,
        close=v,
        volume=1.0,
    )


def _seed_store(tmp_path, name: str = "replay.db") -> TidemarkStore:
    engine = create_store_engine(f"sqlite:///{tmp_path / name}")
    init_db(engine)
    store = TidemarkStore(engine)
    fetched_at = dt.datetime.now(dt.UTC)

    candles_4h = [_candle_4h(BASE + dt.timedelta(hours=4 * i), v) for i, v in enumerate(_VALUES_4H)]
    store.upsert_candles(VENUE, SYMBOL, "4h", candles_4h, fetched_at)

    hours_total = 4 * len(_VALUES_4H)
    candles_1h = []
    for i in range(hours_total):
        v = _VALUES_4H[min(i // 4, len(_VALUES_4H) - 1)]
        open_time = BASE + dt.timedelta(hours=i)
        candles_1h.append(
            RawCandle(
                open_time=open_time,
                close_time=open_time + dt.timedelta(hours=1),
                open=v,
                high=v + 0.5,
                low=v - 0.5,
                close=v,
                volume=1.0,
            )
        )
    store.upsert_candles(VENUE, SYMBOL, "1h", candles_1h, fetched_at)

    return store


def test_replay_report_is_deterministic_across_two_runs(tmp_path) -> None:
    store = _seed_store(tmp_path)
    generated_at = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)

    first = replay_report.build_replay_report(
        store, VENUE, [SYMBOL], mtf.RULE_VERSION, "test command", generated_at
    )
    second = replay_report.build_replay_report(
        store, VENUE, [SYMBOL], mtf.RULE_VERSION, "test command", generated_at
    )

    assert first == second


def test_build_replay_report_never_writes_to_the_store(tmp_path, monkeypatch) -> None:
    store = _seed_store(tmp_path)

    def _boom(*args, **kwargs):
        raise AssertionError("replay must never write to the store")

    monkeypatch.setattr(TidemarkStore, "save_context_record", _boom)
    monkeypatch.setattr(TidemarkStore, "save_journal_entry", _boom)
    monkeypatch.setattr(TidemarkStore, "save_observation", _boom)

    replay_report.build_replay_report(
        store, VENUE, [SYMBOL], mtf.RULE_VERSION, "test command", dt.datetime.now(dt.UTC)
    )


def test_replay_writes_nothing_row_counts_unchanged(tmp_path) -> None:
    store = _seed_store(tmp_path)
    before = (
        store.count_journal_entries(),
        len(store.context_history(SYMBOL)),
        len(store.observation_history(SYMBOL)),
    )

    replay_report.build_replay_report(
        store, VENUE, [SYMBOL], mtf.RULE_VERSION, "test command", dt.datetime.now(dt.UTC)
    )

    after = (
        store.count_journal_entries(),
        len(store.context_history(SYMBOL)),
        len(store.observation_history(SYMBOL)),
    )
    assert before == after == (0, 0, 0)


# -- look-ahead guard, applied to this module's own efficient truncation -----


def _section1_fields(record) -> dict:
    return {
        "state": record.state,
        "watch": record.watch,
        "grade": record.grade,
        "reason_code": record.reason_code,
        "active_levels": record.active_levels,
        "fib": record.fib,
        "swings_used": record.swings_used,
    }


def test_replay_section1_look_ahead_guard(tmp_path) -> None:
    """`replay_section1`'s slice-based truncation must produce exactly the
    same result, for every candle, as truncating the database itself to
    that candle and replaying from scratch - the same property `tests/
    context/test_look_ahead_guard.py` already proves for `_evaluate_symbol`.
    """
    fetched_at = dt.datetime.now(dt.UTC)
    all_candles = [
        _candle_4h(BASE + dt.timedelta(hours=4 * i), v) for i, v in enumerate(_VALUES_4H)
    ]

    full_engine = create_store_engine(f"sqlite:///{tmp_path / 'full.db'}")
    init_db(full_engine)
    full_store = TidemarkStore(full_engine)
    full_store.upsert_candles(VENUE, SYMBOL, "4h", all_candles, fetched_at)
    full_by_tf = {"4h": full_store.get_candles(VENUE, SYMBOL, "4h"), "1d": [], "1w": []}
    full_records = replay_report.replay_section1(full_by_tf, SYMBOL)

    for i in range(len(all_candles)):
        truncated_engine = create_store_engine(f"sqlite:///{tmp_path / f'trunc_{i}.db'}")
        init_db(truncated_engine)
        truncated_store = TidemarkStore(truncated_engine)
        truncated_store.upsert_candles(VENUE, SYMBOL, "4h", all_candles[: i + 1], fetched_at)
        trunc_by_tf = {"4h": truncated_store.get_candles(VENUE, SYMBOL, "4h"), "1d": [], "1w": []}
        trunc_records = replay_report.replay_section1(trunc_by_tf, SYMBOL)

        assert len(trunc_records) == i + 1
        assert _section1_fields(trunc_records[-1]) == _section1_fields(full_records[i]), (
            f"mismatch at candle index {i}"
        )


# -- per-evaluation vs per-session: separate code paths, never summed --------


def test_report_data_model_never_combines_table_totals() -> None:
    field_names = {f.name for f in dataclasses.fields(replay_report.ReplayReport)}
    assert field_names == {
        "rule_version",
        "command",
        "generated_at",
        "snapshot",
        "table1",
        "table2",
        "table3",
    }


def test_table1_table2_table3_are_independent_units(tmp_path) -> None:
    store = _seed_store(tmp_path)
    report = replay_report.build_replay_report(
        store, VENUE, [SYMBOL], mtf.RULE_VERSION, "test command", dt.datetime.now(dt.UTC)
    )

    table1_evaluations = report.table1[-1].total_evaluations  # Section 1, 4H unit
    table2_sessions = report.table2[-1].session_count  # Section 2, session unit
    table3_evaluations = report.table3[-1].total_evaluations  # Section 2, 1H unit

    # Table 1 counts 4H closes; Table 3 counts 1H closes under a WATCH -
    # different timeframes entirely, never the same figure by construction.
    assert table1_evaluations == len(_VALUES_4H)
    # A session spans many 1H rows, so a session count can never exceed the
    # row count it was grouped from.
    assert table2_sessions <= table3_evaluations


def test_full_replay_table2_outcomes_sum_to_session_count(tmp_path) -> None:
    """The outcome-sum invariant, re-checked end to end (not just against
    the hand-built fixture) through the corrected group_sessions/
    _classify_session.
    """
    store = _seed_store(tmp_path)
    report = replay_report.build_replay_report(
        store, VENUE, [SYMBOL], mtf.RULE_VERSION, "test command", dt.datetime.now(dt.UTC)
    )

    for row in report.table2:
        assert sum(row.outcome_counts.values()) == row.session_count
