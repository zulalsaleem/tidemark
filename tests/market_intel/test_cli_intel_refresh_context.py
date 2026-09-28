"""`tidemark intel refresh-context`: runs the distributions measurement
over the latest universe snapshot and writes exactly one row to
`universe_context_cache`, carrying the snapshot id and period.
No live network: `_coinalyze_client` is monkeypatched.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from tidemark import cli as cli_module
from tidemark.cli import app
from tidemark.data.models import UniverseSnapshot, UniverseSnapshotRow
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.market_intel.universe_context_store import (
    UniverseContextCache,
    init_universe_context_store,
    latest_universe_context,
)
from tidemark.market_intel.universe_context_store import make_engine as make_context_engine

VENUE = "binanceusdm"


def _coinalyze_symbol(ccxt_symbol: str) -> str:
    return f"{ccxt_symbol.split('/')[0]}USDT_PERP.A"


def _market_row(ccxt_symbol: str, **overrides) -> dict:
    base = {
        "symbol": _coinalyze_symbol(ccxt_symbol),
        "exchange": "A",
        "base_asset": ccxt_symbol.split("/")[0],
        "quote_asset": "USDT",
        "is_perpetual": True,
        "has_long_short_ratio_data": True,
        "has_ohlcv_data": True,
        "has_buy_sell_data": True,
        "oi_lq_vol_denominated_in": "BASE_ASSET",
    }
    base.update(overrides)
    return base


def _bucket(symbol: str, **fields) -> list[dict]:
    return [{"symbol": symbol, "history": [{"t": fields.pop("t"), **fields}]}]


class _FakeCoinalyze:
    def __init__(self, ccxt_symbols: list[str]) -> None:
        self._rows = [_market_row(s) for s in ccxt_symbols]
        self.calls_in_last_minute = 0

    def future_markets(self):
        return self._rows

    def long_short_ratio_history(self, symbol, interval, from_ts, to_ts):
        return _bucket(symbol, t=from_ts, r=1.5, l=60.0, s=40.0)

    def funding_rate_history(self, symbol, interval, from_ts, to_ts):
        return _bucket(symbol, t=from_ts, o=0.01, h=0.01, l=0.01, c=0.01)

    def open_interest_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        return _bucket(symbol, t=from_ts, o=1000.0, h=1010.0, l=1000.0, c=1010.0)

    def ohlcv_history(self, symbol, interval, from_ts, to_ts):
        return _bucket(symbol, t=from_ts, v=150.0, bv=100.0)


runner = CliRunner()


def _use_temp_db(tmp_path, monkeypatch) -> str:
    db_path = (tmp_path / "tidemark.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("TIDEMARK_DATABASE_URL", database_url)
    return database_url


def _seed_snapshot(database_url: str, snapshot_id: str, symbols: list[str]) -> None:
    engine = create_store_engine(database_url)
    init_db(engine)
    store = TidemarkStore(engine)
    store.save_universe_snapshot(
        UniverseSnapshot(
            snapshot_id=snapshot_id,
            snapshot_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            methodology_version="v1",
            venue=VENUE,
            metric_name="dollar_volume",
            metric_window_days=30,
            k=50,
            n_selected=len(symbols),
            provenance="FORWARD",
            candle_hash="abc123",
            counts_by_exclusion_reason={},
        ),
        [
            UniverseSnapshotRow(
                snapshot_id=snapshot_id,
                symbol=symbol,
                rank=i + 1,
                metric_value=100.0,
                eligible=True,
                selected=True,
            )
            for i, symbol in enumerate(symbols)
        ],
    )


def test_missing_coinalyze_key_exits_cleanly(monkeypatch, tmp_path) -> None:
    _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("TIDEMARK_COINALYZE_API_KEY", "")

    result = runner.invoke(app, ["intel", "refresh-context"])

    assert result.exit_code == 1
    assert "TIDEMARK_COINALYZE_API_KEY is not set" in result.output


def test_no_universe_snapshot_exits_cleanly(monkeypatch, tmp_path) -> None:
    _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: _FakeCoinalyze([]))

    result = runner.invoke(app, ["intel", "refresh-context"])

    assert result.exit_code == 1
    assert "No universe snapshot" in result.output


def test_writes_exactly_one_row_with_the_snapshot_id_and_period(monkeypatch, tmp_path) -> None:
    database_url = _use_temp_db(tmp_path, monkeypatch)
    symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    _seed_snapshot(database_url, "snap-1", symbols)
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: _FakeCoinalyze(symbols))

    result = runner.invoke(app, ["intel", "refresh-context"])

    assert result.exit_code == 0
    assert "snap-1" in result.output
    assert "Wrote 1 row" in result.output

    context_engine = make_context_engine(database_url)
    init_universe_context_store(context_engine)
    row = latest_universe_context(context_engine)
    assert row is not None
    assert row.universe_snapshot_id == "snap-1"
    assert row.long_short_ratio_n == 2
    assert row.long_short_ratio_median is not None


def test_a_second_run_inserts_a_second_row_not_an_update(monkeypatch, tmp_path) -> None:
    database_url = _use_temp_db(tmp_path, monkeypatch)
    symbols = ["BTC/USDT:USDT"]
    _seed_snapshot(database_url, "snap-1", symbols)
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: _FakeCoinalyze(symbols))

    runner.invoke(app, ["intel", "refresh-context"])
    result = runner.invoke(app, ["intel", "refresh-context"])

    assert result.exit_code == 0
    context_engine = make_context_engine(database_url)
    init_universe_context_store(context_engine)
    with sessionmaker(bind=context_engine)() as session:
        count = session.query(UniverseContextCache).count()
    assert count == 2  # append-only
