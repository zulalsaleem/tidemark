"""Orchestrates `tidemark run`'s evaluation -> journal -> change detector
-> Telegram pipeline.

Data-layer orchestration only, mirroring `data/ingest.py`'s pattern: one
symbol failing never aborts the others, and the run's overall status is
COMPLETED/PARTIAL/FAILED via the existing run lifecycle. A Telegram
failure is never a pipeline failure — the journal write already
succeeded by the time a send is even attempted, and `TelegramNotifier`
itself never raises.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass

import pandas as pd

from tidemark.context import htf
from tidemark.core.atr import atr as compute_atr
from tidemark.data.models import Candle
from tidemark.data.store import TidemarkStore
from tidemark.journal.changes import detect_change
from tidemark.journal.records import build_journal_entry
from tidemark.notify.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SymbolRunOutcome:
    """The result of running the pipeline for one symbol."""

    symbol: str
    journaled: bool  # a *new* journal row was written (False = repeat: a no-op)
    alert_sent: bool
    alert_reason: str | None
    error: str | None


@dataclass(frozen=True)
class PipelineRunOutcome:
    """The result of one full `tidemark run`."""

    run_id: str
    status: str
    outcomes: list[SymbolRunOutcome]


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


def run_pipeline(
    store: TidemarkStore,
    notifier: TelegramNotifier,
    venue: str,
    symbols: list[str],
    now: dt.datetime | None = None,
) -> PipelineRunOutcome:
    """Evaluate, journal, detect changes, and notify for each symbol."""
    now = now or dt.datetime.now(dt.UTC)
    run_id = uuid.uuid4().hex
    store.start_run(run_id, "run", now)

    # If an exception escapes below, `status` stays FAILED and `finally`
    # still records that — a crash mid-run must never leave a RUNNING
    # row with no final update, or no row at all.
    status = "FAILED"
    outcomes: list[SymbolRunOutcome] = []
    try:
        outcomes = [_run_symbol(store, notifier, venue, symbol, now) for symbol in symbols]

        fail_count = sum(1 for o in outcomes if o.error is not None)
        success_count = len(outcomes) - fail_count
        if fail_count == 0:
            status = "COMPLETED"
        elif success_count == 0:
            status = "FAILED"
        else:
            status = "PARTIAL"
    finally:
        # RunSymbolStat is shaped for candle-fetch counts (fetched/
        # inserted/duplicates/rejected); it doesn't fit journal/alert
        # outcomes, so this run records no per-symbol stats there — the
        # Run row's own status/timestamps are what PART D actually asks
        # this command to use.
        store.finish_run(run_id, now, status, stats={})

    return PipelineRunOutcome(run_id=run_id, status=status, outcomes=outcomes)


def _run_symbol(
    store: TidemarkStore,
    notifier: TelegramNotifier,
    venue: str,
    symbol: str,
    now: dt.datetime,
) -> SymbolRunOutcome:
    try:
        candles_4h = _candles_to_frame(store.get_candles(venue, symbol, "4h"))
        if len(candles_4h) == 0:
            return SymbolRunOutcome(symbol, False, False, None, "no 4H candles")
        candles_1d = _candles_to_frame(store.get_candles(venue, symbol, "1d"))
        candles_1w = _candles_to_frame(store.get_candles(venue, symbol, "1w"))

        atr_series = compute_atr(candles_4h)
        atr_value = atr_series.iloc[-1]
        record = htf.evaluate(
            symbol,
            candles_4h,
            atr_value,
            candles_1d=candles_1d if len(candles_1d) > 0 else None,
            candles_1w=candles_1w if len(candles_1w) > 0 else None,
        )

        # Fetch the previous row *before* writing this evaluation's row,
        # so "previous" never accidentally means "myself".
        previous = store.latest_journal_entry(symbol, record.rule_version)
        entry = build_journal_entry(record, recorded_at=now)
        write_result = store.save_journal_entry(entry)

        if not write_result.inserted:
            # A repeat of an already-journaled candle: no new row, no
            # change detection, no alert.
            return SymbolRunOutcome(symbol, False, False, None, None)

        reason = detect_change(previous, record)
        if reason is None:
            return SymbolRunOutcome(symbol, True, False, None, None)

        sent = notifier.send_alert(record, reason, symbol)
        store.record_alert_outcome(write_result.entry.id, alert_sent=sent, alert_reason=reason)
        return SymbolRunOutcome(symbol, True, sent, reason, None)
    except Exception as exc:
        logger.error("run failed for symbol=%s error=%s", symbol, exc)
        return SymbolRunOutcome(symbol, False, False, None, str(exc))
