"""Orchestrates `tidemark observe run`: evaluate Section 2 and journal
every row it returns.

Mirrors `journal/pipeline.py`'s per-symbol isolation and run-lifecycle
bookkeeping, with one deliberate omission: no `TelegramNotifier`
anywhere in this module, and no change detector. Section 2 writes to
its own observation journal and nothing else (hard constraint: no
Telegram alerts from Section 2).
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass

import pandas as pd

from tidemark.context import mtf
from tidemark.data.models import Candle, Observation
from tidemark.data.store import TidemarkStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SymbolObserveOutcome:
    """The result of running Section 2 observation for one symbol."""

    symbol: str
    evaluated: int  # rows `mtf.evaluate` returned
    inserted: int  # of those, how many were *new* journal rows
    error: str | None


@dataclass(frozen=True)
class ObservePipelineRunOutcome:
    """The result of one full `tidemark observe run`."""

    run_id: str
    status: str
    outcomes: list[SymbolObserveOutcome]


def _candles_to_frame(candles: list[Candle]) -> pd.DataFrame:
    """Adapt stored `Candle` rows into the DataFrame shape core/context expect."""
    return pd.DataFrame(
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


def run_observe_pipeline(
    store: TidemarkStore,
    venue: str,
    symbols: list[str],
    now: dt.datetime | None = None,
) -> ObservePipelineRunOutcome:
    """Evaluate Section 2 and journal every observation row, per symbol."""
    now = now or dt.datetime.now(dt.UTC)
    run_id = uuid.uuid4().hex
    store.start_run(run_id, "observe", now)

    status = "FAILED"
    outcomes: list[SymbolObserveOutcome] = []
    try:
        outcomes = [_observe_symbol(store, venue, symbol) for symbol in symbols]

        fail_count = sum(1 for o in outcomes if o.error is not None)
        success_count = len(outcomes) - fail_count
        if fail_count == 0:
            status = "COMPLETED"
        elif success_count == 0:
            status = "FAILED"
        else:
            status = "PARTIAL"
    finally:
        store.finish_run(run_id, now, status, stats={})

    return ObservePipelineRunOutcome(run_id=run_id, status=status, outcomes=outcomes)


def _observe_symbol(store: TidemarkStore, venue: str, symbol: str) -> SymbolObserveOutcome:
    try:
        candles_1h = _candles_to_frame(store.get_candles(venue, symbol, "1h"))
        if len(candles_1h) == 0:
            return SymbolObserveOutcome(symbol, 0, 0, "no 1H candles")

        section_1_history = store.journal_history(symbol)
        results = mtf.evaluate(symbol, section_1_history, candles_1h)

        inserted = 0
        for result in results:
            observation = Observation(
                asset=result.asset,
                evaluated_at=result.evaluated_at,
                rule_version=result.rule_version,
                section_1_state=result.section_1_state,
                section_1_watch=result.section_1_watch,
                section_1_grade=result.section_1_grade,
                section_1_level_price=result.section_1_level_price,
                interaction_detected=result.interaction_detected,
                reaction_tier=result.reaction_tier,
                reaction_condition_matched=result.reaction_condition_matched,
                reaction_started_at=result.reaction_started_at,
                structure_reference_price=result.structure_reference_price,
                structure_reference_confirmed_at=result.structure_reference_confirmed_at,
                structure_change=result.structure_change,
                failure=result.failure,
                expiry=result.expiry,
                state=result.state,
                reason_code=result.reason_code,
                swings_used=result.swings_used,
            )
            write_result = store.save_observation(observation)
            if write_result.inserted:
                inserted += 1

        return SymbolObserveOutcome(symbol, len(results), inserted, None)
    except Exception as exc:
        logger.error("observe failed for symbol=%s error=%s", symbol, exc)
        return SymbolObserveOutcome(symbol, 0, 0, str(exc))
