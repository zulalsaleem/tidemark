"""Horizontal levels and zones.

Section 1 parameters: swing cluster distance = 0.5 x ATR, level/zone
tolerance = 0.25 x ATR, horizontal lookback = 120 x 4H candles (~20 days),
major level = >=2 touches OR weekly high/low.
"""

from __future__ import annotations

import pandas as pd

from tidemark.core.swings import HIGH, LOW
from tidemark.data.models import Level, Swing

SUPPORT = "support"
RESISTANCE = "resistance"

_CLUSTER_DISTANCE_ATR = 0.5
_ZONE_TOLERANCE_ATR = 0.25


def cluster_swings_into_levels(swings: list[Swing], atr_value: float) -> list[Level]:
    """Group nearby confirmed swings into horizontal levels/zones.

    A level requires >= 2 confirmed swing points within `0.5 * atr_value`
    of each other (Section 1 PARAMETERS: swing cluster distance). Swing
    highs and swing lows are clustered separately, so a level is always
    unambiguously a resistance (from highs) or a support (from lows).

    `swings` should already be restricted by the caller to confirmed
    swings within the rulebook's horizontal lookback (120 x 4H candles) —
    this function does not filter by time itself.

    Level price is the mean of the cluster's member prices. The level's
    zone is `price +/- 0.25 * atr_value` (Section 1 PARAMETERS: level/zone
    tolerance), using the same `atr_value` passed in. A cluster's `touches`
    is its member count; since a cluster requires >= 2 members to exist at
    all, every swing-cluster level is major by definition
    (major = touches >= 2 OR weekly high/low).
    """
    levels: list[Level] = []
    for kind, level_kind in ((HIGH, RESISTANCE), (LOW, SUPPORT)):
        members = sorted((s for s in swings if s.kind == kind), key=lambda s: s.price)
        levels.extend(_cluster_same_kind(members, level_kind, atr_value))
    return levels


def _cluster_same_kind(members: list[Swing], level_kind: str, atr_value: float) -> list[Level]:
    clusters: list[list[Swing]] = []
    current: list[Swing] = []
    threshold = _CLUSTER_DISTANCE_ATR * atr_value

    for swing in members:
        if current and swing.price - current[-1].price > threshold:
            clusters.append(current)
            current = []
        current.append(swing)
    if current:
        clusters.append(current)

    levels: list[Level] = []
    zone_tolerance = _ZONE_TOLERANCE_ATR * atr_value
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        price = sum(s.price for s in cluster) / len(cluster)
        levels.append(
            Level(
                kind=level_kind,
                price=price,
                zone_low=price - zone_tolerance,
                zone_high=price + zone_tolerance,
                touches=len(cluster),
                is_major=True,
                source="swing_cluster",
                formed_at=max(s.confirmed_at for s in cluster),
            )
        )
    return levels


def prev_period_high_low(candles: pd.DataFrame, period: str) -> tuple[float, float]:
    """Return (high, low) for the previous completed day or week.

    `period` must be "day" or "week". `candles` must contain only closed
    1d/1w candles ordered oldest to newest; the previous period is the
    most recent row, and there is no in-progress-period row to exclude
    because Tidemark only ever stores closed candles.
    """
    if period not in ("day", "week"):
        raise ValueError(f"period must be 'day' or 'week', got {period!r}")
    if len(candles) == 0:
        raise ValueError("no candles available to determine previous period high/low")

    last = candles.iloc[-1]
    return float(last["high"]), float(last["low"])


def prev_period_levels(candles: pd.DataFrame, period: str, atr_value: float) -> list[Level]:
    """Build the previous day/week high and low as `Level` records.

    Major = weekly high/low unconditionally (Section 1 PARAMETERS); a
    previous day high/low alone has a single touch and is not major.
    """
    high, low = prev_period_high_low(candles, period)
    formed_at = candles["close_time"].iloc[-1]
    is_major = period == "week"
    zone_tolerance = _ZONE_TOLERANCE_ATR * atr_value

    return [
        Level(
            kind=RESISTANCE,
            price=high,
            zone_low=high - zone_tolerance,
            zone_high=high + zone_tolerance,
            touches=1,
            is_major=is_major,
            source=f"prev_{period}_high",
            formed_at=formed_at,
        ),
        Level(
            kind=SUPPORT,
            price=low,
            zone_low=low - zone_tolerance,
            zone_high=low + zone_tolerance,
            touches=1,
            is_major=is_major,
            source=f"prev_{period}_low",
            formed_at=formed_at,
        ),
    ]
