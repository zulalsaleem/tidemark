"""render_briefing: two independent sections (BTC STRUCTURE, DERIVATIVES
CONTEXT), no trade direction or bias, and none of the forbidden trading-
instruction words - this message never needs "Buy volume"/"Sell volume"
at all, so unlike /coin's renderer, bare "buy"/"sell" are banned outright
here.
"""

from __future__ import annotations

import datetime as dt

from tidemark.market_intel.briefing import StructureSnapshot
from tidemark.market_intel.derivatives_classifier import (
    FundingInput,
    OpenInterestInput,
    PriceInput,
    classify,
)
from tidemark.market_intel.models import NO_DATA, OK
from tidemark.market_intel.telegram_render import BRIEFING_FOOTER, render_briefing

NOW = dt.datetime(2026, 9, 27, 19, 5, tzinfo=dt.UTC)
PERIOD_START = dt.datetime(2026, 9, 27, 18, 0, tzinfo=dt.UTC)
PERIOD_CLOSE = dt.datetime(2026, 9, 27, 19, 0, tzinfo=dt.UTC)

_FORBIDDEN = [
    "entry",
    "stop",
    " sl ",
    " tp ",
    "target",
    "r:r",
    "buy",
    "sell",
    "long setup",
    "short setup",
]


def _assert_no_forbidden_language(message: str) -> None:
    lowered = f" {message.lower()} "
    for word in _FORBIDDEN:
        assert word not in lowered, f"unexpected {word!r} in rendered briefing"


def _price(change_pct=1.0, status=OK, reason=None) -> PriceInput:
    return PriceInput(
        status, change_pct if status == OK else None, PERIOD_START, PERIOD_CLOSE, reason
    )


def _oi(change_pct=1.0, status=OK, reason=None) -> OpenInterestInput:
    return OpenInterestInput(
        status, change_pct if status == OK else None, PERIOD_START, PERIOD_CLOSE, reason
    )


def _funding(value=0.01, status=OK, reason=None) -> FundingInput:
    return FundingInput(
        status, value if status == OK else None, None, PERIOD_START, PERIOD_CLOSE, reason
    )


def _available_structure(**overrides) -> StructureSnapshot:
    defaults = dict(
        available=True,
        stale=False,
        state="BULLISH",
        watch="LONG_WATCH",
        grade="A",
        rule_version="section-01-v1.1",
        evaluated_at=dt.datetime(2026, 9, 27, 16, 0, tzinfo=dt.UTC),
    )
    defaults.update(overrides)
    return StructureSnapshot(**defaults)


# -- BTC STRUCTURE section -----------------------------------------------


def test_available_structure_shows_state_watch_grade_rule_version_and_candle() -> None:
    classification = classify(_price(), _oi(), _funding())
    message = render_briefing(_available_structure(), classification, NOW)

    assert "BTC STRUCTURE" in message
    assert "State: BULLISH / Watch: LONG_WATCH, grade A" in message
    assert "Rule version: section-01-v1.1" in message
    assert "4H candle: 16:00 UTC" in message


def test_structure_with_no_grade_omits_the_grade_clause() -> None:
    classification = classify(_price(), _oi(), _funding())
    message = render_briefing(_available_structure(grade=None), classification, NOW)

    assert "State: BULLISH / Watch: LONG_WATCH" in message
    assert "grade" not in message.split("DERIVATIVES CONTEXT")[0].lower()


def test_missing_context_record_renders_unavailable() -> None:
    classification = classify(_price(), _oi(), _funding())
    message = render_briefing(StructureSnapshot(available=False), classification, NOW)

    assert "UNAVAILABLE (no stored Section 1 record for BTC)" in message


def test_stale_context_record_renders_unavailable_with_a_different_reason() -> None:
    classification = classify(_price(), _oi(), _funding())
    message = render_briefing(StructureSnapshot(available=False, stale=True), classification, NOW)

    assert "UNAVAILABLE (stored Section 1 record is stale)" in message


# -- DERIVATIVES CONTEXT section ------------------------------------------


def test_d1_match_shows_identifier_interpretation_and_inputs() -> None:
    classification = classify(_price(1.0), _oi(1.0), _funding(0.01))
    message = render_briefing(_available_structure(), classification, NOW)

    assert "DERIVATIVES CONTEXT" in message
    assert "D1: long participation increasing" in message
    assert "Price: 1.000% (period 18:00–19:00 UTC)" in message
    assert "Open interest: 1.000% (period 18:00–19:00 UTC)" in message
    assert "Funding: 0.010% (period 18:00–19:00 UTC)" in message
    assert "rulebook: derivatives-context-v0.1" in message


def test_no_match_shows_no_match_and_its_reason_never_an_interpretation() -> None:
    # FLAT price + FLAT OI + a rising funding trend matches none of D1-D6.
    funding = FundingInput(OK, 0.02, 0.01, PERIOD_START, PERIOD_CLOSE)
    classification = classify(_price(0.0), _oi(0.0), funding)
    message = render_briefing(_available_structure(), classification, NOW)

    assert "NO_MATCH (" in message
    assert "no rulebook combination" in message
    assert "D1" not in message
    assert "D2" not in message


def test_unavailable_price_input_names_itself_never_a_fabricated_number() -> None:
    classification = classify(_price(status=NO_DATA, reason="no data"), _oi(1.0), _funding(0.01))
    message = render_briefing(_available_structure(), classification, NOW)

    assert "Price: UNAVAILABLE (NO_DATA: no data)" in message
    assert "Price: 0" not in message


# -- footer and forbidden language ----------------------------------------


def test_footer_names_both_sources_and_states_not_a_trade_signal() -> None:
    assert "Tidemark Section 1" in BRIEFING_FOOTER
    assert "Coinalyze" in BRIEFING_FOOTER
    assert "not a trade signal" in BRIEFING_FOOTER.lower()


def test_footer_is_present_in_every_rendered_message() -> None:
    classification = classify(_price(), _oi(), _funding())
    message = render_briefing(_available_structure(), classification, NOW)
    assert BRIEFING_FOOTER in message

    unavailable_message = render_briefing(StructureSnapshot(available=False), classification, NOW)
    assert BRIEFING_FOOTER in unavailable_message


def test_no_forbidden_trading_language_in_a_matched_briefing() -> None:
    classification = classify(_price(1.0), _oi(1.0), _funding(0.01))
    _assert_no_forbidden_language(render_briefing(_available_structure(), classification, NOW))


def test_no_forbidden_trading_language_in_a_no_match_briefing() -> None:
    classification = classify(_price(0.0), _oi(0.0), _funding(0.01))
    _assert_no_forbidden_language(render_briefing(_available_structure(), classification, NOW))


def test_no_forbidden_trading_language_when_structure_is_unavailable() -> None:
    classification = classify(_price(1.0), _oi(1.0), _funding(0.01))
    _assert_no_forbidden_language(
        render_briefing(StructureSnapshot(available=False), classification, NOW)
    )


def test_long_watch_state_does_not_trip_the_long_setup_ban() -> None:
    # LONG_WATCH/SHORT_WATCH are Section 1's own state names, not trading
    # instructions - only the phrases "long setup"/"short setup" are
    # forbidden, never the bare word "long"/"short" that appears inside
    # LONG_WATCH/SHORT_WATCH.
    classification = classify(_price(1.0), _oi(1.0), _funding(0.01))
    message = render_briefing(_available_structure(watch="SHORT_WATCH"), classification, NOW)
    _assert_no_forbidden_language(message)
