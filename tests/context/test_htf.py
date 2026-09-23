"""Section 1 decision matrix: every row, breaks, wicks, and grading.

Fixtures are hand-built 4H candle sequences with known, manually-verified
fractal pivots (N=2) rather than real data, per the rulebook's testing
requirement. `atr_value` is passed directly to `evaluate` (it is a plain
parameter, not derived from the fixture), which keeps fixtures small.
"""

import datetime as dt

import pandas as pd
import pytest

from tidemark.context import htf

START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
ATR = 2.0


def _candles(values: list[float], r: float = 0.0, start: dt.datetime = START) -> pd.DataFrame:
    close_times = [start + dt.timedelta(hours=4 * i) for i in range(len(values))]
    return pd.DataFrame(
        {
            "high": [v + r for v in values],
            "low": [v - r for v in values],
            "close": values,
            "close_time": close_times,
        }
    )


def _append(candles: pd.DataFrame, values: list[float], r: float = 0.0) -> pd.DataFrame:
    start = candles["close_time"].iloc[-1] + dt.timedelta(hours=4)
    return pd.concat([candles, _candles(values, r=r, start=start)], ignore_index=True)


# Base BULLISH structure: swing low 100 -> swing high 130 -> swing low 110
# (HL, since 110 > 100) -> swing high 150 (HH, since 150 > 130). Confirmed
# swings: L1=100 (formed idx4, confirmed idx6), H1=130 (formed idx7,
# confirmed idx9), L2=110 (formed idx11, confirmed idx13), H2=150 (formed
# idx15, confirmed idx17). Bias becomes BULLISH once the last swing (H2)
# confirms at idx17.
_BULLISH_BASE = [
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

# Mirror of _BULLISH_BASE (reflected around 125): LH (140 < 150) and LL
# (100 < 120) once confirmed -> BEARISH. Confirmed swings: H1=150 (formed
# idx4, confirmed idx6), L1=120 (formed idx7, confirmed idx9), H2=140
# (formed idx11, confirmed idx13), L2=100 (formed idx15, confirmed idx17).
_BEARISH_BASE = [250 - v for v in _BULLISH_BASE]


def _week(high: float, low: float, close_time: dt.datetime) -> pd.DataFrame:
    return pd.DataFrame({"high": [high], "low": [low], "close_time": [close_time]})


# --- Row 1: INSUFFICIENT_STRUCTURE -> WAIT / NOT_ENOUGH_SWINGS ------------


def test_row1_insufficient_structure() -> None:
    candles = _candles([100, 100, 100])
    record = htf.evaluate("BTCUSDT", candles, atr_value=ATR)

    assert record.state == htf.INSUFFICIENT_STRUCTURE
    assert record.watch == htf.WAIT
    assert record.grade is None
    assert record.reason_code == htf.NOT_ENOUGH_SWINGS


def test_missing_atr_is_also_not_enough_swings() -> None:
    candles = _candles(_BULLISH_BASE)
    record = htf.evaluate("BTCUSDT", candles, atr_value=None)

    assert record.state == htf.INSUFFICIENT_STRUCTURE
    assert record.reason_code == htf.NOT_ENOUGH_SWINGS


# --- Row 2: STRUCTURE_BROKEN_* -> WAIT / STRUCTURE_BROKEN ------------------


def test_row2_close_beyond_last_confirmed_hl_breaks_structure() -> None:
    candles = _append(_candles(_BULLISH_BASE), [105])  # close 105 < HL 110
    record = htf.evaluate("BTCUSDT", candles, atr_value=ATR)

    assert record.state == htf.STRUCTURE_BROKEN_BULL
    assert record.watch == htf.WAIT
    assert record.grade is None
    assert record.reason_code == htf.STRUCTURE_BROKEN


def test_wick_beyond_hl_does_not_break_structure() -> None:
    candles = _candles(_BULLISH_BASE)
    # Append a candle whose wick dips under the HL (110) but closes above it.
    tail_start = candles["close_time"].iloc[-1] + dt.timedelta(hours=4)
    tail = pd.DataFrame(
        {
            "high": [113.0],
            "low": [105.0],  # wicks below 110
            "close": [112.0],  # closes above 110
            "close_time": [tail_start],
        }
    )
    candles = pd.concat([candles, tail], ignore_index=True)

    record = htf.evaluate("BTCUSDT", candles, atr_value=ATR)

    assert record.state == htf.BULLISH


def test_broken_state_persists_until_new_post_break_swing_confirms() -> None:
    candles = _append(_candles(_BULLISH_BASE), [105])  # break at idx19
    break_at = candles["close_time"].iloc[-1]

    # Descend to a brand-new swing low (90) after the break, then rise —
    # this new low forms at idx22, confirms at idx24.
    candles = _append(candles, [100, 95, 90, 95, 100])

    # One candle before the new low's confirmation: still broken.
    before_confirm = candles.iloc[:-1]
    record_before = htf.evaluate("BTCUSDT", before_confirm, atr_value=ATR)
    assert record_before.state == htf.STRUCTURE_BROKEN_BULL

    # At confirmation: bias recomputes using the last 2 confirmed highs
    # (unchanged, still HH) and the last 2 confirmed lows (old HL=110,
    # brand-new low=90 -> LL) -> mixed -> NEUTRAL, not simply "back to
    # bullish" even though the original swings still look bullish alone.
    record_after = htf.evaluate("BTCUSDT", candles, atr_value=ATR)
    assert record_after.state == htf.NEUTRAL
    assert break_at < candles["close_time"].iloc[-1]


# --- Row 3: NEUTRAL -> WAIT / NEUTRAL_STRUCTURE ----------------------------


def test_row3_neutral_structure() -> None:
    # HH (140 > 130) but LL (90 < 100): mixed -> NEUTRAL. Two trailing
    # candles are padding so the last swing (H2 at idx15) has confirmed
    # (idx17) by the time this fixture ends.
    values = [
        120,
        115,
        110,
        105,
        100,
        110,
        120,
        130,
        120,
        110,
        100,
        90,
        100,
        110,
        120,
        140,
        130,
        120,
    ]
    candles = _candles(values)
    record = htf.evaluate("BTCUSDT", candles, atr_value=ATR)

    assert record.state == htf.NEUTRAL
    assert record.watch == htf.WAIT
    assert record.grade is None
    assert record.reason_code == htf.NEUTRAL_STRUCTURE


# --- Rows 4/5: BULLISH + major support, with/without Fib overlap ----------


def test_row4_bullish_major_support_and_fib_zone_grade_a() -> None:
    candles = _append(_candles(_BULLISH_BASE), [119])
    candles.loc[candles.index[-1], ["high", "low"]] = [120.0, 119.2]
    week = _week(high=200.0, low=119.0, close_time=candles["close_time"].iloc[-1])

    record = htf.evaluate("BTCUSDT", candles, atr_value=ATR, candles_1w=week)

    assert record.state == htf.BULLISH
    assert record.watch == htf.LONG_WATCH
    assert record.grade == "A"
    assert record.reason_code == htf.MAJOR_SUPPORT_FIB


def test_row5_bullish_major_support_without_fib_grade_b() -> None:
    candles = _append(_candles(_BULLISH_BASE), [131])
    candles.loc[candles.index[-1], ["high", "low"]] = [132.0, 119.2]
    week = _week(high=200.0, low=119.0, close_time=candles["close_time"].iloc[-1])

    record = htf.evaluate("BTCUSDT", candles, atr_value=ATR, candles_1w=week)

    assert record.state == htf.BULLISH
    assert record.watch == htf.LONG_WATCH
    assert record.grade == "B"
    assert record.reason_code == htf.MAJOR_SUPPORT


# --- Rows 6/7: BEARISH + major resistance, with/without Fib overlap -------


def test_row6_bearish_major_resistance_and_fib_zone_grade_a() -> None:
    candles = _append(_candles(_BEARISH_BASE), [124])
    candles.loc[candles.index[-1], ["high", "low"]] = [125.2, 123.0]
    week = _week(high=125.0, low=10.0, close_time=candles["close_time"].iloc[-1])

    record = htf.evaluate("BTCUSDT", candles, atr_value=ATR, candles_1w=week)

    assert record.state == htf.BEARISH
    assert record.watch == htf.SHORT_WATCH
    assert record.grade == "A"
    assert record.reason_code == htf.MAJOR_RESISTANCE_FIB


def test_row7_bearish_major_resistance_without_fib_grade_b() -> None:
    candles = _append(_candles(_BEARISH_BASE), [115])
    candles.loc[candles.index[-1], ["high", "low"]] = [125.2, 113.0]
    week = _week(high=125.0, low=10.0, close_time=candles["close_time"].iloc[-1])

    record = htf.evaluate("BTCUSDT", candles, atr_value=ATR, candles_1w=week)

    assert record.state == htf.BEARISH
    assert record.watch == htf.SHORT_WATCH
    assert record.grade == "B"
    assert record.reason_code == htf.MAJOR_RESISTANCE


# --- Row 8: Fib zone, no major level -> WAIT / FIB_ONLY (never a WATCH) ---


def test_row8_fib_only_no_major_level_never_a_watch() -> None:
    candles = _append(_candles(_BULLISH_BASE), [125])
    week = _week(high=15.0, low=10.0, close_time=candles["close_time"].iloc[-1])

    record = htf.evaluate("BTCUSDT", candles, atr_value=ATR, candles_1w=week)

    assert record.state == htf.BULLISH
    assert record.watch == htf.WAIT
    assert record.grade is None
    assert record.reason_code == htf.FIB_ONLY
    assert record.watch != htf.LONG_WATCH
    assert record.watch != htf.SHORT_WATCH


# --- Row 9: anything else -> WAIT / NOT_IN_ZONE ----------------------------


def test_row9_not_in_zone() -> None:
    candles = _append(_candles(_BULLISH_BASE), [135])

    record = htf.evaluate("BTCUSDT", candles, atr_value=ATR)

    assert record.state == htf.BULLISH
    assert record.watch == htf.WAIT
    assert record.grade is None
    assert record.reason_code == htf.NOT_IN_ZONE


# --- Output record shape ----------------------------------------------------


def test_rule_version_is_locked_section_identifier() -> None:
    assert htf.RULE_VERSION == "section-01-v1.0"


def test_output_record_carries_rule_version_and_swings_used() -> None:
    candles = _append(_candles(_BULLISH_BASE), [135])
    record = htf.evaluate("BTCUSDT", candles, atr_value=ATR)

    assert record.rule_version == "section-01-v1.0"
    assert record.asset == "BTCUSDT"
    assert len(record.swings_used) == 4
    for swing in record.swings_used:
        assert "formed_at" in swing
        assert "confirmed_at" in swing


def test_candles_4h_required() -> None:
    with pytest.raises(ValueError):
        htf.evaluate("BTCUSDT", pd.DataFrame(columns=["high", "low", "close", "close_time"]), 2.0)
