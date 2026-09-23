"""Smoke test: the CLI is wired up end to end, plus context evaluate/history/explain."""

import datetime as dt

import pytest
from typer.testing import CliRunner

from tidemark import __version__
from tidemark.cli import app
from tidemark.data.exchange import RawCandle
from tidemark.data.store import TidemarkStore, create_store_engine, init_db

runner = CliRunner()

VENUE = "binanceusdm"
SYMBOL = "BTC/USDT:USDT"
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


def test_data_status_flags_stale_running_run(tmp_path, monkeypatch) -> None:
    """A RUNNING row that never got a finish_run update (a simulated hard
    kill: the process died between start_run and finish_run) must be
    surfaced by `data status`, flagged STALE once it's older than 2 hours.
    """
    db_path = (tmp_path / "tidemark.db").as_posix()
    monkeypatch.setenv("TIDEMARK_DATABASE_URL", f"sqlite:///{db_path}")

    engine = create_store_engine(f"sqlite:///{db_path}")
    init_db(engine)
    store = TidemarkStore(engine)
    stale_start = dt.datetime.now(dt.UTC) - dt.timedelta(hours=3)
    store.start_run("stuck-run", "backfill", stale_start)

    result = runner.invoke(app, ["data", "status"])

    assert result.exit_code == 0
    assert "stuck-run" in result.stdout
    assert "RUNNING" in result.stdout
    assert "STALE" in result.stdout


def test_data_status_does_not_flag_recent_running_run(tmp_path, monkeypatch) -> None:
    db_path = (tmp_path / "tidemark.db").as_posix()
    monkeypatch.setenv("TIDEMARK_DATABASE_URL", f"sqlite:///{db_path}")

    engine = create_store_engine(f"sqlite:///{db_path}")
    init_db(engine)
    store = TidemarkStore(engine)
    recent_start = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    store.start_run("fresh-run", "backfill", recent_start)

    result = runner.invoke(app, ["data", "status"])

    assert result.exit_code == 0
    assert "fresh-run" in result.stdout
    assert "STALE" not in result.stdout


# -- context evaluate/history/explain (Section 1) ----------------------------


def _use_temp_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    db_path = tmp_path / "test.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("TIDEMARK_DATABASE_URL", url)
    return url


def _raw_candle(open_time: dt.datetime, close: float = 100.0) -> RawCandle:
    return RawCandle(
        open_time=open_time,
        close_time=open_time + dt.timedelta(hours=4),
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1.0,
    )


def _seed_candles(url: str, symbol: str, n: int, timeframe: str = "4h") -> None:
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    candles = [_raw_candle(START + dt.timedelta(hours=4 * i)) for i in range(n)]
    store.upsert_candles(VENUE, symbol, timeframe, candles, dt.datetime.now(dt.UTC))


def test_context_evaluate_prints_state_and_persists(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, SYMBOL, n=3)

    result = runner.invoke(app, ["context", "evaluate", "--symbol", SYMBOL])

    assert result.exit_code == 0
    assert "state=INSUFFICIENT_STRUCTURE" in result.stdout
    assert "No setups found." in result.stdout

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    assert store.latest_context_record(SYMBOL) is not None


def test_context_evaluate_twice_writes_one_row(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, SYMBOL, n=3)

    runner.invoke(app, ["context", "evaluate", "--symbol", SYMBOL])
    runner.invoke(app, ["context", "evaluate", "--symbol", SYMBOL])

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    assert len(store.context_history(SYMBOL)) == 1


def test_context_evaluate_missing_candles_errors(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["context", "evaluate", "--symbol", "NOPE/USDT:USDT"])

    assert result.exit_code != 0
    assert "No 4H candles" in result.stdout


def test_context_history_reports_no_setups_when_empty(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["context", "history", "--symbol", SYMBOL])

    assert result.exit_code == 0
    assert "No setups found." in result.stdout


def test_context_history_shows_candle_close_time(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, SYMBOL, n=3)
    runner.invoke(app, ["context", "evaluate", "--symbol", SYMBOL])

    # A large --days window, since the fixture's fixed candle timestamps
    # (2026-01-01) are far in the past relative to the real clock.
    result = runner.invoke(app, ["context", "history", "--symbol", SYMBOL, "--days", "36500"])

    assert result.exit_code == 0
    expected_close = (START + dt.timedelta(hours=4 * 3)).isoformat()
    assert expected_close in result.stdout


def test_context_explain_without_prior_evaluation_errors(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["context", "explain", "--symbol", SYMBOL])

    assert result.exit_code != 0
    assert "No context record" in result.stdout


def test_context_explain_shows_state_and_swings(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, SYMBOL, n=3)
    runner.invoke(app, ["context", "evaluate", "--symbol", SYMBOL])

    result = runner.invoke(app, ["context", "explain", "--symbol", SYMBOL])

    assert result.exit_code == 0
    assert "State:  INSUFFICIENT_STRUCTURE" in result.stdout
    assert "Swings used:" in result.stdout
    assert "Active levels:" in result.stdout


def test_context_evaluate_as_of_matches_truncated_database(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--as-of against the full history must match evaluating a database
    that only ever had candles up to that point (the look-ahead guard,
    exercised through the CLI's own candle-loading path)."""
    full_url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(full_url, SYMBOL, n=20)
    as_of = (START + dt.timedelta(hours=4 * 9)).isoformat()

    result_full = runner.invoke(app, ["context", "evaluate", "--symbol", SYMBOL, "--as-of", as_of])
    assert result_full.exit_code == 0

    truncated_url = f"sqlite:///{tmp_path / 'truncated.db'}"
    monkeypatch.setenv("TIDEMARK_DATABASE_URL", truncated_url)
    _seed_candles(truncated_url, SYMBOL, n=10)  # candles 0..9, closing at +4*9h

    result_truncated = runner.invoke(app, ["context", "evaluate", "--symbol", SYMBOL])
    assert result_truncated.exit_code == 0

    # Same state/watch/grade/reason line modulo the DB it came from.
    assert (
        result_full.stdout.split("\n")[0].split("  ", 1)[1]
        == (result_truncated.stdout.split("\n")[0].split("  ", 1)[1])
    )
