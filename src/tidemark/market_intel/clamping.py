"""Closed-period boundary computation.

Coinalyze does NOT truncate history requests to closed periods: asking
for data `to=now` returns a bucket that is still accumulating (verified
live - see the Coinalyze inspection report and docs/adr/0011). Every
metric here must be evaluated against its own latest COMPLETED period,
per CLAUDE.md's "closed candles only" standing rule, so this client
computes the boundary itself rather than trusting the API's `to`.

The rule, for period length `S` seconds and the current instant `now`:
the latest closed period is `[floor(now, S) - S, floor(now, S))`, where
`floor` aligns to UTC-epoch-aligned multiples of `S` (exact for every
interval Coinalyze offers - 1min through daily all divide the UTC day
evenly). At 10:37 UTC, the 1h period is 09:00-10:00, never 10:00-11:00;
the 4h period is 04:00-08:00, never 08:00-12:00. At exactly a boundary
(e.g. 10:00:00.000000), the period that just closed is used, not the one
that starts that instant.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

# Coinalyze's documented interval/granularity enum, in seconds.
INTERVAL_SECONDS: dict[str, int] = {
    "1min": 60,
    "5min": 300,
    "15min": 900,
    "30min": 1800,
    "1hour": 3600,
    "2hour": 7200,
    "4hour": 14400,
    "6hour": 21600,
    "12hour": 43200,
    "daily": 86400,
}


@dataclass(frozen=True)
class ClosedPeriod:
    """A half-open UTC interval `[start, close)` that has fully elapsed
    as of the `now` it was computed against."""

    start: dt.datetime
    close: dt.datetime


def closed_period(now: dt.datetime, interval: str) -> ClosedPeriod:
    """The latest fully-closed period of `interval` length as of `now`.

    `now` must be a timezone-aware UTC datetime - naive datetimes are
    rejected rather than silently assumed to be UTC, since a silent
    assumption here would be exactly the kind of look-ahead bug this
    function exists to prevent.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware (UTC)")
    if interval not in INTERVAL_SECONDS:
        raise ValueError(f"unknown interval {interval!r}")

    interval_seconds = INTERVAL_SECONDS[interval]
    now_epoch = now.timestamp()
    close_epoch = (int(now_epoch) // interval_seconds) * interval_seconds
    start_epoch = close_epoch - interval_seconds
    return ClosedPeriod(
        start=dt.datetime.fromtimestamp(start_epoch, tz=dt.UTC),
        close=dt.datetime.fromtimestamp(close_epoch, tz=dt.UTC),
    )
