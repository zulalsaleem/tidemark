"""Full-timeframe candle sync for the currently selected universe (Phase
6, Merge 3).

`tidemark universe sync` backfills every timeframe the Section 1/2
engines need (1H, 4H, 1D, 1W) for the SELECTED symbols of the latest
universe snapshot only — never a symbol outside the selection. Reuses
`data/ingest.py`'s existing chunked-upsert backfill path unmodified: same
idempotent upsert (re-running with the same `--days` inserts zero new
duplicates), same per-symbol/timeframe failure isolation.

This is deliberately simpler than `data/symbol_source.py`'s resolution:
sync's job is preparing candles for whatever the latest snapshot says,
independent of whether an observer run would currently trust that
snapshot for evaluation (that staleness judgment is
`resolve_symbols`'s alone). Sync never applies a staleness fallback and
never touches `TIDEMARK_SYMBOLS`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from tidemark.data.exchange import ExchangeClient
from tidemark.data.ingest import RunOutcome, SymbolTimeframeOutcome, run_backfill
from tidemark.data.store import TidemarkStore

# Every timeframe context/htf.py (4H/1D/1W) and context/mtf.py (1H) read.
SYNC_TIMEFRAMES: tuple[str, ...] = ("1h", "4h", "1d", "1w")

# Matches data/universe_snapshot.py's own 4H backfill default - not itself
# a rulebook parameter.
DEFAULT_SYNC_DAYS = 180


def selected_symbols(store: TidemarkStore, venue: str) -> list[str]:
    """The SELECTED symbols of the latest universe snapshot for `venue`,
    in rank order; empty if no snapshot exists for it.
    """
    snapshots = store.universe_snapshots(venue)
    if not snapshots:
        return []
    rows = store.universe_snapshot_rows(snapshots[0].snapshot_id)
    return [row.symbol for row in rows if row.selected]


def run_universe_sync(
    store: TidemarkStore,
    exchange: ExchangeClient,
    venue: str,
    days: int = DEFAULT_SYNC_DAYS,
    now: dt.datetime | None = None,
    on_outcome: Callable[[SymbolTimeframeOutcome], None] | None = None,
) -> RunOutcome:
    """Backfill 1H/4H/1D/1W for the SELECTED symbols of the latest
    universe snapshot. A no-op (empty symbol list, COMPLETED) if no
    snapshot exists or it selected nothing.
    """
    symbols = selected_symbols(store, venue)
    return run_backfill(
        store, exchange, venue, symbols, list(SYNC_TIMEFRAMES), days, now=now, on_outcome=on_outcome
    )
