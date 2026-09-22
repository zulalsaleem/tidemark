"""Retracement legs and fibonacci zones.

Section 1 parameters: minimum Fib leg = 2 x ATR, Fib zone = 0.500-0.786
(logged against the nearest level). A leg's zone becomes invalid once
price/structure invalidates it — see the `invalidated_at` field on the
output record's `fib` block.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class FibLeg:
    """A retracement leg anchored between two swing points."""

    anchor_start: float
    anchor_end: float
    valid_from: dt.datetime
    invalidated_at: dt.datetime | None


def find_valid_leg(swings: list, atr_value: float) -> FibLeg | None:
    """Find the current valid retracement leg, if any.

    A leg qualifies only if its range is at least `2 * atr_value`.

    Not implemented in Phase 0.
    """
    raise NotImplementedError


def in_fib_zone(price: float, leg: FibLeg) -> bool:
    """Return whether `price` falls within the 0.500-0.786 retracement zone.

    Not implemented in Phase 0.
    """
    raise NotImplementedError
