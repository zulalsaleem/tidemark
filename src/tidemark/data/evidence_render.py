"""Text/JSON rendering of an `EvidenceReport` — no I/O, no store access.

Separate from `evidence.py`'s computation, mirroring `replay/report.py` +
`replay/render.py`'s split, so the two are testable independently.
Every table iterates over explicitly sorted keys (never raw dict/set
iteration order), which is what makes `tidemark evidence`'s output
ordering deterministic regardless of how the underlying archive was
scanned.
"""

from __future__ import annotations

import datetime as dt

from tidemark.data.evidence import (
    EMERGING,
    INSUFFICIENT,
    REACTION_CYCLE_BUCKETS,
    SESSION_OUTCOMES,
    SUFFICIENT,
    EvidenceReport,
)

_VERDICT_ORDER = {INSUFFICIENT: 0, EMERGING: 1, SUFFICIENT: 2}


def _fmt(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _fmt_dt(value: dt.datetime | None) -> str:
    return value.isoformat() if value is not None else "-"


def _sorted_counts(counts: dict[str, int]) -> list[tuple[str, int]]:
    return sorted(counts.items())


def render_coverage(report: EvidenceReport) -> str:
    cov = report.coverage
    lines = [
        "## A. Archive coverage",
        "",
        f"- earliest evaluated_at: {_fmt_dt(cov.earliest_evaluated_at)}",
        f"- latest evaluated_at:   {_fmt_dt(cov.latest_evaluated_at)}",
        f"- archive span:          {cov.span_days:.2f} days",
        f"- symbols observed:      {len(cov.symbols_observed)}",
        f"- total observation rows: {cov.total_observation_count}",
        "",
        "| Symbol | Rows | First | Last | Missing hourly ticks |",
        "| --- | ---: | --- | --- | ---: |",
    ]
    for s in cov.per_symbol:
        lines.append(
            f"| {s.symbol} | {s.observation_count} | {_fmt_dt(s.first_evaluated_at)} "
            f"| {_fmt_dt(s.last_evaluated_at)} | {s.missing_hourly_ticks} |"
        )
    if not cov.per_symbol:
        lines.append("| (no observation rows in range) | | | | |")
    return "\n".join(lines)


def render_sessions(report: EvidenceReport) -> str:
    s = report.sessions
    lines = [
        "## B. Section 2 sessions",
        "",
        f"- total sessions: {s.total_sessions}",
        f"- active (not yet terminated): {s.active_sessions}",
        f"- duration (candles): p25={_fmt(s.duration_candles.p25)} "
        f"median={_fmt(s.duration_candles.median)} p75={_fmt(s.duration_candles.p75)} "
        f"p90={_fmt(s.duration_candles.p90)}",
        "",
        "| Outcome | Count |",
        "| --- | ---: |",
    ]
    for outcome in SESSION_OUTCOMES:
        lines.append(f"| {outcome} | {s.outcome_counts.get(outcome, 0)} |")
    lines.append("")
    lines.append("| Symbol | Sessions |")
    lines.append("| --- | ---: |")
    for symbol, count in sorted(s.sessions_by_symbol.items()):
        lines.append(f"| {symbol} | {count} |")
    lines.append("")
    lines.append("| Week | Sessions |")
    lines.append("| --- | ---: |")
    for week, count in _sorted_counts(s.sessions_by_week):
        lines.append(f"| {week} | {count} |")
    lines.append("")
    lines.append("| Month | Sessions |")
    lines.append("| --- | ---: |")
    for month, count in _sorted_counts(s.sessions_by_month):
        lines.append(f"| {month} | {count} |")
    return "\n".join(lines)


def render_sec2_01(report: EvidenceReport) -> str:
    e = report.sec2_01
    lines = [
        "## C. SEC2-01 evidence (R1) — raw counts only, no validity judgment",
        "",
        f"- sessions containing R1: {e.sessions_with_r1}",
        f"- R1 as first qualifying event: {e.r1_as_first_qualifying_event}",
        f"- R1 followed by another structural event: {e.r1_followed_by_another_structural_event}",
        f"- R1 followed by resolution: {e.r1_followed_by_resolution}",
        f"- R1 followed by invalidation: {e.r1_followed_by_invalidation}",
        f"- unresolved open R1 sessions: {e.unresolved_open_r1_sessions}",
        "",
        "| Week | Sessions with R1 |",
        "| --- | ---: |",
    ]
    for week, count in _sorted_counts(e.sessions_with_r1_by_week):
        lines.append(f"| {week} | {count} |")
    lines.append("")
    lines.append("| Month | Sessions with R1 |")
    lines.append("| --- | ---: |")
    for month, count in _sorted_counts(e.sessions_with_r1_by_month):
        lines.append(f"| {month} | {count} |")
    return "\n".join(lines)


def render_sec2_02(report: EvidenceReport) -> str:
    e = report.sec2_02
    lines = [
        "## D. SEC2-02 evidence (CONTINUATION_CANDIDATE_NOT_EVALUATED)",
        "",
        f"- sessions containing at least one occurrence: {e.sessions_with_continuation_candidate}",
        f"- total occurrences: {e.total_occurrences}",
        "",
        "| Week | Occurrences |",
        "| --- | ---: |",
    ]
    for week, count in _sorted_counts(e.occurrences_by_week):
        lines.append(f"| {week} | {count} |")
    lines.append("")
    lines.append("| Month | Occurrences |")
    lines.append("| --- | ---: |")
    for month, count in _sorted_counts(e.occurrences_by_month):
        lines.append(f"| {month} | {count} |")
    lines.append("")
    lines.append("Raw events (symbol, evaluated_at, session_started_at, preceding, following):")
    lines.append(
        "| Symbol | Evaluated at | Session started at | Preceding state | Following state |"
    )
    lines.append("| --- | --- | --- | --- | --- |")
    for ev in e.events:
        lines.append(
            f"| {ev.symbol} | {ev.evaluated_at.isoformat()} | {ev.session_started_at.isoformat()} "
            f"| {ev.preceding_state or '-'} | {ev.following_state or '-'} |"
        )
    if not e.events:
        lines.append("| (none) | | | | |")
    return "\n".join(lines)


def render_sec2_03(report: EvidenceReport) -> str:
    e = report.sec2_03
    lines = [
        "## E. SEC2-03 evidence (session terminations / level identity)",
        "",
        f"- repeated encounters with the same (symbol, level) — groups: "
        f"{e.repeated_level_encounter_groups}, sessions: {e.repeated_level_encounter_sessions}",
        f"- level-related terminations (SUPPORT_FAILURE/RESISTANCE_FAILURE): "
        f"{e.level_related_terminations}",
        f"- terminated sessions whose level remains active or reappears later: "
        f"{e.level_remains_active_or_reappears}",
        "",
        "| Termination reason | Count |",
        "| --- | ---: |",
    ]
    for reason, count in _sorted_counts(e.terminations_by_reason):
        lines.append(f"| {reason} | {count} |")
    if not e.terminations_by_reason:
        lines.append("| (no terminated sessions) | |")
    return "\n".join(lines)


def render_sec2_04(report: EvidenceReport) -> str:
    e = report.sec2_04
    lines = [
        "## F. SEC2-04 evidence (12-candle reaction-expiry boundary)",
        "",
        "Unit: one reaction cycle (a session may hold more than one).",
        "",
        "| Bucket | Count |",
        "| --- | ---: |",
    ]
    for bucket in REACTION_CYCLE_BUCKETS:
        lines.append(f"| {bucket} | {e.cycle_counts.get(bucket, 0)} |")
    lines.append("")
    lines.append(
        f"Duration (candles, completed cycles only): p25={_fmt(e.duration_candles.p25)} "
        f"median={_fmt(e.duration_candles.median)} p75={_fmt(e.duration_candles.p75)} "
        f"p90={_fmt(e.duration_candles.p90)}"
    )
    return "\n".join(lines)


def render_regime_proxy(report: EvidenceReport) -> str:
    proxy = report.regime_proxy
    lines = [
        "## Regime proxy (Section 1 state distribution — a PROXY for market",
        "## conditions, never a classification)",
        "",
    ]
    for label, periods in (("Week", proxy.by_week), ("Month", proxy.by_month)):
        lines.append(f"### By {label.lower()}")
        lines.append("")
        lines.append(f"| {label} | Distinct symbols with sessions | State | Count |")
        lines.append("| --- | ---: | --- | ---: |")
        for p in periods:
            symbols = p.distinct_symbols_with_sessions
            if not p.section_1_state_counts:
                lines.append(f"| {p.period} | {symbols} | (no journal rows) | 0 |")
                continue
            for state, count in sorted(p.section_1_state_counts.items()):
                lines.append(f"| {p.period} | {symbols} | {state} | {count} |")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_sufficiency(report: EvidenceReport) -> str:
    lines = [
        "## Data sufficiency verdicts",
        "",
        "The verdict is about whether the question can be INVESTIGATED — never what the answer is.",
        "",
        "| Question | Verdict | Raw count | Missing |",
        "| --- | --- | ---: | --- |",
    ]
    for v in report.sufficiency:
        missing = ", ".join(v.missing) if v.missing else "-"
        lines.append(f"| {v.question} | {v.verdict} | {v.raw_count} | {missing} |")
    return "\n".join(lines)


def render_evidence_report(report: EvidenceReport, *, full: bool) -> str:
    """Render the full report, or (when `full=False`, the young-archive
    default) only the coverage summary and sufficiency verdicts.
    """
    symbols_filter = ", ".join(report.symbols_filter) if report.symbols_filter else "(all)"
    header = [
        "# Tidemark evidence report",
        "",
        f"- command: `{report.command}`",
        f"- generated_at: {report.generated_at.isoformat()}",
        f"- symbols filter: {symbols_filter}",
        f"- from: {_fmt_dt(report.since)}  to: {_fmt_dt(report.until)}",
        "",
    ]

    if not full:
        header.append(
            "**Archive spans fewer than 14 days — this run is refusing full "
            "evidence output by default.** Pass --allow-insufficient to see "
            "the full report anyway, marked preliminary."
        )
        header.append("")
        parts = [*header, render_coverage(report), "", render_sufficiency(report)]
        return "\n".join(parts)

    parts = list(header)
    if report.is_young_archive:
        parts.append(
            "**PRELIMINARY — archive spans fewer than 14 days. Not valid for "
            "Section 2 conclusions.**"
        )
        parts.append("")

    parts += [
        render_coverage(report),
        "",
        render_sessions(report),
        "",
        render_sec2_01(report),
        "",
        render_sec2_02(report),
        "",
        render_sec2_03(report),
        "",
        render_sec2_04(report),
        "",
        render_regime_proxy(report),
        "",
        render_sufficiency(report),
    ]
    return "\n".join(parts)


# --- JSON -----------------------------------------------------------------


def _dt_or_none(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def evidence_report_to_dict(report: EvidenceReport, *, full: bool) -> dict:
    """Machine-readable form, respecting the same `full` gate as the text
    renderer — a young archive without --allow-insufficient gets only
    coverage + sufficiency in JSON too.
    """
    cov = report.coverage
    result: dict = {
        "command": report.command,
        "generated_at": report.generated_at.isoformat(),
        "symbols_filter": list(report.symbols_filter) if report.symbols_filter else None,
        "since": _dt_or_none(report.since),
        "until": _dt_or_none(report.until),
        "is_young_archive": report.is_young_archive,
        "full_report": full,
        "coverage": {
            "earliest_evaluated_at": _dt_or_none(cov.earliest_evaluated_at),
            "latest_evaluated_at": _dt_or_none(cov.latest_evaluated_at),
            "span_days": cov.span_days,
            "symbols_observed": list(cov.symbols_observed),
            "total_observation_count": cov.total_observation_count,
            "per_symbol": [
                {
                    "symbol": s.symbol,
                    "observation_count": s.observation_count,
                    "first_evaluated_at": _dt_or_none(s.first_evaluated_at),
                    "last_evaluated_at": _dt_or_none(s.last_evaluated_at),
                    "missing_hourly_ticks": s.missing_hourly_ticks,
                }
                for s in cov.per_symbol
            ],
        },
        "sufficiency": [
            {
                "question": v.question,
                "verdict": v.verdict,
                "raw_count": v.raw_count,
                "missing": list(v.missing),
            }
            for v in report.sufficiency
        ],
    }
    if not full:
        return result

    s = report.sessions
    result["sessions"] = {
        "total_sessions": s.total_sessions,
        "active_sessions": s.active_sessions,
        "outcome_counts": dict(sorted(s.outcome_counts.items())),
        "duration_candles": {
            "p25": s.duration_candles.p25,
            "median": s.duration_candles.median,
            "p75": s.duration_candles.p75,
            "p90": s.duration_candles.p90,
        },
        "sessions_by_symbol": dict(sorted(s.sessions_by_symbol.items())),
        "sessions_by_week": dict(sorted(s.sessions_by_week.items())),
        "sessions_by_month": dict(sorted(s.sessions_by_month.items())),
    }

    e1 = report.sec2_01
    result["sec2_01"] = {
        "sessions_with_r1": e1.sessions_with_r1,
        "r1_as_first_qualifying_event": e1.r1_as_first_qualifying_event,
        "r1_followed_by_another_structural_event": e1.r1_followed_by_another_structural_event,
        "r1_followed_by_resolution": e1.r1_followed_by_resolution,
        "r1_followed_by_invalidation": e1.r1_followed_by_invalidation,
        "unresolved_open_r1_sessions": e1.unresolved_open_r1_sessions,
        "sessions_with_r1_by_week": dict(sorted(e1.sessions_with_r1_by_week.items())),
        "sessions_with_r1_by_month": dict(sorted(e1.sessions_with_r1_by_month.items())),
    }

    e2 = report.sec2_02
    result["sec2_02"] = {
        "sessions_with_continuation_candidate": e2.sessions_with_continuation_candidate,
        "total_occurrences": e2.total_occurrences,
        "occurrences_by_week": dict(sorted(e2.occurrences_by_week.items())),
        "occurrences_by_month": dict(sorted(e2.occurrences_by_month.items())),
        "events": [
            {
                "symbol": ev.symbol,
                "session_started_at": ev.session_started_at.isoformat(),
                "evaluated_at": ev.evaluated_at.isoformat(),
                "preceding_state": ev.preceding_state,
                "following_state": ev.following_state,
            }
            for ev in e2.events
        ],
    }

    e3 = report.sec2_03
    result["sec2_03"] = {
        "terminations_by_reason": dict(sorted(e3.terminations_by_reason.items())),
        "repeated_level_encounter_groups": e3.repeated_level_encounter_groups,
        "repeated_level_encounter_sessions": e3.repeated_level_encounter_sessions,
        "level_related_terminations": e3.level_related_terminations,
        "level_remains_active_or_reappears": e3.level_remains_active_or_reappears,
    }

    e4 = report.sec2_04
    result["sec2_04"] = {
        "cycle_counts": dict(sorted(e4.cycle_counts.items())),
        "duration_candles": {
            "p25": e4.duration_candles.p25,
            "median": e4.duration_candles.median,
            "p75": e4.duration_candles.p75,
            "p90": e4.duration_candles.p90,
        },
    }

    proxy = report.regime_proxy
    result["regime_proxy"] = {
        "by_week": [
            {
                "period": p.period,
                "section_1_state_counts": dict(sorted(p.section_1_state_counts.items())),
                "distinct_symbols_with_sessions": p.distinct_symbols_with_sessions,
            }
            for p in proxy.by_week
        ],
        "by_month": [
            {
                "period": p.period,
                "section_1_state_counts": dict(sorted(p.section_1_state_counts.items())),
                "distinct_symbols_with_sessions": p.distinct_symbols_with_sessions,
            }
            for p in proxy.by_month
        ],
    }

    return result
