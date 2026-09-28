"""Smoke test: the CLI is wired up end to end, plus context evaluate/history/explain."""

import datetime as dt
import json

import pytest
from sqlalchemy import inspect
from typer.testing import CliRunner

from tidemark import __version__
from tidemark import cli as cli_module
from tidemark.cli import _encode_for_display, _parse_csv, app
from tidemark.context import htf, mtf
from tidemark.data import asset_class as asset_class_module
from tidemark.data.exchange import ExchangeClient, RawCandle
from tidemark.data.models import (
    ContextRecord,
    JournalEntry,
    MarketRegistry,
    Observation,
    UniverseSnapshot,
    UniverseSnapshotRow,
)
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.data.timeframes import TIMEFRAMES
from tidemark.journal.records import build_journal_entry
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


def _raw_1d_candle(open_time: dt.datetime, close: float = 100.0) -> RawCandle:
    return RawCandle(
        open_time=open_time,
        close_time=open_time + dt.timedelta(days=1),
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


def _seed_journal_entry(url: str, symbol: str, evaluated_at: dt.datetime, **overrides) -> None:
    """Seeds `journal_entries` the way `tidemark run` actually does: an
    in-memory `ContextRecord` -> `build_journal_entry` -> `save_journal_
    entry` - never a bespoke row insert, and never the removed `context
    evaluate`/`save_context_record` path. A bespoke insert is exactly what
    let `context_read.py` read the wrong (removed) `context_records` table
    for months undetected - see
    docs/adr/0011-market-intelligence-layer.md's "Addendum: removing
    context_records".
    """
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    defaults = dict(
        asset=symbol,
        evaluated_at=evaluated_at,
        rule_version=htf.RULE_VERSION,
        state="INSUFFICIENT_STRUCTURE",
        watch=htf.WAIT,
        grade=None,
        reason_code="NOT_ENOUGH_SWINGS",
        active_levels=[],
        fib={},
        swings_used=[],
    )
    defaults.update(overrides)
    record = ContextRecord(**defaults)
    store.save_journal_entry(build_journal_entry(record, recorded_at=evaluated_at))


def test_context_history_reports_no_setups_when_empty(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["context", "history", "--symbol", SYMBOL])

    assert result.exit_code == 0
    assert "No setups found." in result.stdout


def test_context_history_shows_data_written_by_the_production_path(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    evaluated_at = START + dt.timedelta(hours=4 * 3)
    _seed_journal_entry(url, SYMBOL, evaluated_at, state="BULLISH", watch="LONG_WATCH", grade="A")

    # A large --days window, since the fixture's fixed timestamp
    # (2026-01-01) is far in the past relative to the real clock.
    result = runner.invoke(app, ["context", "history", "--symbol", SYMBOL, "--days", "36500"])

    assert result.exit_code == 0
    assert evaluated_at.isoformat() in result.stdout
    assert "BULLISH" in result.stdout
    assert "LONG_WATCH" in result.stdout


def test_context_explain_without_prior_evaluation_errors(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["context", "explain", "--symbol", SYMBOL])

    assert result.exit_code != 0
    assert "No Section 1 result recorded" in result.stdout


def test_context_explain_shows_data_written_by_the_production_path(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_journal_entry(url, SYMBOL, START + dt.timedelta(hours=4 * 3))

    result = runner.invoke(app, ["context", "explain", "--symbol", SYMBOL])

    assert result.exit_code == 0
    assert "State:  INSUFFICIENT_STRUCTURE" in result.stdout
    assert "Swings used:" in result.stdout
    assert "Active levels:" in result.stdout


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


def test_run_explicit_symbols_records_explicit_source_on_the_run_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier
) -> None:
    """The run record (Phase 6, Merge 3) must trace back to which symbol
    source produced it - an explicit --symbols override records EXPLICIT
    with no snapshot id."""
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, SYMBOL, n=3)

    result = runner.invoke(app, ["run", "--symbols", SYMBOL])

    assert result.exit_code == 0
    assert "symbol source: EXPLICIT" in result.stdout

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    run = store.latest_runs_by_command()["run"]
    assert run.symbol_source == "EXPLICIT"
    assert run.symbol_source_snapshot_id is None


def test_run_snapshot_source_records_snapshot_id_on_the_run_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier
) -> None:
    """When no --symbols override is given and a fresh snapshot exists,
    the run record stores SNAPSHOT plus the snapshot id it used, so a
    journal entry can always be traced back to the universe that
    produced it."""
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_registry_row(url, SYMBOL)
    _seed_snapshot_with_selection(
        url, "snap-1", SYMBOL, dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
    )
    _seed_candles(url, SYMBOL, n=3)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0
    assert "symbol source: SNAPSHOT (snap-1)" in result.stdout

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    run = store.latest_runs_by_command()["run"]
    assert run.symbol_source == "SNAPSHOT"
    assert run.symbol_source_snapshot_id == "snap-1"


def test_run_falls_back_to_tidemark_symbols_and_warns_when_no_snapshot_exists(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier
) -> None:
    """TIDEMARK_SYMBOLS regression check (ADR 0009): with no snapshot and
    no --symbols override, TIDEMARK_SYMBOLS still works as the last-resort
    fallback, and the CLI surfaces a clear warning naming the source."""
    url = _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("TIDEMARK_SYMBOLS", SYMBOL)
    _seed_candles(url, SYMBOL, n=3)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0
    assert "symbol source: TIDEMARK_SYMBOLS_FALLBACK" in result.stdout
    assert "WARNING" in result.stdout

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    run = store.latest_runs_by_command()["run"]
    assert run.symbol_source == "TIDEMARK_SYMBOLS_FALLBACK"
    assert run.symbol_source_snapshot_id is None
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
    store.start_run(
        "r1",
        "run",
        now - dt.timedelta(minutes=10),
        symbol_source="SNAPSHOT",
        symbol_source_snapshot_id="healthy-snap",
    )
    store.finish_run("r1", now - dt.timedelta(minutes=5), "COMPLETED", {})

    # A fresh universe snapshot (Phase 6, Merge 3) so check_universe_
    # freshness and the symbol-source resolution _build_health_report now
    # performs both report OK - "everything healthy" must include the
    # universe dimension too.
    store.save_universe_snapshot(
        UniverseSnapshot(
            snapshot_id="healthy-snap",
            snapshot_at=now,
            methodology_version="universe-v2",
            venue=VENUE,
            metric_name="MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D",
            metric_window_days=30,
            k=50,
            n_selected=1,
            provenance="FORWARD",
            candle_hash="deadbeef",
            counts_by_exclusion_reason={},
        ),
        [
            UniverseSnapshotRow(
                snapshot_id="healthy-snap",
                symbol=SYMBOL,
                rank=1,
                metric_value=1000.0,
                eligible=True,
                selected=True,
                exclusion_reason=None,
            )
        ],
    )

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
    assert any(n == "universe_freshness" for n in names)
    assert any(n == "symbol_source" for n in names)
    assert payload["last_run_symbol_source"] == "SNAPSHOT"
    assert payload["last_run_symbol_source_snapshot_id"] == "healthy-snap"


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


def test_observe_run_journals_under_v0_2(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Live observation runs on v0.2 (grade-only session termination) as of
    # 2026-09-25 - see docs/rulebook/section-02-v0.2-justification.md and
    # docs/rulebook/README.md. Rows collected before that date keep
    # rule_version "section-02-v0.1" and are never rewritten.
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_section1_watch(url, SYMBOL, START)
    _seed_1h_candles(url, SYMBOL, n=3)

    result = runner.invoke(app, ["observe", "run", "--symbols", SYMBOL])

    assert result.exit_code == 0
    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    observations = store.observation_history(SYMBOL)
    assert len(observations) == 3
    assert all(o.rule_version == mtf.RULE_VERSION_V2 for o in observations)
    assert mtf.RULE_VERSION_V2 == "section-02-v0.2"


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


# -- replay (Phase 5B) ---------------------------------------------------------


def test_replay_rejects_an_unimplemented_rule_version(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["replay", "--rule-version", "section-02-v0.3"])

    assert result.exit_code == 1
    assert "section-02-v0.3" in result.stdout
    assert "section-02-v0.1" in result.stdout
    assert "section-02-v0.2" in result.stdout


def test_replay_accepts_v0_2_rule_version(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, SYMBOL, n=30)
    _seed_1h_candles(url, SYMBOL, n=50)

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    before = (
        store.count_journal_entries(),
        len(store.observation_history(SYMBOL)),
    )

    result = runner.invoke(
        app, ["replay", "--rule-version", "section-02-v0.2", "--symbols", SYMBOL]
    )

    after = (
        store.count_journal_entries(),
        len(store.observation_history(SYMBOL)),
    )

    assert result.exit_code == 0
    assert before == after == (0, 0)
    assert "## Table 2" in result.stdout
    assert "Grade at start" in result.stdout
    assert "**sum**" in result.stdout


def test_replay_writes_nothing_and_prints_all_three_tables(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, SYMBOL, n=30)
    _seed_1h_candles(url, SYMBOL, n=50)

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    before = (
        store.count_journal_entries(),
        len(store.observation_history(SYMBOL)),
    )

    result = runner.invoke(
        app, ["replay", "--rule-version", "section-02-v0.1", "--symbols", SYMBOL]
    )

    after = (
        store.count_journal_entries(),
        len(store.observation_history(SYMBOL)),
    )

    assert result.exit_code == 0
    assert before == after == (0, 0)
    assert "## Data snapshot" in result.stdout
    assert "## Table 1" in result.stdout
    assert "## Table 2" in result.stdout
    assert "## Table 3" in result.stdout
    assert "**sum**" in result.stdout


def test_replay_reports_deterministically_across_two_invocations(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, SYMBOL, n=20)
    _seed_1h_candles(url, SYMBOL, n=40)

    first = runner.invoke(app, ["replay", "--rule-version", "section-02-v0.1", "--symbols", SYMBOL])
    second = runner.invoke(
        app, ["replay", "--rule-version", "section-02-v0.1", "--symbols", SYMBOL]
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    # generated_at is the one deliberately time-varying line; everything else
    # - including the snapshot hash and every table - must match exactly.
    first_lines = [line for line in first.stdout.splitlines() if "generated_at" not in line]
    second_lines = [line for line in second.stdout.splitlines() if "generated_at" not in line]
    assert first_lines == second_lines


# -- universe (Phase 6, Merge 1) -----------------------------------------------


def _seed_registry_row(url: str, symbol: str) -> None:
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    base = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    store.upsert_market_registry_row(
        MarketRegistry(
            venue=VENUE,
            symbol=symbol,
            contract_type="perpetual",
            quote_currency="USDT",
            first_candle_seen_at=base,
            last_candle_seen_at=base + dt.timedelta(days=1),
            first_seen_in_venue_list_at=base,
            last_seen_in_venue_list_at=base + dt.timedelta(days=1),
            status="ACTIVE",
            section1_first_usable_at=None,
            section1_eligibility_checked_at=None,
        )
    )


def _seed_snapshot(url: str, snapshot_id: str) -> None:
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    store.save_universe_snapshot(
        UniverseSnapshot(
            snapshot_id=snapshot_id,
            snapshot_at=dt.datetime(2026, 9, 25, tzinfo=dt.UTC),
            methodology_version="universe-v1",
            venue=VENUE,
            metric_name="MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D",
            metric_window_days=30,
            k=50,
            n_selected=1,
            provenance="FORWARD",
            candle_hash="deadbeef",
            counts_by_exclusion_reason={},
        ),
        [
            UniverseSnapshotRow(
                snapshot_id=snapshot_id,
                symbol=SYMBOL,
                rank=1,
                metric_value=1_000_000.0,
                eligible=True,
                selected=True,
                exclusion_reason=None,
            ),
            UniverseSnapshotRow(
                snapshot_id=snapshot_id,
                symbol="DOGE/USDT:USDT",
                rank=2,
                metric_value=10.0,
                eligible=False,
                selected=False,
                exclusion_reason="BELOW_RANK_CUTOFF",
            ),
        ],
    )


def test_universe_registry_reports_none_when_empty(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["universe", "registry"])

    assert result.exit_code == 0
    assert "No registry rows found yet." in result.stdout


def test_universe_registry_lists_seeded_rows(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_registry_row(url, SYMBOL)

    result = runner.invoke(app, ["universe", "registry"])

    assert result.exit_code == 0
    assert SYMBOL in result.stdout
    assert "ACTIVE" in result.stdout


def test_universe_snapshots_reports_none_when_empty(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["universe", "snapshots"])

    assert result.exit_code == 0
    assert "No snapshots found yet." in result.stdout


def test_universe_snapshots_lists_seeded_headers(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_snapshot(url, "snap-1")

    result = runner.invoke(app, ["universe", "snapshots"])

    assert result.exit_code == 0
    assert "snap-1" in result.stdout
    assert "FORWARD" in result.stdout


def test_universe_show_reports_error_for_unknown_snapshot(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["universe", "show", "--snapshot-id", "nope"])

    assert result.exit_code == 1
    assert "No snapshot found" in result.stdout


def test_universe_show_lists_full_ranking_selected_first(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_snapshot(url, "snap-1")

    result = runner.invoke(app, ["universe", "show", "--snapshot-id", "snap-1"])

    assert result.exit_code == 0
    assert SYMBOL in result.stdout
    assert "DOGE/USDT:USDT" in result.stdout
    assert "BELOW_RANK_CUTOFF" in result.stdout
    # the selected symbol is listed before the excluded one
    assert result.stdout.index(SYMBOL) < result.stdout.index("DOGE/USDT:USDT")


# -- universe snapshot / coverage (Phase 6, Merge 2B) --------------------------

_ELIGIBLE_4H_VALUES = [
    120,
    115,
    110,
    105,
    100,
    110,
    120,
    130,
    120,
    115,
    112,
    110,
    120,
    130,
    140,
    150,
    140,
    130,
    120,
]


def _seed_eligible_symbol(url: str, symbol: str, as_of: dt.datetime) -> None:
    """A registry symbol with >=30 closed daily candles and a 4H zigzag
    that exits INSUFFICIENT_STRUCTURE, so a BACKFILLED snapshot at
    `as_of` selects it.
    """
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    seen_at = as_of - dt.timedelta(days=200)
    store.record_market_listing(VENUE, symbol, "perpetual", "USDT", seen_at)
    store.record_classification(
        VENUE,
        symbol,
        "COIN",
        asset_class_module.CRYPTO,
        asset_class_module.CLASSIFICATION_SOURCE,
        seen_at,
        asset_class_module.CLASSIFICATION_METHODOLOGY_VERSION,
    )
    daily = [_raw_1d_candle(as_of - dt.timedelta(days=40 - i), close=100.0) for i in range(40)]
    store.upsert_candles(VENUE, symbol, "1d", daily, as_of)
    start = as_of - dt.timedelta(hours=4 * len(_ELIGIBLE_4H_VALUES))
    four_h = [
        RawCandle(
            open_time=start + dt.timedelta(hours=4 * i),
            close_time=start + dt.timedelta(hours=4 * (i + 1)),
            open=v,
            high=v,
            low=v,
            close=v,
            volume=1.0,
        )
        for i, v in enumerate(_ELIGIBLE_4H_VALUES)
    ]
    store.upsert_candles(VENUE, symbol, "4h", four_h, as_of)


def test_universe_snapshot_command_backfilled_generates_a_snapshot(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    as_of = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    _seed_eligible_symbol(url, SYMBOL, as_of)

    result = runner.invoke(app, ["universe", "snapshot", "--as-of", as_of.isoformat()])

    assert result.exit_code == 0
    assert "BACKFILLED" in result.stdout
    assert "n_selected=1/50" in result.stdout

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    [snapshot] = store.universe_snapshots(VENUE)
    assert snapshot.provenance == "BACKFILLED"
    [row] = store.universe_snapshot_rows(snapshot.snapshot_id)
    assert row.symbol == SYMBOL
    assert row.selected is True


def test_universe_snapshot_command_backfilled_never_touches_registry_cache(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    as_of = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    _seed_eligible_symbol(url, SYMBOL, as_of)

    runner.invoke(app, ["universe", "snapshot", "--as-of", as_of.isoformat()])

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    row = store.market_registry_row(VENUE, SYMBOL)
    assert row is not None
    assert row.section1_first_usable_at is None


def test_universe_coverage_reports_gracefully_when_empty(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["universe", "coverage"])

    assert result.exit_code == 0
    assert "No snapshots found yet." in result.stdout


def test_universe_coverage_reports_snapshot_summary(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    as_of = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    _seed_eligible_symbol(url, SYMBOL, as_of)
    runner.invoke(app, ["universe", "snapshot", "--as-of", as_of.isoformat()])

    result = runner.invoke(app, ["universe", "coverage"])

    assert result.exit_code == 0
    assert "ranked:               1" in result.stdout
    assert "eligible:             1" in result.stdout
    assert "selected:             1" in result.stdout


def test_universe_coverage_accepts_explicit_snapshot_id(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_snapshot(url, "snap-1")

    result = runner.invoke(app, ["universe", "coverage", "--snapshot-id", "snap-1"])

    assert result.exit_code == 0
    assert "snap-1" in result.stdout


def test_universe_coverage_unknown_snapshot_id_errors(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["universe", "coverage", "--snapshot-id", "nope"])

    assert result.exit_code == 1


# -- universe sync (Phase 6, Merge 3) ------------------------------------------


class _FakeSyncCcxtExchange:
    apiKey = ""
    secret = ""

    def __init__(self, rows: list[list]) -> None:
        self._rows = rows

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):  # noqa: ARG002
        since = since or 0
        rows = [r for r in self._rows if r[0] >= since]
        return rows[:limit] if limit is not None else rows


def test_universe_sync_reports_no_op_when_no_snapshot_exists(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["universe", "sync"])

    assert result.exit_code == 0
    assert "No universe snapshot" in result.stdout


def test_universe_sync_fetches_every_timeframe_for_the_selected_symbol_only(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backfills 1H/4H/1D/1W for the currently SELECTED symbols only."""
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_registry_row(url, SYMBOL)
    _seed_snapshot_with_selection(url, "snap-1", SYMBOL, dt.datetime.now(dt.UTC))

    open_time = dt.datetime.now(dt.UTC) - dt.timedelta(days=10)
    row = [int(open_time.timestamp() * 1000), 100.0, 110.0, 90.0, 105.0, 10.0]
    fake_ccxt = _FakeSyncCcxtExchange([row])
    monkeypatch.setattr(
        cli_module,
        "ExchangeClient",
        lambda venue: ExchangeClient(exchange=fake_ccxt, venue=venue),
    )

    result = runner.invoke(app, ["universe", "sync", "--days", "15"])

    assert result.exit_code == 0
    assert "Syncing 1 selected symbol" in result.stdout

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    for timeframe in ("1h", "4h", "1d", "1w"):
        assert store.count_candles(VENUE, SYMBOL, timeframe) == 1


def test_universe_sync_is_idempotent_across_two_invocations(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_registry_row(url, SYMBOL)
    _seed_snapshot_with_selection(url, "snap-1", SYMBOL, dt.datetime.now(dt.UTC))

    open_time = dt.datetime.now(dt.UTC) - dt.timedelta(days=10)
    row = [int(open_time.timestamp() * 1000), 100.0, 110.0, 90.0, 105.0, 10.0]
    fake_ccxt = _FakeSyncCcxtExchange([row])
    monkeypatch.setattr(
        cli_module,
        "ExchangeClient",
        lambda venue: ExchangeClient(exchange=fake_ccxt, venue=venue),
    )

    first = runner.invoke(app, ["universe", "sync", "--days", "15"])
    second = runner.invoke(app, ["universe", "sync", "--days", "15"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "duplicates=1" in second.stdout

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    assert store.count_candles(VENUE, SYMBOL, "4h") == 1


def test_observer_uses_a_freshly_generated_snapshots_selection(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 6, Merge 3: a real, freshly generated snapshot (not just
    seeded registry/snapshot rows) takes priority over TIDEMARK_SYMBOLS -
    `observe run` must process the snapshot's selected symbol, not the
    TIDEMARK_SYMBOLS one, when no --symbols override is given."""
    url = _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("TIDEMARK_SYMBOLS", SYMBOL)
    as_of = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)  # fresh: well under 48h
    _seed_eligible_symbol(url, "ETH/USDT:USDT", as_of)  # a different, selected symbol

    snapshot_result = runner.invoke(app, ["universe", "snapshot", "--as-of", as_of.isoformat()])
    assert snapshot_result.exit_code == 0

    _seed_section1_watch(url, "ETH/USDT:USDT", START)
    _seed_1h_candles(url, "ETH/USDT:USDT", n=2)

    result = runner.invoke(app, ["observe", "run"])

    assert result.exit_code == 0
    assert "symbol source: SNAPSHOT" in result.stdout
    assert "ETH/USDT:USDT" in result.stdout
    assert SYMBOL not in result.stdout


def test_universe_commands_read_no_quote_volume_column(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hard constraint, checked via the schema itself: `candles` gained no
    quote_volume column for this merge - there is nothing for a universe
    command to have read it from."""
    url = _use_temp_db(tmp_path, monkeypatch)
    engine = create_store_engine(url)
    init_db(engine)
    columns = {col["name"] for col in inspect(engine).get_columns("candles")}
    assert "quote_volume" not in columns


# -- Phase 6, Merge 3: observer's symbol-source resolution order --------------


def _seed_snapshot_with_selection(
    url: str, snapshot_id: str, selected_symbol: str, snapshot_at: dt.datetime
) -> None:
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    store.save_universe_snapshot(
        UniverseSnapshot(
            snapshot_id=snapshot_id,
            snapshot_at=snapshot_at,
            methodology_version="universe-v2",
            venue=VENUE,
            metric_name="MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D",
            metric_window_days=30,
            k=50,
            n_selected=1,
            provenance="FORWARD",
            candle_hash="deadbeef",
            counts_by_exclusion_reason={},
        ),
        [
            UniverseSnapshotRow(
                snapshot_id=snapshot_id,
                symbol=selected_symbol,
                rank=1,
                metric_value=1_000_000.0,
                eligible=True,
                selected=True,
                exclusion_reason=None,
            )
        ],
    )


def test_observer_prefers_a_fresh_snapshots_selection_over_tidemark_symbols(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per ADR 0009's resolution order, a fresh, valid universe snapshot
    takes priority over TIDEMARK_SYMBOLS even when TIDEMARK_SYMBOLS names
    a different symbol - TIDEMARK_SYMBOLS is a fallback only, used when no
    snapshot exists or the latest one is stale.
    """
    url = _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("TIDEMARK_SYMBOLS", SYMBOL)

    # Seed a FRESH snapshot selecting a DIFFERENT symbol - it must be the
    # one `observe run` evaluates below, not the TIDEMARK_SYMBOLS one.
    _seed_registry_row(url, "ETH/USDT:USDT")
    _seed_snapshot_with_selection(
        url, "snap-1", "ETH/USDT:USDT", dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
    )

    _seed_section1_watch(url, "ETH/USDT:USDT", START)
    _seed_1h_candles(url, "ETH/USDT:USDT", n=2)

    result = runner.invoke(app, ["observe", "run"])  # no --symbols: reads the snapshot

    assert result.exit_code == 0
    assert "symbol source: SNAPSHOT" in result.stdout
    assert "ETH/USDT:USDT" in result.stdout
    assert SYMBOL not in result.stdout

    engine = create_store_engine(url)
    store = TidemarkStore(engine)
    assert len(store.observation_history("ETH/USDT:USDT")) == 2


def test_observer_falls_back_to_tidemark_symbols_when_no_snapshot_exists(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Populated through Merge 2A's actual write paths
    (record_market_listing/record_candle_coverage) rather than a
    hand-built MarketRegistry row: registry rows alone are not a
    snapshot, so the observer must still fall back to TIDEMARK_SYMBOLS.
    """
    url = _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("TIDEMARK_SYMBOLS", SYMBOL)

    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    now = dt.datetime.now(dt.UTC)
    store.record_market_listing(VENUE, "ETH/USDT:USDT", "perpetual", "USDT", now)
    store.record_candle_coverage(VENUE, "ETH/USDT:USDT", now, now)

    _seed_section1_watch(url, SYMBOL, START)
    _seed_1h_candles(url, SYMBOL, n=2)

    result = runner.invoke(app, ["observe", "run"])

    assert result.exit_code == 0
    assert SYMBOL in result.stdout
    assert "ETH/USDT:USDT" not in result.stdout


def test_observer_symbol_source_unaffected_by_asset_class_classification(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UNIV-08 regression check: classifying registry symbols (some of
    them NON_CRYPTO) does not itself create a universe snapshot, so
    `observe run` still falls back to TIDEMARK_SYMBOLS."""
    url = _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("TIDEMARK_SYMBOLS", SYMBOL)

    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    now = dt.datetime.now(dt.UTC)
    store.record_market_listing(VENUE, "MSTR/USDT:USDT", "perpetual", "USDT", now)
    store.record_classification(
        VENUE,
        "MSTR/USDT:USDT",
        "EQUITY",
        asset_class_module.NON_CRYPTO,
        asset_class_module.CLASSIFICATION_SOURCE,
        now,
        asset_class_module.CLASSIFICATION_METHODOLOGY_VERSION,
    )

    _seed_section1_watch(url, SYMBOL, START)
    _seed_1h_candles(url, SYMBOL, n=2)

    result = runner.invoke(app, ["observe", "run"])

    assert result.exit_code == 0
    assert SYMBOL in result.stdout
    assert "MSTR/USDT:USDT" not in result.stdout


# -- universe registry: extended output (Phase 6, Merge 2A) -------------------


def test_universe_registry_shows_row_counts_and_handles_no_candle_coverage(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merge 2A: a freshly-discovered symbol has no candle coverage yet
    (first_candle_seen_at/last_candle_seen_at are None) - the registry
    command must render that as '-', not crash, and show the stored 1D
    row count.
    """
    url = _use_temp_db(tmp_path, monkeypatch)
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    now = dt.datetime.now(dt.UTC)
    store.record_market_listing(VENUE, SYMBOL, "perpetual", "USDT", now)

    result = runner.invoke(app, ["universe", "registry"])

    assert result.exit_code == 0
    assert "ROWS" in result.stdout
    row_line = next(line for line in result.stdout.splitlines() if SYMBOL in line)
    symbol, status, first_candle, last_candle, rows, usable = row_line.split()
    assert symbol == SYMBOL
    assert status == "ACTIVE"
    assert first_candle == "-"  # no candle coverage yet renders as '-', not a crash
    assert last_candle == "-"
    assert rows == "0"
    assert usable == "-"


def test_universe_registry_shows_candle_row_count_after_backfill(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    now = dt.datetime.now(dt.UTC)
    store.record_market_listing(VENUE, SYMBOL, "perpetual", "USDT", now)
    store.record_candle_coverage(VENUE, SYMBOL, now, now)
    store.upsert_candles(VENUE, SYMBOL, "1d", [_raw_1d_candle(now)], now)

    result = runner.invoke(app, ["universe", "registry"])

    assert result.exit_code == 0
    row_line = next(line for line in result.stdout.splitlines() if SYMBOL in line)
    fields = row_line.split()
    assert fields[4] == "1"  # ROWS


# -- Windows console encoding: real venue symbols are not always ASCII --------
# A live acceptance run against binanceusdm surfaced 5 real CJK-ticker
# meme-coin perpetuals (e.g. "哈基米/USDT:USDT") that crashed `universe
# registry` under Windows' legacy cp1252 console codepage - every command
# before Phase 6 only ever printed operator-chosen ASCII (TIDEMARK_SYMBOLS),
# so this never came up before `universe registry`/`backfill`/`show` started
# printing symbols straight from the live venue listing.


def test_encode_for_display_replaces_characters_the_encoding_cannot_represent() -> None:
    result = _encode_for_display("哈基米/USDT:USDT", "cp1252")
    assert result != "哈基米/USDT:USDT"  # substituted, not raised
    # a plain-ASCII symbol round-trips unchanged
    assert _encode_for_display("BTC/USDT:USDT", "cp1252") == "BTC/USDT:USDT"


def test_encode_for_display_is_a_noop_under_utf8() -> None:
    assert _encode_for_display("哈基米/USDT:USDT", "utf-8") == "哈基米/USDT:USDT"


def test_universe_registry_does_not_crash_on_a_non_ascii_symbol(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    now = dt.datetime.now(dt.UTC)
    store.record_market_listing(VENUE, "哈基米/USDT:USDT", "perpetual", "USDT", now)

    result = runner.invoke(app, ["universe", "registry"])

    assert result.exit_code == 0


def test_universe_show_does_not_crash_on_a_non_ascii_symbol(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    store.save_universe_snapshot(
        UniverseSnapshot(
            snapshot_id="snap-cjk",
            snapshot_at=dt.datetime(2026, 9, 25, tzinfo=dt.UTC),
            methodology_version="universe-v1",
            venue=VENUE,
            metric_name="MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D",
            metric_window_days=30,
            k=50,
            n_selected=1,
            provenance="FORWARD",
            candle_hash="deadbeef",
            counts_by_exclusion_reason={},
        ),
        [
            UniverseSnapshotRow(
                snapshot_id="snap-cjk",
                symbol="哈基米/USDT:USDT",
                rank=1,
                metric_value=1.0,
                eligible=True,
                selected=True,
                exclusion_reason=None,
            )
        ],
    )

    result = runner.invoke(app, ["universe", "show", "--snapshot-id", "snap-cjk"])

    assert result.exit_code == 0


def test_run_does_not_crash_on_a_non_ascii_symbol(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _fake_notifier
) -> None:
    """Phase 6, Merge 3 regression: `run` can now default to a snapshot's
    selection, which - unlike TIDEMARK_SYMBOLS - can name a non-ASCII
    ticker (Merge 2A found live CJK-named meme-coin perpetuals on
    binanceusdm). `_print_pipeline_outcome` must use `_safe_echo`, the
    same as every universe inspection command."""
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_candles(url, "哈基米/USDT:USDT", n=3)

    result = runner.invoke(app, ["run", "--symbols", "哈基米/USDT:USDT"])

    assert result.exit_code == 0


def test_observe_run_does_not_crash_on_a_non_ascii_symbol(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same regression as above, for `observe run`'s
    `_print_observe_outcome`."""
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_section1_watch(url, "哈基米/USDT:USDT", START)
    _seed_1h_candles(url, "哈基米/USDT:USDT", n=2)

    result = runner.invoke(app, ["observe", "run", "--symbols", "哈基米/USDT:USDT"])

    assert result.exit_code == 0


def test_health_check_does_not_crash_on_a_non_ascii_symbol(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same regression, for `health check`'s human-readable per-check
    line: TIDEMARK_SYMBOLS (and, since Merge 3, a snapshot) can now name a
    non-ASCII ticker where `run`/`observe run` are concerned, and
    `health check` builds its per-symbol checks from the same resolved
    list."""
    url = _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("TIDEMARK_SYMBOLS", "哈基米/USDT:USDT")
    _seed_candles(url, "哈基米/USDT:USDT", n=3, timeframe="1h")

    result = runner.invoke(app, ["health", "check"])

    # health check exits via typer.Exit(code=EXIT_CODES[status]) on every
    # path - that raises SystemExit by design, not a crash. A genuine
    # crash (e.g. the UnicodeEncodeError this regression guards against)
    # would show up as some other exception type instead.
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert result.exit_code in (0, 1, 2)
    assert "Overall:" in result.stdout


# -- tidemark evidence ---------------------------------------------------------


def _seed_observation(url: str, evaluated_at: dt.datetime, state: str) -> None:
    engine = create_store_engine(url)
    init_db(engine)
    store = TidemarkStore(engine)
    store.save_observation(
        Observation(
            asset=SYMBOL,
            evaluated_at=evaluated_at,
            rule_version=mtf.RULE_VERSION_V2,
            section_1_state=htf.BULLISH,
            section_1_watch=htf.LONG_WATCH,
            section_1_grade="B",
            section_1_level_price=100.0,
            session_started_at=evaluated_at,
            grade_at_start="B",
            grade_history=[],
            interaction_detected=False,
            reaction_tier=None,
            reaction_condition_matched=None,
            reaction_started_at=None,
            structure_reference_price=None,
            structure_reference_confirmed_at=None,
            structure_change=None,
            failure=None,
            expiry=False,
            state=state,
            reason_code=state,
            swings_used=[],
        )
    )


def test_evidence_refuses_full_report_on_a_young_archive(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_observation(url, START, mtf.NO_INTERACTION)

    result = runner.invoke(app, ["evidence"])

    assert result.exit_code == 1
    assert "too young" in result.stdout or "fewer than 14 days" in result.stdout
    assert "Data sufficiency verdicts" in result.stdout
    # The refusal never prints the full per-session tables.
    assert "Section 2 sessions" not in result.stdout


def test_evidence_allow_insufficient_prints_the_full_preliminary_report(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_observation(url, START, mtf.NO_INTERACTION)

    result = runner.invoke(app, ["evidence", "--allow-insufficient"])

    assert result.exit_code == 0
    assert "PRELIMINARY" in result.stdout
    assert "Section 2 sessions" in result.stdout


def test_evidence_on_an_empty_archive_reports_gracefully(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    engine = create_store_engine(url)
    init_db(engine)

    result = runner.invoke(app, ["evidence"])

    assert result.exit_code == 1
    assert "Archive coverage" in result.stdout


def test_evidence_json_output_is_valid_json(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _use_temp_db(tmp_path, monkeypatch)
    _seed_observation(url, START, mtf.NO_INTERACTION)

    result = runner.invoke(app, ["evidence", "--json", "--allow-insufficient"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["full_report"] is True
    assert payload["is_young_archive"] is True
