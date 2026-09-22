"""Canonical timeframe set for Tidemark's data layer.

Per Rulebook Section 1, the 4H timeframe decides context and the distance
unit (ATR) is measured on 4H; 1H is handled separately in Section 2. 1D
and 1W are stored for the previous-day/previous-week high/low inputs that
Section 1's level logic will need.
"""

from __future__ import annotations

import datetime as dt

TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "1h", "4h", "1d", "1w")

TIMEFRAME_DURATIONS: dict[str, dt.timedelta] = {
    "5m": dt.timedelta(minutes=5),
    "15m": dt.timedelta(minutes=15),
    "1h": dt.timedelta(hours=1),
    "4h": dt.timedelta(hours=4),
    "1d": dt.timedelta(days=1),
    "1w": dt.timedelta(weeks=1),
}
