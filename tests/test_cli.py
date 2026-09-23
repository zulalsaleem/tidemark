"""Smoke test: the CLI is wired up end to end, plus context evaluate/history/explain."""

import datetime as dt

import pytest
from typer.testing import CliRunner

from tidemark import __version__
from tidemark import cli as cli_module
from tidemark.cli import _parse_csv, app
from tidemark.data.exchange import RawCandle
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.data.timeframes import TIMEFRAMES
from tidemark.notify.telegram import build_message

runner = CliRunner()

VENUE = "binanceusdm"
SYMBOL = "BTC/USDT:USDT"
START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


# -- _parse_csv: every timeframe parses correctly, alone and comma-separated -


@pytest.mark.parametrize("timeframe", list(TIMEFRAMES))
def test_parse_csv_handles_each_timeframe_alone(timeframe: str) -> None:
    assert _parse_csv([timeframe]) == [timeframe]


def test_parse_csv_handles_every_timeframe_as_one_comma_separated_value() -> None:
    assert _parse_csv([",".join(TIMEFRAMES)]) == list(TIMEFRAMES)


def test_parse_csv_handles_every_timeframe_as_repeated_flag_values() -> None:
    # Simulates `--timeframes 5m --timeframes 15m ...`: one list item per
    # occurrence, no commas at all.
    assert _parse_csv(list(TIMEFRAMES)) == list(TIMEFRAMES)


def test_parse_csv_handles_a_mix_of_comma_and_repeated_values() -> None:
    # Simulates `--timeframes 5m,15m,1h --timeframes 4h,1d,1w`.
    assert _parse_csv(["5m,15m,1h", "4h,1d,1w"]) == list(TIMEFRAMES)


def test_parse_csv_preserves_1d_intact() -> None:
    # Regression guard for the reported symptom: 1d must survive parsing
    # unchanged, alone and inside a comma list. (The bug that motivated
    # this was PowerShell's own unquoted-argument tokenizer treating "1d"
    # as a decimal-literal number and re-stringifying it as "1" *before*
    # the process starts — this function never sees a corrupted string in
    # that case, since the corruption happens upstream of Python entirely.
    # Quoting the value, or passing it via a repeated flag as tested
    # above, avoids the shell ever doing that.)
    assert _parse_csv(["1d"]) == ["1d"]
    assert _parse_csv(["4h,1d,1w"]) == ["4h", "1d", "1w"]


def test_parse_csv_returns_none_for_no_values() -> None:
    assert _parse_csv(None) is None
    assert _parse_csv([]) is None


def test_data_gaps_accepts_repeated_timeframes_flag(tmp_path, monkeypatch) -> None:
    db_path = (tmp_path / "tidemark.db").as_posix()
    monkeypatch.setenv("TIDEMARK_DATABASE_URL", f"sqlite:///{db_path}")

    result = runner.invoke(
        app,
        [
            "data",
            "gaps",
            "--symbols",
            SYMBOL,
            "--timeframes",
            "4h",
            "--timeframes",
            "1d",
            "--timeframes",
            "1w",
        ],
    )

    assert result.exit_code == 0
    rows = [line for line in result.stdout.splitlines() if SYMBOL in line]
    assert {line.split()[1] for line in rows} == {"4h", "1d", "1w"}


def test_data_gaps_accepts_comma_separated_timeframes_flag(tmp_path, monkeypatch) -> None:
    db_path = (tmp_path / "tidemark.db").as_posix()
    monkeypatch.setenv("TIDEMARK_DATABASE_URL", f"sqlite:///{db_path}")

    result = runner.invoke(
        app,
        ["data", "gaps", "--symbols", SYMBOL, "--timeframes", "4h,1d,1w"],
    )

    assert result.exit_code == 0
    rows = [line for line in result.stdout.splitlines() if SYMBOL in line]
    assert {line.split()[1] for line in rows} == {"4h", "1d", "1w"}


def test_help_exits_cleanly() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Tidemark" in result.stdout


def test_version_command_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


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


# -- run / journal / notify (Phase 3) -----------------------------------------


class _FakeNotifier:
    """Stands in for TelegramNotifier: no real network anywhere."""

    sent_messages: list[str] = []
    succeed: bool = True

    def __init__(self, bot_token, chat_id) -> None:  # noqa: ARG002
        pass

    def send_text(self, text: str) -> bool:
        _FakeNotifier.sent_messages.append(text)
        return _FakeNotifier.succeed

    def send_alert(self, record, alert_reason, symbol) -> bool:
        return self.send_text(build_message(record, alert_reason, symbol))


@pytest.fixture(autouse=False)
def _fake_notifier(monkeypatch: pytest.MonkeyPatch):
    _FakeNotifier.sent_messages = []
    _FakeNotifier.succeed = True
    monkeypatch.setattr(cli_module, "TelegramNotifier", _FakeNotifier)
    return _FakeNotifier


def test_run_first_evaluation_journals_with_no_alert(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, SYMBOL, n=3)

    result = runner.invoke(app, ["run", "--symbols", SYMBOL])

    assert result.exit_code == 0
    assert "journaled, no alert" in result.stdout
    assert _fake_notifier.sent_messages == []

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    assert len(store.journal_history(SYMBOL)) == 1


def test_run_twice_same_candle_writes_one_journal_row_and_sends_no_alert(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, SYMBOL, n=3)

    first = runner.invoke(app, ["run", "--symbols", SYMBOL])
    second = runner.invoke(app, ["run", "--symbols", SYMBOL])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "repeat (already journaled, no alert)" in second.stdout
    assert _fake_notifier.sent_messages == []

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    assert len(store.journal_history(SYMBOL)) == 1


def test_run_one_symbol_missing_candles_gives_partial(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, SYMBOL, n=3)

    result = runner.invoke(app, ["run", "--symbols", SYMBOL, "--symbols", "NOPE/USDT:USDT"])

    assert result.exit_code == 0
    assert ": PARTIAL" in result.stdout
    assert "NOPE/USDT:USDT" in result.stdout
    assert "FAILED: no 4H candles" in result.stdout


def test_run_missing_credentials_does_not_crash(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, SYMBOL, n=3)
    monkeypatch.delenv("TIDEMARK_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TIDEMARK_TELEGRAM_CHAT_ID", raising=False)

    result = runner.invoke(app, ["run", "--symbols", SYMBOL])

    assert result.exit_code == 0
    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    assert len(store.journal_history(SYMBOL)) == 1


def test_journal_list_shows_rows(tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, SYMBOL, n=3)
    runner.invoke(app, ["run", "--symbols", SYMBOL])

    result = runner.invoke(app, ["journal", "list", "--symbol", SYMBOL, "--days", "36500"])

    assert result.exit_code == 0
    assert "alert_sent=False" in result.stdout


def test_journal_list_reports_none_when_empty(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["journal", "list", "--symbol", SYMBOL])

    assert result.exit_code == 0
    assert "No journal rows found." in result.stdout


def test_journal_alerts_reports_none_when_empty(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["journal", "alerts"])

    assert result.exit_code == 0
    assert "No alerts sent." in result.stdout


def test_notify_test_sends_fixed_message_and_writes_nothing(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    engine = create_store_engine(url)
    init_db(engine)

    result = runner.invoke(app, ["notify", "test"])

    assert result.exit_code == 0
    assert "Test message sent." in result.stdout
    assert len(_fake_notifier.sent_messages) == 1

    store = TidemarkStore(engine)
    assert store.journal_alerts() == []


def test_notify_test_reports_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier
) -> None:
    _use_temp_db(tmp_path, monkeypatch)
    _fake_notifier.succeed = False

    result = runner.invoke(app, ["notify", "test"])

    assert result.exit_code != 0
    assert "NOT sent" in result.stdout
