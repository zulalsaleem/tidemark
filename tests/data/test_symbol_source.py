"""Symbol-source resolution for the observer pipelines (Phase 6, Merge 3).
No network - pure store-backed logic.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tidemark.data.models import UniverseSnapshot, UniverseSnapshotRow
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.data.symbol_source import (
    DEFAULT_STALENESS_THRESHOLD,
    EXPLICIT,
    SNAPSHOT,
    TIDEMARK_SYMBOLS_FALLBACK,
    resolve_symbols,
)

VENUE = "binanceusdm"
NOW = dt.datetime(2026, 9, 27, tzinfo=dt.UTC)
FALLBACK = ["BTC/USDT:USDT", "ETH/USDT:USDT"]


@pytest.fixture
def store() -> TidemarkStore:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    return TidemarkStore(engine)


def _save_snapshot(
    store: TidemarkStore,
    snapshot_id: str,
    snapshot_at: dt.datetime,
    selected: list[str],
    ranked_extra: list[str] | None = None,
) -> None:
    header = UniverseSnapshot(
        snapshot_id=snapshot_id,
        snapshot_at=snapshot_at,
        methodology_version="universe-v2",
        venue=VENUE,
        metric_name="MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D",
        metric_window_days=30,
        k=50,
        n_selected=len(selected),
        provenance="FORWARD",
        candle_hash="deadbeef",
        counts_by_exclusion_reason={},
    )
    rows = []
    rank = 1
    for symbol in selected:
        rows.append(
            UniverseSnapshotRow(
                snapshot_id=snapshot_id,
                symbol=symbol,
                rank=rank,
                metric_value=1000.0 - rank,
                eligible=True,
                selected=True,
                exclusion_reason=None,
            )
        )
        rank += 1
    for symbol in ranked_extra or []:
        rows.append(
            UniverseSnapshotRow(
                snapshot_id=snapshot_id,
                symbol=symbol,
                rank=rank,
                metric_value=1.0,
                eligible=False,
                selected=False,
                exclusion_reason="NOT_ASSESSED",
            )
        )
        rank += 1
    store.save_universe_snapshot(header, rows)


def test_explicit_symbols_always_win(store: TidemarkStore) -> None:
    _save_snapshot(store, "snap-1", NOW, ["SOL/USDT:USDT"])

    resolved = resolve_symbols(store, VENUE, ["ADA/USDT:USDT"], FALLBACK, NOW)

    assert resolved.symbols == ["ADA/USDT:USDT"]
    assert resolved.source == EXPLICIT
    assert resolved.snapshot_id is None
    assert resolved.warning is None


def test_snapshot_symbols_used_when_fresh_and_no_explicit_override(store: TidemarkStore) -> None:
    _save_snapshot(store, "snap-1", NOW - dt.timedelta(hours=2), ["SOL/USDT:USDT", "ADA/USDT:USDT"])

    resolved = resolve_symbols(store, VENUE, None, FALLBACK, NOW)

    assert resolved.symbols == ["SOL/USDT:USDT", "ADA/USDT:USDT"]
    assert resolved.source == SNAPSHOT
    assert resolved.snapshot_id == "snap-1"
    assert resolved.warning is None


def test_snapshot_symbols_are_in_rank_order(store: TidemarkStore) -> None:
    """Selected symbols come back in rank order, not insertion order."""
    header = UniverseSnapshot(
        snapshot_id="snap-1",
        snapshot_at=NOW,
        methodology_version="universe-v2",
        venue=VENUE,
        metric_name="MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D",
        metric_window_days=30,
        k=50,
        n_selected=2,
        provenance="FORWARD",
        candle_hash="deadbeef",
        counts_by_exclusion_reason={},
    )
    rows = [
        UniverseSnapshotRow(
            snapshot_id="snap-1",
            symbol="SECOND/USDT:USDT",
            rank=2,
            metric_value=5.0,
            eligible=True,
            selected=True,
            exclusion_reason=None,
        ),
        UniverseSnapshotRow(
            snapshot_id="snap-1",
            symbol="FIRST/USDT:USDT",
            rank=1,
            metric_value=10.0,
            eligible=True,
            selected=True,
            exclusion_reason=None,
        ),
    ]
    store.save_universe_snapshot(header, rows)

    resolved = resolve_symbols(store, VENUE, None, FALLBACK, NOW)

    assert resolved.symbols == ["FIRST/USDT:USDT", "SECOND/USDT:USDT"]


def test_fallback_fires_with_a_warning_when_no_snapshot_exists(store: TidemarkStore) -> None:
    resolved = resolve_symbols(store, VENUE, None, FALLBACK, NOW)

    assert resolved.symbols == FALLBACK
    assert resolved.source == TIDEMARK_SYMBOLS_FALLBACK
    assert resolved.snapshot_id is None
    assert resolved.warning is not None
    assert "no universe snapshot" in resolved.warning.lower()


def test_stale_snapshot_falls_back_and_warns(store: TidemarkStore) -> None:
    stale_at = NOW - DEFAULT_STALENESS_THRESHOLD - dt.timedelta(hours=1)
    _save_snapshot(store, "old-snap", stale_at, ["SOL/USDT:USDT"])

    resolved = resolve_symbols(store, VENUE, None, FALLBACK, NOW)

    assert resolved.symbols == FALLBACK
    assert resolved.source == TIDEMARK_SYMBOLS_FALLBACK
    assert resolved.snapshot_id is None
    assert resolved.warning is not None
    assert "old-snap" in resolved.warning
    assert "stale" in resolved.warning.lower() or "old" in resolved.warning.lower()


def test_snapshot_exactly_at_the_staleness_threshold_is_still_used(store: TidemarkStore) -> None:
    edge_at = NOW - DEFAULT_STALENESS_THRESHOLD
    _save_snapshot(store, "edge-snap", edge_at, ["SOL/USDT:USDT"])

    resolved = resolve_symbols(store, VENUE, None, FALLBACK, NOW)

    assert resolved.source == SNAPSHOT
    assert resolved.snapshot_id == "edge-snap"


def test_custom_staleness_threshold_is_honored(store: TidemarkStore) -> None:
    _save_snapshot(store, "snap-1", NOW - dt.timedelta(hours=3), ["SOL/USDT:USDT"])

    resolved = resolve_symbols(
        store, VENUE, None, FALLBACK, NOW, staleness_threshold=dt.timedelta(hours=1)
    )

    assert resolved.source == TIDEMARK_SYMBOLS_FALLBACK


def test_snapshot_with_no_selected_symbols_falls_back_and_warns(store: TidemarkStore) -> None:
    _save_snapshot(store, "snap-1", NOW, selected=[], ranked_extra=["SOL/USDT:USDT"])

    resolved = resolve_symbols(store, VENUE, None, FALLBACK, NOW)

    assert resolved.symbols == FALLBACK
    assert resolved.source == TIDEMARK_SYMBOLS_FALLBACK
    assert resolved.warning is not None
    assert "selected no symbols" in resolved.warning.lower()


def test_latest_snapshot_is_used_when_several_exist(store: TidemarkStore) -> None:
    _save_snapshot(store, "older", NOW - dt.timedelta(hours=10), ["OLD/USDT:USDT"])
    _save_snapshot(store, "newer", NOW - dt.timedelta(hours=1), ["NEW/USDT:USDT"])

    resolved = resolve_symbols(store, VENUE, None, FALLBACK, NOW)

    assert resolved.snapshot_id == "newer"
    assert resolved.symbols == ["NEW/USDT:USDT"]


def test_empty_explicit_symbols_list_does_not_count_as_an_override(store: TidemarkStore) -> None:
    """An empty --symbols list (nothing passed) must fall through to
    snapshot/fallback resolution, not be treated as 'explicitly zero
    symbols'."""
    _save_snapshot(store, "snap-1", NOW, ["SOL/USDT:USDT"])

    resolved = resolve_symbols(store, VENUE, [], FALLBACK, NOW)

    assert resolved.source == SNAPSHOT
