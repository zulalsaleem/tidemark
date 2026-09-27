"""Symbol-source resolution for the observer pipelines (Phase 6, Merge 3).

`tidemark run`, `tidemark observe run`, and the health check must all
agree on which symbols they operate over. Resolution order:

  1. An explicit `--symbols` argument — unchanged behaviour, always wins.
  2. The SELECTED symbols of the latest universe snapshot for the venue,
     if one exists and isn't stale.
  3. `TIDEMARK_SYMBOLS` (`settings.symbol_list()`) as a last-resort
     fallback, with a warning naming which source was used and why.

`TIDEMARK_SYMBOLS` is not deleted — per docs/adr/0009-universe-selection-
architecture.md, it remains a manual/development/test override forever.
A missing or stale snapshot is never a crash: this falls back and logs a
warning rather than raising, mirroring the standing "no setups found is a
success, not a failure" spirit for the case where no *universe* exists
yet either.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from tidemark.data.store import TidemarkStore

logger = logging.getLogger(__name__)

EXPLICIT = "EXPLICIT"
SNAPSHOT = "SNAPSHOT"
TIDEMARK_SYMBOLS_FALLBACK = "TIDEMARK_SYMBOLS_FALLBACK"

# Configurable via Settings.universe_staleness_hours; this is the default
# a caller gets if it doesn't pass its own.
DEFAULT_STALENESS_THRESHOLD = dt.timedelta(hours=48)


@dataclass(frozen=True)
class ResolvedSymbols:
    """The symbol list a pipeline should run over, and where it came from."""

    symbols: list[str]
    source: str
    snapshot_id: str | None
    warning: str | None


def resolve_symbols(
    store: TidemarkStore,
    venue: str,
    explicit_symbols: list[str] | None,
    fallback_symbols: list[str],
    now: dt.datetime,
    staleness_threshold: dt.timedelta = DEFAULT_STALENESS_THRESHOLD,
) -> ResolvedSymbols:
    """Resolve which symbols a run should operate over.

    `explicit_symbols` (an operator-given `--symbols` flag) always wins,
    exactly as before Merge 3. Otherwise, the latest universe snapshot
    for `venue` is used if one exists and its `snapshot_at` is no older
    than `staleness_threshold` and it selected at least one symbol — its
    SELECTED rows only, in rank order (the order `TidemarkStore.
    universe_snapshot_rows` already returns). Otherwise `fallback_symbols`
    (`TIDEMARK_SYMBOLS`) is used, and a warning is logged and returned
    naming why.
    """
    if explicit_symbols:
        return ResolvedSymbols(
            symbols=list(explicit_symbols), source=EXPLICIT, snapshot_id=None, warning=None
        )

    snapshots = store.universe_snapshots(venue)
    latest = snapshots[0] if snapshots else None

    if latest is None:
        warning = (
            f"No universe snapshot exists for venue={venue!r}; falling back to "
            "TIDEMARK_SYMBOLS. Run `tidemark universe snapshot` to generate one."
        )
        logger.warning(warning)
        return ResolvedSymbols(
            symbols=list(fallback_symbols),
            source=TIDEMARK_SYMBOLS_FALLBACK,
            snapshot_id=None,
            warning=warning,
        )

    age = now - latest.snapshot_at
    if age > staleness_threshold:
        warning = (
            f"Latest universe snapshot {latest.snapshot_id!r} is {age} old "
            f"(> {staleness_threshold}); falling back to TIDEMARK_SYMBOLS."
        )
        logger.warning(warning)
        return ResolvedSymbols(
            symbols=list(fallback_symbols),
            source=TIDEMARK_SYMBOLS_FALLBACK,
            snapshot_id=None,
            warning=warning,
        )

    rows = store.universe_snapshot_rows(latest.snapshot_id)
    selected = [row.symbol for row in rows if row.selected]
    if not selected:
        warning = (
            f"Latest universe snapshot {latest.snapshot_id!r} selected no symbols; "
            "falling back to TIDEMARK_SYMBOLS."
        )
        logger.warning(warning)
        return ResolvedSymbols(
            symbols=list(fallback_symbols),
            source=TIDEMARK_SYMBOLS_FALLBACK,
            snapshot_id=None,
            warning=warning,
        )

    return ResolvedSymbols(
        symbols=selected, source=SNAPSHOT, snapshot_id=latest.snapshot_id, warning=None
    )
