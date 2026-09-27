"""D1-D6 classification against docs/rulebook/derivatives-context-v0.1.md:
every defined combination, every one of the twelve undefined ones,
boundary values, and every "never guess" failure mode.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tidemark.market_intel.derivatives_classifier import (
    DOWN,
    FLAT,
    NEGATIVE,
    NO_MATCH,
    POSITIVE,
    UP,
    ClassificationResult,
    FundingInput,
    OpenInterestInput,
    PriceInput,
    classify,
    classify_funding_sign,
    classify_funding_trend,
    classify_oi,
    classify_price,
)
from tidemark.market_intel.models import MARKET_NOT_FOUND, NO_DATA, OK

PERIOD_START = dt.datetime(2026, 9, 27, 18, 0, tzinfo=dt.UTC)
PERIOD_CLOSE = dt.datetime(2026, 9, 27, 19, 0, tzinfo=dt.UTC)


def _price(change_pct: float) -> PriceInput:
    return PriceInput(OK, change_pct, PERIOD_START, PERIOD_CLOSE)


def _oi(change_pct: float) -> OpenInterestInput:
    return OpenInterestInput(OK, change_pct, PERIOD_START, PERIOD_CLOSE)


def _funding(value: float, previous_value: float | None = None) -> FundingInput:
    return FundingInput(OK, value, previous_value, PERIOD_START, PERIOD_CLOSE)


# -- classify_price / classify_oi: threshold and boundary behavior -----------


@pytest.mark.parametrize(
    ("change_pct", "expected"),
    [(0.251, UP), (1.0, UP), (-0.251, DOWN), (-1.0, DOWN), (0.25, FLAT), (-0.25, FLAT), (0.0, FLAT)],
)
def test_classify_price_thresholds(change_pct: float, expected: str) -> None:
    assert classify_price(change_pct) == expected


@pytest.mark.parametrize(
    ("change_pct", "expected"),
    [(0.501, UP), (2.0, UP), (-0.501, DOWN), (-2.0, DOWN), (0.5, FLAT), (-0.5, FLAT), (0.0, FLAT)],
)
def test_classify_oi_thresholds(change_pct: float, expected: str) -> None:
    assert classify_oi(change_pct) == expected


def test_classify_funding_sign_zero_is_its_own_state() -> None:
    assert classify_funding_sign(0.001) == POSITIVE
    assert classify_funding_sign(-0.001) == NEGATIVE
    assert classify_funding_sign(0.0) == "ZERO"


def test_classify_funding_trend_equal_is_unchanged() -> None:
    assert classify_funding_trend(0.002, 0.001) == "RISING"
    assert classify_funding_trend(0.001, 0.002) == "FALLING"
    assert classify_funding_trend(0.001, 0.001) == "UNCHANGED"


# -- D1-D6: each classified correctly from fixture inputs ---------------------


def test_d1_price_up_oi_up_funding_positive() -> None:
    result = classify(_price(1.0), _oi(1.0), _funding(0.01))
    assert result.result == "D1"
    assert result.interpretation is not None
    assert "long participation increasing" in result.interpretation
    assert result.reason is None


def test_d2_price_up_oi_down_funding_positive() -> None:
    result = classify(_price(1.0), _oi(-1.0), _funding(0.01))
    assert result.result == "D2"
    assert "short covering" in result.interpretation


def test_d3_price_down_oi_up_funding_negative() -> None:
    result = classify(_price(-1.0), _oi(1.0), _funding(-0.01))
    assert result.result == "D3"
    assert "short positioning increasing" in result.interpretation


def test_d4_price_down_oi_down_funding_positive() -> None:
    result = classify(_price(-1.0), _oi(-1.0), _funding(0.01))
    assert result.result == "D4"
    assert "flushed" in result.interpretation


def test_d5_price_flat_oi_up_funding_rising() -> None:
    result = classify(_price(0.0), _oi(1.0), _funding(0.02, previous_value=0.01))
    assert result.result == "D5"
    assert "breakout risk" in result.interpretation


def test_d6_price_flat_oi_down_funding_falling() -> None:
    result = classify(_price(0.0), _oi(-1.0), _funding(0.01, previous_value=0.02))
    assert result.result == "D6"
    assert "de-risking" in result.interpretation


# -- every one of the twelve undefined combinations gives NO_MATCH -----------

_UNDEFINED_UP_DOWN = [
    (UP, UP, NEGATIVE),
    (UP, DOWN, NEGATIVE),
    (UP, FLAT, POSITIVE),
    (UP, FLAT, NEGATIVE),
    (DOWN, UP, POSITIVE),
    (DOWN, DOWN, NEGATIVE),
    (DOWN, FLAT, POSITIVE),
    (DOWN, FLAT, NEGATIVE),
]


@pytest.mark.parametrize(("price_state", "oi_state", "funding_state"), _UNDEFINED_UP_DOWN)
def test_undefined_up_down_combinations_are_no_match(
    price_state: str, oi_state: str, funding_state: str
) -> None:
    price_pct = 1.0 if price_state == UP else -1.0
    oi_pct = {UP: 1.0, DOWN: -1.0, FLAT: 0.0}[oi_state]
    funding_value = 0.01 if funding_state == POSITIVE else -0.01

    result = classify(_price(price_pct), _oi(oi_pct), _funding(funding_value))

    assert result.result == NO_MATCH
    assert result.interpretation is None
    assert "no rulebook combination" in result.reason


def test_flat_up_falling_is_no_match() -> None:
    result = classify(_price(0.0), _oi(1.0), _funding(0.01, previous_value=0.02))
    assert result.result == NO_MATCH


def test_flat_down_rising_is_no_match() -> None:
    result = classify(_price(0.0), _oi(-1.0), _funding(0.02, previous_value=0.01))
    assert result.result == NO_MATCH


def test_flat_flat_rising_is_no_match() -> None:
    result = classify(_price(0.0), _oi(0.0), _funding(0.02, previous_value=0.01))
    assert result.result == NO_MATCH


def test_flat_flat_falling_is_no_match() -> None:
    result = classify(_price(0.0), _oi(0.0), _funding(0.01, previous_value=0.02))
    assert result.result == NO_MATCH


# -- boundary values classify as FLAT, never nudged into UP/DOWN -------------


def test_price_boundary_values_are_flat_not_a_match() -> None:
    assert classify(_price(0.25), _oi(1.0), _funding(0.01)).result == NO_MATCH  # price FLAT, no D-id
    assert classify(_price(-0.25), _oi(1.0), _funding(-0.01)).result == NO_MATCH


def test_oi_boundary_values_are_flat_not_a_match() -> None:
    assert classify(_price(1.0), _oi(0.5), _funding(0.01)).result == NO_MATCH  # OI FLAT, no D-id
    assert classify(_price(1.0), _oi(-0.5), _funding(0.01)).result == NO_MATCH


def test_price_and_oi_just_past_boundary_do_match() -> None:
    # Sanity: 0.251/-0.501 (just past the boundary) DO reach D1/D4 - proves
    # the FLAT-at-exactly-threshold tests above are testing the boundary,
    # not simply a broken classifier that never matches D1/D4 at all.
    assert classify(_price(0.251), _oi(0.501), _funding(0.01)).result == "D1"
    assert classify(_price(-0.251), _oi(-0.501), _funding(0.01)).result == "D4"


# -- funding exactly 0.0, and an UNCHANGED trend, are both undefined ---------


def test_funding_exactly_zero_is_no_match_for_up_down_price() -> None:
    result = classify(_price(1.0), _oi(1.0), _funding(0.0))
    assert result.result == NO_MATCH
    assert "funding=ZERO" in result.reason


def test_funding_unchanged_trend_is_no_match_for_flat_price() -> None:
    result = classify(_price(0.0), _oi(1.0), _funding(0.01, previous_value=0.01))
    assert result.result == NO_MATCH
    assert "funding=UNCHANGED" in result.reason


# -- missing/UNAVAILABLE inputs: NO_MATCH with a reason, never a guess -------


@pytest.mark.parametrize("bad_status", [NO_DATA, MARKET_NOT_FOUND])
def test_unavailable_price_input_is_no_match_with_reason(bad_status: str) -> None:
    bad_price = PriceInput(bad_status, None, None, None, reason="Coinalyze returned no data")
    result = classify(bad_price, _oi(1.0), _funding(0.01))

    assert result.result == NO_MATCH
    assert result.interpretation is None
    assert "price" in result.reason


def test_unavailable_oi_input_is_no_match_with_reason() -> None:
    bad_oi = OpenInterestInput(NO_DATA, None, None, None, reason="no data")
    result = classify(_price(1.0), bad_oi, _funding(0.01))

    assert result.result == NO_MATCH
    assert "open interest" in result.reason


def test_unavailable_funding_input_is_no_match_with_reason() -> None:
    bad_funding = FundingInput(MARKET_NOT_FOUND, None, None, None, None, reason="not listed")
    result = classify(_price(1.0), _oi(1.0), bad_funding)

    assert result.result == NO_MATCH
    assert "funding" in result.reason


def test_multiple_unavailable_inputs_are_all_named() -> None:
    bad_price = PriceInput(NO_DATA, None, None, None)
    bad_funding = FundingInput(NO_DATA, None, None, None, None)
    result = classify(bad_price, _oi(1.0), bad_funding)

    assert result.result == NO_MATCH
    assert "price" in result.reason
    assert "funding" in result.reason


def test_flat_price_with_no_previous_funding_is_no_match() -> None:
    result = classify(_price(0.0), _oi(1.0), _funding(0.01, previous_value=None))

    assert result.result == NO_MATCH
    assert "previous closed funding reading" in result.reason


# -- the result always carries its own inputs and the rulebook version ------


def test_result_carries_its_inputs_and_rulebook_version() -> None:
    price = _price(1.0)
    oi = _oi(1.0)
    funding = _funding(0.01)

    result = classify(price, oi, funding)

    assert isinstance(result, ClassificationResult)
    assert result.price is price
    assert result.open_interest is oi
    assert result.funding is funding
    assert result.rulebook_version == "derivatives-context-v0.1"
