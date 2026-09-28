"""`tidemark intel distributions [--json] [--symbols ...]`: the universe
snapshot is the default symbol source (the real `save_universe_snapshot`
write path, not a bespoke insert - see test_context_read.py's docstring
for why that matters), `--symbols` overrides it, missing settings exit
cleanly, and the rendered output never contains an interpretation term.
No live network: `_coinalyze_client` is monkeypatched.
"""

from __future__ import annotations

import datetime as dt
import json as json_module

from typer.testing import CliRunner

from tidemark import cli as cli_module
from tidemark.cli import app
from tidemark.data.models import UniverseSnapshot, UniverseSnapshotRow
from tidemark.data.store import TidemarkStore, create_store_engine, init_db

runner = CliRunner()

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


# -- missing settings exit cleanly --------------------------------------------


def test_missing_coinalyze_key_exits_cleanly(monkeypatch, tmp_path) -> None:
    _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("TIDEMARK_COINALYZE_API_KEY", "")

    result = runner.invoke(app, ["intel", "distributions"])

    assert result.exit_code == 1
    assert "TIDEMARK_COINALYZE_API_KEY is not set" in result.output


def test_no_universe_snapshot_and_no_symbols_exits_cleanly(monkeypatch, tmp_path) -> None:
    _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: _FakeCoinalyze([]))

    result = runner.invoke(app, ["intel", "distributions"])

    assert result.exit_code == 1
    assert "No universe snapshot" in result.output


# -- the universe snapshot is the default source ------------------------------


def test_universe_snapshot_is_the_default_symbol_source(monkeypatch, tmp_path) -> None:
    database_url = _use_temp_db(tmp_path, monkeypatch)
    symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    _seed_snapshot(database_url, "snap-1", symbols)
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: _FakeCoinalyze(symbols))

    result = runner.invoke(app, ["intel", "distributions"])

    assert result.exit_code == 0
    assert "snap-1" in result.output
    assert "source: SNAPSHOT" in result.output
    assert "BTC/USDT:USDT" in result.output
    assert "ETH/USDT:USDT" in result.output


# -- --symbols overrides it ----------------------------------------------------


def test_symbols_flag_overrides_the_universe_snapshot(monkeypatch, tmp_path) -> None:
    database_url = _use_temp_db(tmp_path, monkeypatch)
    _seed_snapshot(database_url, "snap-1", ["BTC/USDT:USDT"])
    monkeypatch.setattr(
        cli_module, "_coinalyze_client", lambda settings: _FakeCoinalyze(["SOL/USDT:USDT"])
    )

    result = runner.invoke(app, ["intel", "distributions", "--symbols", "SOL/USDT:USDT"])

    assert result.exit_code == 0
    assert "source: EXPLICIT" in result.output
    assert "SOL/USDT:USDT" in result.output
    assert "BTC/USDT:USDT" not in result.output  # the snapshot's symbol, never fetched


# -- --json is valid --------------------------------------------------------


def test_json_output_is_valid_and_matches_the_symbols(monkeypatch, tmp_path) -> None:
    _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cli_module, "_coinalyze_client", lambda settings: _FakeCoinalyze(["BTC/USDT:USDT"])
    )

    result = runner.invoke(app, ["intel", "distributions", "--symbols", "BTC/USDT:USDT", "--json"])

    assert result.exit_code == 0
    payload = json_module.loads(result.output)
    assert payload["source"] == "EXPLICIT"
    assert payload["rows"][0]["ccxt_symbol"] == "BTC/USDT:USDT"
    assert payload["rows"][0]["long_short_ratio"]["status"] == "OK"
    assert len(payload["summaries"]) == 4


# -- no interpretation language anywhere in the output ------------------------

_FORBIDDEN_TERMS = (
    "entry",
    "stop",
    " sl ",
    " tp ",
    "target",
    "r:r",
    "buy signal",
    "sell signal",
    "long setup",
    "short setup",
    "setup",
    "elevated",
    "crowded",
    "normal",
    "bullish",
    "bearish",
)


def test_output_contains_no_interpretation_language(monkeypatch, tmp_path) -> None:
    _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cli_module, "_coinalyze_client", lambda settings: _FakeCoinalyze(["BTC/USDT:USDT"])
    )

    result = runner.invoke(app, ["intel", "distributions", "--symbols", "BTC/USDT:USDT"])

    assert result.exit_code == 0
    lowered = result.output.lower()
    for term in _FORBIDDEN_TERMS:
        assert term not in lowered, f"forbidden interpretation term {term!r} found in output"
