"""Section 1 eligibility assessment (Phase 6, Merge 2B, PART C).

UNIV-01 (docs/adr/0009-universe-selection-architecture.md): eligibility
is "has Section 1 demonstrably exited INSUFFICIENT_STRUCTURE at least
once" — never a calendar-history requirement, since the Phase 6
preflight found no day-count derivable from the rulebook's own
parameters (2 confirmed swing highs AND 2 confirmed swing lows is
price-dependent and unbounded above). Eligibility is NOT "has produced a
trade" and NOT "is currently in a WATCH".

Runs the LOCKED Section 1 v1.1 engine unmodified via
`replay.report.replay_section1` (itself a thin point-in-time wrapper
around `context.htf.evaluate`) — this module reimplements no rule,
parameter, or threshold. It is a pure function of already-truncated
candle lists: no store access, no network, so it is directly testable
against fixtures, and is itself lookahead-safe by construction (truncate
first, then evaluate — the same guard `tests/context/
test_look_ahead_guard.py` exercises for a single evaluation).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from tidemark.context import htf
from tidemark.data.models import Candle
from tidemark.data.timeframes import TIMEFRAME_DURATIONS
from tidemark.replay.report import replay_section1

# ATR(14)'s own mechanical floor (core/atr.py) — the one calendar/candle-
# count fact the Phase 6 preflight found actually derivable from the
# rulebook's parameters, unlike the unbounded "2 confirmed swing highs
# AND 2 confirmed swing lows" requirement. Fewer 4H candles than this and
# Section 1 cannot produce anything but INSUFFICIENT_STRUCTURE — not
# because it never exits, but because it was never given enough to try.
MIN_4H_CANDLES_FOR_ATR = 14

INSUFFICIENT_VOLUME_HISTORY = "INSUFFICIENT_VOLUME_HISTORY"
INSUFFICIENT_4H_HISTORY = "INSUFFICIENT_4H_HISTORY"
NEVER_EXITED_INSUFFICIENT_STRUCTURE = "NEVER_EXITED_INSUFFICIENT_STRUCTURE"
DATA_GAPS = "DATA_GAPS"
INVALID_OHLCV = "INVALID_OHLCV"
NOT_ASSESSED = "NOT_ASSESSED"
ABSENT_FROM_VENUE = "ABSENT_FROM_VENUE"

# The full shared exclusion-reason vocabulary (PART C). A row this module
# produces can only ever carry INSUFFICIENT_4H_HISTORY, NEVER_EXITED_
# INSUFFICIENT_STRUCTURE, DATA_GAPS, or INVALID_OHLCV (or None, if
# eligible) — INSUFFICIENT_VOLUME_HISTORY and NOT_ASSESSED are assigned
# one level up, before a symbol ever reaches this function (see
# data/universe_snapshot.py), and ABSENT_FROM_VENUE cannot occur here at
# all: only ACTIVE registry symbols are ever ranked and assessed. It is
# listed here for completeness with the registry's own status vocabulary.
EXCLUSION_REASONS: tuple[str, ...] = (
    INSUFFICIENT_VOLUME_HISTORY,
    INSUFFICIENT_4H_HISTORY,
    NEVER_EXITED_INSUFFICIENT_STRUCTURE,
    DATA_GAPS,
    INVALID_OHLCV,
    NOT_ASSESSED,
    ABSENT_FROM_VENUE,
)


@dataclass(frozen=True)
class EligibilityAssessment:
    """One symbol's Section 1 eligibility outcome, as of a point in time."""

    eligible: bool
    exclusion_reason: str | None
    section1_first_usable_at: dt.datetime | None


def _has_gaps(candles: list[Candle], duration: dt.timedelta) -> bool:
    return any(
        curr.open_time - prev.open_time != duration
        for prev, curr in zip(candles, candles[1:], strict=False)
    )


def assess_section1_eligibility(
    symbol: str,
    candles_4h: list[Candle],
    candles_1d: list[Candle],
    candles_1w: list[Candle],
    as_of: dt.datetime,
    rejected_4h_count: int,
) -> EligibilityAssessment:
    """Assess one symbol's Section 1 eligibility as of `as_of`.

    `candles_4h`/`candles_1d`/`candles_1w` must already be truncated by
    the caller to `close_time <= as_of` (see
    `data/universe_snapshot.py`) — this function performs no store
    access and trusts its inputs completely, which is what keeps it a
    pure, directly-testable function. `rejected_4h_count` is the count of
    rejected 4H candles for this symbol as of the same `as_of` point
    (see `TidemarkStore.count_rejected_candles`).

    Checks run in this order, first match wins:
      1. Fewer than `MIN_4H_CANDLES_FOR_ATR` 4H candles ->
         INSUFFICIENT_4H_HISTORY.
      2. Any rejected 4H candle on record -> INVALID_OHLCV.
      3. A gap in the 4H candle sequence -> DATA_GAPS.
      4. Section 1 (replayed point-in-time, unmodified) never exits
         INSUFFICIENT_STRUCTURE -> NEVER_EXITED_INSUFFICIENT_STRUCTURE.
      5. Otherwise eligible, with `section1_first_usable_at` set to the
         `evaluated_at` of the first exit.
    """
    if len(candles_4h) < MIN_4H_CANDLES_FOR_ATR:
        return EligibilityAssessment(False, INSUFFICIENT_4H_HISTORY, None)

    if rejected_4h_count > 0:
        return EligibilityAssessment(False, INVALID_OHLCV, None)

    if _has_gaps(candles_4h, TIMEFRAME_DURATIONS["4h"]):
        return EligibilityAssessment(False, DATA_GAPS, None)

    # Stop as soon as the first exit is found rather than replaying the
    # rest of the stored history for an answer this function no longer
    # needs - in practice this happens almost immediately after ATR(14)
    # warms up, so this keeps assessing one symbol fast regardless of how
    # much 4H history it has stored.
    records = replay_section1(
        {"4h": candles_4h, "1d": candles_1d, "1w": candles_1w},
        symbol,
        stop_when=lambda r: r.state != htf.INSUFFICIENT_STRUCTURE,
    )
    first_exit = (
        records[-1] if records and records[-1].state != htf.INSUFFICIENT_STRUCTURE else None
    )
    if first_exit is None:
        return EligibilityAssessment(False, NEVER_EXITED_INSUFFICIENT_STRUCTURE, None)

    return EligibilityAssessment(True, None, first_exit.evaluated_at)
