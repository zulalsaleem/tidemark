"""D1-D6 classifier for docs/rulebook/derivatives-context-v0.1.md.

Every threshold below is copied verbatim from that document - this
module defines none of its own, per CLAUDE.md's standing rule against
inventing or tuning rulebook behavior. If the document's thresholds ever
change, this module changes to match (a new rulebook version, reviewed
on its own), never the other way around.

Pure functions only: this module takes plain values in and returns a
plain `ClassificationResult` - no I/O, no network, no database. Fetching
the inputs (`briefing_data.py`) and deciding what to do with the result
(`briefing.py`) are separate, deliberately, so this module can be
exhaustively unit-tested against fixture inputs alone.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from tidemark.market_intel.models import MARKET_NOT_FOUND, NO_DATA, OK

RULEBOOK_VERSION = "derivatives-context-v0.1"

# -- rulebook thresholds (docs/rulebook/derivatives-context-v0.1.md) ---------

PRICE_UP_THRESHOLD_PCT = 0.25
PRICE_DOWN_THRESHOLD_PCT = -0.25
OI_UP_THRESHOLD_PCT = 0.5
OI_DOWN_THRESHOLD_PCT = -0.5

UP, DOWN, FLAT = "UP", "DOWN", "FLAT"
POSITIVE, NEGATIVE, ZERO = "POSITIVE", "NEGATIVE", "ZERO"
RISING, FALLING, UNCHANGED = "RISING", "FALLING", "UNCHANGED"
NO_MATCH = "NO_MATCH"

_INTERPRETATIONS: dict[tuple[str, str, str], tuple[str, str]] = {
    (UP, UP, POSITIVE): (
        "D1",
        "long participation increasing. New money is entering on the long "
        "side; existing shorts are not the ones driving the move.",
    ),
    (UP, DOWN, POSITIVE): (
        "D2",
        "possible short covering. Price is rising while open interest "
        "shrinks, consistent with short positions closing rather than new "
        "longs opening; funding staying positive says longs still hold a "
        "net premium.",
    ),
    (DOWN, UP, NEGATIVE): (
        "D3",
        "short positioning increasing. New money is entering on the short "
        "side as price falls; funding flipping negative says shorts are "
        "now paying longs to hold the position.",
    ),
    (DOWN, DOWN, POSITIVE): (
        "D4",
        "longs being flushed/deleveraged. Price falling while OI shrinks "
        "and funding is still positive is consistent with long positions "
        "being forcibly or voluntarily closed, not fresh shorts opening.",
    ),
    (FLAT, UP, RISING): (
        "D5",
        "position buildup, breakout risk. Price is not moving but "
        "positioning is growing and funding is climbing — read as "
        "increasing directional pressure without resolution yet, a "
        "condition worth watching rather than a signal to act on.",
    ),
    (FLAT, DOWN, FALLING): (
        "D6",
        "position reduction. Participants are closing positions on both "
        "sides without a directional resolution; read as de-risking, not "
        "as a setup.",
    ),
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


def classify_funding_sign(value: float) -> str:
    if value > 0:
        return POSITIVE
    if value < 0:
        return NEGATIVE
    return ZERO  # undefined by the rulebook - never matches a combination


def classify_funding_trend(current: float, previous: float) -> str:
    if current > previous:
        return RISING
    if current < previous:
        return FALLING
    return UNCHANGED  # undefined by the rulebook - never matches a combination


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
class FundingInput:
    """`previous_value` is only needed for a FLAT price (D5/D6's rising/
    falling comparison) - `None` when unavailable, which is itself
    NO_MATCH-inducing exactly when a FLAT price actually needs it.
    """

    status: str
    value: float | None
    previous_value: float | None
    period_start: dt.datetime | None
    period_close: dt.datetime | None
    reason: str | None = None


@dataclass(frozen=True)
class ClassificationResult:
    result: str  # "D1".."D6" or NO_MATCH
    interpretation: str | None  # verbatim rulebook text; None for NO_MATCH
    reason: str | None  # populated for NO_MATCH; None for a D1-D6 match
    price: PriceInput
    open_interest: OpenInterestInput
    funding: FundingInput
    rulebook_version: str = RULEBOOK_VERSION


def _no_match(
    reason: str, price: PriceInput, oi: OpenInterestInput, funding: FundingInput
) -> ClassificationResult:
    return ClassificationResult(NO_MATCH, None, reason, price, oi, funding)


def classify(
    price: PriceInput, oi: OpenInterestInput, funding: FundingInput
) -> ClassificationResult:
    """Classify one closed-1H reading into D1-D6 or NO_MATCH.

    Never guesses: a missing/non-OK input, a funding reading of exactly
    0.0, an UNCHANGED funding trend, or any of the twelve undefined
    combinations all produce NO_MATCH with a reason - see
    docs/rulebook/derivatives-context-v0.1.md.
    """
    missing = [
        name
        for name, inp in (("price", price), ("open interest", oi), ("funding", funding))
        if inp.status != OK
    ]
    if missing:
        return _no_match(f"missing/unavailable input(s): {', '.join(missing)}", price, oi, funding)

    price_state = classify_price(price.change_pct)
    oi_state = classify_oi(oi.change_pct)

    if price_state in (UP, DOWN):
        funding_state = classify_funding_sign(funding.value)
    else:
        if funding.previous_value is None:
            return _no_match(
                "price is FLAT but no previous closed funding reading is available "
                "to determine rising/falling",
                price,
                oi,
                funding,
            )
        funding_state = classify_funding_trend(funding.value, funding.previous_value)

    match = _INTERPRETATIONS.get((price_state, oi_state, funding_state))
    if match is None:
        reason = (
            f"no rulebook combination for (price={price_state}, oi={oi_state}, "
            f"funding={funding_state})"
        )
        return _no_match(reason, price, oi, funding)

    d_id, interpretation = match
    return ClassificationResult(d_id, interpretation, None, price, oi, funding)


__all__ = [
    "DOWN",
    "FALLING",
    "FLAT",
    "MARKET_NOT_FOUND",
    "NEGATIVE",
    "NO_DATA",
    "NO_MATCH",
    "POSITIVE",
    "RISING",
    "RULEBOOK_VERSION",
    "UNCHANGED",
    "UP",
    "ZERO",
    "ClassificationResult",
    "FundingInput",
    "OpenInterestInput",
    "PriceInput",
    "classify",
    "classify_funding_sign",
    "classify_funding_trend",
    "classify_oi",
    "classify_price",
]
