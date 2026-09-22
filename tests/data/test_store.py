"""Engine/session plumbing works; unimplemented CRUD raises clearly."""

import pytest
from sqlalchemy import inspect

from tidemark.data.store import TidemarkStore, create_store_engine, init_db


def test_init_db_creates_expected_tables() -> None:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"candles", "swings", "levels", "context_records", "journal_entries"} <= tables


def test_unimplemented_reads_raise_not_implemented() -> None:
    engine = create_store_engine("sqlite:///:memory:")
    store = TidemarkStore(engine)
    with pytest.raises(NotImplementedError):
        store.get_candles("BTCUSDT", "4h", 10)
