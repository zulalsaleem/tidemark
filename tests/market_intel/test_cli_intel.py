"""`tidemark intel market`: missing key exits cleanly, MARKET_NOT_FOUND
renders cleanly, and a full OK snapshot prints (text and --json). No
live network: `_coinalyze_client` is monkeypatched to a fake client,
exactly like `_notifier` is swapped out elsewhere in this test suite.
"""

from __future__ import annotations

import datetime as dt
import json

from typer.testing import CliRunner

from tidemark import cli as cli_module
from tidemark.cli import app

runner = CliRunner()

SYMBOL = "BTC/USDT:USDT"
COINALYZE_SYMBOL = "BTCUSDT_PERP.A"


def _market_row(**overrides) -> dict:
    base = {
        "symbol": COINALYZE_SYMBOL,
        "exchange": "A",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "is_perpetual": True,
        "has_long_short_ratio_data": True,
        "has_ohlcv_data": True,
        "has_buy_sell_data": True,
        "oi_lq_vol_denominated_in": "BASE_ASSET",
    }
    base.update(overrides)
    return base


class _FakeClient:
    """Minimal duck-typed stand-in for `CoinalyzeClient`."""

    def __init__(self, future_markets_rows, **history) -> None:
        self._rows = future_markets_rows
        self._history = history

    def future_markets(self):
        return self._rows

    def open_interest(self, symbols, convert_to_usd=True):
        return self._history.get("oi", [])

    def funding_rate(self, symbols):
        return self._history.get("funding", [])

    def predicted_funding_rate(self, symbols):
        return self._history.get("predicted", [])

    def open_interest_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        return self._history.get("oi_history", [{"symbol": COINALYZE_SYMBOL, "history": []}])

    def long_short_ratio_history(self, symbol, interval, from_ts, to_ts):
        return self._history.get("ls_history", [{"symbol": COINALYZE_SYMBOL, "history": []}])

    def liquidation_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        return self._history.get("liq_history", [{"symbol": COINALYZE_SYMBOL, "history": []}])

    def ohlcv_history(self, symbol, interval, from_ts, to_ts):
        return self._history.get("ohlcv_history", [{"symbol": COINALYZE_SYMBOL, "history": []}])


# -- missing key: clean exit, not a crash ------------------------------------


def test_missing_api_key_exits_cleanly_without_a_traceback(monkeypatch) -> None:
    monkeypatch.setenv("TIDEMARK_COINALYZE_API_KEY", "")

    result = runner.invoke(app, ["intel", "market", "--symbol", SYMBOL])

    assert result.exit_code == 1
    assert "TIDEMARK_COINALYZE_API_KEY is not set" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


# -- MARKET_NOT_FOUND reports cleanly, non-zero exit -------------------------


def test_market_not_found_reports_cleanly(monkeypatch) -> None:
    fake_client = _FakeClient(future_markets_rows=[])
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: fake_client)

    result = runner.invoke(app, ["intel", "market", "--symbol", "NONSENSE/USDT:USDT"])

    assert result.exit_code == 1
    assert "MARKET_NOT_FOUND" in result.output
    assert "UNAVAILABLE" in result.output


# -- OK path: text and --json ------------------------------------------------


def _full_ok_client() -> _FakeClient:
    period_start_epoch = (
        int(dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0).timestamp()) - 3600
    )
    return _FakeClient(
        future_markets_rows=[_market_row()],
        oi=[{"symbol": COINALYZE_SYMBOL, "value": 100.0, "update": 1_700_000_000_000}],
        funding=[{"symbol": COINALYZE_SYMBOL, "value": 0.001, "update": 1_700_000_000_000}],
        predicted=[{"symbol": COINALYZE_SYMBOL, "value": 0.002, "update": 1_700_000_000_000}],
        oi_history=[
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [{"t": period_start_epoch, "o": 90.0, "h": 100.0, "l": 85.0, "c": 95.0}],
            }
        ],
        ls_history=[
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [{"t": period_start_epoch, "r": 1.1, "l": 52.0, "s": 48.0}],
            }
        ],
        liq_history=[
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [{"t": period_start_epoch, "l": 10.0, "s": 20.0}],
            }
        ],
        ohlcv_history=[
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [
                    {
                        "t": period_start_epoch,
                        "o": 90.0,
                        "h": 100.0,
                        "l": 85.0,
                        "c": 95.0,
                        "v": 50.0,
                        "bv": 30.0,
                        "tx": 5,
                        "btx": 3,
                    }
                ],
            }
        ],
    )


def test_ok_path_prints_text_report(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: _full_ok_client())

    result = runner.invoke(app, ["intel", "market", "--symbol", SYMBOL])

    assert result.exit_code == 0
    assert "market_status: OK" in result.output
    assert "Open interest: 100.0 USD" in result.output
    assert "Sell volume: 20.0 base asset units" in result.output  # v - bv = 50 - 30


def test_ok_path_prints_valid_json(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: _full_ok_client())

    result = runner.invoke(app, ["intel", "market", "--symbol", SYMBOL, "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["market_status"] == "OK"
    assert payload["open_interest"]["value"] == 100.0
    assert payload["open_interest"]["unit"] == "USD"
