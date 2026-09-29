"""Nine-state classification against docs/rulebook/position-flow-v0.1.md:
every one of the nine defined combinations, boundary values, and every
"never guess" missing-data failure mode.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tidemark.market_intel.models import MARKET_NOT_FOUND, NO_DATA, OK
from tidemark.market_intel.position_flow_classifier import (
    DOWN,
    FLAT,
    NO_MATCH,
    UP,
    OpenInterestInput,
    PositionFlowResult,
    PriceInput,
    classify,
    classify_oi,
    classify_price,
)

PERIOD_START = dt.datetime(2026, 9, 27, 18, 0, tzinfo=dt.UTC)
PERIOD_CLOSE = dt.datetime(2026, 9, 27, 19, 0, tzinfo=dt.UTC)


def _price(change_pct: float | None, status: str = OK, reason: str | None = None) -> PriceInput:
    return PriceInput(status, change_pct, PERIOD_START, PERIOD_CLOSE, reason)


def _oi(change_pct: float | None, status: str = OK, reason: str | None = None) -> OpenInterestInput:
    return OpenInterestInput(status, change_pct, PERIOD_START, PERIOD_CLOSE, reason)


# -- classify_price / classify_oi: threshold and boundary behavior -----------


@pytest.mark.parametrize(
    ("change_pct", "expected"),
    [
        (0.250001, UP),
        (1.0, UP),
        (-0.250001, DOWN),
        (-1.0, DOWN),
        (0.25, FLAT),
        (-0.25, FLAT),
        (0.0, FLAT),
    ],
)
def test_classify_price_thresholds(change_pct: float, expected: str) -> None:
    assert classify_price(change_pct) == expected


@pytest.mark.parametrize(
    ("change_pct", "expected"),
    [
        (0.250001, UP),
        (1.0, UP),
        (-0.250001, DOWN),
        (-1.0, DOWN),
        (0.25, FLAT),
        (-0.25, FLAT),
        (0.0, FLAT),
    ],
)
def test_classify_oi_thresholds(change_pct: float, expected: str) -> None:
    assert classify_oi(change_pct) == expected


# -- all nine combinations -----------------------------------------------------


@pytest.mark.parametrize(
    ("price_pct", "oi_pct", "expected_state"),
    [
        (0.30, 0.30, "LONG_BUILDUP"),
        (0.30, 0.00, "PRICE_RISE_NO_OI_EXPANSION"),
        (0.30, -0.30, "SHORT_COVERING"),
        (0.00, 0.30, "NEW_PARTICIPATION"),
        (0.00, 0.00, "QUIET"),
        (0.00, -0.30, "DELEVERAGING"),
        (-0.30, 0.30, "SHORT_BUILDUP"),
        (-0.30, 0.00, "PRICE_FALL_NO_OI_EXPANSION"),
        (-0.30, -0.30, "LONG_UNWIND"),
    ],
)
def test_all_nine_states(price_pct: float, oi_pct: float, expected_state: str) -> None:
    result = classify(_price(price_pct), _oi(oi_pct))

    assert result.result == expected_state
    assert result.reason is None
    assert result.rulebook_version == "position-flow-v0.1"


def test_nine_states_cover_every_price_oi_combination() -> None:
    """3 (price) x 3 (OI) = 9 - every combination is defined, unlike
    derivatives-context-v0.1's 18-combination, 6-defined matrix."""
    for price_state in (UP, DOWN, FLAT):
        for oi_state in (UP, DOWN, FLAT):
            price_pct = {UP: 1.0, DOWN: -1.0, FLAT: 0.0}[price_state]
            oi_pct = {UP: 1.0, DOWN: -1.0, FLAT: 0.0}[oi_state]
            result = classify(_price(price_pct), _oi(oi_pct))
            assert result.result != NO_MATCH


# -- missing data: never a guess -----------------------------------------------


def test_missing_price_is_no_match() -> None:
    result = classify(_price(None, status=NO_DATA, reason="no data"), _oi(0.5))

    assert result.result == NO_MATCH
    assert "price" in result.reason
    assert "open interest" not in result.reason


def test_missing_oi_is_no_match() -> None:
    result = classify(_price(0.5), _oi(None, status=NO_DATA, reason="no data"))

    assert result.result == NO_MATCH
    assert "open interest" in result.reason
    assert "unavailable input(s): open interest" in result.reason  # not "price"


def test_both_missing_is_no_match_naming_both() -> None:
    result = classify(
        _price(None, status=MARKET_NOT_FOUND, reason="not found"),
        _oi(None, status=MARKET_NOT_FOUND, reason="not found"),
    )

    assert result.result == NO_MATCH
    assert "price" in result.reason
    assert "open interest" in result.reason


def test_no_match_never_guesses_from_the_available_input() -> None:
    """A missing OI input must not fall back to classifying on price
    alone, or vice versa - the whole result is NO_MATCH, full stop."""
    result = classify(_price(5.0), _oi(None, status=NO_DATA, reason="unavailable"))

    assert result.result == NO_MATCH
    assert result.price.change_pct == 5.0  # the input is preserved for display
    assert result.open_interest.change_pct is None


def test_result_carries_both_inputs_for_display_regardless_of_outcome() -> None:
    result = classify(_price(0.3), _oi(0.3))

    assert isinstance(result, PositionFlowResult)
    assert result.price.change_pct == 0.3
    assert result.open_interest.change_pct == 0.3
