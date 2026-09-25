"""Universe snapshot generation (Phase 6, Merge 2B, PARTS B/D).

Orchestrates the full generate-a-snapshot flow in the order PART B fixes
exactly:
  1. Rank all ACTIVE registry symbols by the volume metric, descending.
  2. Take the top K = 50 as the assessment set.
  3. Backfill 4H candles for those 50 only, if not already stored.
  4. Assess Section 1 eligibility for those 50.
  5. Select the top N = 30 eligible by rank.

K = 50 is ARCHITECTURAL — headroom over N = 30 so ineligible symbols in
the assessment set can be skipped without running out of candidates. It
is not a tuned parameter and must not be swept (docs/adr/0009).

FORWARD vs BACKFILLED (UNIV-06, UNIVERSE_AS_OF_INVARIANT): omitting
`as_of` generates a live snapshot at `now` — step 3 may reach the
exchange, and eligibility found here is cached onto `market_registry`
(`section1_first_usable_at`/`section1_eligibility_checked_at`) so a
later snapshot never has to recompute a symbol's whole history. Giving
`as_of` reconstructs what a snapshot would have looked like at that past
timestamp — step 3 never touches the network (there is nothing to
backfill "as of the past"), and nothing is ever written back to the
registry cache, since a value computed from data truncated at an earlier
T must never be treated as the general, full-history truth for a later
FORWARD run to trust.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass

from tidemark.data.exchange import ExchangeClient
from tidemark.data.ingest import run_backfill
from tidemark.data.models import Candle, MarketRegistry, UniverseSnapshot, UniverseSnapshotRow
from tidemark.data.store import TidemarkStore, UniverseSnapshotWriteResult
from tidemark.data.universe_eligibility import (
    INSUFFICIENT_VOLUME_HISTORY,
    NOT_ASSESSED,
    EligibilityAssessment,
    assess_section1_eligibility,
)
from tidemark.data.universe_metric import (
    METRIC_NAME,
    METRIC_WINDOW_DAYS,
    median_daily_derived_quote_volume_30d,
)

METHODOLOGY_VERSION = "universe-v1"

K = 50  # architectural: headroom over N, never swept - see ADR 0009 UNIV-03
N = 30

# Matches data/ingest.py's own DEFAULT_UPDATE_FALLBACK_DAYS - not itself a
# rulebook parameter, just how far back the assessment-set 4H backfill
# reaches when a symbol has no 4H data stored yet.
DEFAULT_4H_BACKFILL_DAYS = 180


@dataclass(frozen=True)
class RankedSymbol:
    """One ACTIVE registry symbol's position in the metric ranking."""

    symbol: str
    metric_value: float | None


def rank_symbols(metrics: dict[str, float | None]) -> list[RankedSymbol]:
    """Rank symbols by metric value, descending (PART B step 1).

    Symbols with no computable metric (fewer than
    `METRIC_WINDOW_DAYS` closed daily candles) sort after every symbol
    that has one — a symbol can't be ranked by a value it doesn't have.
    Ties, including among every "no metric" symbol, are broken
    alphabetically by symbol: deterministic and reproducible, so the same
    (venue, candle data) always ranks the same way (PART G).
    """

    def _key(symbol: str) -> tuple:
        value = metrics[symbol]
        has_value = value is not None
        return (0 if has_value else 1, -(value if has_value else 0.0), symbol)

    ordered = sorted(metrics, key=_key)
    return [RankedSymbol(symbol=s, metric_value=metrics[s]) for s in ordered]


def _truncate(candles: list[Candle], as_of: dt.datetime) -> list[Candle]:
    return [c for c in candles if c.close_time <= as_of]


def _load_metrics(
    store: TidemarkStore, venue: str, registry_rows: list[MarketRegistry], as_of: dt.datetime
) -> dict[str, list[Candle]]:
    """Return each symbol's daily candles truncated to `as_of` - both the
    metric input and (later) the candle-hash input, read once.
    """
    return {
        row.symbol: _truncate(store.get_candles(venue, row.symbol, "1d"), as_of)
        for row in registry_rows
    }


def _assess_symbol(
    store: TidemarkStore, venue: str, symbol: str, as_of: dt.datetime
) -> tuple[EligibilityAssessment, list[Candle], list[Candle]]:
    """Load truncated 4H/1D/1W candles for one symbol and assess Section
    1 eligibility against them. Returns (assessment, candles_4h, candles_1w)
    so the caller can fold the same rows into the snapshot's candle hash
    without a second query.
    """
    candles_4h = _truncate(store.get_candles(venue, symbol, "4h"), as_of)
    candles_1d = _truncate(store.get_candles(venue, symbol, "1d"), as_of)
    candles_1w = _truncate(store.get_candles(venue, symbol, "1w"), as_of)
    rejected = store.count_rejected_candles(venue, symbol, "4h", as_of=as_of)
    assessment = assess_section1_eligibility(
        symbol, candles_4h, candles_1d, candles_1w, as_of, rejected
    )
    return assessment, candles_4h, candles_1w


def _backfill_missing_4h(
    store: TidemarkStore,
    exchange: ExchangeClient,
    venue: str,
    assessment_set: list[RankedSymbol],
    now: dt.datetime,
    days: int,
) -> None:
    """Backfill 4H candles for assessment-set symbols with none stored yet
    (PART B step 3) - "if not already stored" is read literally: a symbol
    that already has any 4H history is left exactly as it is, not
    incrementally updated here.
    """
    missing = [
        rs.symbol
        for rs in assessment_set
        if rs.metric_value is not None and store.count_candles(venue, rs.symbol, "4h") == 0
    ]
    if missing:
        run_backfill(store, exchange, venue, missing, ["4h"], days, now=now)


def _candle_hash_row(venue: str, symbol: str, timeframe: str, candle: Candle) -> str:
    return "|".join(
        str(v)
        for v in (
            venue,
            symbol,
            timeframe,
            candle.open_time.isoformat(),
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
        )
    )


def generate_universe_snapshot(
    store: TidemarkStore,
    exchange: ExchangeClient | None,
    venue: str,
    as_of: dt.datetime | None = None,
    now: dt.datetime | None = None,
    four_h_backfill_days: int = DEFAULT_4H_BACKFILL_DAYS,
) -> UniverseSnapshotWriteResult:
    """Generate and persist one universe snapshot.

    See the module docstring for the FORWARD (`as_of=None`) vs BACKFILLED
    distinction. `exchange` is required for a FORWARD snapshot (step 3
    may need it) and is never touched for a BACKFILLED one.
    """
    now = now or dt.datetime.now(dt.UTC)
    is_forward = as_of is None
    snapshot_at = now if is_forward else as_of
    provenance = "FORWARD" if is_forward else "BACKFILLED"

    if is_forward and exchange is None:
        raise ValueError("exchange is required to generate a FORWARD snapshot")

    # Registry membership as of `snapshot_at`: `status` alone can't say
    # whether a symbol was active or absent AT an earlier T (the schema
    # tracks current status, not a status history), so a BACKFILLED
    # snapshot is additionally restricted to symbols already listed by
    # `snapshot_at` - the best point-in-time approximation the current
    # schema supports (see docs/adr/0009's Merge 2B addendum). This is a
    # no-op restriction for a FORWARD snapshot, where snapshot_at == now.
    registry_rows = [
        row
        for row in store.market_registry(venue)
        if row.status == "ACTIVE" and row.first_seen_in_venue_list_at <= snapshot_at
    ]

    # -- PART B step 1: rank every ACTIVE symbol by the metric ----------------
    daily_by_symbol = _load_metrics(store, venue, registry_rows, snapshot_at)
    metrics = {
        symbol: median_daily_derived_quote_volume_30d(candles, snapshot_at)
        for symbol, candles in daily_by_symbol.items()
    }
    ranked = rank_symbols(metrics)

    # -- PART B step 2: top K as the assessment set ---------------------------
    assessment_set = ranked[:K]
    assessment_symbols = {rs.symbol for rs in assessment_set if rs.metric_value is not None}

    # -- PART B step 3: backfill 4H for the assessment set (FORWARD only) -----
    if is_forward:
        _backfill_missing_4h(store, exchange, venue, assessment_set, now, four_h_backfill_days)

    # -- PART B steps 4-5: assess eligibility, then rows in rank order --------
    hash_rows: list[str] = []
    rows: list[UniverseSnapshotRow] = []
    exclusion_counts: dict[str, int] = {}

    registry_by_symbol = {row.symbol: row for row in registry_rows}

    for symbol, candles in daily_by_symbol.items():
        for c in candles:
            hash_rows.append(_candle_hash_row(venue, symbol, "1d", c))

    for rank, rs in enumerate(ranked, start=1):
        cached_first_usable_at = None
        if is_forward:
            registry_row = registry_by_symbol.get(rs.symbol)
            if registry_row is not None:
                cached_first_usable_at = registry_row.section1_first_usable_at

        if rs.metric_value is None:
            eligible: bool | None = False
            reason: str | None = INSUFFICIENT_VOLUME_HISTORY
            first_usable_at = None
        elif rs.symbol not in assessment_symbols:
            eligible = None
            reason = NOT_ASSESSED
            first_usable_at = None
        elif cached_first_usable_at is not None:
            # PART C: "persist it so a later snapshot does not recompute
            # the whole history" - a FORWARD run with an already-cached
            # exit needs no candles at all to know the symbol is still
            # eligible (once true, always true - a first exit doesn't
            # un-happen).
            eligible = True
            reason = None
            first_usable_at = cached_first_usable_at
            store.record_section1_eligibility(venue, rs.symbol, first_usable_at, now)
        else:
            assessment, candles_4h, candles_1w = _assess_symbol(
                store, venue, rs.symbol, snapshot_at
            )
            for c in candles_4h:
                hash_rows.append(_candle_hash_row(venue, rs.symbol, "4h", c))
            for c in candles_1w:
                hash_rows.append(_candle_hash_row(venue, rs.symbol, "1w", c))
            eligible = assessment.eligible
            reason = assessment.exclusion_reason
            first_usable_at = assessment.section1_first_usable_at

            # Cache eligibility onto the registry, FORWARD only (see
            # module docstring) - a BACKFILLED computation from data
            # truncated at an earlier T must never overwrite the
            # registry's current, full-history knowledge.
            if is_forward:
                store.record_section1_eligibility(venue, rs.symbol, first_usable_at, now)

        if reason is not None:
            exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1

        rows.append(
            UniverseSnapshotRow(
                symbol=rs.symbol,
                rank=rank,
                metric_value=rs.metric_value,
                eligible=eligible,
                selected=False,
                exclusion_reason=reason,
            )
        )

    # -- select the top N eligible, in the rank order already established ----
    selected_count = 0
    for row in rows:
        if row.eligible is True and selected_count < N:
            row.selected = True
            selected_count += 1

    candle_hash = hashlib.sha256("\n".join(sorted(hash_rows)).encode("utf-8")).hexdigest()

    header = UniverseSnapshot(
        snapshot_id=uuid.uuid4().hex,
        snapshot_at=snapshot_at,
        methodology_version=METHODOLOGY_VERSION,
        venue=venue,
        metric_name=METRIC_NAME,
        metric_window_days=METRIC_WINDOW_DAYS,
        k=K,
        n_selected=selected_count,
        provenance=provenance,
        candle_hash=candle_hash,
        counts_by_exclusion_reason=exclusion_counts,
    )

    return store.save_universe_snapshot(header, rows)
