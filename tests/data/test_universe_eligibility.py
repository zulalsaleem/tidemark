"""Section 1 eligibility assessment (Phase 6, Merge 2B, PART C).

Pure functions, no store/network. Runs the LOCKED Section 1 v1.1 engine
unmodified via replay.report.replay_section1 - these tests verify this
module reimplements no rule by cross-checking against context.htf.evaluate
directly where relevant.
"""

from __future__ import annotations

import datetime as dt

from tidemark.context import htf
from tidemark.data.models import Candle
from tidemark.data.universe_eligibility import (
    DATA_GAPS,
    INSUFFICIENT_4H_HISTORY,
    INVALID_OHLCV,
    MIN_4H_CANDLES_FOR_ATR,
    NEVER_EXITED_INSUFFICIENT_STRUCTURE,
    assess_section1_eligibility,
)

START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
SYMBOL = "BTC/USDT:USDT"

# The same zigzag used by tests/context/test_look_ahead_guard.py: enough
# state transitions to exit INSUFFICIENT_STRUCTURE (into BULLISH) partway
# through.
_EXITS_VALUES = [
    120,
    115,
    110,
    105,
    100,
    110,
    120,
    130,
    120,
    115,
    112,
    110,
    120,
    130,
    140,
    150,
    140,
    130,
    120,
]


def _four_h(values: list[float]) -> list[Candle]:
    out = []
    for i, v in enumerate(values):
        open_time = START + dt.timedelta(hours=4 * i)
        out.append(
            Candle(
                venue="binanceusdm",
                symbol=SYMBOL,
                timeframe="4h",
                open_time=open_time,
                close_time=open_time + dt.timedelta(hours=4),
                open=v,
                high=v,
                low=v,
                close=v,
                volume=1.0,
                fetched_at=START,
            )
        )
    return out


def test_fewer_than_min_candles_gives_insufficient_4h_history() -> None:
    candles = _four_h([100.0] * (MIN_4H_CANDLES_FOR_ATR - 1))
    as_of = candles[-1].close_time

    result = assess_section1_eligibility(SYMBOL, candles, [], [], as_of, rejected_4h_count=0)

    assert result.eligible is False
    assert result.exclusion_reason == INSUFFICIENT_4H_HISTORY
    assert result.section1_first_usable_at is None


def test_flat_history_never_exits_insufficient_structure() -> None:
    """A long flat candle sequence never produces a swing, so Section 1
    can never exit INSUFFICIENT_STRUCTURE - the honest, correctly labeled
    outcome, not a data-insufficiency problem."""
    candles = _four_h([100.0] * 60)
    as_of = candles[-1].close_time

    result = assess_section1_eligibility(SYMBOL, candles, [], [], as_of, rejected_4h_count=0)

    assert result.eligible is False
    assert result.exclusion_reason == NEVER_EXITED_INSUFFICIENT_STRUCTURE
    assert result.section1_first_usable_at is None


def test_exiting_insufficient_structure_makes_it_eligible() -> None:
    candles = _four_h(_EXITS_VALUES)
    as_of = candles[-1].close_time

    result = assess_section1_eligibility(SYMBOL, candles, [], [], as_of, rejected_4h_count=0)

    assert result.eligible is True
    assert result.exclusion_reason is None
    assert result.section1_first_usable_at is not None
    assert result.section1_first_usable_at <= as_of


def test_section1_first_usable_at_matches_the_first_non_insufficient_evaluation() -> None:
    """Cross-check against context.htf.evaluate directly (via core/atr and
    core/swings, the same engine) - this module must not reimplement or
    diverge from what a direct Section 1 evaluation produces at that
    point in the same history.
    """
    import pandas as pd

    from tidemark.core.atr import atr as compute_atr

    candles = _four_h(_EXITS_VALUES)
    as_of = candles[-1].close_time

    result = assess_section1_eligibility(SYMBOL, candles, [], [], as_of, rejected_4h_count=0)

    frame = pd.DataFrame(
        {
            "open_time": [c.open_time for c in candles],
            "close_time": [c.close_time for c in candles],
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [c.volume for c in candles],
        }
    )
    # Find the first prefix whose Section 1 evaluation isn't INSUFFICIENT_STRUCTURE.
    first_usable = None
    for i in range(len(candles)):
        trunc = frame.iloc[: i + 1]
        atr_value = compute_atr(trunc).iloc[-1]
        record = htf.evaluate(SYMBOL, trunc, atr_value)
        if record.state != htf.INSUFFICIENT_STRUCTURE:
            first_usable = record.evaluated_at
            break

    assert result.section1_first_usable_at == first_usable


def test_rejected_candles_give_invalid_ohlcv() -> None:
    candles = _four_h(_EXITS_VALUES)
    as_of = candles[-1].close_time

    result = assess_section1_eligibility(SYMBOL, candles, [], [], as_of, rejected_4h_count=1)

    assert result.eligible is False
    assert result.exclusion_reason == INVALID_OHLCV


def test_a_gap_in_the_4h_sequence_gives_data_gaps() -> None:
    candles = _four_h(_EXITS_VALUES)
    del candles[10]  # remove one candle, leaving a gap in open_time spacing
    as_of = candles[-1].close_time

    result = assess_section1_eligibility(SYMBOL, candles, [], [], as_of, rejected_4h_count=0)

    assert result.eligible is False
    assert result.exclusion_reason == DATA_GAPS


def test_lookahead_safe_truncation_never_sees_candles_beyond_as_of() -> None:
    """This module trusts its caller's truncation completely - given a
    prefix that itself never exits INSUFFICIENT_STRUCTURE, the presence
    of exiting candles later in the *full* history (which the caller
    would never have passed in for an earlier as_of) must not matter,
    because this test never gives them to the function at all."""
    candles = _four_h(_EXITS_VALUES)
    prefix = candles[:8]  # well before the exit in the full sequence
    as_of = prefix[-1].close_time

    result = assess_section1_eligibility(SYMBOL, prefix, [], [], as_of, rejected_4h_count=0)

    assert result.eligible is False
    assert result.exclusion_reason in (INSUFFICIENT_4H_HISTORY, NEVER_EXITED_INSUFFICIENT_STRUCTURE)
