"""Pure Markdown rendering of a ReplayReport - table 2's rendered sum row
must match its session count (the same invariant `test_report.py` checks
at the computation layer, checked again at the render layer)."""

from __future__ import annotations

import datetime as dt

from tidemark.context import mtf
from tidemark.replay import render
from tidemark.replay.report import (
    GRADE_ONLY_CHANGE,
    STATE_OR_WATCH_CHANGE,
    WITH_INTERACTION,
    WITHOUT_INTERACTION,
    DataSnapshot,
    ReplayReport,
    StructureChangeDetail,
    Table1Row,
    Table2Row,
    Table3Row,
)

BASE = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _table2_row(**overrides) -> Table2Row:
    defaults = dict(
        symbol="BTC/USDT:USDT",
        session_count=0,
        sessions_with_interaction=0,
        outcome_counts=dict.fromkeys(
            (
                "STRUCTURE_CHANGE_LONG",
                "STRUCTURE_CHANGE_SHORT",
                "LEVEL_FAILURE_SUPPORT",
                "LEVEL_FAILURE_RESISTANCE",
                mtf.REACTION_EXPIRED,
                mtf.HTF_CONTEXT_INVALIDATED,
                "STILL_OPEN_AT_END_OF_DATA",
            ),
            0,
        ),
        highest_tier_counts={mtf.R1: 0, mtf.R2: 0, mtf.R3: 0, "none": 0},
        median_session_length=None,
        max_session_length=None,
        invalidation_reason_counts={GRADE_ONLY_CHANGE: 0, STATE_OR_WATCH_CHANGE: 0},
        no_reaction_interaction_counts={WITH_INTERACTION: 0, WITHOUT_INTERACTION: 0},
        structure_changes=[],
    )
    defaults.update(overrides)
    return Table2Row(**defaults)


def _empty_snapshot() -> DataSnapshot:
    return DataSnapshot(
        venue="binanceusdm",
        symbols=("BTC/USDT:USDT",),
        timeframes=("4h", "1d", "1w", "1h"),
        per_symbol_timeframe=(),
        row_hash="deadbeef",
    )


def _report(table2_row: Table2Row) -> ReplayReport:
    table1_row = Table1Row(
        symbol="BTC/USDT:USDT",
        total_evaluations=0,
        state_counts={},
        reason_counts={},
        watch_grade_counts={},
        episode_count=0,
        median_episode_length=None,
        max_episode_length=None,
    )
    table3_row = Table3Row(symbol="BTC/USDT:USDT", total_evaluations=0, state_counts={})
    return ReplayReport(
        rule_version=mtf.RULE_VERSION,
        command="tidemark replay --rule-version section-02-v0.1",
        generated_at=BASE,
        snapshot=_empty_snapshot(),
        table1=[table1_row],
        table2=[table2_row],
        table3=[table3_row],
    )


def test_render_table2_sum_row_matches_session_count() -> None:
    row = _table2_row(
        session_count=9,
        sessions_with_interaction=4,
        outcome_counts={
            "STRUCTURE_CHANGE_LONG": 1,
            "STRUCTURE_CHANGE_SHORT": 1,
            "LEVEL_FAILURE_SUPPORT": 2,
            "LEVEL_FAILURE_RESISTANCE": 1,
            mtf.REACTION_EXPIRED: 1,
            mtf.HTF_CONTEXT_INVALIDATED: 2,
            "STILL_OPEN_AT_END_OF_DATA": 1,
        },
        highest_tier_counts={mtf.R1: 2, mtf.R2: 1, mtf.R3: 1, "none": 5},
        median_session_length=3.0,
        max_session_length=12,
        invalidation_reason_counts={GRADE_ONLY_CHANGE: 1, STATE_OR_WATCH_CHANGE: 1},
        no_reaction_interaction_counts={WITH_INTERACTION: 2, WITHOUT_INTERACTION: 3},
    )

    text = render.render_table2(_report(row))

    assert "| **sum** | **9** |" in text
    assert "Sessions: 9" in text
    assert "Why sessions ending in HTF_CONTEXT_INVALIDATED actually ended (2 sessions):" in text
    assert "No-reaction sessions, split by interaction (5 sessions):" in text


def test_render_table2_lists_structure_change_details() -> None:
    detail = StructureChangeDetail(
        symbol="BTC/USDT:USDT",
        session_start=BASE,
        trigger_at=BASE + dt.timedelta(hours=8),
        direction=mtf.BEARISH_STRUCTURE_CHANGE,
        grade_at_start="B",
        reaction_tier=mtf.R3,
    )
    row = _table2_row(
        session_count=1,
        outcome_counts={"STRUCTURE_CHANGE_SHORT": 1},
        structure_changes=[detail],
    )

    text = render.render_table2(_report(row))

    assert "### Structure-change sessions - detail" in text
    assert "BEARISH_STRUCTURE_CHANGE" in text
    assert "| B | R3 |" in text


def test_render_report_includes_all_three_table_headers() -> None:
    row = _table2_row()

    text = render.render_report(_report(row))

    assert "## Data snapshot" in text
    assert "## Table 1 — Section 1, per evaluation" in text
    assert "## Table 2 — Section 2, per SESSION" in text
    assert "## Table 3 — Section 2, per EVALUATION" in text
    assert "evaluation counts, not" in text
