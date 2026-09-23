"""Section 1 — HTF context (4H).

Implements `docs/rulebook/section-01-htf-context-v1.0.md`, which is
LOCKED. This module must implement exactly what that document specifies —
nothing more, nothing tuned, nothing inferred. If a future rulebook change
is needed, a new version file is added; this module then targets that new
version rather than editing the old one in place.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from tidemark.core import fib as fib_module
from tidemark.core import levels as levels_module
from tidemark.core.swings import HIGH, LOW, confirmed_swings_as_of, find_swings
from tidemark.data.models import ContextRecord, Level, Swing

RULE_VERSION = "section-01-v1.0"

# Structural bias states.
INSUFFICIENT_STRUCTURE = "INSUFFICIENT_STRUCTURE"
BULLISH = "BULLISH"
BEARISH = "BEARISH"
NEUTRAL = "NEUTRAL"
STRUCTURE_BROKEN_BULL = "STRUCTURE_BROKEN_BULL"
STRUCTURE_BROKEN_BEAR = "STRUCTURE_BROKEN_BEAR"
_BROKEN_STATES = (STRUCTURE_BROKEN_BULL, STRUCTURE_BROKEN_BEAR)

# Watch values.
WAIT = "WAIT"
LONG_WATCH = "LONG_WATCH"
SHORT_WATCH = "SHORT_WATCH"

# Reason codes. The rulebook names NOT_ENOUGH_SWINGS, STRUCTURE_BROKEN,
# NEUTRAL_STRUCTURE, FIB_ONLY, and NOT_IN_ZONE explicitly (matrix rows
# 1/2/3/8/9). Rows 4-7 (the WATCH rows) aren't given explicit codes in the
# rulebook text, so these four are this module's own diagnostic labels —
# they carry no decision weight beyond what `watch`/`grade` already encode.
NOT_ENOUGH_SWINGS = "NOT_ENOUGH_SWINGS"
STRUCTURE_BROKEN = "STRUCTURE_BROKEN"
NEUTRAL_STRUCTURE = "NEUTRAL_STRUCTURE"
MAJOR_SUPPORT_FIB = "MAJOR_SUPPORT_FIB"
MAJOR_SUPPORT = "MAJOR_SUPPORT"
MAJOR_RESISTANCE_FIB = "MAJOR_RESISTANCE_FIB"
MAJOR_RESISTANCE = "MAJOR_RESISTANCE"
FIB_ONLY = "FIB_ONLY"
NOT_IN_ZONE = "NOT_IN_ZONE"

_HORIZONTAL_LOOKBACK_CANDLES = 120


@dataclass
class _StateResult:
    """Result of replaying the structure/break state machine to the last candle."""

    state: str
    highs_used: list[Swing] = field(default_factory=list)
    lows_used: list[Swing] = field(default_factory=list)
    watched_level: float | None = None


def _compute_bias(highs: list[Swing], lows: list[Swing]) -> str:
    if len(highs) < 2 or len(lows) < 2:
        return INSUFFICIENT_STRUCTURE
    higher_high = highs[-1].price > highs[-2].price
    higher_low = lows[-1].price > lows[-2].price
    lower_high = highs[-1].price < highs[-2].price
    lower_low = lows[-1].price < lows[-2].price
    if higher_high and higher_low:
        return BULLISH
    if lower_high and lower_low:
        return BEARISH
    return NEUTRAL


def _watched_level_for(state: str, highs: list[Swing], lows: list[Swing]) -> float | None:
    if state == BULLISH and lows:
        return lows[-1].price
    if state == BEARISH and highs:
        return highs[-1].price
    return None


def run_state_machine(candles_4h: pd.DataFrame, swings: list[Swing]) -> _StateResult:
    """Replay the structure/break state machine over closed 4H candles.

    This is a full, deterministic replay from the start of `candles_4h` —
    not an incrementally persisted state — so the same (candles, swings)
    input always reproduces the same result regardless of when it is run
    (the look-ahead guard: truncating the inputs is the only thing that
    can change the answer).

    Bias is recomputed from the last 2 confirmed swing highs and lows on
    every candle where the state is not currently broken. A break enters
    when a 4H CLOSE (not a wick) passes beyond the last confirmed HL (in
    BULLISH) or LH (in BEARISH). A broken state persists until >= 1 new
    swing formed after the break confirms, at which point bias is
    recomputed fresh.

    STRUCTURE_BROKEN_BULL names the case where a BULLISH structure broke
    (close below the last confirmed HL); STRUCTURE_BROKEN_BEAR mirrors it
    for a broken BEARISH structure — the rulebook names the break by which
    structure it invalidated, not by the direction of the breaking close.
    """
    state = INSUFFICIENT_STRUCTURE
    watched_level: float | None = None
    break_at: dt.datetime | None = None
    highs_used: list[Swing] = []
    lows_used: list[Swing] = []

    for _, candle in candles_4h.iterrows():
        t = candle["close_time"]
        confirmed_now = confirmed_swings_as_of(swings, t)
        highs = sorted((s for s in confirmed_now if s.kind == HIGH), key=lambda s: s.formed_at)[-2:]
        lows = sorted((s for s in confirmed_now if s.kind == LOW), key=lambda s: s.formed_at)[-2:]

        if state in _BROKEN_STATES:
            new_swing_confirmed = any(
                s.formed_at > break_at and s.confirmed_at <= t for s in swings
            )
            if new_swing_confirmed:
                state = _compute_bias(highs, lows)
                highs_used, lows_used = highs, lows
                watched_level = _watched_level_for(state, highs, lows)
                break_at = None
        else:
            state = _compute_bias(highs, lows)
            highs_used, lows_used = highs, lows
            watched_level = _watched_level_for(state, highs, lows)

        close = candle["close"]
        if state == BULLISH and watched_level is not None and close < watched_level:
            state = STRUCTURE_BROKEN_BULL
            break_at = t
        elif state == BEARISH and watched_level is not None and close > watched_level:
            state = STRUCTURE_BROKEN_BEAR
            break_at = t

    return _StateResult(
        state=state, highs_used=highs_used, lows_used=lows_used, watched_level=watched_level
    )


def _holds_zone(candle: pd.Series, level: Level, *, is_support: bool) -> bool:
    """Whether the candle holds a level: low/high touches the zone and the
    close sits at/beyond the zone's near edge (Section 1 definition)."""
    if is_support:
        touched = level.zone_low <= candle["low"] <= level.zone_high
        return touched and candle["close"] >= level.zone_low
    touched = level.zone_low <= candle["high"] <= level.zone_high
    return touched and candle["close"] <= level.zone_high


def _level_to_dict(level: Level) -> dict:
    return {
        "kind": level.kind,
        "price": level.price,
        "zone_low": level.zone_low,
        "zone_high": level.zone_high,
        "touches": level.touches,
        "is_major": level.is_major,
        "source": level.source,
        "formed_at": level.formed_at.isoformat(),
    }


def _swing_to_dict(swing: Swing) -> dict:
    return {
        "kind": swing.kind,
        "price": swing.price,
        "formed_at": swing.formed_at.isoformat(),
        "confirmed_at": swing.confirmed_at.isoformat(),
    }


def _fib_to_dict(leg: fib_module.FibLeg | None, price: float) -> dict:
    if leg is None:
        return {}
    return {
        "direction": leg.direction,
        "anchor_start": leg.anchor_start,
        "anchor_end": leg.anchor_end,
        "valid_from": leg.valid_from.isoformat(),
        "invalidated_at": leg.invalidated_at.isoformat() if leg.invalidated_at else None,
        "level_500": leg.level_500,
        "level_618": leg.level_618,
        "level_786": leg.level_786,
        "zone_low": leg.zone_low,
        "zone_high": leg.zone_high,
        "nearest_level": fib_module.nearest_fib_level(price, leg),
    }


def evaluate(
    asset: str,
    candles_4h: pd.DataFrame,
    atr_value: float | None,
    candles_1d: pd.DataFrame | None = None,
    candles_1w: pd.DataFrame | None = None,
) -> ContextRecord:
    """Evaluate Section 1's decision matrix against closed 4H candles.

    Recalculated at every 4H close. `candles_4h`, `candles_1d`, and
    `candles_1w` must contain closed candles only, ordered oldest to
    newest, already truncated to whatever "as of" point is being
    evaluated — this function looks only at the rows it is given, so
    truncating the inputs is what the look-ahead guard relies on.

    `atr_value` is ATR(14) on `candles_4h` as of its last row. `None`
    means fewer than 14 4H candles exist yet, which this function treats
    as INSUFFICIENT_STRUCTURE / NOT_ENOUGH_SWINGS, since every downstream
    calculation (levels, fib, zone tolerances) is expressed in units of
    it.
    """
    if len(candles_4h) == 0:
        raise ValueError("candles_4h must contain at least one closed candle")

    evaluated_at = candles_4h["close_time"].iloc[-1]
    latest_candle = candles_4h.iloc[-1]

    if atr_value is None:
        return ContextRecord(
            asset=asset,
            evaluated_at=evaluated_at,
            rule_version=RULE_VERSION,
            state=INSUFFICIENT_STRUCTURE,
            watch=WAIT,
            grade=None,
            reason_code=NOT_ENOUGH_SWINGS,
            active_levels=[],
            fib={},
            swings_used=[],
        )

    swings = find_swings(candles_4h, fractal_n=2)
    confirmed = confirmed_swings_as_of(swings, evaluated_at)
    result = run_state_machine(candles_4h, swings)

    lookback = candles_4h.tail(_HORIZONTAL_LOOKBACK_CANDLES)
    lookback_start = lookback["close_time"].iloc[0]
    swings_in_lookback = [s for s in confirmed if s.formed_at >= lookback_start]
    active_levels = list(levels_module.cluster_swings_into_levels(swings_in_lookback, atr_value))

    if candles_1d is not None and len(candles_1d) > 0:
        active_levels += levels_module.prev_period_levels(candles_1d, "day", atr_value)
    if candles_1w is not None and len(candles_1w) > 0:
        active_levels += levels_module.prev_period_levels(candles_1w, "week", atr_value)

    holds_major_support = any(
        level.kind == levels_module.SUPPORT
        and level.is_major
        and _holds_zone(latest_candle, level, is_support=True)
        for level in active_levels
    )
    holds_major_resistance = any(
        level.kind == levels_module.RESISTANCE
        and level.is_major
        and _holds_zone(latest_candle, level, is_support=False)
        for level in active_levels
    )

    leg: fib_module.FibLeg | None = None
    price_in_fib_zone = False
    if result.state == BULLISH:
        leg = fib_module.find_valid_leg(confirmed, atr_value, fib_module.BULLISH, candles_4h)
    elif result.state == BEARISH:
        leg = fib_module.find_valid_leg(confirmed, atr_value, fib_module.BEARISH, candles_4h)
    if leg is not None and leg.invalidated_at is None:
        price_in_fib_zone = fib_module.in_fib_zone(latest_candle["close"], leg)

    state = result.state
    if state == INSUFFICIENT_STRUCTURE:
        watch, grade, reason_code = WAIT, None, NOT_ENOUGH_SWINGS
    elif state in _BROKEN_STATES:
        watch, grade, reason_code = WAIT, None, STRUCTURE_BROKEN
    elif state == NEUTRAL:
        watch, grade, reason_code = WAIT, None, NEUTRAL_STRUCTURE
    elif state == BULLISH and holds_major_support and price_in_fib_zone:
        watch, grade, reason_code = LONG_WATCH, "A", MAJOR_SUPPORT_FIB
    elif state == BULLISH and holds_major_support:
        watch, grade, reason_code = LONG_WATCH, "B", MAJOR_SUPPORT
    elif state == BEARISH and holds_major_resistance and price_in_fib_zone:
        watch, grade, reason_code = SHORT_WATCH, "A", MAJOR_RESISTANCE_FIB
    elif state == BEARISH and holds_major_resistance:
        watch, grade, reason_code = SHORT_WATCH, "B", MAJOR_RESISTANCE
    elif price_in_fib_zone:
        watch, grade, reason_code = WAIT, None, FIB_ONLY
    else:
        watch, grade, reason_code = WAIT, None, NOT_IN_ZONE

    swings_used = [_swing_to_dict(s) for s in (*result.highs_used, *result.lows_used)]

    return ContextRecord(
        asset=asset,
        evaluated_at=evaluated_at,
        rule_version=RULE_VERSION,
        state=state,
        watch=watch,
        grade=grade,
        reason_code=reason_code,
        active_levels=[_level_to_dict(level) for level in active_levels],
        fib=_fib_to_dict(leg, latest_candle["close"]),
        swings_used=swings_used,
    )
