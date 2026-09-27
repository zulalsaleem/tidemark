"""Orchestrates the hourly BTC briefing: fetch the classifier's inputs,
classify, read BTC's stored Section 1 structure (the one narrow boundary
exception - see `context_read.py`), decide whether this evaluation is
worth sending, render the message, and record every evaluation, sent or
not, in `market_intel`'s own table.

State-change alerting, per docs/adr/0011-market-intelligence-layer.md:
- NO_MATCH never sends, regardless of whether it differs from the
  previous evaluation.
- Otherwise, a classification differing from the immediately PRIOR
  evaluation (sent or not - mirroring `journal.changes`'s own "compare
  against the previous recorded row" pattern) sends.
- Otherwise, BTC's stored Section 1 (state, watch) differing from what
  the last SENT briefing carried also sends - a deliberately different
  baseline from the classification check, since the point of this
  trigger is "what I was last actually told about structure is now
  stale", not "structure ticked between two evaluations nobody saw".
- Anything else records only.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import Engine

from tidemark.market_intel.briefing_data import BTC_CCXT_SYMBOL, INTERVAL, fetch_classifier_inputs
from tidemark.market_intel.clamping import closed_period
from tidemark.market_intel.client import CoinalyzeClient
from tidemark.market_intel.context_read import is_stale, read_latest_context_record
from tidemark.market_intel.derivatives_classifier import NO_MATCH, ClassificationResult, classify
from tidemark.market_intel.evaluation_store import (
    EvaluationRecord,
    latest_evaluation_before,
    latest_sent_evaluation_before,
    record_evaluation,
)
from tidemark.market_intel.future_markets import FutureMarketsCache
from tidemark.market_intel.telegram_render import render_briefing

CLASSIFICATION_CHANGED = "classification_changed"
STRUCTURAL_CHANGE = "structural_change"


@dataclass(frozen=True)
class StructureSnapshot:
    """BTC's stored Section 1 result, or why it isn't shown."""

    available: bool
    stale: bool = False
    state: str | None = None
    watch: str | None = None
    grade: str | None = None
    rule_version: str | None = None
    evaluated_at: dt.datetime | None = None


@dataclass(frozen=True)
class BriefingResult:
    evaluated_at: dt.datetime
    classification: ClassificationResult
    structure: StructureSnapshot
    should_send: bool
    send_reason: str | None
    message: str


def _read_structure(database_url: str, now: dt.datetime) -> StructureSnapshot:
    record = read_latest_context_record(database_url, BTC_CCXT_SYMBOL)
    if record is None:
        return StructureSnapshot(available=False)
    if is_stale(record, now):
        return StructureSnapshot(available=False, stale=True)
    return StructureSnapshot(
        available=True,
        state=record.state,
        watch=record.watch,
        grade=record.grade,
        rule_version=record.rule_version,
        evaluated_at=record.evaluated_at,
    )


def _decide_send(
    current_classification: str,
    previous_classification: str | None,
    current_structure_key: tuple[str | None, str | None],
    last_sent_structure_key: tuple[str | None, str | None] | None,
) -> tuple[bool, str | None]:
    if current_classification == NO_MATCH:
        return False, None
    if previous_classification is not None and current_classification != previous_classification:
        return True, CLASSIFICATION_CHANGED
    if last_sent_structure_key is not None and current_structure_key != last_sent_structure_key:
        return True, STRUCTURAL_CHANGE
    return False, None


def _evaluation_record(
    classification: ClassificationResult,
    structure: StructureSnapshot,
    evaluated_at: dt.datetime,
    now: dt.datetime,
    sent: bool,
    send_reason: str | None,
) -> EvaluationRecord:
    price, oi, funding = classification.price, classification.open_interest, classification.funding
    return EvaluationRecord(
        asset=BTC_CCXT_SYMBOL,
        evaluated_at=evaluated_at,
        recorded_at=now,
        rulebook_version=classification.rulebook_version,
        classification=classification.result,
        classification_reason=classification.reason,
        price_status=price.status,
        price_change_pct=price.change_pct,
        price_period_start=price.period_start,
        price_period_close=price.period_close,
        oi_status=oi.status,
        oi_change_pct=oi.change_pct,
        oi_period_start=oi.period_start,
        oi_period_close=oi.period_close,
        funding_status=funding.status,
        funding_value=funding.value,
        funding_previous_value=funding.previous_value,
        funding_period_start=funding.period_start,
        funding_period_close=funding.period_close,
        structure_available=structure.available,
        structure_state=structure.state,
        structure_watch=structure.watch,
        structure_grade=structure.grade,
        structure_rule_version=structure.rule_version,
        structure_evaluated_at=structure.evaluated_at,
        sent=sent,
        send_reason=send_reason,
    )


def evaluate_briefing(
    client: CoinalyzeClient,
    cache: FutureMarketsCache,
    evaluation_engine: Engine,
    database_url: str,
    venue: str,
    now: dt.datetime,
) -> BriefingResult:
    """Runs one full hourly evaluation and records it - always, whether
    or not it turns out to be worth sending. Delivering it via Telegram
    is the caller's job (see `cli.py`): this function only decides
    whether it SHOULD be sent and persists that decision as `sent=False`
    with `send_reason` still recorded, so a caller that later actually
    delivers it can update just those two fields afterward (see
    `evaluation_store.record_evaluation`).
    """
    price, oi, funding = fetch_classifier_inputs(client, cache, venue, now)
    classification = classify(price, oi, funding)
    evaluated_at = closed_period(now, INTERVAL).close

    structure = _read_structure(database_url, now)
    current_structure_key = (structure.state, structure.watch)

    previous = latest_evaluation_before(evaluation_engine, BTC_CCXT_SYMBOL, evaluated_at)
    previous_classification = previous.classification if previous is not None else None

    last_sent = latest_sent_evaluation_before(evaluation_engine, BTC_CCXT_SYMBOL, evaluated_at)
    last_sent_structure_key = (
        (last_sent.structure_state, last_sent.structure_watch) if last_sent is not None else None
    )

    should_send, send_reason = _decide_send(
        classification.result,
        previous_classification,
        current_structure_key,
        last_sent_structure_key,
    )

    message = render_briefing(structure, classification, now)

    record_evaluation(
        evaluation_engine,
        _evaluation_record(
            classification, structure, evaluated_at, now, sent=False, send_reason=send_reason
        ),
    )

    return BriefingResult(
        evaluated_at=evaluated_at,
        classification=classification,
        structure=structure,
        should_send=should_send,
        send_reason=send_reason,
        message=message,
    )


def mark_sent(
    evaluation_engine: Engine,
    result: BriefingResult,
    now: dt.datetime,
) -> None:
    """Updates the already-recorded evaluation's `sent`/`send_reason`
    after a real Telegram delivery - the one narrow field-level exception
    `evaluation_store.record_evaluation` allows on a repeat write.
    """
    record_evaluation(
        evaluation_engine,
        _evaluation_record(
            result.classification,
            result.structure,
            result.evaluated_at,
            now,
            sent=True,
            send_reason=result.send_reason,
        ),
    )
