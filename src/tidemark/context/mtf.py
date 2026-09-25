"""Section 2 — 1H behaviour (PROVISIONAL — OBSERVATION ONLY).

Implements both `docs/rulebook/section-02-1h-behaviour-v0.1.md` and
`section-02-1h-behaviour-v0.2.md`, selected per call via `evaluate()`'s
`rule_version` argument (`RULE_VERSION_V1`/`RULE_VERSION_V2`). The two
versions differ in exactly one respect - what ends a session, in
`_session_ended` - and are otherwise identical: reaction tiers, structure
confirmation, zone failure, and the 12-candle expiry are shared code.
This module is a MEASUREMENT layer, not a signal layer: it observes what
1H price does at a 4H WATCH area and returns rows to journal. It never
computes an entry, stop, target, or R:R, never recommends a handoff, and
is never called anywhere near `notify/telegram.py`.

`evaluate()` is a full, deterministic replay over the given 1H candles
and Section 1 journal history — not incrementally persisted state — so
truncating both inputs to an "as of" point reproduces exactly the rows
that existed at that point (the same look-ahead guard `context/htf.py`
relies on).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from tidemark.context import htf
from tidemark.core import levels as levels_module
from tidemark.core.atr import atr as compute_atr
from tidemark.core.swings import HIGH, LOW, confirmed_swings_as_of, find_swings
from tidemark.data.models import JournalEntry, Swing

RULE_VERSION_V1 = "section-02-v0.1"
RULE_VERSION_V2 = "section-02-v0.2"
SUPPORTED_RULE_VERSIONS = (RULE_VERSION_V1, RULE_VERSION_V2)

# Reaction tiers (Stage A).
R1 = "R1"
R2 = "R2"
R3 = "R3"

# States (full enum from the rulebook's STATES list; NO_HTF_CONTEXT is the
# activation gate, not a journaled row - see `evaluate()`).
NO_INTERACTION = "NO_INTERACTION"
REACTION_DETECTED = "REACTION_DETECTED"
NO_STRUCTURAL_REFERENCE = "NO_STRUCTURAL_REFERENCE"
CONTINUATION_CANDIDATE_NOT_EVALUATED = "CONTINUATION_CANDIDATE_NOT_EVALUATED"
BULLISH_STRUCTURE_CHANGE = "BULLISH_STRUCTURE_CHANGE"
BEARISH_STRUCTURE_CHANGE = "BEARISH_STRUCTURE_CHANGE"
HANDOFF_TO_15M = "HANDOFF_TO_15M"
SUPPORT_FAILURE = "SUPPORT_FAILURE"
RESISTANCE_FAILURE = "RESISTANCE_FAILURE"
STAND_DOWN = "STAND_DOWN"
HTF_CONTEXT_INVALIDATED = "HTF_CONTEXT_INVALIDATED"
REACTION_EXPIRED = "REACTION_EXPIRED"

# PARAMETERS [ALL PROVISIONAL] (section-02-1h-behaviour-v0.1.md).
FRACTAL_N = 2
ATR_PERIOD = 14
R2_RANGE_ATR_MULTIPLIER = 1.5
REACTION_EXPIRY_CANDLES = 12

_WATCH_ROLE = {
    htf.LONG_WATCH: levels_module.SUPPORT,
    htf.SHORT_WATCH: levels_module.RESISTANCE,
}


@dataclass(frozen=True)
class ObservationResult:
    """One evaluated Section 2 row - the OUTPUT RECORD shape from PART F,
    not yet persisted. Field names match `data.models.Observation`
    exactly (minus `id`), so a caller can build one from the other
    positionally.
    """

    asset: str
    evaluated_at: dt.datetime
    rule_version: str
    section_1_state: str
    section_1_watch: str
    section_1_grade: str | None
    section_1_level_price: float | None
    session_started_at: dt.datetime
    grade_at_start: str | None
    grade_history: list[dict]
    interaction_detected: bool
    reaction_tier: str | None
    reaction_condition_matched: str | None
    reaction_started_at: dt.datetime | None
    structure_reference_price: float | None
    structure_reference_confirmed_at: dt.datetime | None
    structure_change: str | None
    failure: str | None
    expiry: bool
    state: str
    reason_code: str
    swings_used: list[dict]


@dataclass
class _Session:
    """Mutable replay state for one observation session, scoped to one
    contiguous run of Section 1 WATCH under a single pinned (state,
    watch, grade) tuple. Ends the moment that tuple changes
    (HTF_CONTEXT_INVALIDATED) - nothing carries over into a new session.
    """

    watch: str
    pin: tuple[str, str, str | None]
    level: dict
    started_at: dt.datetime
    grade_at_start: str | None
    current_grade: str | None
    grade_history: list[dict] = field(default_factory=list)
    terminal: str | None = None
    interaction_occurred: bool = False
    reaction_tier: str | None = None
    reaction_condition: str | None = None
    reaction_started_at: dt.datetime | None = None
    candles_since_reaction: int = 0
    pivot_confirmed: bool = False
    pivot_swing: Swing | None = None


def evaluate(
    asset: str,
    section_1_history: list[JournalEntry],
    candles_1h: pd.DataFrame,
    rule_version: str = RULE_VERSION_V1,
) -> list[ObservationResult]:
    """Replay Section 2 over closed 1H candles, gated by Section 1 WATCH.

    `section_1_history` should be the asset's Section 1 journal rows (any
    order; sorted here). `candles_1h` must contain only closed 1H
    candles, ordered oldest to newest, already truncated to whatever "as
    of" point is being evaluated.

    `rule_version` selects which session-termination rule governs this
    replay - `RULE_VERSION_V1` (docs/rulebook/section-02-1h-behaviour-
    v0.1.md: a session ends the moment the pinned (state, watch, grade)
    tuple stops matching) or `RULE_VERSION_V2` (section-02-1h-behaviour-
    v0.2.md: a session ends only on a WATCH direction change, a WATCH
    disappearing, or the held level's price leaving the *original*
    level's zone - a grade change alone no longer ends it). Everything
    else - reaction tiers, structure confirmation, expiry - is identical
    between the two; only `_session_ended` and the grade bookkeeping
    below differ.

    Returns one row per 1H candle where a Section 2 observation applies -
    i.e. only while the as-of Section 1 record for that candle is
    LONG_WATCH or SHORT_WATCH (PART F: "one row per 1H close per asset
    under an active WATCH"). A candle with no as-of Section 1 record yet,
    or whose as-of record is WAIT, produces no row at all - that is what
    NO_HTF_CONTEXT means; it is never itself a journaled state.
    """
    if rule_version not in SUPPORTED_RULE_VERSIONS:
        raise ValueError(f"unsupported Section 2 rule_version: {rule_version!r}")

    if len(candles_1h) == 0:
        return []

    history = sorted(section_1_history, key=lambda e: e.evaluated_at)
    swings = find_swings(candles_1h, fractal_n=FRACTAL_N)
    atr_series = compute_atr(candles_1h, period=ATR_PERIOD)

    results: list[ObservationResult] = []
    session: _Session | None = None
    h_idx = -1

    for i in range(len(candles_1h)):
        candle = candles_1h.iloc[i]
        t = _to_datetime(candle["close_time"])

        while h_idx + 1 < len(history) and history[h_idx + 1].evaluated_at <= t:
            h_idx += 1
        if h_idx < 0:
            continue
        as_of = history[h_idx]

        if session is None:
            if as_of.watch not in _WATCH_ROLE:
                continue
            level = _pick_level(as_of.active_levels, _WATCH_ROLE[as_of.watch])
            if level is None:
                # WATCH is only ever graded when a major level already
                # holds (Section 1 decision matrix) - this should not
                # happen, but Section 2 never manufactures a level to
                # watch, so it simply waits rather than guessing one.
                continue
            session = _Session(
                watch=as_of.watch,
                pin=(as_of.state, as_of.watch, as_of.grade),
                level=level,
                started_at=t,
                grade_at_start=as_of.grade,
                current_grade=as_of.grade,
            )
        else:
            if as_of.grade != session.current_grade:
                session.grade_history = [
                    *session.grade_history,
                    {"evaluated_at": t.isoformat(), "grade": as_of.grade},
                ]
                session.current_grade = as_of.grade
            if _session_ended(rule_version, session, as_of):
                results.append(_invalidated_row(asset, t, as_of, session, rule_version))
                session = None
                continue

        prior = candles_1h.iloc[i - 1] if i > 0 else None
        atr_value = atr_series.iloc[i]
        results.append(
            _evaluate_candle(
                asset, t, as_of, session, candle, prior, swings, atr_value, rule_version
            )
        )

    return results


def _session_ended(rule_version: str, session: _Session, as_of: JournalEntry) -> bool:
    """Whether `as_of` ends the current session under `rule_version`.

    v0.1: the pinned (state, watch, grade) tuple must still match exactly
    (rulebook v0.1, HTF_CONTEXT_INVALIDATED).

    v0.2: grade is no longer part of this test (section-02-v0.2-
    justification.md, Part 2). A session ends only when the WATCH
    direction changes or disappears (`as_of.watch != session.watch`
    covers both - `session.watch` is never WAIT), or when the currently
    held major level of that role no longer falls inside the *original*
    pinned level's zone. `session.level` itself is never reassigned - it
    stays pinned to whatever was held at session start for the life of
    the session, exactly as v0.1 already did; only this containment
    check reads the newest as-of active_levels.
    """
    if rule_version == RULE_VERSION_V1:
        return (as_of.state, as_of.watch, as_of.grade) != session.pin

    if as_of.watch != session.watch:
        return True
    current_level = _pick_level(as_of.active_levels, _WATCH_ROLE[session.watch])
    if current_level is None:
        return True
    zone_low, zone_high = session.level["zone_low"], session.level["zone_high"]
    return not (zone_low <= current_level["price"] <= zone_high)


def _pick_level(active_levels: list[dict], role: str) -> dict | None:
    """The pinned reference level: max-touches among held levels of the
    matching role, per the as-of Section 1 record. Never recomputed -
    Section 1 is authoritative for WHERE (rulebook INTERACTION).
    """
    candidates = [lvl for lvl in active_levels if lvl.get("held") and lvl.get("role") == role]
    if not candidates:
        return None
    return max(candidates, key=lambda lvl: lvl["touches"])


def _base_fields(
    asset: str, t: dt.datetime, as_of: JournalEntry, session: _Session, rule_version: str
) -> dict:
    return {
        "asset": asset,
        "evaluated_at": t,
        "rule_version": rule_version,
        "section_1_state": as_of.state,
        "section_1_watch": as_of.watch,
        "section_1_grade": as_of.grade,
        "section_1_level_price": session.level["price"],
        "session_started_at": session.started_at,
        "grade_at_start": session.grade_at_start,
        "grade_history": list(session.grade_history),
    }


def _empty_row(base: dict, *, state: str) -> ObservationResult:
    return ObservationResult(
        **base,
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


def _invalidated_row(
    asset: str, t: dt.datetime, as_of: JournalEntry, session: _Session, rule_version: str
) -> ObservationResult:
    """The row that closes out `session`. Stamped with `session.started_at`
    (via `_base_fields`, unchanged) - this row belongs to the session it
    ends, not to a new one: `report.group_sessions`'s v0.2 path groups rows
    by `session_started_at` equality specifically so this closing row
    stays attached to its session, mirroring what v0.1's fixed grouping
    achieves by checking `prev.state == HTF_CONTEXT_INVALIDATED` (see
    `report._group_sessions_v1`/`_group_sessions_v2` and ADR 0008's Table 2
    correction). `_classify_session` then scans every row in the group for
    `structure_change`/`failure` rather than trusting the last row alone,
    so this row correctly closing out a resolved session doesn't hide that
    resolution.
    """
    return _empty_row(
        _base_fields(asset, t, as_of, session, rule_version), state=HTF_CONTEXT_INVALIDATED
    )


def _overlaps_zone(candle: pd.Series, level: dict) -> bool:
    """Whether this candle's own range intersects the pinned level's zone
    at all - the rulebook's INTERACTION test, reusing Section 1's zone
    bounds rather than a second tolerance (OPEN QUESTIONS (3)).
    """
    return candle["low"] <= level["zone_high"] and candle["high"] >= level["zone_low"]


def _correct_side(close: float, level_price: float, is_long: bool) -> bool:
    """Whether `close` sits on the side a reaction in this direction
    needs. Mirrors RULE 1.7a's own tie-break (close >= price -> SUPPORT)
    so a level exactly at the close price is never double-counted as
    both sides.
    """
    role_is_support = close >= level_price
    return role_is_support if is_long else not role_is_support


def _r1(candle: pd.Series, level: dict, is_long: bool) -> bool:
    if not _overlaps_zone(candle, level):
        return False
    price = level["price"]
    if is_long:
        return candle["close"] > candle["low"] and _correct_side(candle["close"], price, True)
    return candle["close"] < candle["high"] and _correct_side(candle["close"], price, False)


def _r2_condition(
    candle: pd.Series, prior: pd.Series | None, atr_value: float | None, is_long: bool
) -> str | None:
    if prior is not None:
        if candle["high"] >= prior["high"] and candle["low"] <= prior["low"]:
            return "R2_ENGULFING"
        if is_long and candle["close"] > prior["high"]:
            return "R2_CLOSE_BEYOND_PRIOR_EXTREME"
        if not is_long and candle["close"] < prior["low"]:
            return "R2_CLOSE_BEYOND_PRIOR_EXTREME"
    candle_range = candle["high"] - candle["low"]
    if atr_value is not None and candle_range >= R2_RANGE_ATR_MULTIPLIER * atr_value:
        return "R2_RANGE_GE_1_5_ATR"
    return None


def _r3(candle: pd.Series, level: dict, is_long: bool) -> bool:
    price = level["price"]
    if is_long:
        return candle["low"] < price and candle["close"] > price
    return candle["high"] > price and candle["close"] < price


def _detect_reaction(
    candle: pd.Series,
    prior: pd.Series | None,
    level: dict,
    atr_value: float | None,
    is_long: bool,
) -> tuple[str | None, str | None]:
    if _r3(candle, level, is_long):
        return R3, "R3_RECLAIM"
    if _r1(candle, level, is_long):
        extra = _r2_condition(candle, prior, atr_value, is_long)
        if extra is not None:
            return R2, extra
        return R1, "R1_BASIC"
    return None, None


def _swing_to_dict(swing: Swing) -> dict:
    return {
        "kind": swing.kind,
        "price": swing.price,
        "formed_at": swing.formed_at.isoformat(),
        "confirmed_at": swing.confirmed_at.isoformat(),
    }


def _evaluate_candle(
    asset: str,
    t: dt.datetime,
    as_of: JournalEntry,
    session: _Session,
    candle: pd.Series,
    prior: pd.Series | None,
    swings: list[Swing],
    atr_value: float | None,
    rule_version: str,
) -> ObservationResult:
    base = _base_fields(asset, t, as_of, session, rule_version)

    if session.terminal is not None:
        return _empty_row(base, state=session.terminal)

    is_long = session.watch == htf.LONG_WATCH
    level = session.level

    if session.interaction_occurred:
        if is_long and candle["close"] < level["zone_low"]:
            session.terminal = STAND_DOWN
            return _failure_row(base, session, SUPPORT_FAILURE)
        if not is_long and candle["close"] > level["zone_high"]:
            session.terminal = STAND_DOWN
            return _failure_row(base, session, RESISTANCE_FAILURE)

    if session.reaction_tier is not None:
        return _continue_reaction(base, session, candle, t, is_long, swings)

    tier, condition = _detect_reaction(candle, prior, level, atr_value, is_long)
    if tier is not None:
        session.reaction_tier = tier
        session.reaction_condition = condition
        session.reaction_started_at = t
        session.candles_since_reaction = 0
        session.pivot_confirmed = False
        session.pivot_swing = None
        session.interaction_occurred = True
        return ObservationResult(
            **base,
            interaction_detected=True,
            reaction_tier=tier,
            reaction_condition_matched=condition,
            reaction_started_at=t,
            structure_reference_price=None,
            structure_reference_confirmed_at=None,
            structure_change=None,
            failure=None,
            expiry=False,
            state=REACTION_DETECTED,
            reason_code=REACTION_DETECTED,
            swings_used=[],
        )

    if _overlaps_zone(candle, level):
        session.interaction_occurred = True
        return ObservationResult(
            **base,
            interaction_detected=True,
            reaction_tier=None,
            reaction_condition_matched=None,
            reaction_started_at=None,
            structure_reference_price=None,
            structure_reference_confirmed_at=None,
            structure_change=None,
            failure=None,
            expiry=False,
            state=CONTINUATION_CANDIDATE_NOT_EVALUATED,
            reason_code=CONTINUATION_CANDIDATE_NOT_EVALUATED,
            swings_used=[],
        )

    return _empty_row(base, state=NO_INTERACTION)


def _failure_row(base: dict, session: _Session, failure: str) -> ObservationResult:
    return ObservationResult(
        **base,
        interaction_detected=True,
        reaction_tier=session.reaction_tier,
        reaction_condition_matched=session.reaction_condition,
        reaction_started_at=session.reaction_started_at,
        structure_reference_price=None,
        structure_reference_confirmed_at=None,
        structure_change=None,
        failure=failure,
        expiry=False,
        state=failure,
        reason_code=failure,
        swings_used=[],
    )


def _continue_reaction(
    base: dict,
    session: _Session,
    candle: pd.Series,
    t: dt.datetime,
    is_long: bool,
    swings: list[Swing],
) -> ObservationResult:
    session.candles_since_reaction += 1
    confirmed_now = confirmed_swings_as_of(swings, t)

    ref_kind = HIGH if is_long else LOW
    started_at = session.reaction_started_at
    refs = sorted(
        (s for s in confirmed_now if s.kind == ref_kind and s.formed_at > started_at),
        key=lambda s: s.formed_at,
    )
    reference = refs[-1] if refs else None

    if not session.pivot_confirmed:
        _update_pivot(session, confirmed_now, is_long)

    if reference is not None and session.pivot_confirmed:
        beyond = candle["close"] > reference.price if is_long else candle["close"] < reference.price
        if beyond:
            session.terminal = HANDOFF_TO_15M
            change = BULLISH_STRUCTURE_CHANGE if is_long else BEARISH_STRUCTURE_CHANGE
            swings_used = [_swing_to_dict(reference)]
            if session.pivot_swing is not None:
                swings_used.append(_swing_to_dict(session.pivot_swing))
            return ObservationResult(
                **base,
                interaction_detected=True,
                reaction_tier=session.reaction_tier,
                reaction_condition_matched=session.reaction_condition,
                reaction_started_at=session.reaction_started_at,
                structure_reference_price=reference.price,
                structure_reference_confirmed_at=reference.confirmed_at,
                structure_change=change,
                failure=None,
                expiry=False,
                state=change,
                reason_code=change,
                swings_used=swings_used,
            )

    if session.candles_since_reaction >= REACTION_EXPIRY_CANDLES:
        reaction_tier = session.reaction_tier
        reaction_condition = session.reaction_condition
        reaction_started_at = session.reaction_started_at
        session.reaction_tier = None
        session.reaction_condition = None
        session.reaction_started_at = None
        session.candles_since_reaction = 0
        session.pivot_confirmed = False
        session.pivot_swing = None
        return ObservationResult(
            **base,
            interaction_detected=True,
            reaction_tier=reaction_tier,
            reaction_condition_matched=reaction_condition,
            reaction_started_at=reaction_started_at,
            structure_reference_price=reference.price if reference else None,
            structure_reference_confirmed_at=reference.confirmed_at if reference else None,
            structure_change=None,
            failure=None,
            expiry=True,
            state=REACTION_EXPIRED,
            reason_code=REACTION_EXPIRED,
            swings_used=[],
        )

    if reference is None:
        return ObservationResult(
            **base,
            interaction_detected=True,
            reaction_tier=session.reaction_tier,
            reaction_condition_matched=session.reaction_condition,
            reaction_started_at=session.reaction_started_at,
            structure_reference_price=None,
            structure_reference_confirmed_at=None,
            structure_change=None,
            failure=None,
            expiry=False,
            state=NO_STRUCTURAL_REFERENCE,
            reason_code=NO_STRUCTURAL_REFERENCE,
            swings_used=[],
        )

    swings_used = [_swing_to_dict(reference)]
    if session.pivot_swing is not None:
        swings_used.append(_swing_to_dict(session.pivot_swing))
    return ObservationResult(
        **base,
        interaction_detected=True,
        reaction_tier=session.reaction_tier,
        reaction_condition_matched=session.reaction_condition,
        reaction_started_at=session.reaction_started_at,
        structure_reference_price=reference.price,
        structure_reference_confirmed_at=reference.confirmed_at,
        structure_change=None,
        failure=None,
        expiry=False,
        state=REACTION_DETECTED,
        reason_code=REACTION_DETECTED,
        swings_used=swings_used,
    )


def _update_pivot(session: _Session, confirmed_now: list[Swing], is_long: bool) -> None:
    """Set `session.pivot_confirmed` the first time a confirmed higher
    low (long) / lower high (short) forms after the reaction started -
    Stage B's first confirmation step, sticky once true. Mirrors
    `context/htf.py`'s own higher/lower comparison: the newest confirmed
    swing of the pivot kind against the one immediately before it.
    """
    pivot_kind = LOW if is_long else HIGH
    started_at = session.reaction_started_at
    same_kind = sorted(
        (s for s in confirmed_now if s.kind == pivot_kind), key=lambda s: s.formed_at
    )
    after_reaction = [s for s in same_kind if s.formed_at > started_at]
    if not after_reaction:
        return
    candidate = after_reaction[-1]
    idx = same_kind.index(candidate)
    if idx == 0:
        return
    previous = same_kind[idx - 1]
    better = candidate.price > previous.price if is_long else candidate.price < previous.price
    if better:
        session.pivot_confirmed = True
        session.pivot_swing = candidate


def _to_datetime(value: object) -> dt.datetime:
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value  # type: ignore[return-value]
