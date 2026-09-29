"""Nine-state classifier for docs/rulebook/position-flow-v0.1.md.

Every threshold below is copied verbatim from that document - this
module defines none of its own, per CLAUDE.md's standing rule against
inventing or tuning rulebook behavior. Deliberately independent from
`derivatives_classifier.py` (a different, independently versioned
rulebook, read by a different consumer - the hourly BTC briefing, not
`/coin`): this module imports nothing from it and defines its own
`PriceInput`/`OpenInterestInput` shapes, even though they happen to look
similar, so the two rulebooks can never be coupled by a shared type.

Pure functions only: this module takes plain values in and returns a
plain `PositionFlowResult` - no I/O, no network, no database, and
notably no funding/long-short/liquidations/buy-sell input at all, since
position-flow-v0.1 defines none of them as classifier inputs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from tidemark.market_intel.models import MARKET_NOT_FOUND, NO_DATA, OK

RULEBOOK_VERSION = "position-flow-v0.1"

# -- rulebook thresholds (docs/rulebook/position-flow-v0.1.md) ---------------

PRICE_UP_THRESHOLD_PCT = 0.25
PRICE_DOWN_THRESHOLD_PCT = -0.25
OI_UP_THRESHOLD_PCT = 0.25
OI_DOWN_THRESHOLD_PCT = -0.25

UP, DOWN, FLAT = "UP", "DOWN", "FLAT"
NO_MATCH = "NO_MATCH"

# All nine (price, oi) combinations are defined - unlike
# derivatives-context-v0.1, there is no undefined combination here.
_STATES: dict[tuple[str, str], str] = {
    (UP, UP): "LONG_BUILDUP",
    (UP, FLAT): "PRICE_RISE_NO_OI_EXPANSION",
    (UP, DOWN): "SHORT_COVERING",
    (FLAT, UP): "NEW_PARTICIPATION",
    (FLAT, FLAT): "QUIET",
    (FLAT, DOWN): "DELEVERAGING",
    (DOWN, UP): "SHORT_BUILDUP",
    (DOWN, FLAT): "PRICE_FALL_NO_OI_EXPANSION",
    (DOWN, DOWN): "LONG_UNWIND",
}


def classify_price(change_pct: float) -> str:
    if change_pct > PRICE_UP_THRESHOLD_PCT:
        return UP
    if change_pct < PRICE_DOWN_THRESHOLD_PCT:
        return DOWN
    return FLAT


def classify_oi(change_pct: float) -> str:
    if change_pct > OI_UP_THRESHOLD_PCT:
        return UP
    if change_pct < OI_DOWN_THRESHOLD_PCT:
        return DOWN
    return FLAT


# -- inputs -------------------------------------------------------------------


@dataclass(frozen=True)
class PriceInput:
    """The closed-1H price change (%) that feeds the classifier.

    `status` is one of the Merge 1 status constants (`OK`/`NO_DATA`/
    `MARKET_NOT_FOUND`) - a non-`OK` status always yields NO_MATCH,
    never a guess.
    """

    status: str
    change_pct: float | None
    period_start: dt.datetime | None
    period_close: dt.datetime | None
    reason: str | None = None


@dataclass(frozen=True)
class OpenInterestInput:
    status: str
    change_pct: float | None
    period_start: dt.datetime | None
    period_close: dt.datetime | None
    reason: str | None = None


@dataclass(frozen=True)
class PositionFlowResult:
    result: str  # one of the nine states above, or NO_MATCH
    reason: str | None  # populated only for NO_MATCH; None for a match
    price: PriceInput
    open_interest: OpenInterestInput
    rulebook_version: str = RULEBOOK_VERSION


def classify(price: PriceInput, oi: OpenInterestInput) -> PositionFlowResult:
    """Classify one closed-1H reading into one of the nine states, or
    NO_MATCH if either input is missing/unavailable - never a guess,
    never a substituted zero, never a classification from whichever
    input happens to be available. See
    docs/rulebook/position-flow-v0.1.md.
    """
    missing = [name for name, inp in (("price", price), ("open interest", oi)) if inp.status != OK]
    if missing:
        reason = f"missing/unavailable input(s): {', '.join(missing)}"
        return PositionFlowResult(NO_MATCH, reason, price, oi)

    price_state = classify_price(price.change_pct)
    oi_state = classify_oi(oi.change_pct)
    result = _STATES[(price_state, oi_state)]  # always present - all 9 combos defined
    return PositionFlowResult(result, None, price, oi)


__all__ = [
    "DOWN",
    "FLAT",
    "MARKET_NOT_FOUND",
    "NO_DATA",
    "NO_MATCH",
    "RULEBOOK_VERSION",
    "UP",
    "OpenInterestInput",
    "PositionFlowResult",
    "PriceInput",
    "classify",
    "classify_oi",
    "classify_price",
]
