"""universe_read.py: the second narrow, read-only exception to the
market_intel isolation boundary. Tests are free to use `tidemark.data.
store` to set up fixtures - only `market_intel`'s own production code is
restricted (see test_import_boundary.py).

Uses the REAL production write path (`TidemarkStore.save_universe_
snapshot`), not a bespoke row insert - see test_context_read.py's own
docstring for why that matters.
"""

from __future__ import annotations

import datetime as dt

from tidemark.data.models import UniverseSnapshot, UniverseSnapshotRow
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.market_intel.universe_read import read_latest_selected_symbols

VENUE = "binanceusdm"


def _snapshot(snapshot_id: str, snapshot_at: dt.datetime, **overrides) -> UniverseSnapshot:
    defaults = dict(
        snapshot_id=snapshot_id,
        snapshot_at=snapshot_at,
        methodology_version="v1",
        venue=VENUE,
        metric_name="dollar_volume",
        metric_window_days=30,
        k=50,
        n_selected=2,
        provenance="FORWARD",
        candle_hash="abc123",
        counts_by_exclusion_reason={},
    )
    defaults.update(overrides)
    return UniverseSnapshot(**defaults)


def _row(snapshot_id: str, symbol: str, rank: int, selected: bool) -> UniverseSnapshotRow:
    return UniverseSnapshotRow(
        snapshot_id=snapshot_id,
        symbol=symbol,
        rank=rank,
        metric_value=100.0,
        eligible=True,
        selected=selected,
    )


def _store(tmp_path) -> TidemarkStore:
    engine = create_store_engine(f"sqlite:///{(tmp_path / 'tidemark.db').as_posix()}")
    init_db(engine)
    return TidemarkStore(engine)


def test_returns_none_and_empty_for_a_fresh_database_with_no_universe_snapshot_table(
    tmp_path,
) -> None:
    db_path = (tmp_path / "empty.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    # Deliberately never call init_db - the table genuinely doesn't exist.

    snapshot_id, symbols = read_latest_selected_symbols(database_url, VENUE)

    assert snapshot_id is None
    assert symbols == []


def test_returns_none_and_empty_when_no_snapshot_exists_for_the_venue(tmp_path) -> None:
    db_path = (tmp_path / "tidemark.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    store = _store(tmp_path)
    store.save_universe_snapshot(
        _snapshot("snap-1", dt.datetime(2026, 1, 1, tzinfo=dt.UTC), venue="bitget"),
        [_row("snap-1", "BTC/USDT:USDT", 1, True)],
    )

    snapshot_id, symbols = read_latest_selected_symbols(database_url, VENUE)

    assert snapshot_id is None
    assert symbols == []


def test_returns_only_selected_symbols_in_rank_order(tmp_path) -> None:
    db_path = (tmp_path / "tidemark.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    store = _store(tmp_path)
    store.save_universe_snapshot(
        _snapshot("snap-1", dt.datetime(2026, 1, 1, tzinfo=dt.UTC)),
        [
            _row("snap-1", "ETH/USDT:USDT", 2, True),
            _row("snap-1", "BTC/USDT:USDT", 1, True),
            _row("snap-1", "DOGE/USDT:USDT", 3, False),  # ranked but not selected
        ],
    )

    snapshot_id, symbols = read_latest_selected_symbols(database_url, VENUE)

    assert snapshot_id == "snap-1"
    assert symbols == ["BTC/USDT:USDT", "ETH/USDT:USDT"]  # rank order, unselected excluded


def test_returns_the_latest_snapshot_for_the_venue(tmp_path) -> None:
    db_path = (tmp_path / "tidemark.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    store = _store(tmp_path)
    older = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    newer = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)
    store.save_universe_snapshot(
        _snapshot("snap-old", older), [_row("snap-old", "BTC/USDT:USDT", 1, True)]
    )
    store.save_universe_snapshot(
        _snapshot("snap-new", newer), [_row("snap-new", "ETH/USDT:USDT", 1, True)]
    )

    snapshot_id, symbols = read_latest_selected_symbols(database_url, VENUE)

    assert snapshot_id == "snap-new"
    assert symbols == ["ETH/USDT:USDT"]


def test_empty_selection_returns_snapshot_id_with_an_empty_symbol_list(tmp_path) -> None:
    db_path = (tmp_path / "tidemark.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    store = _store(tmp_path)
    store.save_universe_snapshot(
        _snapshot("snap-1", dt.datetime(2026, 1, 1, tzinfo=dt.UTC), n_selected=0),
        [_row("snap-1", "BTC/USDT:USDT", 1, False)],
    )

    snapshot_id, symbols = read_latest_selected_symbols(database_url, VENUE)

    assert snapshot_id == "snap-1"
    assert symbols == []
