"""Venue symbol discovery (Phase 6, Merge 2A, PART A).

Lists active USDT-quoted perpetual contracts on the configured venue via
`ExchangeClient.list_perpetual_symbols` (ccxt's unified `load_markets` —
never a venue-specific raw endpoint, never touching the candle-fetch
path) and syncs `market_registry` against that listing. A separate,
additive step from candle ingestion (`data/ingest.py`): discovery only
writes to `market_registry`, never to `candles`.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass

from tidemark.data.exchange import ExchangeClient
from tidemark.data.store import TidemarkStore

logger = logging.getLogger(__name__)

DEFAULT_QUOTE_CURRENCY = "USDT"


@dataclass(frozen=True)
class DiscoveryOutcome:
    """The result of one `tidemark universe discover` run."""

    run_id: str
    status: str
    discovered: int  # symbols currently listed, upserted as ACTIVE
    marked_absent: int  # previously-registered symbols no longer listed


def run_discovery(
    store: TidemarkStore,
    exchange: ExchangeClient,
    venue: str,
    quote_currency: str = DEFAULT_QUOTE_CURRENCY,
    now: dt.datetime | None = None,
) -> DiscoveryOutcome:
    """List the venue's active perpetual contracts and sync
    `market_registry` against them.

    Every currently-listed symbol is upserted ACTIVE via
    `record_market_listing` (idempotent: re-running only bumps
    `last_seen_in_venue_list_at`, never inserts a duplicate). Every
    previously-registered symbol no longer in the listing is marked
    ABSENT_FROM_VENUE via `mark_absent_from_venue` — never deleted, since
    the registry is the historical record of what the venue has ever
    contained (docs/adr/0009-universe-selection-architecture.md).

    A single listing call either wholly succeeds or wholly fails — unlike
    `data/ingest.py`'s per-symbol/timeframe network calls, there is no
    per-symbol network step here to isolate a partial failure from, so
    this reports COMPLETED or FAILED only, never PARTIAL.
    """
    now = now or dt.datetime.now(dt.UTC)
    run_id = uuid.uuid4().hex
    store.start_run(run_id, "discover", now)

    # If an exception escapes below, `status` stays FAILED and `finally`
    # still records that — mirrors data/ingest.py's crash-safety pattern.
    status = "FAILED"
    discovered = 0
    marked_absent = 0
    try:
        listings = exchange.list_perpetual_symbols(quote_currency=quote_currency)
        symbols_seen = {listing.symbol for listing in listings}

        for listing in listings:
            store.record_market_listing(
                venue=venue,
                symbol=listing.symbol,
                contract_type=listing.contract_type,
                quote_currency=listing.quote_currency,
                seen_at=now,
            )
            discovered += 1

        marked_absent = store.mark_absent_from_venue(venue, symbols_seen)
        status = "COMPLETED"
    finally:
        store.finish_run(run_id, now, status, stats={})

    return DiscoveryOutcome(
        run_id=run_id, status=status, discovered=discovered, marked_absent=marked_absent
    )
