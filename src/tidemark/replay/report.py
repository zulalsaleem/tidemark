"""Read-only, point-in-time replay of Section 1 and Section 2 over stored
candles, and the report it produces.

This module never writes to `context_records`, `journal_entries`, or
`observations` — it only reads candles via `TidemarkStore.get_candles` and
calls the pure `htf.evaluate`/`mtf.evaluate` functions. It is the engine
behind `tidemark replay` (see `cli.py`) and `docs/replay/
section-02-v0.1-baseline.md` (see ADR 0008 for why this exists as a repo
command rather than a one-off script).

Two units are computed and reported separately, by separate functions, and
are never summed together:
  - Section 1 is measured **per evaluation** (one row per 4H close) — Table 1.
  - Section 2 is measured **per session** (Table 2, one row per contiguous
    Section 1 WATCH pin) and **per evaluation** (Table 3, one row per 1H
    close under an active WATCH). A session spans many evaluations, so a
    session count and an evaluation count are different units of the same
    data and mixing them (as an earlier ad-hoc analysis did) is invalid.
"""

from __future__ import annotations

import bisect
import datetime as dt
import hashlib
import statistics
from dataclasses import dataclass

import pandas as pd

from tidemark.context import htf, mtf
from tidemark.core.atr import atr as compute_atr
from tidemark.data.models import Candle, ContextRecord
from tidemark.data.store import TidemarkStore

# Timeframes read by a replay: 4H/1D/1W feed Section 1, 1H additionally
# feeds Section 2. Fixed order so the snapshot hash is reproducible.
TIMEFRAMES_USED: tuple[str, ...] = ("4h", "1d", "1w", "1h")

_STILL_ACTIVE_STATES = (
    mtf.NO_INTERACTION,
    mtf.REACTION_DETECTED,
    mtf.NO_STRUCTURAL_REFERENCE,
    mtf.CONTINUATION_CANDIDATE_NOT_EVALUATED,
)

STRUCTURE_CHANGE_LONG = "STRUCTURE_CHANGE_LONG"
STRUCTURE_CHANGE_SHORT = "STRUCTURE_CHANGE_SHORT"
LEVEL_FAILURE_SUPPORT = "LEVEL_FAILURE_SUPPORT"
LEVEL_FAILURE_RESISTANCE = "LEVEL_FAILURE_RESISTANCE"
STILL_OPEN_AT_END_OF_DATA = "STILL_OPEN_AT_END_OF_DATA"

# Every Table 2 session lands in exactly one of these buckets - see
# `_classify_session`. Fixed order for reproducible report output.
SESSION_OUTCOMES: tuple[str, ...] = (
    STRUCTURE_CHANGE_LONG,
    STRUCTURE_CHANGE_SHORT,
    LEVEL_FAILURE_SUPPORT,
    LEVEL_FAILURE_RESISTANCE,
    mtf.REACTION_EXPIRED,
    mtf.HTF_CONTEXT_INVALIDATED,
    STILL_OPEN_AT_END_OF_DATA,
)

_TIER_RANK = {mtf.R1: 1, mtf.R2: 2, mtf.R3: 3}

# Why a session that ended on HTF_CONTEXT_INVALIDATED actually ended: only
# the grade changed (state and watch unchanged), or state and/or watch
# changed too.
GRADE_ONLY_CHANGE = "GRADE_ONLY_CHANGE"
STATE_OR_WATCH_CHANGE = "STATE_OR_WATCH_CHANGE"
INVALIDATION_REASONS: tuple[str, ...] = (GRADE_ONLY_CHANGE, STATE_OR_WATCH_CHANGE)

# For a session with no reaction tier ever detected: did it interact with
# the level at all (CONTINUATION_CANDIDATE_NOT_EVALUATED at some point) or
# never touch the zone (NO_INTERACTION throughout).
WITH_INTERACTION = "WITH_INTERACTION"
WITHOUT_INTERACTION = "WITHOUT_INTERACTION"
NO_REACTION_INTERACTION_BUCKETS: tuple[str, ...] = (WITH_INTERACTION, WITHOUT_INTERACTION)


# -- data snapshot (PART B) ---------------------------------------------------


@dataclass(frozen=True)
class SymbolTimeframeSnapshot:
    symbol: str
    timeframe: str
    row_count: int
    first_open_time: dt.datetime | None
    last_open_time: dt.datetime | None


@dataclass(frozen=True)
class DataSnapshot:
    """Describes exactly which candle rows a replay used, and a hash over
    them, so a later report can say plainly whether it ran against the
    same history as this one.
    """

    venue: str
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    per_symbol_timeframe: tuple[SymbolTimeframeSnapshot, ...]
    row_hash: str


def _snapshot_hash(rows: list[tuple]) -> str:
    """A stable SHA-256 over (venue, symbol, timeframe, open_time, OHLCV)
    tuples, in a fixed order - independent of dict/set iteration order.
    """
    hasher = hashlib.sha256()
    for row in rows:
        hasher.update("|".join(str(v) for v in row).encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def _load_candles(
    store: TidemarkStore, venue: str, symbol: str, timeframe: str, since: dt.datetime | None
) -> list[Candle]:
    candles = store.get_candles(venue, symbol, timeframe)
    if since is not None:
        candles = [c for c in candles if c.open_time >= since]
    return candles


def build_snapshot(
    store: TidemarkStore,
    venue: str,
    symbols: list[str],
    since: dt.datetime | None,
) -> tuple[DataSnapshot, dict[str, dict[str, list[Candle]]]]:
    """Read every candle a replay will use and describe it.

    Returns the snapshot description alongside the actual candle rows
    (`{symbol: {timeframe: [Candle, ...]}}`), so the replay functions below
    read the store exactly once and the snapshot's hash always matches
    what was actually replayed.
    """
    per_symbol_timeframe: list[SymbolTimeframeSnapshot] = []
    hash_rows: list[tuple] = []
    candles: dict[str, dict[str, list[Candle]]] = {}

    for symbol in symbols:
        candles[symbol] = {}
        for timeframe in TIMEFRAMES_USED:
            rows = _load_candles(store, venue, symbol, timeframe, since)
            candles[symbol][timeframe] = rows
            per_symbol_timeframe.append(
                SymbolTimeframeSnapshot(
                    symbol=symbol,
                    timeframe=timeframe,
                    row_count=len(rows),
                    first_open_time=rows[0].open_time if rows else None,
                    last_open_time=rows[-1].open_time if rows else None,
                )
            )
            for c in rows:
                hash_rows.append(
                    (
                        venue,
                        symbol,
                        timeframe,
                        c.open_time.isoformat(),
                        c.open,
                        c.high,
                        c.low,
                        c.close,
                        c.volume,
                    )
                )

    snapshot = DataSnapshot(
        venue=venue,
        symbols=tuple(symbols),
        timeframes=TIMEFRAMES_USED,
        per_symbol_timeframe=tuple(per_symbol_timeframe),
        row_hash=_snapshot_hash(hash_rows),
    )
    return snapshot, candles


# -- Section 1 replay, per evaluation (feeds Table 1, and Section 2's input) -


def _candles_to_frame(candles: list[Candle]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [c.open_time for c in candles],
            "close_time": [c.close_time for c in candles],
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [c.volume for c in candles],
        }
    )


def replay_section1(
    candles_by_timeframe: dict[str, list[Candle]], symbol: str
) -> list[ContextRecord]:
    """Replay Section 1 at every closed 4H candle, point-in-time.

    For candle i, `candles_4h[: i + 1]` (a prefix of the same ascending
    list) is exactly "every 4H candle with close_time <= candle i's own
    close_time" - the same truncation the look-ahead guard exercises via a
    truncated database, just computed as a slice instead of a second query.
    1D/1W are truncated the same way via `bisect` on their own close times.
    Nothing is written anywhere; this returns the evaluated records only.
    """
    candles_4h = candles_by_timeframe["4h"]
    candles_1d = candles_by_timeframe["1d"]
    candles_1w = candles_by_timeframe["1w"]
    if len(candles_4h) == 0:
        return []

    frame_4h_full = _candles_to_frame(candles_4h)
    frame_1d_full = _candles_to_frame(candles_1d) if candles_1d else None
    frame_1w_full = _candles_to_frame(candles_1w) if candles_1w else None
    close_1d = [c.close_time for c in candles_1d]
    close_1w = [c.close_time for c in candles_1w]

    records: list[ContextRecord] = []
    for i in range(len(candles_4h)):
        as_of = candles_4h[i].close_time
        trunc_4h = frame_4h_full.iloc[: i + 1]

        trunc_1d = None
        if frame_1d_full is not None:
            cutoff = bisect.bisect_right(close_1d, as_of)
            trunc_1d = frame_1d_full.iloc[:cutoff] if cutoff > 0 else None

        trunc_1w = None
        if frame_1w_full is not None:
            cutoff = bisect.bisect_right(close_1w, as_of)
            trunc_1w = frame_1w_full.iloc[:cutoff] if cutoff > 0 else None

        atr_value = compute_atr(trunc_4h).iloc[-1]
        record = htf.evaluate(symbol, trunc_4h, atr_value, candles_1d=trunc_1d, candles_1w=trunc_1w)
        records.append(record)

    return records


# -- Section 2 replay: sessions (Table 2) and evaluations (Table 3) ----------


def replay_section2(
    candles_by_timeframe: dict[str, list[Candle]],
    symbol: str,
    section_1_history: list[ContextRecord],
) -> list[mtf.ObservationResult]:
    """Replay Section 2 once over the full available 1H history. `mtf.
    evaluate` is itself a from-the-start, no-look-ahead replay (see its own
    docstring and `tests/context/test_mtf.py`'s look-ahead guard), so no
    extra truncation is needed here beyond what's already in `candles_1h`.
    """
    candles_1h = candles_by_timeframe["1h"]
    if len(candles_1h) == 0:
        return []
    frame_1h = _candles_to_frame(candles_1h)
    return mtf.evaluate(symbol, section_1_history, frame_1h)


def group_sessions(rows: list[mtf.ObservationResult]) -> list[list[mtf.ObservationResult]]:
    """Group Section 2 output rows (already in evaluated_at order) into
    sessions: a new session starts on the first row, whenever there's a
    time gap since the previous row (Section 1 left WATCH and later
    re-entered it - no rows are emitted while it's away), or right after a
    row whose own state is HTF_CONTEXT_INVALIDATED - that row is the
    CLOSING row of the session it ends, not the opening row of a new one.

    This does NOT compare each row's own (section_1_state, section_1_watch,
    section_1_grade) fields against the previous row's, because the
    invalidation row's own fields reflect the *new*, post-change Section 1
    record - `mtf.py`'s `_invalidated_row` builds it from `as_of` (the
    record that triggered the change), not from `session.pin` (what the
    ending session was pinned to). Comparing fields would put the
    invalidation row on the wrong side of the boundary it's supposed to
    mark. (A prior version of this function did exactly that - it's the
    subject of ADR 0008's Table 2 correction.)
    """
    sessions: list[list[mtf.ObservationResult]] = []
    current: list[mtf.ObservationResult] = []
    prev: mtf.ObservationResult | None = None
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


def _classify_session(session: list[mtf.ObservationResult]) -> str:
    """Every session ends in exactly one outcome - see `SESSION_OUTCOMES`.

    Resolution (a structure change or a level failure) is looked for
    anywhere in the session, never inferred from the last row alone: since
    `group_sessions` now attaches a session's closing HTF_CONTEXT_INVALIDATED
    row to the session it ends, an already-resolved session (structure
    change or failure fired earlier) can still end with that trailing
    invalidation row as its last row - and that later invalidation must
    not override the session's true, earlier resolution. Each direction is
    read from the triggering row's own `structure_change`/`failure` field,
    never guessed from which terminal *state* happens to be last (that was
    Bug 2 - the earlier version returned STRUCTURE_CHANGE_LONG whenever
    the last row was HANDOFF_TO_15M, which is the same direction-agnostic
    echo state for both bullish and bearish confirmations).

    Raises rather than guessing if a future state isn't yet mapped here,
    which is what keeps "outcome counts sum to the session total" true by
    construction instead of by accident.
    """
    for r in session:
        if r.structure_change == mtf.BULLISH_STRUCTURE_CHANGE:
            return STRUCTURE_CHANGE_LONG
        if r.structure_change == mtf.BEARISH_STRUCTURE_CHANGE:
            return STRUCTURE_CHANGE_SHORT
    for r in session:
        if r.failure == mtf.SUPPORT_FAILURE:
            return LEVEL_FAILURE_SUPPORT
        if r.failure == mtf.RESISTANCE_FAILURE:
            return LEVEL_FAILURE_RESISTANCE

    last = session[-1]
    if last.state == mtf.HTF_CONTEXT_INVALIDATED:
        return mtf.HTF_CONTEXT_INVALIDATED
    if last.state == mtf.REACTION_EXPIRED:
        return mtf.REACTION_EXPIRED
    if last.state in _STILL_ACTIVE_STATES:
        # An unresolved session can only end here (rather than on
        # HTF_CONTEXT_INVALIDATED) by running out of candles - there is no
        # other way `mtf.evaluate`'s loop stops emitting rows for a
        # still-active session.
        return STILL_OPEN_AT_END_OF_DATA
    raise ValueError(f"unclassified Section 2 session-ending state: {last.state!r}")


def _highest_tier(session: list[mtf.ObservationResult]) -> str | None:
    tiers = [r.reaction_tier for r in session if r.reaction_tier is not None]
    if not tiers:
        return None
    return max(tiers, key=lambda t: _TIER_RANK[t])


# -- report tables -------------------------------------------------------------


@dataclass(frozen=True)
class Table1Row:
    """Section 1, per evaluation."""

    symbol: str
    total_evaluations: int
    state_counts: dict[str, int]
    reason_counts: dict[str, int]
    watch_grade_counts: dict[tuple[str, str | None], int]
    episode_count: int
    median_episode_length: float | None
    max_episode_length: int | None


@dataclass(frozen=True)
class StructureChangeDetail:
    """One STRUCTURE_CHANGE_LONG/_SHORT session: the grade Section 1 held
    when the session started, and the reaction tier that preceded the
    confirming close.
    """

    symbol: str
    session_start: dt.datetime
    trigger_at: dt.datetime
    direction: str  # mtf.BULLISH_STRUCTURE_CHANGE or mtf.BEARISH_STRUCTURE_CHANGE
    grade_at_start: str | None
    reaction_tier: str | None


@dataclass(frozen=True)
class Table2Row:
    """Section 2, per session."""

    symbol: str
    session_count: int
    sessions_with_interaction: int
    outcome_counts: dict[str, int]
    highest_tier_counts: dict[str, int]
    median_session_length: float | None
    max_session_length: int | None
    invalidation_reason_counts: dict[str, int]
    no_reaction_interaction_counts: dict[str, int]
    structure_changes: list[StructureChangeDetail]


@dataclass(frozen=True)
class Table3Row:
    """Section 2, per evaluation (row-level state counts)."""

    symbol: str
    total_evaluations: int
    state_counts: dict[str, int]


@dataclass(frozen=True)
class ReplayReport:
    rule_version: str
    command: str
    generated_at: dt.datetime
    snapshot: DataSnapshot
    table1: list[Table1Row]  # last element is the "ALL" overall row
    table2: list[Table2Row]
    table3: list[Table3Row]


def _find_watch_episodes(records: list[ContextRecord]) -> list[list[ContextRecord]]:
    """Maximal runs of consecutive evaluations (by evaluated_at order)
    where watch != WAIT.
    """
    episodes: list[list[ContextRecord]] = []
    current: list[ContextRecord] = []
    for r in records:
        if r.watch != htf.WAIT:
            current.append(r)
        else:
            if current:
                episodes.append(current)
                current = []
    if current:
        episodes.append(current)
    return episodes


def _build_table1_row(symbol: str, records: list[ContextRecord]) -> Table1Row:
    state_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    watch_grade_counts: dict[tuple[str, str | None], int] = {}
    for r in records:
        state_counts[r.state] = state_counts.get(r.state, 0) + 1
        reason_counts[r.reason_code] = reason_counts.get(r.reason_code, 0) + 1
        if r.watch != htf.WAIT:
            key = (r.watch, r.grade)
            watch_grade_counts[key] = watch_grade_counts.get(key, 0) + 1

    episodes = _find_watch_episodes(records)
    lengths = [len(e) for e in episodes]
    return Table1Row(
        symbol=symbol,
        total_evaluations=len(records),
        state_counts=state_counts,
        reason_counts=reason_counts,
        watch_grade_counts=watch_grade_counts,
        episode_count=len(episodes),
        median_episode_length=statistics.median(lengths) if lengths else None,
        max_episode_length=max(lengths) if lengths else None,
    )


def _build_table1(records_by_symbol: dict[str, list[ContextRecord]]) -> list[Table1Row]:
    rows = [_build_table1_row(symbol, records_by_symbol[symbol]) for symbol in records_by_symbol]
    all_records = [r for recs in records_by_symbol.values() for r in recs]
    overall = _build_table1_row("ALL", all_records)
    return [*rows, overall]


def _build_table2_row(symbol: str, sessions: list[list[mtf.ObservationResult]]) -> Table2Row:
    outcome_counts: dict[str, int] = dict.fromkeys(SESSION_OUTCOMES, 0)
    tier_counts: dict[str, int] = {mtf.R1: 0, mtf.R2: 0, mtf.R3: 0, "none": 0}
    invalidation_reason_counts: dict[str, int] = dict.fromkeys(INVALIDATION_REASONS, 0)
    no_reaction_interaction_counts: dict[str, int] = dict.fromkeys(
        NO_REACTION_INTERACTION_BUCKETS, 0
    )
    structure_changes: list[StructureChangeDetail] = []
    with_interaction = 0
    lengths = []
    for session in sessions:
        outcome = _classify_session(session)
        outcome_counts[outcome] += 1
        interacted = any(row.interaction_detected for row in session)
        if interacted:
            with_interaction += 1
        tier = _highest_tier(session)
        tier_counts["none" if tier is None else tier] += 1
        lengths.append(len(session))

        # Gated on the session's classified outcome, not merely on its
        # last row's raw state: an already-resolved session (structure
        # change or failure) can also pick up a trailing invalidation row
        # (see group_sessions/_classify_session), and that later
        # invalidation isn't why the session's outcome was decided.
        if outcome == mtf.HTF_CONTEXT_INVALIDATED:
            start, end = session[0], session[-1]
            grade_only = (
                start.section_1_state == end.section_1_state
                and start.section_1_watch == end.section_1_watch
            )
            reason = GRADE_ONLY_CHANGE if grade_only else STATE_OR_WATCH_CHANGE
            invalidation_reason_counts[reason] += 1

        if tier is None:
            bucket = WITH_INTERACTION if interacted else WITHOUT_INTERACTION
            no_reaction_interaction_counts[bucket] += 1

        for row in session:
            if row.structure_change is not None:
                structure_changes.append(
                    StructureChangeDetail(
                        symbol=session[0].asset,
                        session_start=session[0].evaluated_at,
                        trigger_at=row.evaluated_at,
                        direction=row.structure_change,
                        grade_at_start=session[0].section_1_grade,
                        reaction_tier=row.reaction_tier,
                    )
                )
                break

    return Table2Row(
        symbol=symbol,
        session_count=len(sessions),
        sessions_with_interaction=with_interaction,
        outcome_counts=outcome_counts,
        highest_tier_counts=tier_counts,
        median_session_length=statistics.median(lengths) if lengths else None,
        max_session_length=max(lengths) if lengths else None,
        invalidation_reason_counts=invalidation_reason_counts,
        no_reaction_interaction_counts=no_reaction_interaction_counts,
        structure_changes=structure_changes,
    )


def _build_table2(
    sessions_by_symbol: dict[str, list[list[mtf.ObservationResult]]],
) -> list[Table2Row]:
    rows = [_build_table2_row(symbol, sessions_by_symbol[symbol]) for symbol in sessions_by_symbol]
    all_sessions = [s for sess in sessions_by_symbol.values() for s in sess]
    overall = _build_table2_row("ALL", all_sessions)
    return [*rows, overall]


def _build_table3_row(symbol: str, rows: list[mtf.ObservationResult]) -> Table3Row:
    state_counts: dict[str, int] = {}
    for r in rows:
        state_counts[r.state] = state_counts.get(r.state, 0) + 1
    return Table3Row(symbol=symbol, total_evaluations=len(rows), state_counts=state_counts)


def _build_table3(obs_by_symbol: dict[str, list[mtf.ObservationResult]]) -> list[Table3Row]:
    rows = [_build_table3_row(symbol, obs_by_symbol[symbol]) for symbol in obs_by_symbol]
    all_rows = [r for obs in obs_by_symbol.values() for r in obs]
    overall = _build_table3_row("ALL", all_rows)
    return [*rows, overall]


def build_replay_report(
    store: TidemarkStore,
    venue: str,
    symbols: list[str],
    rule_version: str,
    command: str,
    generated_at: dt.datetime,
    days: int | None = None,
) -> ReplayReport:
    """Build the full read-only replay report. Never writes to the store."""
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days) if days is not None else None
    snapshot, candles = build_snapshot(store, venue, symbols, since)

    records_by_symbol: dict[str, list[ContextRecord]] = {}
    obs_by_symbol: dict[str, list[mtf.ObservationResult]] = {}
    sessions_by_symbol: dict[str, list[list[mtf.ObservationResult]]] = {}
    for symbol in symbols:
        records = replay_section1(candles[symbol], symbol)
        records_by_symbol[symbol] = records
        obs = replay_section2(candles[symbol], symbol, records)
        obs_by_symbol[symbol] = obs
        sessions_by_symbol[symbol] = group_sessions(obs)

    return ReplayReport(
        rule_version=rule_version,
        command=command,
        generated_at=generated_at,
        snapshot=snapshot,
        table1=_build_table1(records_by_symbol),
        table2=_build_table2(sessions_by_symbol),
        table3=_build_table3(obs_by_symbol),
    )
