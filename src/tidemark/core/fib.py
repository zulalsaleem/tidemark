"""Retracement legs and fibonacci zones.

Section 1 parameters: minimum Fib leg = 2 x ATR, Fib zone = 0.500-0.786
(logged against the nearest level). A leg's zone becomes invalid once
price/structure invalidates it — see the `invalidated_at` field on the
output record's `fib` block.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from tidemark.core.swings import HIGH, LOW
from tidemark.data.models import Swing

BULLISH = "bullish"
BEARISH = "bearish"

_MIN_LEG_ATR = 2.0
_ZONE_EXTENSION_ATR = 0.25


@dataclass(frozen=True)
class FibLeg:
    """A retracement leg anchored between two confirmed swing points.

    `anchor_start` is the leg's origin price (100% retracement point);
    `anchor_end` is the swing price the leg runs to (0% retracement
    point) — for a bullish leg that's the swing low and swing high
    respectively, mirrored for a bearish leg.
    """

    direction: str
    anchor_start: float
    anchor_end: float
    valid_from: dt.datetime
    level_500: float
    level_618: float
    level_786: float
    zone_low: float
    zone_high: float
    invalidated_at: dt.datetime | None = None


def find_valid_leg(
    swings: list[Swing],
    atr_value: float,
    direction: str,
    candles_4h: pd.DataFrame | None = None,
) -> FibLeg | None:
    """Find the current retracement leg for `direction`, if any.

    Bullish: from the confirmed swing low preceding the most recent
    confirmed swing high, up to that high. Bearish is the mirror. A leg
    qualifies only if its range is at least `2 * atr_value`, else there
    is no leg.

    `valid_from` is the confirmed_at of the swing anchoring `anchor_end`
    (the leg's endpoint). If `candles_4h` (closed 4H candles) is given,
    `invalidated_at` is set to the close time of the first 4H candle at
    or after `valid_from` whose close is beyond `anchor_start` (a 100%
    retracement).
    """
    if direction not in (BULLISH, BEARISH):
        raise ValueError(f"direction must be '{BULLISH}' or '{BEARISH}', got {direction!r}")

    end_kind = HIGH if direction == BULLISH else LOW
    start_kind = LOW if direction == BULLISH else HIGH

    ends = sorted((s for s in swings if s.kind == end_kind), key=lambda s: s.formed_at)
    if not ends:
        return None
    end_swing = ends[-1]

    starts = [s for s in swings if s.kind == start_kind and s.formed_at < end_swing.formed_at]
    if not starts:
        return None
    start_swing = max(starts, key=lambda s: s.formed_at)

    anchor_start = start_swing.price
    anchor_end = end_swing.price
    leg_range = abs(anchor_end - anchor_start)
    if leg_range < _MIN_LEG_ATR * atr_value:
        return None

    if direction == BULLISH:
        level_500 = anchor_end - 0.500 * leg_range
        level_618 = anchor_end - 0.618 * leg_range
        level_786 = anchor_end - 0.786 * leg_range
        zone_low = level_786 - _ZONE_EXTENSION_ATR * atr_value
        zone_high = level_500 + _ZONE_EXTENSION_ATR * atr_value
    else:
        level_500 = anchor_end + 0.500 * leg_range
        level_618 = anchor_end + 0.618 * leg_range
        level_786 = anchor_end + 0.786 * leg_range
        zone_low = level_500 - _ZONE_EXTENSION_ATR * atr_value
        zone_high = level_786 + _ZONE_EXTENSION_ATR * atr_value

    valid_from = end_swing.confirmed_at
    invalidated_at = None
    if candles_4h is not None:
        invalidated_at = find_invalidation(direction, anchor_start, valid_from, candles_4h)

    return FibLeg(
        direction=direction,
        anchor_start=anchor_start,
        anchor_end=anchor_end,
        valid_from=valid_from,
        level_500=level_500,
        level_618=level_618,
        level_786=level_786,
        zone_low=zone_low,
        zone_high=zone_high,
        invalidated_at=invalidated_at,
    )


def find_invalidation(
    direction: str,
    anchor_start: float,
    valid_from: dt.datetime,
    candles_4h: pd.DataFrame,
) -> dt.datetime | None:
    """Return the close time of the first 4H close beyond `anchor_start`.

    A leg is invalidated by a 4H CLOSE (not a wick) that retraces 100% of
    the leg, i.e. closes beyond the leg's starting anchor.
    """
    subsequent = candles_4h[candles_4h["close_time"] >= valid_from]
    if direction == BULLISH:
        breaches = subsequent[subsequent["close"] < anchor_start]
    else:
        breaches = subsequent[subsequent["close"] > anchor_start]
    if breaches.empty:
        return None
    return breaches["close_time"].iloc[0]


def in_fib_zone(price: float, leg: FibLeg) -> bool:
    """Return whether `price` falls within the leg's 0.500-0.786 zone."""
    return leg.zone_low <= price <= leg.zone_high


def nearest_fib_level(price: float, leg: FibLeg) -> str:
    """Return which of 0.500/0.618/0.786 `price` is closest to (as a str)."""
    candidates = {
        "0.500": leg.level_500,
        "0.618": leg.level_618,
        "0.786": leg.level_786,
    }
    return min(candidates, key=lambda label: abs(price - candidates[label]))
