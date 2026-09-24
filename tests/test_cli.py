"""Smoke test: the CLI is wired up end to end, plus context evaluate/history/explain."""

import datetime as dt
import json

import pytest
from typer.testing import CliRunner

from tidemark import __version__
from tidemark import cli as cli_module
from tidemark.cli import _parse_csv, app
from tidemark.context import htf
from tidemark.data.exchange import RawCandle
from tidemark.data.models import JournalEntry
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
    # None here reads as "never attempted" (missing credentials) to
    # notify_test's message branching - the connection-vs-http-error
    # distinction itself is covered directly in tests/notify/test_telegram.py.
    last_error = None
    is_configured: bool = True

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
    _FakeNotifier.last_error = None
    _FakeNotifier.is_configured = True
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


def test_notify_test_reports_connection_failure_distinctly(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier
) -> None:
    """A connection-level failure never reached Telegram, so the message
    must not imply anything about whether credentials are valid."""
    from tidemark.notify.telegram import TelegramSendError

    _use_temp_db(tmp_path, monkeypatch)
    _fake_notifier.succeed = False
    _fake_notifier.last_error = TelegramSendError(kind="connection", detail="timed out")

    result = runner.invoke(app, ["notify", "test"])

    assert result.exit_code != 0
    assert "unreachable" in result.stdout
    assert "not verified" in result.stdout
    assert "firewall" in result.stdout
    assert "TIDEMARK_TELEGRAM_BOT_TOKEN" not in result.stdout


def test_notify_test_reports_http_401_with_status_and_hint(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier
) -> None:
    from tidemark.notify.telegram import TelegramSendError

    _use_temp_db(tmp_path, monkeypatch)
    _fake_notifier.succeed = False
    _fake_notifier.last_error = TelegramSendError(
        kind="http_error", detail="Unauthorized", status_code=401
    )

    result = runner.invoke(app, ["notify", "test"])

    assert result.exit_code != 0
    assert "HTTP 401" in result.stdout
    assert "Unauthorized" in result.stdout
    assert "TIDEMARK_TELEGRAM_BOT_TOKEN" in result.stdout


def test_notify_test_reports_http_400_chat_not_found_with_hint(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier
) -> None:
    from tidemark.notify.telegram import TelegramSendError

    _use_temp_db(tmp_path, monkeypatch)
    _fake_notifier.succeed = False
    _fake_notifier.last_error = TelegramSendError(
        kind="http_error", detail="Bad Request: chat not found", status_code=400
    )

    result = runner.invoke(app, ["notify", "test"])

    assert result.exit_code != 0
    assert "HTTP 400" in result.stdout
    assert "chat not found" in result.stdout
    assert "TIDEMARK_TELEGRAM_CHAT_ID" in result.stdout


# -- health check / heartbeat (Phase 4B) --------------------------------------


def _healthy_setup(url: str, monkeypatch: pytest.MonkeyPatch, now: dt.datetime) -> None:
    """Seeds a store with fresh candles, a recently-completed run, and a
    recent journal entry - everything OK - and configures dummy Telegram
    credentials so telegram_config is also OK.
    """
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    for timeframe in ("4h", "1d", "1w"):
        candle = RawCandle(
            open_time=now - dt.timedelta(hours=5),
            close_time=now - dt.timedelta(hours=1),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1.0,
        )
        store.upsert_candles(VENUE, SYMBOL, timeframe, [candle], now)
    store.start_run("r1", "run", now - dt.timedelta(minutes=10))
    store.finish_run("r1", now - dt.timedelta(minutes=5), "COMPLETED", {})

    from tidemark.data.models import JournalEntry

    with store._session_factory() as session:
        session.add(
            JournalEntry(
                asset=SYMBOL,
                evaluated_at=now - dt.timedelta(hours=1),
                recorded_at=now - dt.timedelta(minutes=5),
                rule_version="section-01-v1.1",
                state="NEUTRAL",
                watch="WAIT",
                grade=None,
                reason_code="NEUTRAL_STRUCTURE",
                active_levels=[],
                fib={},
                swings_used=[],
                alert_sent=False,
                alert_reason=None,
            )
        )
        session.commit()

    monkeypatch.setenv("TIDEMARK_SYMBOLS", SYMBOL)
    monkeypatch.setenv("TIDEMARK_TELEGRAM_BOT_TOKEN", "dummy-token")
    monkeypatch.setenv("TIDEMARK_TELEGRAM_CHAT_ID", "dummy-chat-id")


def test_health_check_exit_code_ok_when_everything_healthy(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _healthy_setup(url, monkeypatch, dt.datetime.now(dt.UTC))

    result = runner.invoke(app, ["health", "check"])

    assert result.exit_code == 0
    assert "Overall: OK" in result.stdout


def test_health_check_exit_code_warn_when_telegram_not_configured(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    now = dt.datetime.now(dt.UTC)
    _healthy_setup(url, monkeypatch, now)
    # Empty-string overrides, not delenv: a real .env file (if present in
    # the working directory) would otherwise still supply real
    # credentials, since pydantic-settings falls back to it when an OS
    # env var is merely absent. An explicit empty value always wins.
    monkeypatch.setenv("TIDEMARK_TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TIDEMARK_TELEGRAM_CHAT_ID", "")

    result = runner.invoke(app, ["health", "check"])

    assert result.exit_code == 1
    assert "Overall: WARN" in result.stdout


def test_health_check_exit_code_fail_on_stale_running_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    now = dt.datetime.now(dt.UTC)
    _healthy_setup(url, monkeypatch, now)

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    store.start_run("stuck", "update", now - dt.timedelta(hours=3))

    result = runner.invoke(app, ["health", "check"])

    assert result.exit_code == 2
    assert "Overall: FAIL" in result.stdout
    assert "crashed" in result.stdout


def test_health_check_json_parses_and_contains_every_check(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _healthy_setup(url, monkeypatch, dt.datetime.now(dt.UTC))

    result = runner.invoke(app, ["health", "check", "--json"])

    payload = json.loads(result.stdout)
    assert payload["status"] == "OK"
    assert isinstance(payload["checks"], list)
    assert len(payload["checks"]) > 0
    names = {c["name"] for c in payload["checks"]}
    assert "database" in names
    assert "telegram_config" in names
    assert any(n.startswith("candles:") for n in names)
    assert any(n.startswith("last_run:") for n in names)
    for check in payload["checks"]:
        assert check["status"] in ("OK", "WARN", "FAIL")
        assert isinstance(check["detail"], str)


def test_health_heartbeat_writes_no_journal_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    now = dt.datetime.now(dt.UTC)
    _healthy_setup(url, monkeypatch, now)

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    before = store.count_journal_entries()

    result = runner.invoke(app, ["health", "heartbeat"])

    assert result.exit_code == 0
    assert len(_fake_notifier.sent_messages) == 1
    after = store.count_journal_entries()
    assert after == before


def test_health_heartbeat_sends_via_notifier_and_includes_rulebook(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _healthy_setup(url, monkeypatch, dt.datetime.now(dt.UTC))

    result = runner.invoke(app, ["health", "heartbeat"])

    assert result.exit_code == 0
    assert "Tidemark healthy" in _fake_notifier.sent_messages[0]
    assert "Rulebook: section-01-v1.1" in _fake_notifier.sent_messages[0]


# -- observe (Phase 5, Section 2) ---------------------------------------------


def _seed_section1_watch(url: str, symbol: str, evaluated_at: dt.datetime) -> None:
    """Write one Section 1 journal row directly (bypassing a full Section 1
    evaluation) so Section 2 has an as-of LONG_WATCH record to activate
    against, with a held support level Section 2 can pin.
    """
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    level = {
        "role": "support",
        "price": 100.0,
        "zone_low": 99.0,
        "zone_high": 101.0,
        "touches": 2,
        "is_major": True,
        "held": True,
        "source": "swing_low_cluster",
        "formed_at": evaluated_at.isoformat(),
    }
    entry = JournalEntry(
        asset=symbol,
        evaluated_at=evaluated_at,
        recorded_at=evaluated_at,
        rule_version=htf.RULE_VERSION,
        state=htf.BULLISH,
        watch=htf.LONG_WATCH,
        grade="B",
        reason_code="MAJOR_SUPPORT",
        active_levels=[level],
        fib={},
        swings_used=[],
        alert_sent=False,
        alert_reason=None,
    )
    store.save_journal_entry(entry)


def _raw_1h_candle(open_time: dt.datetime, close: float) -> RawCandle:
    return RawCandle(
        open_time=open_time,
        close_time=open_time + dt.timedelta(hours=1),
        open=close,
        high=close + 0.6,
        low=close - 0.4,
        close=close,
        volume=1.0,
    )


def _seed_1h_candles(url: str, symbol: str, n: int) -> None:
    """Flat 1H candles that each interact with the seeded support zone
    and close back away from their own low - an R1 reaction on every row.
    """
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    candles = [_raw_1h_candle(START + dt.timedelta(hours=i), 100.5) for i in range(n)]
    store.upsert_candles(VENUE, symbol, "1h", candles, dt.datetime.now(dt.UTC))


def test_observe_run_journals_a_row_per_1h_candle(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_section1_watch(url, SYMBOL, START)
    _seed_1h_candles(url, SYMBOL, n=3)

    result = runner.invoke(app, ["observe", "run", "--symbols", SYMBOL])

    assert result.exit_code == 0
    assert "COMPLETED" in result.stdout

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    assert len(store.observation_history(SYMBOL)) == 3


def test_observe_run_twice_is_idempotent(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_section1_watch(url, SYMBOL, START)
    _seed_1h_candles(url, SYMBOL, n=3)

    runner.invoke(app, ["observe", "run", "--symbols", SYMBOL])
    runner.invoke(app, ["observe", "run", "--symbols", SYMBOL])

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    assert len(store.observation_history(SYMBOL)) == 3


def test_observe_list_and_stats_show_rows(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_section1_watch(url, SYMBOL, START)
    _seed_1h_candles(url, SYMBOL, n=3)
    runner.invoke(app, ["observe", "run", "--symbols", SYMBOL])

    list_result = runner.invoke(app, ["observe", "list", "--symbol", SYMBOL, "--days", "36500"])
    stats_result = runner.invoke(app, ["observe", "stats", "--days", "36500"])

    assert list_result.exit_code == 0
    assert "REACTION_DETECTED" in list_result.stdout
    assert stats_result.exit_code == 0
    assert "Total observation rows: 3" in stats_result.stdout
    assert "REACTION_DETECTED" in stats_result.stdout


def test_observe_list_reports_none_when_empty(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["observe", "list", "--symbol", SYMBOL])

    assert result.exit_code == 0
    assert "No observation rows found." in result.stdout


def test_observe_stats_reports_none_when_empty(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["observe", "stats"])

    assert result.exit_code == 0
    assert "No observation rows found." in result.stdout


def test_observe_run_never_constructs_a_telegram_notifier(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hard constraint: no Telegram alerts from Section 2. Rather than
    trust that `cli.observe_run` merely *doesn't call* a notifier, make
    constructing one raise - proving the observe path never even touches
    `TelegramNotifier`, unlike `run` (Section 1's pipeline).
    """
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_section1_watch(url, SYMBOL, START)
    _seed_1h_candles(url, SYMBOL, n=3)

    class _ExplodingNotifier:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("Section 2 must never construct a TelegramNotifier")

    monkeypatch.setattr(cli_module, "TelegramNotifier", _ExplodingNotifier)

    result = runner.invoke(app, ["observe", "run", "--symbols", SYMBOL])

    assert result.exit_code == 0


_FORBIDDEN_SIGNAL_WORDS = ("entry", "stop", "sl", "tp", "target", "r:r", "buy", "sell")


def test_observe_output_never_contains_trading_signal_language(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_section1_watch(url, SYMBOL, START)
    _seed_1h_candles(url, SYMBOL, n=3)
    runner.invoke(app, ["observe", "run", "--symbols", SYMBOL])

    list_result = runner.invoke(app, ["observe", "list", "--symbol", SYMBOL, "--days", "36500"])
    stats_result = runner.invoke(app, ["observe", "stats", "--days", "36500"])

    combined = (list_result.stdout + stats_result.stdout).lower()
    for word in _FORBIDDEN_SIGNAL_WORDS:
        assert word not in combined, f"found forbidden word {word!r} in observe output"
