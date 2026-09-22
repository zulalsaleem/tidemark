"""Horizontal levels and zones.

Section 1 parameters: swing cluster distance = 0.5 x ATR, level/zone
tolerance = 0.25 x ATR, horizontal lookback = 120 x 4H candles (~20 days),
major level = >=2 touches OR weekly high/low.
"""

from __future__ import annotations

from tidemark.data.models import Level, Swing


def cluster_swings_into_levels(swings: list[Swing], atr_value: float) -> list[Level]:
    """Group nearby swings into horizontal levels/zones.

    Not implemented in Phase 0.
    """
    raise NotImplementedError


def prev_period_high_low(candles, period: str) -> tuple[float, float]:
    """Return (high, low) for the previous completed day or week.

    `period` must be "day" or "week".

    Not implemented in Phase 0.
    """
    raise NotImplementedError
