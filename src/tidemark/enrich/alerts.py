"""The alert enricher: composes and sends the Section 1 WATCH alerts that
`tidemark run` decided on but no longer sends itself.

This module is the ONLY place the research engine and `market_intel` meet
(docs/adr/0012-alert-enricher.md). It may read both: journal rows from
`tidemark.data.models`, Section 1 text from `tidemark.notify.telegram`, and
market context from `tidemark.market_intel`. Neither side imports this
module, and the import-boundary test enforces that.

What it decides, and what it does not:
- Which journal entries are alert-worthy is decided by the change detector
  and recorded on the journal row (`alert_reason`). This module only reads
  that decision. It never recomputes it.
- Nothing here writes to `journal_entries` or any research table. Its only
  writes are to market_intel's own cursor tables (`alert_cursor.py`), and
  only on a real send.
- Context that cannot be fetched is rendered as UNAVAILABLE, never zero and
  never inferred. A derivatives outage never suppresses a structural alert.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, inspect, select
from sqlalchemy.orm import Session

from tidemark.data.models import JournalEntry
from tidemark.market_intel import alert_cursor
from tidemark.market_intel.alert_cursor import SentRecord
from tidemark.market_intel.client import CoinalyzeClient
from tidemark.market_intel.context_read import read_section1, section1_from_entry
from tidemark.market_intel.errors import (
    CoinalyzeConnectionError,
    CoinalyzeError,
    CoinalyzeHttpError,
    MissingApiKeyError,
    RateLimitedError,
)
from tidemark.market_intel.future_markets import FutureMarketsCache
from tidemark.market_intel.market_context import (
    BTC_SYMBOL,
    ETH_SYMBOL,
    MarketContextBundle,
    ReferenceSnapshotCache,
    build_market_context,
    fetch_market_context,
    unavailable_asset_context,
)
from tidemark.market_intel.service import fetch_market_intel
from tidemark.market_intel.telegram_render import MARKET_CONTEXT_FOOTER, render_market_context_block
from tidemark.notify.telegram import section1_parts

logger = logging.getLogger(__name__)

WOULD_SEND = "WOULD SEND"
SENT = "SENT"
NO_ALERT = "NO ALERT"
ALREADY_SENT = "ALREADY SENT"
FAILED = "FAILED"


@dataclass(frozen=True)
class EntryDecision:
    journal_entry_id: int
    asset: str
    evaluated_at: dt.datetime
    alert_reason: str | None
    decision: str
    text: str | None = None


@dataclass(frozen=True)
class EnrichOutcome:
    send: bool
    checkpoint_before: int | None
    first_run_baseline: JournalEntry | None
    decisions: tuple[EntryDecision, ...]
    failure: str | None
    checkpoint_after: int | None

    @property
    def first_run(self) -> bool:
        return self.checkpoint_before is None


def _journal_session(database_url: str) -> tuple[Engine, Session]:
    engine = create_engine(database_url)
    return engine, Session(engine)


def latest_journal_entry(database_url: str) -> JournalEntry | None:
    engine, session = _journal_session(database_url)
    try:
        with session:
            return session.scalars(
                select(JournalEntry).order_by(JournalEntry.id.desc()).limit(1)
            ).first()
    finally:
        engine.dispose()


def journal_entries_after(database_url: str, checkpoint: int) -> list[JournalEntry]:
    """Every journal entry strictly after the checkpoint, in id order - a
    missed run therefore yields several entries, all processed in turn.
    """
    engine, session = _journal_session(database_url)
    try:
        with session:
            stmt = (
                select(JournalEntry)
                .where(JournalEntry.id > checkpoint)
                .order_by(JournalEntry.id.asc())
            )
            return list(session.scalars(stmt))
    finally:
        engine.dispose()


def _outage_reason(exc: Exception) -> str:
    """A short, fixed reason - never the raw exception text, which could
    carry request details.
    """
    if isinstance(exc, MissingApiKeyError):
        return "Coinalyze API key is not configured"
    if isinstance(exc, RateLimitedError):
        return "Coinalyze rate limit reached this minute"
    if isinstance(exc, CoinalyzeHttpError):
        return f"Coinalyze returned HTTP {exc.status_code}"
    if isinstance(exc, CoinalyzeConnectionError):
        return "could not reach Coinalyze"
    return "Coinalyze unavailable"


def build_live_bundle(
    entry: JournalEntry,
    now: dt.datetime,
    *,
    database_url: str,
    venue: str,
    api_key,
    reference_cache: ReferenceSnapshotCache | None = None,
) -> MarketContextBundle:
    """The market context for one alert. The coin's Section 1 facts are the
    entry itself; BTC and ETH use their latest stored Section 1 (with the
    freshness guard). Any Coinalyze failure yields UNAVAILABLE context for
    the affected assets, with the Section 1 facts still present.
    """
    coin_section1 = section1_from_entry(entry)
    try:
        client = CoinalyzeClient(api_key=api_key)
        cache = FutureMarketsCache(client)
        snapshot = fetch_market_intel(client, cache, entry.asset, venue, now)
        return fetch_market_context(
            client,
            cache,
            snapshot,
            venue,
            now,
            database_url,
            coin_section1=coin_section1,
            reference_cache=reference_cache,
        )
    except CoinalyzeError as exc:
        reason = _outage_reason(exc)
    except Exception:  # noqa: BLE001 - a derivatives failure must never suppress the alert
        logger.exception("market context failed for %s; sending Section 1 facts only", entry.asset)
        reason = "Coinalyze unavailable"
    return _unavailable_bundle(entry.asset, reason, coin_section1, database_url, now)


def _unavailable_bundle(
    asset: str,
    reason: str,
    coin_section1,
    database_url: str,
    now: dt.datetime,
) -> MarketContextBundle:
    coin = unavailable_asset_context(asset, reason, coin_section1)
    btc = unavailable_asset_context(
        BTC_SYMBOL, reason, read_section1(database_url, BTC_SYMBOL, now)
    )
    eth = unavailable_asset_context(
        ETH_SYMBOL, reason, read_section1(database_url, ETH_SYMBOL, now)
    )
    return build_market_context(coin, btc, eth)


def compose_enriched_alert(
    entry: JournalEntry,
    bundle: MarketContextBundle,
    reference: dt.datetime,
) -> str:
    """The Section 1 facts exactly as `tidemark run` used to send them, then
    the market context block, then the rulebook versions and the footer.
    Every piece comes from an existing renderer; this function only orders
    them.
    """
    header, body = section1_parts(entry, entry.alert_reason, entry.asset)
    lines = [
        header,
        "",
        body,
        "",
        render_market_context_block(bundle, reference),
        "",
        "Rulebooks: " + ", ".join(bundle.rulebook_versions),
        MARKET_CONTEXT_FOOTER,
    ]
    return "\n".join(lines)


BundleBuilder = Callable[[JournalEntry, dt.datetime], MarketContextBundle]
SendText = Callable[[str], bool]


def run_enrichment(
    *,
    database_url: str,
    cursor_engine: Engine,
    now: dt.datetime,
    send: bool,
    build_bundle: BundleBuilder,
    send_text: SendText | None,
) -> EnrichOutcome:
    """Process journal entries after the checkpoint, in order.

    Dry run (`send=False`): composes what would be sent and writes nothing -
    no checkpoint, no sent record, no table creation.

    Send: an entry with no alert reason advances the checkpoint past itself.
    An alert-worthy entry is sent, then recorded and the checkpoint advanced
    past it in one transaction. A failed send stops the run with the
    checkpoint on the last good entry, so the next run retries from there.
    An entry already recorded as sent advances without sending again.
    """
    checkpoint = alert_cursor.get_checkpoint(cursor_engine)

    if checkpoint is None:
        baseline = latest_journal_entry(database_url)
        baseline_id = baseline.id if baseline is not None else 0
        if send:
            alert_cursor.init_alert_cursor_store(cursor_engine)
            alert_cursor.initialise_checkpoint(cursor_engine, baseline_id, now)
        return EnrichOutcome(
            send=send,
            checkpoint_before=None,
            first_run_baseline=baseline,
            decisions=(),
            failure=None,
            checkpoint_after=baseline_id if send else None,
        )

    decisions: list[EntryDecision] = []
    failure: str | None = None
    last_processed = checkpoint

    for entry in journal_entries_after(database_url, checkpoint):
        base = {
            "journal_entry_id": entry.id,
            "asset": entry.asset,
            "evaluated_at": entry.evaluated_at,
            "alert_reason": entry.alert_reason,
        }

        if entry.alert_reason is None:
            if send:
                alert_cursor.advance_checkpoint(cursor_engine, entry.id, now)
            decisions.append(EntryDecision(**base, decision=NO_ALERT))
            last_processed = entry.id
            continue

        if alert_cursor.is_sent(cursor_engine, entry.id):
            if send:
                alert_cursor.advance_checkpoint(cursor_engine, entry.id, now)
            decisions.append(EntryDecision(**base, decision=ALREADY_SENT))
            last_processed = entry.id
            continue

        try:
            bundle = build_bundle(entry, now)
            text = compose_enriched_alert(entry, bundle, now)
        except Exception as exc:  # noqa: BLE001 - nothing reached Telegram, so stop here
            logger.exception("could not compose alert for journal entry %s", entry.id)
            decisions.append(EntryDecision(**base, decision=FAILED))
            failure = f"could not compose the alert for journal entry {entry.id}: {exc}"
            break

        if not send:
            decisions.append(EntryDecision(**base, decision=WOULD_SEND, text=text))
            last_processed = entry.id
            continue

        if send_text is None or not send_text(text):
            decisions.append(EntryDecision(**base, decision=FAILED, text=text))
            failure = (
                f"Telegram send failed for journal entry {entry.id}; the checkpoint stays "
                f"at {last_processed} and the next run retries from here"
            )
            break

        alert_cursor.record_sent(
            cursor_engine,
            entry.id,
            entry.asset,
            entry.evaluated_at,
            entry.alert_reason,
            now,
        )
        decisions.append(EntryDecision(**base, decision=SENT, text=text))
        last_processed = entry.id

    checkpoint_after = alert_cursor.get_checkpoint(cursor_engine) if send else None
    return EnrichOutcome(
        send=send,
        checkpoint_before=checkpoint,
        first_run_baseline=None,
        decisions=tuple(decisions),
        failure=failure,
        checkpoint_after=checkpoint_after,
    )


def sent_journal_alerts(
    database_url: str, since: dt.datetime | None = None
) -> list[tuple[JournalEntry, SentRecord]]:
    """Every alert the enricher actually delivered, joined back to the journal
    row it describes, newest first. The join is read-only and lives here, not
    in the research store, so the research engine never imports market_intel.

    Alerts sent by `tidemark run` before the enricher existed are not in the
    sent table and are not listed; their journal rows keep `alert_sent=True`.
    """
    engine = create_engine(database_url)
    try:
        if not inspect(engine).has_table(alert_cursor.SENT_TABLE):
            return []
        with Session(engine) as session:
            stmt = select(JournalEntry, alert_cursor.SentAlert).join(
                alert_cursor.SentAlert,
                alert_cursor.SentAlert.journal_entry_id == JournalEntry.id,
            )
            if since is not None:
                stmt = stmt.where(JournalEntry.evaluated_at >= since)
            stmt = stmt.order_by(JournalEntry.evaluated_at.desc())
            return [
                (
                    journal,
                    SentRecord(
                        journal_entry_id=sent.journal_entry_id,
                        asset=sent.asset,
                        evaluated_at=sent.evaluated_at,
                        alert_reason=sent.alert_reason,
                        sent_at=sent.sent_at,
                    ),
                )
                for journal, sent in session.execute(stmt).all()
            ]
    finally:
        engine.dispose()
