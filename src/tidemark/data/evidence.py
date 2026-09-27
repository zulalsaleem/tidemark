"""Read-only evidence queries over the persisted archive, backing
`tidemark evidence`.

See docs/adr/0010-evidence-command.md for the full rationale. The short
version: unlike `tidemark replay` (`replay/report.py`, ADR 0008), this
module never re-evaluates the rulebook. It only reads rows that
`tidemark run`/`tidemark observe run` already persisted
(`journal_entries` via `TidemarkStore.all_journal_entries`,
`observations` via `TidemarkStore.all_observations`) and aggregates them.
No candle is read, no `htf.evaluate`/`mtf.evaluate` call happens here,
and nothing here is a simulation — running this command twice against an
unchanged archive must produce byte-identical numbers.

Every metric here is a **raw count of what happened**, never a judgment
of whether it was good, bad, valid, or successful — see
`docs/rulebook/section-02-1h-behaviour-v0.2.md`'s OPEN QUESTIONS
(SEC2-01 through SEC2-04). This module builds the instrument those
questions would eventually be measured with; it does not answer them.

Session grouping/classification here is a deliberate, self-contained copy
of the same rulebook logic `replay/report.py` implements
(`_group_sessions_v1`/`_group_sessions_v2`/`_classify_session`), not a
shared import. `tidemark replay` and `tidemark evidence` answer different
questions (a live, re-simulate-able report vs. a frozen, months-apart-
comparable archive query) and must be free to evolve independently —
importing replay's internals would mean a change made for replay's own
reasons could silently shift what an evidence metric already reported
means. See ADR 0010.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass

from tidemark.context import htf, mtf
from tidemark.core import levels as levels_module
from tidemark.data.models import JournalEntry, Observation
from tidemark.data.store import TidemarkStore

# Mirrors `context/mtf.py`'s own `_WATCH_ROLE` (not imported directly - see
# the module docstring on why evidence keeps a frozen, independent copy of
# rulebook-derived mappings rather than reaching into another module's
# private attributes).
_WATCH_ROLE = {
    htf.LONG_WATCH: levels_module.SUPPORT,
    htf.SHORT_WATCH: levels_module.RESISTANCE,
}

# --- frozen thresholds (evidentiary, not rulebook parameters) ---------------
#
# These decide whether a SEC2-0x question can be *investigated* at all —
# never what its answer would be. They are process judgments about sample
# size, not trading rules, so they live here rather than in
# docs/rulebook/. See docs/adr/0010-evidence-command.md.

MIN_ARCHIVE_SPAN_DAYS = 14
MIN_SUFFICIENCY_SYMBOLS = 3
INSUFFICIENT_MAX_COUNT = 4
EMERGING_MAX_COUNT = 29

INSUFFICIENT = "INSUFFICIENT"
EMERGING = "EMERGING"
SUFFICIENT = "SUFFICIENT"
SUFFICIENCY_VERDICTS = (INSUFFICIENT, EMERGING, SUFFICIENT)

MORE_SESSIONS = "more sessions"
MORE_SYMBOLS = "more symbols"
MORE_TIME = "more time"
MORE_VARIED_CONDITIONS = "more varied conditions"

# --- session outcomes (evidence's own copy — see module docstring) ---------

OUTCOME_STRUCTURE_CHANGE_LONG = "STRUCTURE_CHANGE_LONG"
OUTCOME_STRUCTURE_CHANGE_SHORT = "STRUCTURE_CHANGE_SHORT"
OUTCOME_LEVEL_FAILURE_SUPPORT = "LEVEL_FAILURE_SUPPORT"
OUTCOME_LEVEL_FAILURE_RESISTANCE = "LEVEL_FAILURE_RESISTANCE"
OUTCOME_REACTION_EXPIRED = mtf.REACTION_EXPIRED
OUTCOME_HTF_CONTEXT_INVALIDATED = mtf.HTF_CONTEXT_INVALIDATED
OUTCOME_STILL_ACTIVE = "STILL_ACTIVE"

SESSION_OUTCOMES: tuple[str, ...] = (
    OUTCOME_STRUCTURE_CHANGE_LONG,
    OUTCOME_STRUCTURE_CHANGE_SHORT,
    OUTCOME_LEVEL_FAILURE_SUPPORT,
    OUTCOME_LEVEL_FAILURE_RESISTANCE,
    OUTCOME_REACTION_EXPIRED,
    OUTCOME_HTF_CONTEXT_INVALIDATED,
    OUTCOME_STILL_ACTIVE,
)

_STILL_ACTIVE_STATES = (
    mtf.NO_INTERACTION,
    mtf.REACTION_DETECTED,
    mtf.NO_STRUCTURAL_REFERENCE,
    mtf.CONTINUATION_CANDIDATE_NOT_EVALUATED,
)

# Reaction-cycle (Section F / SEC2-04) length buckets against the
# rulebook's fixed 12-candle reaction expiry (REACTION_EXPIRY_CANDLES).
EXACTLY_12 = "EXACTLY_12"
BEFORE_12 = "BEFORE_12"
BEYOND_12 = "BEYOND_12"
STILL_OPEN = "STILL_OPEN"
REACTION_CYCLE_BUCKETS: tuple[str, ...] = (BEFORE_12, EXACTLY_12, BEYOND_12, STILL_OPEN)


# --- small pure helpers ------------------------------------------------------


def _week_label(t: dt.datetime) -> str:
    iso = t.date().isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _month_label(t: dt.datetime) -> str:
    return f"{t.year:04d}-{t.month:02d}"


def _percentiles(values: list[int]) -> DurationStats:
    if not values:
        return DurationStats(None, None, None, None)
    ordered = sorted(values)
    if len(ordered) == 1:
        only = float(ordered[0])
        return DurationStats(only, only, only, only)

    def _pct(p: float) -> float:
        # Nearest-rank on a 0..1 fraction of the sorted list — simple,
        # deterministic, and stable for the small sample sizes an
        # observation archive actually produces.
        idx = min(len(ordered) - 1, max(0, round(p * (len(ordered) - 1))))
        return float(ordered[idx])

    return DurationStats(_pct(0.25), statistics.median(ordered), _pct(0.75), _pct(0.90))


def _count_by(items: list, key) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        k = key(item)
        counts[k] = counts.get(k, 0) + 1
    return counts


# --- dataclasses --------------------------------------------------------------


@dataclass(frozen=True)
class DurationStats:
    p25: float | None
    median: float | None
    p75: float | None
    p90: float | None


@dataclass(frozen=True)
class SymbolCoverage:
    symbol: str
    observation_count: int
    first_evaluated_at: dt.datetime | None
    last_evaluated_at: dt.datetime | None
    missing_hourly_ticks: int


@dataclass(frozen=True)
class ArchiveCoverage:
    """PART A — ARCHIVE COVERAGE."""

    earliest_evaluated_at: dt.datetime | None
    latest_evaluated_at: dt.datetime | None
    span_days: float
    symbols_observed: tuple[str, ...]
    total_observation_count: int
    per_symbol: tuple[SymbolCoverage, ...]


@dataclass(frozen=True)
class SessionSummary:
    """PART B — SECTION 2 SESSIONS."""

    total_sessions: int
    outcome_counts: dict[str, int]
    duration_candles: DurationStats
    sessions_by_symbol: dict[str, int]
    sessions_by_week: dict[str, int]
    sessions_by_month: dict[str, int]

    @property
    def active_sessions(self) -> int:
        return self.outcome_counts.get(OUTCOME_STILL_ACTIVE, 0)


@dataclass(frozen=True)
class Sec201Evidence:
    """PART C — SEC2-01 EVIDENCE (R1)."""

    sessions_with_r1: int
    r1_as_first_qualifying_event: int
    r1_followed_by_another_structural_event: int
    r1_followed_by_resolution: int
    r1_followed_by_invalidation: int
    unresolved_open_r1_sessions: int
    sessions_with_r1_by_week: dict[str, int]
    sessions_with_r1_by_month: dict[str, int]


@dataclass(frozen=True)
class ContinuationEvent:
    symbol: str
    session_started_at: dt.datetime
    evaluated_at: dt.datetime
    preceding_state: str | None
    following_state: str | None


@dataclass(frozen=True)
class Sec202Evidence:
    """PART D — SEC2-02 EVIDENCE (CONTINUATION_CANDIDATE_NOT_EVALUATED)."""

    sessions_with_continuation_candidate: int
    total_occurrences: int
    events: tuple[ContinuationEvent, ...]
    occurrences_by_week: dict[str, int]
    occurrences_by_month: dict[str, int]


@dataclass(frozen=True)
class Sec203Evidence:
    """PART E — SEC2-03 EVIDENCE (session terminations / level identity)."""

    terminations_by_reason: dict[str, int]
    repeated_level_encounter_groups: int
    repeated_level_encounter_sessions: int
    level_related_terminations: int
    level_remains_active_or_reappears: int
    terminations_by_reason_by_week: dict[str, dict[str, int]]
    terminations_by_reason_by_month: dict[str, dict[str, int]]


@dataclass(frozen=True)
class Sec204Evidence:
    """PART F — SEC2-04 EVIDENCE (12-candle reaction-expiry boundary).

    Unit is the REACTION CYCLE, not the session — a session can hold more
    than one reaction cycle (a reaction expires, then a new one starts
    later in the same still-open session), and conflating the two units
    is exactly what ADR 0008 already flagged as a mistake for Section 2
    counting in general.
    """

    cycle_counts: dict[str, int]
    duration_candles: DurationStats
    cycle_counts_by_week: dict[str, dict[str, int]]
    cycle_counts_by_month: dict[str, dict[str, int]]


@dataclass(frozen=True)
class RegimeProxyPeriod:
    period: str
    section_1_state_counts: dict[str, int]
    distinct_symbols_with_sessions: int


@dataclass(frozen=True)
class RegimeProxy:
    """Requirement 2 — a PROXY for market conditions, never a
    classification. Section 1 state distribution, across observed
    symbols, grouped by week and month.
    """

    by_week: tuple[RegimeProxyPeriod, ...]
    by_month: tuple[RegimeProxyPeriod, ...]


@dataclass(frozen=True)
class SufficiencyVerdict:
    question: str
    verdict: str
    raw_count: int
    missing: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceReport:
    generated_at: dt.datetime
    command: str
    symbols_filter: tuple[str, ...] | None
    since: dt.datetime | None
    until: dt.datetime | None
    coverage: ArchiveCoverage
    sessions: SessionSummary
    sec2_01: Sec201Evidence
    sec2_02: Sec202Evidence
    sec2_03: Sec203Evidence
    sec2_04: Sec204Evidence
    regime_proxy: RegimeProxy
    sufficiency: tuple[SufficiencyVerdict, ...]
    is_young_archive: bool


# --- filtering ---------------------------------------------------------------


def _filter_observations(
    rows: list[Observation],
    symbols: list[str] | None,
    since: dt.datetime | None,
    until: dt.datetime | None,
) -> list[Observation]:
    filtered = rows
    if symbols is not None:
        wanted = set(symbols)
        filtered = [r for r in filtered if r.asset in wanted]
    if since is not None:
        filtered = [r for r in filtered if r.evaluated_at >= since]
    if until is not None:
        filtered = [r for r in filtered if r.evaluated_at <= until]
    return filtered


def _filter_journal(
    rows: list[JournalEntry],
    symbols: list[str] | None,
    since: dt.datetime | None,
    until: dt.datetime | None,
) -> list[JournalEntry]:
    filtered = rows
    if symbols is not None:
        wanted = set(symbols)
        filtered = [r for r in filtered if r.asset in wanted]
    if since is not None:
        filtered = [r for r in filtered if r.evaluated_at >= since]
    if until is not None:
        filtered = [r for r in filtered if r.evaluated_at <= until]
    return filtered


# --- PART A: archive coverage -------------------------------------------------


def _build_coverage(observations: list[Observation]) -> ArchiveCoverage:
    if not observations:
        return ArchiveCoverage(
            earliest_evaluated_at=None,
            latest_evaluated_at=None,
            span_days=0.0,
            symbols_observed=(),
            total_observation_count=0,
            per_symbol=(),
        )

    by_symbol: dict[str, list[Observation]] = {}
    for row in observations:
        by_symbol.setdefault(row.asset, []).append(row)

    per_symbol: list[SymbolCoverage] = []
    for symbol in sorted(by_symbol):
        rows = sorted(by_symbol[symbol], key=lambda r: r.evaluated_at)
        missing = 0
        for prev, curr in zip(rows, rows[1:], strict=False):
            if (
                curr.session_started_at == prev.session_started_at
                and curr.evaluated_at - prev.evaluated_at > dt.timedelta(hours=1)
            ):
                hours = int((curr.evaluated_at - prev.evaluated_at) / dt.timedelta(hours=1))
                missing += hours - 1
        per_symbol.append(
            SymbolCoverage(
                symbol=symbol,
                observation_count=len(rows),
                first_evaluated_at=rows[0].evaluated_at,
                last_evaluated_at=rows[-1].evaluated_at,
                missing_hourly_ticks=missing,
            )
        )

    all_times = [r.evaluated_at for r in observations]
    earliest, latest = min(all_times), max(all_times)
    span_days = (latest - earliest) / dt.timedelta(days=1)

    return ArchiveCoverage(
        earliest_evaluated_at=earliest,
        latest_evaluated_at=latest,
        span_days=span_days,
        symbols_observed=tuple(sorted(by_symbol)),
        total_observation_count=len(observations),
        per_symbol=tuple(per_symbol),
    )


# --- session grouping (evidence's own frozen copy) --------------------------


def _group_sessions_v1(rows: list[Observation]) -> list[list[Observation]]:
    sessions: list[list[Observation]] = []
    current: list[Observation] = []
    prev: Observation | None = None
    for row in rows:
        is_new = (
            prev is None
            or prev.state == mtf.HTF_CONTEXT_INVALIDATED
            or row.evaluated_at - prev.evaluated_at != dt.timedelta(hours=1)
        )
        if is_new and current:
            sessions.append(current)
            current = []
        current.append(row)
        prev = row
    if current:
        sessions.append(current)
    return sessions


def _group_sessions_v2(rows: list[Observation]) -> list[list[Observation]]:
    sessions: list[list[Observation]] = []
    current: list[Observation] = []
    prev: Observation | None = None
    for row in rows:
        is_new = (
            prev is None
            or row.session_started_at != prev.session_started_at
            or row.evaluated_at - prev.evaluated_at != dt.timedelta(hours=1)
        )
        if is_new and current:
            sessions.append(current)
            current = []
        current.append(row)
        prev = row
    if current:
        sessions.append(current)
    return sessions


def _group_all_sessions(observations: list[Observation]) -> list[list[Observation]]:
    """Group persisted rows into sessions, per symbol and rule_version — a
    session never spans a rule_version boundary (`section-02-v0.1` rows and
    `section-02-v0.2` rows are entirely separate populations; see
    `docs/rulebook/section-02-1h-behaviour-v0.2.md`'s CHANGE FROM v0.1).
    """
    by_key: dict[tuple[str, str], list[Observation]] = {}
    for row in observations:
        by_key.setdefault((row.asset, row.rule_version), []).append(row)

    sessions: list[list[Observation]] = []
    for (_symbol, rule_version), rows in by_key.items():
        ordered = sorted(rows, key=lambda r: r.evaluated_at)
        grouper = _group_sessions_v1 if rule_version == mtf.RULE_VERSION_V1 else _group_sessions_v2
        sessions.extend(grouper(ordered))
    return sessions


def _session_outcome(session: list[Observation]) -> str:
    for row in session:
        if row.structure_change == mtf.BULLISH_STRUCTURE_CHANGE:
            return OUTCOME_STRUCTURE_CHANGE_LONG
        if row.structure_change == mtf.BEARISH_STRUCTURE_CHANGE:
            return OUTCOME_STRUCTURE_CHANGE_SHORT
    for row in session:
        if row.failure == mtf.SUPPORT_FAILURE:
            return OUTCOME_LEVEL_FAILURE_SUPPORT
        if row.failure == mtf.RESISTANCE_FAILURE:
            return OUTCOME_LEVEL_FAILURE_RESISTANCE

    last = session[-1]
    if last.state == mtf.HTF_CONTEXT_INVALIDATED:
        return OUTCOME_HTF_CONTEXT_INVALIDATED
    if last.state == mtf.REACTION_EXPIRED:
        return OUTCOME_REACTION_EXPIRED
    if last.state in _STILL_ACTIVE_STATES:
        return OUTCOME_STILL_ACTIVE
    raise ValueError(f"unclassified Section 2 session-ending state: {last.state!r}")


# --- PART B: sessions ----------------------------------------------------------


def _build_sessions(sessions: list[list[Observation]]) -> SessionSummary:
    outcome_counts: dict[str, int] = dict.fromkeys(SESSION_OUTCOMES, 0)
    lengths: list[int] = []
    sessions_by_symbol: dict[str, int] = {}
    sessions_by_week: dict[str, int] = {}
    sessions_by_month: dict[str, int] = {}

    for session in sessions:
        outcome_counts[_session_outcome(session)] += 1
        lengths.append(len(session))
        start = session[0]
        sessions_by_symbol[start.asset] = sessions_by_symbol.get(start.asset, 0) + 1
        week = _week_label(start.session_started_at)
        month = _month_label(start.session_started_at)
        sessions_by_week[week] = sessions_by_week.get(week, 0) + 1
        sessions_by_month[month] = sessions_by_month.get(month, 0) + 1

    return SessionSummary(
        total_sessions=len(sessions),
        outcome_counts=outcome_counts,
        duration_candles=_percentiles(lengths),
        sessions_by_symbol=sessions_by_symbol,
        sessions_by_week=sessions_by_week,
        sessions_by_month=sessions_by_month,
    )


# --- PART C: SEC2-01 (R1) ------------------------------------------------------


def _build_sec2_01(sessions: list[list[Observation]]) -> Sec201Evidence:
    sessions_with_r1 = 0
    r1_first = 0
    r1_then_structural = 0
    r1_then_resolution = 0
    r1_then_invalidation = 0
    unresolved_open_r1 = 0
    by_week: dict[str, int] = {}
    by_month: dict[str, int] = {}

    for session in sessions:
        r1_rows = [r for r in session if r.reaction_tier == mtf.R1]
        if not r1_rows:
            continue
        sessions_with_r1 += 1
        week = _week_label(session[0].session_started_at)
        month = _month_label(session[0].session_started_at)
        by_week[week] = by_week.get(week, 0) + 1
        by_month[month] = by_month.get(month, 0) + 1

        first_reaction = next((r for r in session if r.reaction_tier is not None), None)
        if first_reaction is not None and first_reaction.reaction_tier == mtf.R1:
            r1_first += 1

        first_r1_at = r1_rows[0].evaluated_at
        after = [r for r in session if r.evaluated_at > first_r1_at]
        if any(
            (r.reaction_tier is not None and r.reaction_tier != mtf.R1)
            or r.structure_change is not None
            or r.failure is not None
            or r.state == mtf.REACTION_EXPIRED
            for r in after
        ):
            r1_then_structural += 1
        if any(r.structure_change is not None for r in after):
            r1_then_resolution += 1

        outcome = _session_outcome(session)
        if outcome == OUTCOME_HTF_CONTEXT_INVALIDATED:
            r1_then_invalidation += 1
        if outcome == OUTCOME_STILL_ACTIVE and session[-1].reaction_tier == mtf.R1:
            unresolved_open_r1 += 1

    return Sec201Evidence(
        sessions_with_r1=sessions_with_r1,
        r1_as_first_qualifying_event=r1_first,
        r1_followed_by_another_structural_event=r1_then_structural,
        r1_followed_by_resolution=r1_then_resolution,
        r1_followed_by_invalidation=r1_then_invalidation,
        unresolved_open_r1_sessions=unresolved_open_r1,
        sessions_with_r1_by_week=by_week,
        sessions_with_r1_by_month=by_month,
    )


# --- PART D: SEC2-02 (continuation candidate) ---------------------------------


def _build_sec2_02(sessions: list[list[Observation]]) -> Sec202Evidence:
    sessions_with_cc = 0
    total_occurrences = 0
    events: list[ContinuationEvent] = []
    by_week: dict[str, int] = {}
    by_month: dict[str, int] = {}

    for session in sessions:
        found = False
        for i, row in enumerate(session):
            if row.state != mtf.CONTINUATION_CANDIDATE_NOT_EVALUATED:
                continue
            found = True
            total_occurrences += 1
            preceding = session[i - 1].state if i > 0 else None
            following = session[i + 1].state if i + 1 < len(session) else None
            events.append(
                ContinuationEvent(
                    symbol=row.asset,
                    session_started_at=row.session_started_at,
                    evaluated_at=row.evaluated_at,
                    preceding_state=preceding,
                    following_state=following,
                )
            )
            week = _week_label(row.evaluated_at)
            month = _month_label(row.evaluated_at)
            by_week[week] = by_week.get(week, 0) + 1
            by_month[month] = by_month.get(month, 0) + 1
        if found:
            sessions_with_cc += 1

    events.sort(key=lambda e: (e.symbol, e.evaluated_at))
    return Sec202Evidence(
        sessions_with_continuation_candidate=sessions_with_cc,
        total_occurrences=total_occurrences,
        events=tuple(events),
        occurrences_by_week=by_week,
        occurrences_by_month=by_month,
    )


# --- PART E: SEC2-03 (terminations / level identity) --------------------------


def _build_sec2_03(
    sessions: list[list[Observation]], journal_by_symbol: dict[str, list[JournalEntry]]
) -> Sec203Evidence:
    terminations_by_reason: dict[str, int] = {}
    by_week: dict[str, dict[str, int]] = {}
    by_month: dict[str, dict[str, int]] = {}
    level_groups: dict[tuple[str, float | None], int] = {}
    level_related = 0
    reappears = 0

    for session in sessions:
        outcome = _session_outcome(session)
        if outcome == OUTCOME_STILL_ACTIVE:
            continue  # not yet terminated - not a termination event
        terminations_by_reason[outcome] = terminations_by_reason.get(outcome, 0) + 1
        week = _week_label(session[0].session_started_at)
        month = _month_label(session[0].session_started_at)
        by_week.setdefault(week, {})
        by_week[week][outcome] = by_week[week].get(outcome, 0) + 1
        by_month.setdefault(month, {})
        by_month[month][outcome] = by_month[month].get(outcome, 0) + 1

        start = session[0]
        key = (start.asset, start.section_1_level_price)
        level_groups[key] = level_groups.get(key, 0) + 1

        if outcome in (OUTCOME_LEVEL_FAILURE_SUPPORT, OUTCOME_LEVEL_FAILURE_RESISTANCE):
            level_related += 1

        end = session[-1]
        later = [
            j for j in journal_by_symbol.get(start.asset, []) if j.evaluated_at > end.evaluated_at
        ]
        role = _WATCH_ROLE.get(start.section_1_watch)
        zone = _level_zone(start, journal_by_symbol.get(start.asset, []))
        if (
            zone is not None
            and role is not None
            and any(_held_level_in_zone(j, role, zone) for j in later)
        ):
            reappears += 1

    repeated_groups = {k: v for k, v in level_groups.items() if v > 1}
    repeated_sessions = sum(repeated_groups.values())

    return Sec203Evidence(
        terminations_by_reason=terminations_by_reason,
        repeated_level_encounter_groups=len(repeated_groups),
        repeated_level_encounter_sessions=repeated_sessions,
        level_related_terminations=level_related,
        level_remains_active_or_reappears=reappears,
        terminations_by_reason_by_week=by_week,
        terminations_by_reason_by_month=by_month,
    )


def _level_zone(
    start_row: Observation, symbol_journal: list[JournalEntry]
) -> tuple[float, float] | None:
    """The pinned level's own zone bounds, read back from the Section 1
    journal entry Section 2 actually pinned against at session start.

    `Observation` stores `section_1_level_price` (a bare price) but not
    the zone bounds Section 1 originally computed for it — `mtf.evaluate`
    never persisted those onto the observation row, only the price. This
    recovers them the same way `mtf.evaluate` originally picked the level:
    the latest journal entry with `evaluated_at <= session_started_at`
    (mirrors `context/mtf.py`'s own `h_idx` walk), matched on (role, price)
    among that entry's `active_levels`. Returns `None` if no such entry or
    matching level exists — never guesses a zone.
    """
    as_of: JournalEntry | None = None
    for entry in symbol_journal:
        if entry.evaluated_at <= start_row.session_started_at:
            as_of = entry
        else:
            break
    if as_of is None:
        return None

    role = _WATCH_ROLE.get(start_row.section_1_watch)
    if role is None:
        return None
    for lvl in as_of.active_levels:
        if lvl.get("role") == role and lvl.get("price") == start_row.section_1_level_price:
            return (lvl["zone_low"], lvl["zone_high"])
    return None


def _held_level_in_zone(entry: JournalEntry, role: str, zone: tuple[float, float]) -> bool:
    """Whether `entry` holds an active level of `role` whose price falls
    within `zone` — a raw fact-check for SEC2-03's "does the level remain
    active or reappear", never a decision about whether level identity
    should terminate a session (OPEN QUESTIONS (4), deferred to v0.3).
    """
    zone_low, zone_high = zone
    return any(
        lvl.get("role") == role
        and lvl.get("held")
        and zone_low <= lvl.get("price", -1) <= zone_high
        for lvl in entry.active_levels
    )


# --- PART F: SEC2-04 (12-candle boundary) --------------------------------------


def _reaction_cycles(session: list[Observation]) -> list[list[Observation]]:
    cycles: list[list[Observation]] = []
    current: list[Observation] = []
    current_start: dt.datetime | None = None
    for row in session:
        if row.reaction_started_at is None:
            if current:
                cycles.append(current)
                current = []
                current_start = None
            continue
        if current_start is None or row.reaction_started_at != current_start:
            if current:
                cycles.append(current)
            current = [row]
            current_start = row.reaction_started_at
        else:
            current.append(row)
    if current:
        cycles.append(current)
    return cycles


def _cycle_bucket(cycle: list[Observation], is_last_cycle_of_open_session: bool) -> str:
    last = cycle[-1]
    length = len(cycle)
    if last.state == mtf.REACTION_EXPIRED:
        if length == mtf.REACTION_EXPIRY_CANDLES:
            return EXACTLY_12
        return BEYOND_12 if length > mtf.REACTION_EXPIRY_CANDLES else BEFORE_12
    if is_last_cycle_of_open_session:
        return STILL_OPEN
    # Resolved (structure change) or cut short by a session-ending failure/
    # invalidation before reaching the 12-candle expiry.
    return BEFORE_12


def _build_sec2_04(sessions: list[list[Observation]]) -> Sec204Evidence:
    cycle_counts: dict[str, int] = dict.fromkeys(REACTION_CYCLE_BUCKETS, 0)
    lengths: list[int] = []
    by_week: dict[str, dict[str, int]] = {}
    by_month: dict[str, dict[str, int]] = {}

    for session in sessions:
        outcome = _session_outcome(session)
        cycles = _reaction_cycles(session)
        for i, cycle in enumerate(cycles):
            is_last = i == len(cycles) - 1 and outcome == OUTCOME_STILL_ACTIVE
            bucket = _cycle_bucket(cycle, is_last)
            cycle_counts[bucket] += 1
            if bucket != STILL_OPEN:
                lengths.append(len(cycle))
            week = _week_label(cycle[0].evaluated_at)
            month = _month_label(cycle[0].evaluated_at)
            by_week.setdefault(week, {})
            by_week[week][bucket] = by_week[week].get(bucket, 0) + 1
            by_month.setdefault(month, {})
            by_month[month][bucket] = by_month[month].get(bucket, 0) + 1

    return Sec204Evidence(
        cycle_counts=cycle_counts,
        duration_candles=_percentiles(lengths),
        cycle_counts_by_week=by_week,
        cycle_counts_by_month=by_month,
    )


# --- REGIME PROXY --------------------------------------------------------------


def _regime_period(
    period: str,
    journal_rows: list[JournalEntry],
    session_starts_by_period: dict[str, set[str]],
) -> RegimeProxyPeriod:
    state_counts = _count_by(journal_rows, lambda j: j.state)
    distinct_symbols = len(session_starts_by_period.get(period, set()))
    return RegimeProxyPeriod(
        period=period,
        section_1_state_counts=state_counts,
        distinct_symbols_with_sessions=distinct_symbols,
    )


def _build_regime_proxy(
    journal_rows: list[JournalEntry], sessions: list[list[Observation]]
) -> RegimeProxy:
    journal_by_week: dict[str, list[JournalEntry]] = {}
    journal_by_month: dict[str, list[JournalEntry]] = {}
    for j in journal_rows:
        journal_by_week.setdefault(_week_label(j.evaluated_at), []).append(j)
        journal_by_month.setdefault(_month_label(j.evaluated_at), []).append(j)

    symbols_by_week: dict[str, set[str]] = {}
    symbols_by_month: dict[str, set[str]] = {}
    for session in sessions:
        start = session[0]
        symbols_by_week.setdefault(_week_label(start.session_started_at), set()).add(start.asset)
        symbols_by_month.setdefault(_month_label(start.session_started_at), set()).add(start.asset)

    all_weeks = sorted(set(journal_by_week) | set(symbols_by_week))
    all_months = sorted(set(journal_by_month) | set(symbols_by_month))

    by_week = tuple(
        _regime_period(w, journal_by_week.get(w, []), symbols_by_week) for w in all_weeks
    )
    by_month = tuple(
        _regime_period(m, journal_by_month.get(m, []), symbols_by_month) for m in all_months
    )
    return RegimeProxy(by_week=by_week, by_month=by_month)


# --- sufficiency verdicts -------------------------------------------------------


def _verdict_for(
    question: str, raw_count: int, symbols_count: int, span_days: float
) -> SufficiencyVerdict:
    missing: list[str] = []
    if span_days < MIN_ARCHIVE_SPAN_DAYS:
        missing.append(MORE_TIME)
    if symbols_count < MIN_SUFFICIENCY_SYMBOLS:
        missing.append(MORE_SYMBOLS)
    if raw_count <= INSUFFICIENT_MAX_COUNT:
        missing.append(MORE_SESSIONS)

    if missing:
        verdict = INSUFFICIENT
    elif raw_count <= EMERGING_MAX_COUNT:
        verdict = EMERGING
        missing.append(MORE_SESSIONS)
        missing.append(MORE_VARIED_CONDITIONS)
    else:
        verdict = SUFFICIENT

    # De-duplicate while keeping first-seen order.
    seen: list[str] = []
    for m in missing:
        if m not in seen:
            seen.append(m)
    return SufficiencyVerdict(
        question=question, verdict=verdict, raw_count=raw_count, missing=tuple(seen)
    )


def _build_sufficiency(
    coverage: ArchiveCoverage,
    sec2_01: Sec201Evidence,
    sec2_02: Sec202Evidence,
    sec2_03: Sec203Evidence,
    sec2_04: Sec204Evidence,
) -> tuple[SufficiencyVerdict, ...]:
    symbols_count = len(coverage.symbols_observed)
    span = coverage.span_days
    completed_cycles = (
        sec2_04.cycle_counts.get(EXACTLY_12, 0)
        + sec2_04.cycle_counts.get(BEFORE_12, 0)
        + sec2_04.cycle_counts.get(BEYOND_12, 0)
    )
    return (
        _verdict_for("SEC2-01", sec2_01.sessions_with_r1, symbols_count, span),
        _verdict_for("SEC2-02", sec2_02.total_occurrences, symbols_count, span),
        _verdict_for("SEC2-03", sec2_03.level_related_terminations, symbols_count, span),
        _verdict_for("SEC2-04", completed_cycles, symbols_count, span),
    )


# --- top-level entry point ------------------------------------------------------


def build_evidence_report(
    store: TidemarkStore,
    command: str,
    generated_at: dt.datetime,
    symbols: list[str] | None = None,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> EvidenceReport:
    """Build the full evidence report by querying the persisted archive.

    Never writes anything and never calls `htf.evaluate`/`mtf.evaluate` —
    only `TidemarkStore.all_observations`/`all_journal_entries`, already
    persisted by `tidemark run`/`tidemark observe run`. Deterministic:
    the same archive, given the same arguments, produces byte-identical
    results (`generated_at`/`command` aside — wall-clock facts about the
    run, not part of the query result).
    """
    all_observations = store.all_observations()
    observations = _filter_observations(all_observations, symbols, since, until)

    all_journal = store.all_journal_entries()
    journal_rows = _filter_journal(all_journal, symbols, since, until)
    journal_by_symbol: dict[str, list[JournalEntry]] = {}
    for j in journal_rows:
        journal_by_symbol.setdefault(j.asset, []).append(j)
    for rows in journal_by_symbol.values():
        rows.sort(key=lambda j: j.evaluated_at)

    coverage = _build_coverage(observations)
    sessions = _group_all_sessions(observations)

    session_summary = _build_sessions(sessions)
    sec2_01 = _build_sec2_01(sessions)
    sec2_02 = _build_sec2_02(sessions)
    sec2_03 = _build_sec2_03(sessions, journal_by_symbol)
    sec2_04 = _build_sec2_04(sessions)
    regime_proxy = _build_regime_proxy(journal_rows, sessions)
    sufficiency = _build_sufficiency(coverage, sec2_01, sec2_02, sec2_03, sec2_04)

    return EvidenceReport(
        generated_at=generated_at,
        command=command,
        symbols_filter=tuple(symbols) if symbols else None,
        since=since,
        until=until,
        coverage=coverage,
        sessions=session_summary,
        sec2_01=sec2_01,
        sec2_02=sec2_02,
        sec2_03=sec2_03,
        sec2_04=sec2_04,
        regime_proxy=regime_proxy,
        sufficiency=sufficiency,
        is_young_archive=coverage.span_days < MIN_ARCHIVE_SPAN_DAYS,
    )
