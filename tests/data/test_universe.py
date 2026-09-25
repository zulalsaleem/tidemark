"""Phase 6, Merge 1: market_registry and universe_snapshot store methods.

Additive-only schema and read/write plumbing - no selection or eligibility
logic exists yet (that's Merge 2). See
docs/adr/0009-universe-selection-architecture.md.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import inspect

from tidemark.data.models import MarketRegistry, UniverseSnapshot, UniverseSnapshotRow
from tidemark.data.store import (
    TidemarkStore,
    UniverseSnapshotWriteResult,
    create_store_engine,
    init_db,
)

VENUE = "binanceusdm"
SYMBOL = "BTC/USDT:USDT"


@pytest.fixture
def store() -> TidemarkStore:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    return TidemarkStore(engine)


def test_init_db_creates_universe_tables() -> None:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"market_registry", "universe_snapshot", "universe_snapshot_row"} <= tables


def test_candle_table_gained_no_new_column() -> None:
    """Hard constraint: no nullable column was added to `Candle` for this
    merge - quote volume is derived at read time, never ingested/stored.
    """
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    columns = {col["name"] for col in inspect(engine).get_columns("candles")}
    assert columns == {
        "id",
        "venue",
        "symbol",
        "timeframe",
        "open_time",
        "close_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "fetched_at",
    }


# -- market_registry ----------------------------------------------------------


def _registry_row(symbol: str = SYMBOL, status: str = "ACTIVE", **overrides) -> MarketRegistry:
    base = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    defaults = dict(
        venue=VENUE,
        symbol=symbol,
        contract_type="perpetual",
        quote_currency="USDT",
        first_candle_seen_at=base,
        last_candle_seen_at=base + dt.timedelta(days=1),
        first_seen_in_venue_list_at=base,
        last_seen_in_venue_list_at=base + dt.timedelta(days=1),
        status=status,
        section1_first_usable_at=None,
        section1_eligibility_checked_at=None,
    )
    defaults.update(overrides)
    return MarketRegistry(**defaults)


def test_upsert_market_registry_row_inserts_a_new_row(store: TidemarkStore) -> None:
    row = store.upsert_market_registry_row(_registry_row())

    assert row.id is not None
    assert row.venue == VENUE
    assert row.symbol == SYMBOL
    assert row.status == "ACTIVE"

    [stored] = store.market_registry(VENUE)
    assert stored.symbol == SYMBOL


def test_upsert_market_registry_row_is_idempotent_on_repeat(store: TidemarkStore) -> None:
    store.upsert_market_registry_row(_registry_row(status="ACTIVE"))
    store.upsert_market_registry_row(_registry_row(status="STALE"))

    rows = store.market_registry(VENUE)
    assert len(rows) == 1
    assert rows[0].status == "STALE"


def test_upsert_market_registry_row_keys_on_venue_and_symbol(store: TidemarkStore) -> None:
    store.upsert_market_registry_row(_registry_row(symbol="BTC/USDT:USDT"))
    store.upsert_market_registry_row(_registry_row(symbol="ETH/USDT:USDT"))

    rows = store.market_registry(VENUE)
    assert {r.symbol for r in rows} == {"BTC/USDT:USDT", "ETH/USDT:USDT"}


def test_market_registry_row_returns_single_entry(store: TidemarkStore) -> None:
    store.upsert_market_registry_row(_registry_row())

    row = store.market_registry_row(VENUE, SYMBOL)
    assert row is not None
    assert row.symbol == SYMBOL


def test_market_registry_row_none_when_missing(store: TidemarkStore) -> None:
    assert store.market_registry_row(VENUE, SYMBOL) is None


def test_market_registry_timestamps_are_utc_aware(store: TidemarkStore) -> None:
    store.upsert_market_registry_row(_registry_row())

    [row] = store.market_registry(VENUE)
    assert row.first_candle_seen_at.tzinfo is not None
    assert row.last_candle_seen_at.tzinfo is not None
    assert row.first_seen_in_venue_list_at.tzinfo is not None
    assert row.last_seen_in_venue_list_at.tzinfo is not None


def test_market_registry_section1_fields_default_to_none(store: TidemarkStore) -> None:
    """UNIV-01: eligibility columns are nullable and unfilled by Merge 1 -
    Merge 2 is what computes section1_first_usable_at."""
    store.upsert_market_registry_row(_registry_row())

    [row] = store.market_registry(VENUE)
    assert row.section1_first_usable_at is None
    assert row.section1_eligibility_checked_at is None


def test_market_registry_section1_fields_round_trip_when_set(store: TidemarkStore) -> None:
    usable_at = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
    checked_at = dt.datetime(2026, 3, 2, tzinfo=dt.UTC)
    store.upsert_market_registry_row(
        _registry_row(
            section1_first_usable_at=usable_at, section1_eligibility_checked_at=checked_at
        )
    )

    [row] = store.market_registry(VENUE)
    assert row.section1_first_usable_at == usable_at
    assert row.section1_eligibility_checked_at == checked_at


def test_first_candle_seen_at_and_last_candle_seen_at_default_to_none(
    store: TidemarkStore,
) -> None:
    """Merge 2A: these are nullable (amended from Merge 1's NOT NULL) so a
    freshly-discovered symbol (listing-only, no candle backfill yet) is a
    valid registry row."""
    row = store.upsert_market_registry_row(
        _registry_row(first_candle_seen_at=None, last_candle_seen_at=None)
    )
    assert row.first_candle_seen_at is None
    assert row.last_candle_seen_at is None


# -- record_market_listing / mark_absent_from_venue / record_candle_coverage --
# (Phase 6, Merge 2A: PART A discovery / PART B backfill write paths)


def test_record_market_listing_inserts_a_new_row_with_no_candle_coverage(
    store: TidemarkStore,
) -> None:
    seen_at = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)

    row = store.record_market_listing(VENUE, SYMBOL, "perpetual", "USDT", seen_at)

    assert row.status == "ACTIVE"
    assert row.first_seen_in_venue_list_at == seen_at
    assert row.last_seen_in_venue_list_at == seen_at
    assert row.first_candle_seen_at is None
    assert row.last_candle_seen_at is None


def test_record_market_listing_never_moves_first_seen_but_bumps_last_seen(
    store: TidemarkStore,
) -> None:
    first_seen = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    second_seen = first_seen + dt.timedelta(days=1)

    store.record_market_listing(VENUE, SYMBOL, "perpetual", "USDT", first_seen)
    row = store.record_market_listing(VENUE, SYMBOL, "perpetual", "USDT", second_seen)

    assert row.first_seen_in_venue_list_at == first_seen
    assert row.last_seen_in_venue_list_at == second_seen


def test_record_market_listing_never_clobbers_existing_candle_coverage(
    store: TidemarkStore,
) -> None:
    """The whole reason record_market_listing exists instead of reusing
    upsert_market_registry_row: discovery must not stomp on candle
    coverage that a prior backfill already recorded."""
    seen_at = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    first_candle = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    last_candle = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    store.record_market_listing(VENUE, SYMBOL, "perpetual", "USDT", seen_at)
    store.record_candle_coverage(VENUE, SYMBOL, first_candle, last_candle)

    row = store.record_market_listing(
        VENUE, SYMBOL, "perpetual", "USDT", seen_at + dt.timedelta(days=1)
    )

    assert row.first_candle_seen_at == first_candle
    assert row.last_candle_seen_at == last_candle


def test_mark_absent_from_venue_flags_symbols_not_in_the_current_listing(
    store: TidemarkStore,
) -> None:
    seen_at = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    store.record_market_listing(VENUE, "BTC/USDT:USDT", "perpetual", "USDT", seen_at)
    store.record_market_listing(VENUE, "ETH/USDT:USDT", "perpetual", "USDT", seen_at)

    changed = store.mark_absent_from_venue(VENUE, {"BTC/USDT:USDT"})

    assert changed == 1
    rows = {r.symbol: r for r in store.market_registry(VENUE)}
    assert rows["BTC/USDT:USDT"].status == "ACTIVE"
    assert rows["ETH/USDT:USDT"].status == "ABSENT_FROM_VENUE"


def test_mark_absent_from_venue_never_deletes_rows(store: TidemarkStore) -> None:
    seen_at = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    store.record_market_listing(VENUE, SYMBOL, "perpetual", "USDT", seen_at)

    store.mark_absent_from_venue(VENUE, set())

    assert len(store.market_registry(VENUE)) == 1


def test_mark_absent_from_venue_does_not_recount_an_already_absent_row(
    store: TidemarkStore,
) -> None:
    seen_at = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    store.record_market_listing(VENUE, SYMBOL, "perpetual", "USDT", seen_at)
    store.mark_absent_from_venue(VENUE, set())

    changed_again = store.mark_absent_from_venue(VENUE, set())

    assert changed_again == 0


def test_record_candle_coverage_updates_only_candle_fields(store: TidemarkStore) -> None:
    seen_at = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    store.record_market_listing(VENUE, SYMBOL, "perpetual", "USDT", seen_at)
    first_candle = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    last_candle = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)

    row = store.record_candle_coverage(VENUE, SYMBOL, first_candle, last_candle)

    assert row is not None
    assert row.first_candle_seen_at == first_candle
    assert row.last_candle_seen_at == last_candle
    # listing fields untouched by a candle-coverage write
    assert row.first_seen_in_venue_list_at == seen_at
    assert row.status == "ACTIVE"


def test_record_candle_coverage_returns_none_for_unregistered_symbol(
    store: TidemarkStore,
) -> None:
    now = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    assert store.record_candle_coverage(VENUE, SYMBOL, now, now) is None


# -- universe_snapshot / universe_snapshot_row ---------------------------------


def _snapshot(
    snapshot_id: str = "snap-1",
    snapshot_at: dt.datetime | None = None,
    methodology_version: str = "universe-v1",
    **overrides,
) -> UniverseSnapshot:
    defaults = dict(
        snapshot_id=snapshot_id,
        snapshot_at=snapshot_at or dt.datetime(2026, 9, 25, tzinfo=dt.UTC),
        methodology_version=methodology_version,
        venue=VENUE,
        metric_name="MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D",
        metric_window_days=30,
        n_selected=2,
        provenance="FORWARD",
        candle_hash="deadbeef",
        counts_by_exclusion_reason={},
    )
    defaults.update(overrides)
    return UniverseSnapshot(**defaults)


def _rows(snapshot_id: str = "snap-1") -> list[UniverseSnapshotRow]:
    return [
        UniverseSnapshotRow(
            snapshot_id=snapshot_id,
            symbol="BTC/USDT:USDT",
            rank=1,
            metric_value=1_000_000.0,
            eligible=True,
            selected=True,
            exclusion_reason=None,
        ),
        UniverseSnapshotRow(
            snapshot_id=snapshot_id,
            symbol="ETH/USDT:USDT",
            rank=2,
            metric_value=500_000.0,
            eligible=True,
            selected=True,
            exclusion_reason=None,
        ),
        UniverseSnapshotRow(
            snapshot_id=snapshot_id,
            symbol="DOGE/USDT:USDT",
            rank=3,
            metric_value=10_000.0,
            eligible=False,
            selected=False,
            exclusion_reason="BELOW_RANK_CUTOFF",
        ),
    ]


def test_save_universe_snapshot_writes_header_and_rows_atomically(store: TidemarkStore) -> None:
    result = store.save_universe_snapshot(_snapshot(), _rows())

    assert isinstance(result, UniverseSnapshotWriteResult)
    assert result.snapshot.snapshot_id == "snap-1"
    assert len(result.rows) == 3

    header = store.universe_snapshot_by_id("snap-1")
    assert header is not None
    assert header.venue == VENUE

    rows = store.universe_snapshot_rows("snap-1")
    assert len(rows) == 3
    assert {r.symbol for r in rows} == {"BTC/USDT:USDT", "ETH/USDT:USDT", "DOGE/USDT:USDT"}


def test_save_universe_snapshot_rejects_duplicate_venue_at_methodology(
    store: TidemarkStore,
) -> None:
    store.save_universe_snapshot(_snapshot(snapshot_id="snap-1"), _rows("snap-1"))

    with pytest.raises(ValueError, match="already exists"):
        store.save_universe_snapshot(_snapshot(snapshot_id="snap-2"), _rows("snap-2"))


def test_save_universe_snapshot_duplicate_rejection_writes_nothing(store: TidemarkStore) -> None:
    """A rejected duplicate must not leave a partial write behind."""
    store.save_universe_snapshot(_snapshot(snapshot_id="snap-1"), _rows("snap-1"))

    with pytest.raises(ValueError, match="already exists"):
        store.save_universe_snapshot(_snapshot(snapshot_id="snap-2"), _rows("snap-2"))

    assert store.universe_snapshot_by_id("snap-2") is None
    assert store.universe_snapshot_rows("snap-2") == []
    assert len(store.universe_snapshots(VENUE)) == 1


def test_universe_snapshots_lists_newest_first(store: TidemarkStore) -> None:
    older = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    newer = dt.datetime(2026, 9, 2, tzinfo=dt.UTC)
    store.save_universe_snapshot(_snapshot(snapshot_id="old", snapshot_at=older), _rows("old"))
    store.save_universe_snapshot(_snapshot(snapshot_id="new", snapshot_at=newer), _rows("new"))

    snapshots = store.universe_snapshots(VENUE)
    assert [s.snapshot_id for s in snapshots] == ["new", "old"]


def test_latest_universe_snapshot_at_or_before_returns_correct_row(store: TidemarkStore) -> None:
    day1 = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    day2 = dt.datetime(2026, 9, 2, tzinfo=dt.UTC)
    day3 = dt.datetime(2026, 9, 3, tzinfo=dt.UTC)
    store.save_universe_snapshot(_snapshot(snapshot_id="d1", snapshot_at=day1), _rows("d1"))
    store.save_universe_snapshot(_snapshot(snapshot_id="d2", snapshot_at=day2), _rows("d2"))

    # exactly on a snapshot's own timestamp
    assert store.latest_universe_snapshot(VENUE, day2).snapshot_id == "d2"
    # between two snapshots: the earlier one still applies
    between = day1 + dt.timedelta(hours=12)
    assert store.latest_universe_snapshot(VENUE, between).snapshot_id == "d1"
    # after the latest
    assert store.latest_universe_snapshot(VENUE, day3).snapshot_id == "d2"
    # before the first snapshot ever taken
    assert store.latest_universe_snapshot(VENUE, day1 - dt.timedelta(days=1)) is None


def test_universe_snapshot_rows_ordered_by_rank(store: TidemarkStore) -> None:
    store.save_universe_snapshot(_snapshot(), _rows())

    rows = store.universe_snapshot_rows("snap-1")
    assert [r.rank for r in rows] == [1, 2, 3]


def test_universe_snapshot_row_excluded_symbol_keeps_its_reason(store: TidemarkStore) -> None:
    """PART A: 'not selected' must never look like 'no data existed' -
    an excluded symbol's rank and exclusion_reason stay recoverable."""
    store.save_universe_snapshot(_snapshot(), _rows())

    rows = {r.symbol: r for r in store.universe_snapshot_rows("snap-1")}
    excluded = rows["DOGE/USDT:USDT"]
    assert excluded.selected is False
    assert excluded.eligible is False
    assert excluded.exclusion_reason == "BELOW_RANK_CUTOFF"
    assert excluded.rank == 3


def test_universe_snapshot_timestamps_are_utc_aware(store: TidemarkStore) -> None:
    store.save_universe_snapshot(_snapshot(), _rows())

    header = store.universe_snapshot_by_id("snap-1")
    assert header is not None
    assert header.snapshot_at.tzinfo is not None


def test_universe_snapshot_counts_by_exclusion_reason_round_trips_as_json(
    store: TidemarkStore,
) -> None:
    snapshot = _snapshot(counts_by_exclusion_reason={"BELOW_RANK_CUTOFF": 1, "NOT_ELIGIBLE": 4})
    store.save_universe_snapshot(snapshot, _rows())

    header = store.universe_snapshot_by_id("snap-1")
    assert header is not None
    assert header.counts_by_exclusion_reason == {"BELOW_RANK_CUTOFF": 1, "NOT_ELIGIBLE": 4}
