"""LOOK-AHEAD GUARD: --as-of on a full DB must match a DB truncated at i.

For every candle i in the fixture, evaluating with `as_of` set to that
candle's close time against a database holding the *entire* fixture must
produce the exact same ContextRecord as evaluating (no as_of) against a
database that only ever had candles 0..i written to it.
"""

import datetime as dt

from tidemark.cli import _evaluate_symbol
from tidemark.data.models import Candle
from tidemark.data.store import TidemarkStore, create_store_engine, init_db

START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
SYMBOL = "BTCUSDT"

# Reuses the BULLISH structure + a break + a new post-break swing from the
# htf matrix-row fixtures: enough state transitions (INSUFFICIENT ->
# BULLISH -> STRUCTURE_BROKEN_BULL -> NEUTRAL) to exercise the guard across
# more than one regime.
_VALUES = [
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
    105,  # break: close 105 < HL 110
    100,
    95,
    90,
    95,
    100,
]


def _candles(values: list[float]) -> list[Candle]:
    out = []
    for i, v in enumerate(values):
        close_time = START + dt.timedelta(hours=4 * (i + 1))
        out.append(
            Candle(
                asset=SYMBOL,
                timeframe="4h",
                open_time=close_time - dt.timedelta(hours=4),
                close_time=close_time,
                open=v,
                high=v,
                low=v,
                close=v,
                volume=1.0,
            )
        )
    return out


def _record_fields(record) -> dict:
    return {
        "state": record.state,
        "watch": record.watch,
        "grade": record.grade,
        "reason_code": record.reason_code,
        "active_levels": record.active_levels,
        "fib": record.fib,
        "swings_used": record.swings_used,
    }


def test_as_of_matches_a_database_truncated_at_every_candle(tmp_path) -> None:
    all_candles = _candles(_VALUES)

    full_engine = create_store_engine(f"sqlite:///{tmp_path / 'full.db'}")
    init_db(full_engine)
    full_store = TidemarkStore(full_engine)
    full_store.save_candles(all_candles)

    for i in range(len(all_candles)):
        as_of = all_candles[i].close_time

        truncated_engine = create_store_engine(f"sqlite:///{tmp_path / f'trunc_{i}.db'}")
        init_db(truncated_engine)
        truncated_store = TidemarkStore(truncated_engine)
        truncated_store.save_candles(all_candles[: i + 1])

        as_of_record = _evaluate_symbol(full_store, SYMBOL, as_of)
        truncated_record = _evaluate_symbol(truncated_store, SYMBOL, None)

        assert _record_fields(as_of_record) == _record_fields(truncated_record), (
            f"mismatch at candle index {i}"
        )
