"""Pure Markdown rendering of a `ReplayReport` - no I/O, no store access.

Separate from `report.py`'s computation so the two can be tested
independently: this module only ever turns a `ReplayReport` (already
computed) into text.
"""

from __future__ import annotations

from tidemark.context import htf, mtf
from tidemark.replay.report import SESSION_OUTCOMES, ReplayReport, Table1Row, Table2Row, Table3Row

_SECTION1_STATES = (
    htf.INSUFFICIENT_STRUCTURE,
    htf.BULLISH,
    htf.BEARISH,
    htf.NEUTRAL,
    htf.STRUCTURE_BROKEN_BULL,
    htf.STRUCTURE_BROKEN_BEAR,
)
_SECTION1_REASONS = (
    htf.NOT_ENOUGH_SWINGS,
    htf.STRUCTURE_BROKEN,
    htf.NEUTRAL_STRUCTURE,
    htf.MAJOR_SUPPORT_FIB,
    htf.MAJOR_SUPPORT,
    htf.MAJOR_RESISTANCE_FIB,
    htf.MAJOR_RESISTANCE,
    htf.FIB_ONLY,
    htf.NOT_IN_ZONE,
)
_SECTION2_STATES = (
    mtf.NO_INTERACTION,
    mtf.REACTION_DETECTED,
    mtf.NO_STRUCTURAL_REFERENCE,
    mtf.CONTINUATION_CANDIDATE_NOT_EVALUATED,
    mtf.BULLISH_STRUCTURE_CHANGE,
    mtf.BEARISH_STRUCTURE_CHANGE,
    mtf.HANDOFF_TO_15M,
    mtf.SUPPORT_FAILURE,
    mtf.RESISTANCE_FAILURE,
    mtf.STAND_DOWN,
    mtf.HTF_CONTEXT_INVALIDATED,
    mtf.REACTION_EXPIRED,
)


def _pct(count: int, total: int) -> str:
    if total == 0:
        return "0.00%"
    return f"{100 * count / total:.2f}%"


def _fmt(value: float | int | None) -> str:
    return "n/a" if value is None else str(value)


def render_snapshot(report: ReplayReport) -> str:
    snap = report.snapshot
    lines = [
        "## Data snapshot",
        "",
        f"- venue: `{snap.venue}`",
        f"- symbols: {', '.join(f'`{s}`' for s in snap.symbols)}",
        f"- timeframes: {', '.join(f'`{t}`' for t in snap.timeframes)}",
        f"- row hash (sha256 over venue/symbol/timeframe/open_time/OHLCV): `{snap.row_hash}`",
        "",
        "| Symbol | Timeframe | Rows | First open_time | Last open_time |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for row in snap.per_symbol_timeframe:
        first = row.first_open_time.isoformat() if row.first_open_time else "-"
        last = row.last_open_time.isoformat() if row.last_open_time else "-"
        lines.append(f"| {row.symbol} | {row.timeframe} | {row.row_count} | {first} | {last} |")
    return "\n".join(lines)


def _render_table1_row(row: Table1Row) -> list[str]:
    lines = [f"### {row.symbol}", "", f"Total evaluations: {row.total_evaluations}", ""]
    lines += ["| State | Count | % |", "| --- | ---: | ---: |"]
    for state in _SECTION1_STATES:
        count = row.state_counts.get(state, 0)
        lines.append(f"| {state} | {count} | {_pct(count, row.total_evaluations)} |")
    lines.append("")
    lines += ["| Reason code | Count | % |", "| --- | ---: | ---: |"]
    for reason in _SECTION1_REASONS:
        count = row.reason_counts.get(reason, 0)
        lines.append(f"| {reason} | {count} | {_pct(count, row.total_evaluations)} |")
    lines.append("")
    lines += ["| Watch | Grade A | Grade B |", "| --- | ---: | ---: |"]
    lines.append(
        f"| LONG_WATCH | {row.watch_grade_counts.get((htf.LONG_WATCH, 'A'), 0)} "
        f"| {row.watch_grade_counts.get((htf.LONG_WATCH, 'B'), 0)} |"
    )
    lines.append(
        f"| SHORT_WATCH | {row.watch_grade_counts.get((htf.SHORT_WATCH, 'A'), 0)} "
        f"| {row.watch_grade_counts.get((htf.SHORT_WATCH, 'B'), 0)} |"
    )
    lines.append("")
    lines.append(
        f"WATCH episodes: {row.episode_count}  |  median length (4H candles): "
        f"{_fmt(row.median_episode_length)}  |  max length: {_fmt(row.max_episode_length)}"
    )
    return lines


def render_table1(report: ReplayReport) -> str:
    lines = [
        "## Table 1 — Section 1, per evaluation",
        "",
        "One row of this table's counts per 4H close, point-in-time "
        "(as-of that close, no look-ahead). `ALL` is every symbol combined.",
        "",
    ]
    for row in report.table1:
        lines += _render_table1_row(row)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_table2_row(row: Table2Row) -> list[str]:
    lines = [
        f"### {row.symbol}",
        "",
        f"Sessions: {row.session_count}  |  with any interaction: {row.sessions_with_interaction}"
        f"  |  median length (1H candles): {_fmt(row.median_session_length)}"
        f"  |  max length: {_fmt(row.max_session_length)}",
        "",
        "| Terminal outcome | Count |",
        "| --- | ---: |",
    ]
    outcome_sum = 0
    for outcome in SESSION_OUTCOMES:
        count = row.outcome_counts.get(outcome, 0)
        outcome_sum += count
        lines.append(f"| {outcome} | {count} |")
    lines.append(f"| **sum** | **{outcome_sum}** |")
    lines.append("")
    lines += ["| Highest reaction tier reached | Sessions |", "| --- | ---: |"]
    for tier in (mtf.R1, mtf.R2, mtf.R3, "none"):
        lines.append(f"| {tier} | {row.highest_tier_counts.get(tier, 0)} |")
    return lines


def render_table2(report: ReplayReport) -> str:
    lines = [
        "## Table 2 — Section 2, per SESSION",
        "",
        "A session is one contiguous run under a single pinned Section 1 "
        "(state, watch, grade). Every session ends in exactly one terminal "
        "outcome; each symbol's outcome counts sum to that symbol's session "
        "count (see the **sum** row). `ALL` is every symbol combined.",
        "",
    ]
    for row in report.table2:
        lines += _render_table2_row(row)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_table3_row(row: Table3Row) -> list[str]:
    lines = [
        f"### {row.symbol}",
        "",
        f"Total evaluations (1H closes under an active WATCH): {row.total_evaluations}",
        "",
        "| State | Count | % |",
        "| --- | ---: | ---: |",
    ]
    for state in _SECTION2_STATES:
        count = row.state_counts.get(state, 0)
        lines.append(f"| {state} | {count} | {_pct(count, row.total_evaluations)} |")
    return lines


def render_table3(report: ReplayReport) -> str:
    lines = [
        "## Table 3 — Section 2, per EVALUATION",
        "",
        "Row-level state counts - these are **evaluation counts, not "
        "outcomes**. A single session shows up as many rows here (one per "
        "1H close), which is why `NO_STRUCTURAL_REFERENCE` in this table is "
        "not comparable to any count in Table 2. `ALL` is every symbol "
        "combined.",
        "",
    ]
    for row in report.table3:
        lines += _render_table3_row(row)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_report(report: ReplayReport) -> str:
    parts = [
        f"# Section {report.rule_version} replay",
        "",
        f"- command: `{report.command}`",
        f"- rule_version: `{report.rule_version}`",
        f"- generated_at: {report.generated_at.isoformat()}",
        "",
        render_snapshot(report),
        "",
        render_table1(report),
        render_table2(report),
        render_table3(report),
    ]
    return "\n".join(parts)
