"""CLI smoke tests, plus `data backfill` / `context evaluate|history|explain`."""

import datetime as dt

import pytest
from typer.testing import CliRunner

from tidemark import __version__, cli
from tidemark.cli import app
from tidemark.data.models import Candle
from tidemark.data.store import TidemarkStore, create_store_engine, init_db

runner = CliRunner()
START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def test_help_exits_cleanly() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Tidemark" in result.stdout


def test_version_command_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_run_command_not_yet_implemented() -> None:
    result = runner.invoke(app, ["run"])
    assert result.exit_code != 0
    assert isinstance(result.exception, NotImplementedError)


def _use_temp_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    db_path = tmp_path / "test.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("TIDEMARK_DATABASE_URL", url)
    return url


def _seed_candles(url: str, symbol: str, n: int, timeframe: str = "4h") -> None:
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    candles = [
        Candle(
            asset=symbol,
            timeframe=timeframe,
            open_time=START + dt.timedelta(hours=4 * i),
            close_time=START + dt.timedelta(hours=4 * (i + 1)),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1.0,
        )
        for i in range(n)
    ]
    store.save_candles(candles)


class _FakeExchangeClient:
    """Returns one page of candles, then an empty page to end the loop."""

    calls = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    def fetch_closed_candles(self, asset, timeframe, limit, since=None):
        _FakeExchangeClient.calls += 1
        if _FakeExchangeClient.calls > 1:
            return []
        return [
            Candle(
                asset=asset,
                timeframe=timeframe,
                open_time=START + dt.timedelta(hours=4 * i),
                close_time=START + dt.timedelta(hours=4 * (i + 1)),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1.0,
            )
            for i in range(3)
        ]


def test_data_backfill_writes_candles(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _FakeExchangeClient.calls = 0
    monkeypatch.setattr(cli, "ExchangeClient", _FakeExchangeClient)

    result = runner.invoke(
        app,
        [
            "data",
            "backfill",
            "--symbols",
            "BTC/USDT:USDT",
            "--timeframes",
            "4h",
            "--days",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert "3 candles" in result.stdout

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    df = store.get_candles("BTC/USDT:USDT", "4h")
    assert len(df) == 3


def test_context_evaluate_prints_state_and_persists(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, "BTCUSDT", n=3)

    result = runner.invoke(app, ["context", "evaluate", "--symbol", "BTCUSDT"])

    assert result.exit_code == 0
    assert "state=INSUFFICIENT_STRUCTURE" in result.stdout
    assert "No setups found." in result.stdout

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    assert store.latest_context_record("BTCUSDT") is not None


def test_context_evaluate_twice_writes_one_row(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, "BTCUSDT", n=3)

    runner.invoke(app, ["context", "evaluate", "--symbol", "BTCUSDT"])
    runner.invoke(app, ["context", "evaluate", "--symbol", "BTCUSDT"])

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    assert len(store.context_history("BTCUSDT")) == 1


def test_context_evaluate_missing_candles_errors(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["context", "evaluate", "--symbol", "NOPE"])

    assert result.exit_code != 0
    assert "No 4H candles" in result.stdout


def test_context_history_reports_no_setups_when_empty(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["context", "history", "--symbol", "BTCUSDT"])

    assert result.exit_code == 0
    assert "No setups found." in result.stdout


def test_context_explain_without_prior_evaluation_errors(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["context", "explain", "--symbol", "BTCUSDT"])

    assert result.exit_code != 0
    assert "No context record" in result.stdout


def test_context_explain_shows_state_and_swings(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, "BTCUSDT", n=3)
    runner.invoke(app, ["context", "evaluate", "--symbol", "BTCUSDT"])

    result = runner.invoke(app, ["context", "explain", "--symbol", "BTCUSDT"])

    assert result.exit_code == 0
    assert "State:  INSUFFICIENT_STRUCTURE" in result.stdout
    assert "Swings used:" in result.stdout
    assert "Active levels:" in result.stdout
