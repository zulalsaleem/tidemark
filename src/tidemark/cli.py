"""Tidemark CLI entrypoint.

Read-only copilot: the CLI can fetch/store market data, evaluate the
rulebook, and emit alerts, but it never places orders and never touches
exchange trading permissions.
"""

from __future__ import annotations

import datetime as dt
import json as json_module

import pandas as pd
import typer

from tidemark import __version__
from tidemark.config.settings import Settings, get_settings
from tidemark.context import htf
from tidemark.core.atr import atr as compute_atr
from tidemark.data.exchange import ExchangeClient
from tidemark.data.ingest import RunOutcome, run_backfill, run_update
from tidemark.data.models import Candle, ContextRecord
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.data.timeframes import TIMEFRAMES
from tidemark.health.checks import EXIT_CODES, HealthReport, run_all_checks
from tidemark.journal.observe_pipeline import ObservePipelineRunOutcome, run_observe_pipeline
from tidemark.journal.pipeline import PipelineRunOutcome, run_pipeline
from tidemark.notify.telegram import TelegramNotifier, build_heartbeat_message

app = typer.Typer(
    name="tidemark",
    help=(
        "Tidemark: a rule-based market-structure copilot for crypto markets. "
        "Read-only. Never places orders."
    ),
    no_args_is_help=True,
)

data_app = typer.Typer(
    name="data",
    help="Market-data ingestion: backfill, update, gaps, status.",
    no_args_is_help=True,
)
app.add_typer(data_app, name="data")

context_app = typer.Typer(
    name="context",
    help="Section 1 HTF context: evaluate, history, explain.",
    no_args_is_help=True,
)
app.add_typer(context_app, name="context")

journal_app = typer.Typer(
    name="journal",
    help="Append-only research record: list, alerts.",
    no_args_is_help=True,
)
app.add_typer(journal_app, name="journal")

observe_app = typer.Typer(
    name="observe",
    help=(
        "Section 2 (1H) observation-only layer: run, list, stats. "
        "Measurement, not signal - see docs/rulebook/section-02-1h-behaviour-v0.1.md."
    ),
    no_args_is_help=True,
)
app.add_typer(observe_app, name="observe")

notify_app = typer.Typer(
    name="notify",
    help="Telegram connectivity: test.",
    no_args_is_help=True,
)
app.add_typer(notify_app, name="notify")

health_app = typer.Typer(
    name="health",
    help="Prove the system actually ran: check, heartbeat.",
    no_args_is_help=True,
)
app.add_typer(health_app, name="health")

STALE_RUNNING_THRESHOLD = dt.timedelta(hours=2)


def _notifier(settings: Settings) -> TelegramNotifier:
    return TelegramNotifier(
        bot_token=settings.telegram_bot_token, chat_id=settings.telegram_chat_id
    )


@app.command()
def run(
    symbols: list[str] = typer.Option(  # noqa: B008
        None,
        "--symbols",
        help="Symbols: repeat the flag or comma-separate; defaults to TIDEMARK_SYMBOLS.",
    ),
) -> None:
    """Evaluate Section 1, journal every result, and alert on change.

    For each symbol: evaluate against stored candles, write the journal
    row (a no-op if this 4H candle was already journaled), run the
    change detector, and send a Telegram alert if it returns a reason.
    One symbol failing gives a PARTIAL run, not FAILED.
    """
    settings = get_settings()
    store = _store(settings)
    notifier = _notifier(settings)
    symbol_list = _parse_csv(symbols) or settings.symbol_list()

    outcome = run_pipeline(store, notifier, settings.venue, symbol_list)
    _print_pipeline_outcome(outcome)


def _print_pipeline_outcome(outcome: PipelineRunOutcome) -> None:
    typer.echo(f"run {outcome.run_id}: {outcome.status}")
    for o in outcome.outcomes:
        if o.error is not None:
            typer.echo(f"  {o.symbol:<16} FAILED: {o.error}")
        elif not o.journaled:
            typer.echo(f"  {o.symbol:<16} repeat (already journaled, no alert)")
        elif o.alert_reason is None:
            typer.echo(f"  {o.symbol:<16} journaled, no alert")
        else:
            sent = "sent" if o.alert_sent else "FAILED TO SEND"
            typer.echo(f"  {o.symbol:<16} journaled, alert={o.alert_reason} ({sent})")


@app.command()
def version() -> None:
    """Print the installed Tidemark version."""
    typer.echo(__version__)


def _parse_csv(values: list[str] | None) -> list[str] | None:
    """Flatten repeated `--flag` occurrences and comma-separated values.

    Accepts both `--symbols A --symbols B` and `--symbols A,B` (and a mix
    of the two), so `--symbols`/`--timeframes` work whether the shell
    delivers one token per occurrence or one token with embedded commas.

    This matters on Windows: PowerShell parses an unquoted comma list as
    an array-literal expression before the process ever starts, and a
    token like `1d` matches its decimal-literal-with-suffix grammar
    (`d` = System.Decimal), so it gets evaluated to the number 1 and
    re-stringified as "1" — silently dropping the "d". Quoting
    (`--timeframes "4h,1d,1w"`) avoids that, and so does the repeated-flag
    form, since no single token then contains a comma for PowerShell's
    parser to act on.
    """
    if not values:
        return None
    items = [item.strip() for value in values for item in value.split(",") if item.strip()]
    return items or None


def _store(settings: Settings) -> TidemarkStore:
    engine = create_store_engine(settings.database_url)
    init_db(engine)
    return TidemarkStore(engine)


def _print_run_outcome(outcome: RunOutcome) -> None:
    typer.echo(f"run {outcome.run_id}: {outcome.status}")
    for o in outcome.outcomes:
        if o.error is not None:
            typer.echo(f"  {o.symbol:<16} {o.timeframe:<5} FAILED: {o.error}")
        else:
            r = o.result
            typer.echo(
                f"  {o.symbol:<16} {o.timeframe:<5} "
                f"fetched={r.fetched} inserted={r.inserted} "
                f"duplicates={r.duplicates_skipped} rejected={r.rejected}"
            )


@data_app.command("backfill")
def data_backfill(
    symbols: list[str] = typer.Option(  # noqa: B008
        None,
        "--symbols",
        help=(
            "Symbols: repeat the flag or comma-separate; defaults to TIDEMARK_SYMBOLS. "
            'In PowerShell, quote a comma-separated value (e.g. --symbols "A,B") or '
            "repeat --symbols instead of leaving it unquoted."
        ),
    ),
    timeframes: list[str] = typer.Option(  # noqa: B008
        None,
        "--timeframes",
        help=(
            "Timeframes: repeat the flag or comma-separate; defaults to all stored "
            "timeframes. In PowerShell, quote a comma-separated value (e.g. "
            '--timeframes "4h,1d,1w") or repeat --timeframes instead of leaving it '
            "unquoted — unquoted, PowerShell parses 1d as a number and drops the d."
        ),
    ),
    days: int = typer.Option(
        None, "--days", help="Backfill depth in days; defaults to TIDEMARK_BACKFILL_DAYS."
    ),
) -> None:
    """Backfill closed candles from the exchange into the store."""
    settings = get_settings()
    store = _store(settings)
    exchange = ExchangeClient(venue=settings.venue)

    symbol_list = _parse_csv(symbols) or settings.symbol_list()
    timeframe_list = _parse_csv(timeframes) or list(TIMEFRAMES)
    depth = days if days is not None else settings.backfill_days

    outcome = run_backfill(store, exchange, settings.venue, symbol_list, timeframe_list, depth)
    _print_run_outcome(outcome)


@data_app.command("update")
def data_update(
    symbols: list[str] = typer.Option(  # noqa: B008
        None,
        "--symbols",
        help="Symbols: repeat the flag or comma-separate; defaults to TIDEMARK_SYMBOLS.",
    ),
    timeframes: list[str] = typer.Option(  # noqa: B008
        None,
        "--timeframes",
        help="Timeframes: repeat the flag or comma-separate; defaults to all stored timeframes.",
    ),
) -> None:
    """Fetch closed candles from the last stored candle up to now."""
    settings = get_settings()
    store = _store(settings)
    exchange = ExchangeClient(venue=settings.venue)

    symbol_list = _parse_csv(symbols) or settings.symbol_list()
    timeframe_list = _parse_csv(timeframes) or list(TIMEFRAMES)

    outcome = run_update(
        store,
        exchange,
        settings.venue,
        symbol_list,
        timeframe_list,
        fallback_days=settings.backfill_days,
    )
    _print_run_outcome(outcome)


@data_app.command("gaps")
def data_gaps(
    symbols: list[str] = typer.Option(  # noqa: B008
        None,
        "--symbols",
        help="Symbols: repeat the flag or comma-separate; defaults to TIDEMARK_SYMBOLS.",
    ),
    timeframes: list[str] = typer.Option(  # noqa: B008
        None,
        "--timeframes",
        help="Timeframes: repeat the flag or comma-separate; defaults to all stored timeframes.",
    ),
) -> None:
    """Report missing candles per symbol/timeframe. Never fabricates data."""
    settings = get_settings()
    store = _store(settings)

    symbol_list = _parse_csv(symbols) or settings.symbol_list()
    timeframe_list = _parse_csv(timeframes) or list(TIMEFRAMES)

    header = f"{'SYMBOL':<16} {'TIMEFRAME':<9} {'GAPS':<6} FIRST_MISSING"
    typer.echo(header)
    for symbol in symbol_list:
        for timeframe in timeframe_list:
            row_count = store.count_candles(settings.venue, symbol, timeframe)
            if row_count < 2:
                typer.echo(f"{symbol:<16} {timeframe:<9} {'-':<6} no data")
                continue
            gaps = store.find_gaps(settings.venue, symbol, timeframe)
            first_missing = gaps[0].isoformat() if gaps else "-"
            typer.echo(f"{symbol:<16} {timeframe:<9} {len(gaps):<6} {first_missing}")


@data_app.command("status")
def data_status(
    symbols: list[str] = typer.Option(  # noqa: B008
        None,
        "--symbols",
        help="Symbols: repeat the flag or comma-separate; defaults to TIDEMARK_SYMBOLS.",
    ),
    timeframes: list[str] = typer.Option(  # noqa: B008
        None,
        "--timeframes",
        help="Timeframes: repeat the flag or comma-separate; defaults to all stored timeframes.",
    ),
) -> None:
    """Show latest candle time, row counts, and the last run's status."""
    settings = get_settings()
    store = _store(settings)

    now = dt.datetime.now(dt.UTC)
    running = store.running_runs()
    if running:
        typer.echo("running:")
        for r in running:
            stale = (
                " — STALE — likely crashed" if now - r.started_at > STALE_RUNNING_THRESHOLD else ""
            )
            typer.echo(
                f"  {r.run_id} ({r.command}) RUNNING since={r.started_at.isoformat()}{stale}"
            )
        typer.echo("")

    last_run = store.latest_run()
    if last_run is None:
        typer.echo("last run: none yet")
    else:
        finished = last_run.finished_at.isoformat() if last_run.finished_at is not None else "-"
        typer.echo(
            f"last run: {last_run.run_id} ({last_run.command}) {last_run.status} "
            f"started={last_run.started_at.isoformat()} "
            f"finished={finished}"
        )

    symbol_list = _parse_csv(symbols) or settings.symbol_list()
    timeframe_list = _parse_csv(timeframes) or list(TIMEFRAMES)

    typer.echo("")
    header = f"{'SYMBOL':<16} {'TIMEFRAME':<9} {'ROWS':<6} LATEST_CLOSE"
    typer.echo(header)
    for symbol in symbol_list:
        for timeframe in timeframe_list:
            row_count = store.count_candles(settings.venue, symbol, timeframe)
            latest = store.latest_candle(settings.venue, symbol, timeframe)
            latest_close = latest.close_time.isoformat() if latest else "no data"
            typer.echo(f"{symbol:<16} {timeframe:<9} {row_count:<6} {latest_close}")


def _candles_to_frame(candles: list[Candle]) -> pd.DataFrame:
    """Adapt stored `Candle` rows into the DataFrame shape core/context expect."""
    return pd.DataFrame(
        {
            "open_time": [c.open_time for c in candles],
            "close_time": [c.close_time for c in candles],
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [c.volume for c in candles],
        }
    )


def _parse_as_of(value: str | None) -> dt.datetime | None:
    if value is None:
        return None
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


def _load_context_candles(
    store: TidemarkStore, venue: str, symbol: str, as_of: dt.datetime | None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fetch closed 4H/1D/1W candles for `symbol`, truncated to `as_of`.

    Fetches the full stored history per timeframe and filters on
    `close_time <= as_of` in Python — this is what lets `--as-of`
    reproduce exactly what was known at that moment, per the rulebook's
    look-ahead guard.
    """
    frames = []
    for timeframe in ("4h", "1d", "1w"):
        candles = store.get_candles(venue, symbol, timeframe)
        if as_of is not None:
            candles = [c for c in candles if c.close_time <= as_of]
        frames.append(_candles_to_frame(candles))
    return tuple(frames)  # type: ignore[return-value]


def _evaluate_symbol(
    store: TidemarkStore, venue: str, symbol: str, as_of: dt.datetime | None
) -> ContextRecord:
    candles_4h, candles_1d, candles_1w = _load_context_candles(store, venue, symbol, as_of)
    if len(candles_4h) == 0:
        typer.echo(f"No 4H candles for {symbol}. Run `tidemark data backfill` first.")
        raise typer.Exit(code=1)

    atr_series = compute_atr(candles_4h)
    atr_value = atr_series.iloc[-1]

    return htf.evaluate(
        symbol,
        candles_4h,
        atr_value,
        candles_1d=candles_1d if len(candles_1d) > 0 else None,
        candles_1w=candles_1w if len(candles_1w) > 0 else None,
    )


@context_app.command("evaluate")
def context_evaluate(
    symbol: str = typer.Option(..., "--symbol"),
    as_of: str | None = typer.Option(
        None, "--as-of", help="ISO timestamp; reproduces output as of that moment"
    ),
) -> None:
    """Evaluate Section 1's decision matrix and persist the result."""
    settings = get_settings()
    store = _store(settings)
    record = _evaluate_symbol(store, settings.venue, symbol, _parse_as_of(as_of))
    store.save_context_record(record)

    grade = record.grade or "-"
    typer.echo(
        f"{symbol} @ {record.evaluated_at.isoformat()}  "
        f"state={record.state}  watch={record.watch}  grade={grade}  "
        f"reason={record.reason_code}"
    )
    if record.watch == htf.WAIT:
        typer.echo("No setups found.")


@context_app.command("history")
def context_history(
    symbol: str = typer.Option(..., "--symbol"),
    days: int = typer.Option(30, "--days"),
) -> None:
    """List past Section 1 evaluations for a symbol, newest first."""
    settings = get_settings()
    store = _store(settings)
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    records = store.context_history(symbol, since=since)

    if not records:
        typer.echo("No setups found.")
        return

    for record in records:
        grade = record.grade or "-"
        typer.echo(
            f"{record.evaluated_at.isoformat()}  {record.state:22}  "
            f"{record.watch:11}  grade={grade}  {record.reason_code}"
        )


@context_app.command("explain")
def context_explain(symbol: str = typer.Option(..., "--symbol")) -> None:
    """Print a human-readable explanation of the latest Section 1 evaluation."""
    settings = get_settings()
    store = _store(settings)
    record = store.latest_context_record(symbol)
    if record is None:
        typer.echo(
            f"No context record for {symbol} yet. "
            f"Run `tidemark context evaluate --symbol {symbol}` first."
        )
        raise typer.Exit(code=1)

    grade = record.grade or "-"
    typer.echo(f"{symbol} - Section 1 HTF Context ({record.rule_version})")
    typer.echo(f"Evaluated at: {record.evaluated_at.isoformat()}")
    typer.echo("")
    typer.echo(f"State:  {record.state}")
    typer.echo(f"Watch:  {record.watch}  (grade {grade})")
    typer.echo(f"Reason: {record.reason_code}")

    typer.echo("")
    typer.echo("Swings used:")
    if not record.swings_used:
        typer.echo("  (none)")
    for swing in record.swings_used:
        typer.echo(
            f"  {swing['kind']:5} {swing['price']:.2f}  "
            f"formed {swing['formed_at']}  confirmed {swing['confirmed_at']}"
        )

    typer.echo("")
    typer.echo("Active levels:")
    if not record.active_levels:
        typer.echo("  (none)")
    for level in record.active_levels:
        major = "major" if level["is_major"] else "minor"
        typer.echo(
            f"  {level['role']:10} {level['price']:.2f}  "
            f"zone[{level['zone_low']:.2f},{level['zone_high']:.2f}]  "
            f"touches={level['touches']}  {major}  source={level['source']}"
        )

    typer.echo("")
    if record.fib:
        fib = record.fib
        typer.echo(
            f"Fib ({fib['direction']} leg {fib['anchor_start']:.2f} -> "
            f"{fib['anchor_end']:.2f}, valid_from {fib['valid_from']}):"
        )
        typer.echo(
            f"  0.500={fib['level_500']:.2f}  0.618={fib['level_618']:.2f}  "
            f"0.786={fib['level_786']:.2f}  "
            f"zone[{fib['zone_low']:.2f},{fib['zone_high']:.2f}]  "
            f"nearest={fib['nearest_level']}"
        )
        typer.echo(f"  invalidated_at: {fib['invalidated_at'] or 'none'}")
    else:
        typer.echo("Fib: (no valid leg)")


@journal_app.command("list")
def journal_list(
    symbol: str = typer.Option(..., "--symbol"),
    days: int = typer.Option(30, "--days"),
) -> None:
    """List journal rows for a symbol, newest first."""
    settings = get_settings()
    store = _store(settings)
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    entries = store.journal_history(symbol, since=since)

    if not entries:
        typer.echo("No journal rows found.")
        return

    for entry in entries:
        grade = entry.grade or "-"
        alert_reason = entry.alert_reason or "-"
        typer.echo(
            f"{entry.evaluated_at.isoformat()}  {entry.state:22}  {entry.watch:11}  "
            f"grade={grade}  {entry.reason_code}  "
            f"alert_sent={entry.alert_sent}  alert_reason={alert_reason}"
        )


@journal_app.command("alerts")
def journal_alerts(days: int = typer.Option(30, "--days")) -> None:
    """List journal rows where an alert was sent, across all symbols, newest first."""
    settings = get_settings()
    store = _store(settings)
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    entries = store.journal_alerts(since=since)

    if not entries:
        typer.echo("No alerts sent.")
        return

    for entry in entries:
        grade = entry.grade or "-"
        typer.echo(
            f"{entry.evaluated_at.isoformat()}  {entry.asset:<16}  {entry.state:22}  "
            f"{entry.watch:11}  grade={grade}  alert_reason={entry.alert_reason}"
        )


@observe_app.command("run")
def observe_run(
    symbols: list[str] = typer.Option(  # noqa: B008
        None,
        "--symbols",
        help="Symbols: repeat the flag or comma-separate; defaults to TIDEMARK_SYMBOLS.",
    ),
) -> None:
    """Evaluate Section 2 (1H) and journal every observation row.

    Measurement only: no entries, stops, targets, R:R, 15M handoff, or
    Telegram alert is ever produced here.
    """
    settings = get_settings()
    store = _store(settings)
    symbol_list = _parse_csv(symbols) or settings.symbol_list()

    outcome = run_observe_pipeline(store, settings.venue, symbol_list)
    _print_observe_outcome(outcome)


def _print_observe_outcome(outcome: ObservePipelineRunOutcome) -> None:
    typer.echo(f"observe {outcome.run_id}: {outcome.status}")
    for o in outcome.outcomes:
        if o.error is not None:
            typer.echo(f"  {o.symbol:<16} FAILED: {o.error}")
        else:
            typer.echo(f"  {o.symbol:<16} evaluated={o.evaluated} inserted={o.inserted}")


@observe_app.command("list")
def observe_list(
    symbol: str = typer.Option(..., "--symbol"),
    days: int = typer.Option(30, "--days"),
) -> None:
    """List Section 2 observation rows for a symbol, newest first."""
    settings = get_settings()
    store = _store(settings)
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    observations = store.observation_history(symbol, since=since)

    if not observations:
        typer.echo("No observation rows found.")
        return

    for obs in observations:
        tier = obs.reaction_tier or "-"
        typer.echo(
            f"{obs.evaluated_at.isoformat()}  {obs.state:32}  "
            f"watch={obs.section_1_watch:11}  tier={tier:<2}  reason={obs.reason_code}"
        )


def _count_by(observations: list, key) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for obs in observations:
        k = key(obs)
        counts[k] = counts.get(k, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


@observe_app.command("stats")
def observe_stats(days: int = typer.Option(30, "--days")) -> None:
    """Section 2 counts by state, reason_code, and reaction tier - the review tool."""
    settings = get_settings()
    store = _store(settings)
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    observations = store.all_observations(since=since)

    if not observations:
        typer.echo("No observation rows found.")
        return

    typer.echo(f"Total observation rows: {len(observations)}")

    typer.echo("")
    typer.echo("By state:")
    for state, count in _count_by(observations, lambda o: o.state):
        typer.echo(f"  {state:<36} {count}")

    typer.echo("")
    typer.echo("By reason_code:")
    for reason, count in _count_by(observations, lambda o: o.reason_code):
        typer.echo(f"  {reason:<36} {count}")

    typer.echo("")
    typer.echo("By reaction tier:")
    for tier, count in _count_by(observations, lambda o: o.reaction_tier or "(none)"):
        typer.echo(f"  {tier:<10} {count}")


_TEST_MESSAGE = (
    "✅ Tidemark connectivity test\n\n"
    "This is a fixed test message confirming your Telegram credentials work.\n"
    "No evaluation ran; nothing was written to the journal."
)


@notify_app.command("test")
def notify_test() -> None:
    """Send one fixed test message. Proves credentials work; writes nothing to the journal."""
    settings = get_settings()
    notifier = _notifier(settings)
    sent = notifier.send_text(_TEST_MESSAGE)
    if sent:
        typer.echo("Test message sent.")
        return

    error = notifier.last_error
    if error is None:
        # send_text never attempted a request at all: missing credentials.
        typer.echo(
            "Test message NOT sent - check TIDEMARK_TELEGRAM_BOT_TOKEN / TIDEMARK_TELEGRAM_CHAT_ID."
        )
    elif error.kind == "connection":
        # The request never reached Telegram, so this says nothing about
        # whether the credentials are valid.
        typer.echo(
            f"Test message NOT sent - the Telegram API was unreachable ({error.detail}). "
            "Credentials were not verified: the request never reached Telegram. This is "
            "usually network filtering or a firewall blocking the connection, not a bad "
            "bot token or chat ID."
        )
    else:
        typer.echo(
            f"Test message NOT sent - Telegram responded with HTTP {error.status_code}: "
            f"{error.detail}"
        )
        if error.status_code == 401:
            typer.echo("HTTP 401 usually means TIDEMARK_TELEGRAM_BOT_TOKEN is invalid.")
        elif error.status_code == 400 and "chat not found" in error.detail.lower():
            typer.echo("This usually means TIDEMARK_TELEGRAM_CHAT_ID is invalid.")
    raise typer.Exit(code=1)


def _build_health_report(settings: Settings) -> HealthReport:
    # Deliberately does NOT call init_db(): health check must see the
    # database exactly as it is, not a freshly-patched version of it -
    # calling init_db first would silently create any missing tables and
    # make the DATABASE check unable to ever catch a broken/uninitialized
    # database.
    engine = create_store_engine(settings.database_url)
    store = TidemarkStore(engine)
    notifier = _notifier(settings)
    now = dt.datetime.now(dt.UTC)
    return run_all_checks(
        store=store,
        engine=engine,
        venue=settings.venue,
        symbols=settings.symbol_list(),
        is_telegram_configured=notifier.is_configured,
        rule_version=htf.RULE_VERSION,
        now=now,
    )


def _report_to_dict(report: HealthReport) -> dict:
    return {
        "status": report.status,
        "generated_at": report.generated_at.isoformat(),
        "rule_version": report.rule_version,
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in report.checks],
        "last_candle_symbol": report.last_candle_symbol,
        "last_candle_timeframe": report.last_candle_timeframe,
        "last_candle_close_time": (
            report.last_candle_close_time.isoformat()
            if report.last_candle_close_time is not None
            else None
        ),
        "last_evaluation_recorded_at": (
            report.last_evaluation_recorded_at.isoformat()
            if report.last_evaluation_recorded_at is not None
            else None
        ),
        "last_evaluation_state": report.last_evaluation_state,
        "last_evaluation_watch": report.last_evaluation_watch,
        "last_run_status": report.last_run_status,
        "total_gaps": report.total_gaps,
        "journal_count": report.journal_count,
    }


@health_app.command("check")
def health_check(
    json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Run every health check and report the result.

    Exit codes: 0 for OK, 1 for WARN, 2 for FAIL - a Telegram failure
    never affects this, since this command never sends anything.
    """
    settings = get_settings()
    report = _build_health_report(settings)

    if json:
        typer.echo(json_module.dumps(_report_to_dict(report), indent=2))
    else:
        typer.echo(f"Overall: {report.status}")
        typer.echo("")
        for check in report.checks:
            typer.echo(f"{check.name:<28} {check.status:<5} {check.detail}")

    raise typer.Exit(code=EXIT_CODES[report.status])


@health_app.command("heartbeat")
def health_heartbeat() -> None:
    """Run every health check and send one Telegram summary.

    The only command in `health` that sends anything. Never writes to
    the journal - a heartbeat is a system-status message, not a research
    observation.
    """
    settings = get_settings()
    report = _build_health_report(settings)
    notifier = _notifier(settings)

    message = build_heartbeat_message(report)
    sent = notifier.send_text(message)

    typer.echo(message)
    if not sent:
        typer.echo("")
        typer.echo("(Telegram send failed - see above for details.)")
        raise typer.Exit(code=2)

    raise typer.Exit(code=EXIT_CODES[report.status])


def main() -> None:
    app()


if __name__ == "__main__":
    main()
