"""Tidemark CLI entrypoint.

Read-only copilot: the CLI can fetch/store market data, evaluate the
rulebook, and emit alerts, but it never places orders and never touches
exchange trading permissions.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json as json_module
import sys

import typer

from tidemark import __version__
from tidemark.config.settings import Settings, get_settings
from tidemark.context import htf, mtf
from tidemark.data.asset_class import (
    NON_CRYPTO_UNDERLYING,
    NON_ELIGIBLE_INDEX,
    UNKNOWN_UNDERLYING_TYPE,
)
from tidemark.data.discover import DiscoveryOutcome, run_discovery
from tidemark.data.evidence import build_evidence_report
from tidemark.data.evidence_render import evidence_report_to_dict, render_evidence_report
from tidemark.data.exchange import ExchangeClient
from tidemark.data.ingest import RunOutcome, SymbolTimeframeOutcome, run_backfill, run_update
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.data.symbol_source import (
    EXPLICIT,
    SNAPSHOT,
    TIDEMARK_SYMBOLS_FALLBACK,
    ResolvedSymbols,
    resolve_symbols,
)
from tidemark.data.timeframes import TIMEFRAMES
from tidemark.data.universe_backfill import (
    DEFAULT_BACKFILL_DAYS,
    UniverseBackfillOutcome,
    active_symbols,
    run_universe_backfill,
)
from tidemark.data.universe_eligibility import INSUFFICIENT_VOLUME_HISTORY, NOT_ASSESSED
from tidemark.data.universe_snapshot import generate_universe_snapshot
from tidemark.data.universe_sync import (
    DEFAULT_SYNC_DAYS,
    SYNC_TIMEFRAMES,
    run_universe_sync,
    selected_symbols,
)
from tidemark.health.checks import EXIT_CODES, HealthReport, run_all_checks
from tidemark.journal.observe_pipeline import ObservePipelineRunOutcome, run_observe_pipeline
from tidemark.journal.pipeline import PipelineRunOutcome, run_pipeline
from tidemark.market_intel.bot import run_forever, run_once
from tidemark.market_intel.bot_state import BotStateStore
from tidemark.market_intel.briefing import BriefingResult, evaluate_briefing, mark_sent
from tidemark.market_intel.client import CoinalyzeClient
from tidemark.market_intel.distributions import (
    DistributionsMeasurement,
    MetricSummary,
    SymbolMetrics,
    measure_distributions,
)
from tidemark.market_intel.errors import (
    CoinalyzeConnectionError,
    CoinalyzeHttpError,
    MissingApiKeyError,
    MissingBotTokenError,
    RateLimitedError,
    TelegramConnectionError,
    TelegramHttpError,
    UnsupportedVenueError,
)
from tidemark.market_intel.evaluation_store import init_evaluation_store
from tidemark.market_intel.evaluation_store import make_engine as make_evaluation_engine
from tidemark.market_intel.future_markets import FutureMarketsCache
from tidemark.market_intel.models import OK, MarketIntelSnapshot
from tidemark.market_intel.service import fetch_market_intel
from tidemark.market_intel.telegram_client import TelegramBotClient
from tidemark.market_intel.universe_context_store import (
    MetricSummaryFields,
    UniverseContextRecord,
    init_universe_context_store,
    record_universe_context,
)
from tidemark.market_intel.universe_context_store import make_engine as make_universe_context_engine
from tidemark.market_intel.universe_read import read_latest_selected_symbols
from tidemark.notify.telegram import TelegramNotifier, build_heartbeat_message
from tidemark.replay.render import render_report
from tidemark.replay.report import build_replay_report

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
    help=(
        "Section 1 HTF context: history, explain. Read-only inspection of "
        "journal_entries, the table `tidemark run` actually writes Section 1 "
        "results to - there is no standalone evaluate/persist command; use "
        "`tidemark run` (production) or `tidemark replay` (point-in-time, "
        "no side effects)."
    ),
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

universe_app = typer.Typer(
    name="universe",
    help=(
        "Universe selection (Phase 6): discover/backfill the venue's symbol "
        "catalog (Merge 2A) plus read-only registry/snapshot inspection. No "
        "selection, eligibility, or metric computation runs here yet - see "
        "docs/adr/0009-universe-selection-architecture.md."
    ),
    no_args_is_help=True,
)
app.add_typer(universe_app, name="universe")

intel_app = typer.Typer(
    name="intel",
    help=(
        "Live market intelligence (Coinalyze derivatives data), strictly "
        "separate from the research engine above. Raw data only - no "
        "interpretation, no bias, no trading recommendation. See "
        "docs/adr/0011-market-intelligence-layer.md."
    ),
    no_args_is_help=True,
)
app.add_typer(intel_app, name="intel")

STALE_RUNNING_THRESHOLD = dt.timedelta(hours=2)


def _notifier(settings: Settings) -> TelegramNotifier:
    return TelegramNotifier(
        bot_token=settings.telegram_bot_token, chat_id=settings.telegram_chat_id
    )


def _resolve_and_report(
    store: TidemarkStore,
    settings: Settings,
    symbols: list[str] | None,
    now: dt.datetime,
) -> ResolvedSymbols:
    """Resolve symbols (Phase 6, Merge 3: explicit --symbols, else the
    latest valid universe snapshot's selection, else TIDEMARK_SYMBOLS)
    and print which source was used, so `tidemark run`/`tidemark observe
    run` always say plainly where their symbol list came from.
    """
    resolved = resolve_symbols(
        store,
        settings.venue,
        _parse_csv(symbols),
        settings.symbol_list(),
        now,
        staleness_threshold=dt.timedelta(hours=settings.universe_staleness_hours),
    )
    if resolved.source == SNAPSHOT:
        typer.echo(
            f"symbol source: SNAPSHOT ({resolved.snapshot_id}), {len(resolved.symbols)} symbol(s)"
        )
    elif resolved.source == EXPLICIT:
        typer.echo(f"symbol source: EXPLICIT, {len(resolved.symbols)} symbol(s)")
    else:
        typer.echo(f"symbol source: {TIDEMARK_SYMBOLS_FALLBACK}, {len(resolved.symbols)} symbol(s)")
        if resolved.warning:
            typer.echo(f"  WARNING: {resolved.warning}")
    return resolved


@app.command()
def run(
    symbols: list[str] = typer.Option(  # noqa: B008
        None,
        "--symbols",
        help="Symbols: repeat the flag or comma-separate; overrides the universe snapshot.",
    ),
) -> None:
    """Evaluate Section 1, journal every result, and alert on change.

    Symbol source (Phase 6, Merge 3): an explicit --symbols always wins;
    otherwise the SELECTED symbols of the latest universe snapshot for
    the venue, if one exists and isn't stale; otherwise TIDEMARK_SYMBOLS
    as a last-resort fallback. For each symbol: evaluate against stored
    candles, write the journal row (a no-op if this 4H candle was already
    journaled), run the change detector, and send a Telegram alert if it
    returns a reason. One symbol failing gives a PARTIAL run, not FAILED.
    """
    settings = get_settings()
    store = _store(settings)
    notifier = _notifier(settings)
    now = dt.datetime.now(dt.UTC)
    resolved = _resolve_and_report(store, settings, symbols, now)

    outcome = run_pipeline(
        store,
        notifier,
        settings.venue,
        resolved.symbols,
        now=now,
        symbol_source=resolved.source,
        symbol_source_snapshot_id=resolved.snapshot_id,
    )
    _print_pipeline_outcome(outcome)


def _print_pipeline_outcome(outcome: PipelineRunOutcome) -> None:
    typer.echo(f"run {outcome.run_id}: {outcome.status}")
    for o in outcome.outcomes:
        if o.error is not None:
            _safe_echo(f"  {o.symbol:<16} FAILED: {o.error}")
        elif not o.journaled:
            _safe_echo(f"  {o.symbol:<16} repeat (already journaled, no alert)")
        elif o.alert_reason is None:
            _safe_echo(f"  {o.symbol:<16} journaled, no alert")
        else:
            sent = "sent" if o.alert_sent else "FAILED TO SEND"
            _safe_echo(f"  {o.symbol:<16} journaled, alert={o.alert_reason} ({sent})")


@app.command()
def version() -> None:
    """Print the installed Tidemark version."""
    typer.echo(__version__)


@app.command()
def replay(
    rule_version: str = typer.Option(
        ...,
        "--rule-version",
        help=(
            "The Section 2 rule version to replay against: "
            f"{', '.join(repr(v) for v in mtf.SUPPORTED_RULE_VERSIONS)}."
        ),
    ),
    symbols: list[str] = typer.Option(  # noqa: B008
        None,
        "--symbols",
        help="Symbols: repeat the flag or comma-separate; defaults to TIDEMARK_SYMBOLS.",
    ),
    days: int = typer.Option(
        None,
        "--days",
        help="Limit the replay to the last N days of stored candles; defaults to all of it.",
    ),
) -> None:
    """Read-only, point-in-time replay of Section 1 and Section 2.

    Writes nothing to the context, journal, or observation tables - report
    only. See docs/adr/0008-replay-as-a-repo-command.md and
    docs/replay/section-02-v0.1-baseline.md.
    """
    if rule_version not in mtf.SUPPORTED_RULE_VERSIONS:
        supported = ", ".join(repr(v) for v in mtf.SUPPORTED_RULE_VERSIONS)
        typer.echo(f"Unknown --rule-version {rule_version!r}. Supported versions: {supported}.")
        raise typer.Exit(code=1)

    settings = get_settings()
    store = _store(settings)
    symbol_list = _parse_csv(symbols) or settings.symbol_list()

    command = f"tidemark replay --rule-version {rule_version}"
    if symbols:
        command += " --symbols " + ",".join(symbol_list)
    if days is not None:
        command += f" --days {days}"

    report = build_replay_report(
        store,
        settings.venue,
        symbol_list,
        rule_version,
        command,
        dt.datetime.now(dt.UTC),
        days=days,
    )
    typer.echo(render_report(report))


@app.command()
def evidence(
    symbol: list[str] = typer.Option(  # noqa: B008
        None,
        "--symbol",
        help="Symbols: repeat the flag or comma-separate; defaults to every observed symbol.",
    ),
    from_: str | None = typer.Option(
        None, "--from", help="ISO date/timestamp (inclusive); defaults to the start of the archive."
    ),
    to: str | None = typer.Option(
        None, "--to", help="ISO date/timestamp (inclusive); defaults to the end of the archive."
    ),
    json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    allow_insufficient: bool = typer.Option(
        False,
        "--allow-insufficient",
        help=(
            "Print the full report even when the archive spans fewer than 14 days, "
            "marked PRELIMINARY and not valid for Section 2 conclusions."
        ),
    ),
) -> None:
    """Query the persisted archive for evidence toward Section 2's open
    questions (SEC2-01 through SEC2-04) — never a new simulation, never a
    conclusion. See docs/adr/0010-evidence-command.md.

    Refuses full output by default when the archive spans fewer than 14
    days: only the coverage summary and sufficiency verdicts print, so a
    single short regime is never mistaken for general evidence. Metric
    definitions are frozen (documented here and in the ADR) so a run
    today means the same thing as a run three months from now.
    """
    settings = get_settings()
    store = _store(settings)
    symbols = _parse_csv(symbol)
    since = _parse_evidence_bound(from_, end_of_day=False)
    until = _parse_evidence_bound(to, end_of_day=True)

    command = "tidemark evidence"
    if symbols:
        command += " --symbol " + ",".join(symbols)
    if from_:
        command += f" --from {from_}"
    if to:
        command += f" --to {to}"
    if allow_insufficient:
        command += " --allow-insufficient"

    report = build_evidence_report(
        store, command, dt.datetime.now(dt.UTC), symbols=symbols, since=since, until=until
    )
    full = allow_insufficient or not report.is_young_archive

    if json:
        typer.echo(json_module.dumps(evidence_report_to_dict(report, full=full), indent=2))
    else:
        _safe_echo(render_evidence_report(report, full=full))

    if report.is_young_archive and not allow_insufficient:
        raise typer.Exit(code=1)


def _parse_evidence_bound(value: str | None, *, end_of_day: bool) -> dt.datetime | None:
    """Parse a `--from`/`--to` bound. A bare date (no time component) is
    widened to that whole UTC day - `--from` to its start, `--to` to its
    end - so `--from 2026-09-01 --to 2026-09-01` covers the full day
    rather than matching nothing.
    """
    if value is None:
        return None
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    is_bare_date = "T" not in value and " " not in value.strip()
    if is_bare_date and end_of_day:
        parsed = parsed + dt.timedelta(days=1) - dt.timedelta(microseconds=1)
    return parsed


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


def _encode_for_display(text: str, encoding: str) -> str:
    """Replace any character `encoding` can't represent with a
    substitute, rather than letting a later write crash on it.
    """
    return text.encode(encoding, errors="replace").decode(encoding)


def _safe_echo(text: str) -> None:
    """Print `text`, substituting any character the terminal's stdout
    encoding can't represent, instead of crashing.

    `TIDEMARK_SYMBOLS`-derived output is always operator-chosen ASCII, so
    every command before Phase 6 could assume plain `typer.echo` was
    safe. `universe discover`/`backfill`/`registry`/`show` (Merge 2A)
    print ticker symbols straight from the live venue listing instead,
    and a real venue can and does list non-Latin-1 tickers (e.g. several
    CJK-named meme-coin perpetuals on binanceusdm today) - those crash a
    plain `echo` under a legacy Windows console codepage (cp1252) that
    can't encode them. This never touches what's stored; only display.
    """
    encoding = sys.stdout.encoding or "utf-8"
    typer.echo(_encode_for_display(text, encoding))


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


def _parse_as_of(value: str | None) -> dt.datetime | None:
    if value is None:
        return None
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


@context_app.command("history")
def context_history(
    symbol: str = typer.Option(..., "--symbol"),
    days: int = typer.Option(30, "--days"),
) -> None:
    """List past Section 1 evaluations for a symbol, newest first.

    Reads `journal_entries` - the table `tidemark run` actually writes
    Section 1 results to (see docs/adr/0011-market-intelligence-layer.md's
    "Addendum: removing context_records").
    """
    settings = get_settings()
    store = _store(settings)
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    records = store.journal_history(symbol, since=since)

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
    """Print a human-readable explanation of the latest Section 1 evaluation.

    Reads `journal_entries` - the table `tidemark run` actually writes
    Section 1 results to (see docs/adr/0011-market-intelligence-layer.md's
    "Addendum: removing context_records").
    """
    settings = get_settings()
    store = _store(settings)
    entries = store.journal_history(symbol)
    record = entries[0] if entries else None
    if record is None:
        typer.echo(f"No Section 1 result recorded for {symbol} yet. Run `tidemark run` first.")
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
        help="Symbols: repeat the flag or comma-separate; overrides the universe snapshot.",
    ),
) -> None:
    """Evaluate Section 2 (1H) and journal every observation row.

    Symbol source (Phase 6, Merge 3): same resolution as `tidemark run` -
    explicit --symbols, else the latest valid universe snapshot's
    selection, else TIDEMARK_SYMBOLS. Measurement only: no entries,
    stops, targets, R:R, 15M handoff, or Telegram alert is ever produced
    here.
    """
    settings = get_settings()
    store = _store(settings)
    now = dt.datetime.now(dt.UTC)
    resolved = _resolve_and_report(store, settings, symbols, now)

    outcome = run_observe_pipeline(
        store,
        settings.venue,
        resolved.symbols,
        now=now,
        symbol_source=resolved.source,
        symbol_source_snapshot_id=resolved.snapshot_id,
    )
    _print_observe_outcome(outcome)


def _print_observe_outcome(outcome: ObservePipelineRunOutcome) -> None:
    typer.echo(f"observe {outcome.run_id}: {outcome.status}")
    for o in outcome.outcomes:
        if o.error is not None:
            _safe_echo(f"  {o.symbol:<16} FAILED: {o.error}")
        else:
            _safe_echo(f"  {o.symbol:<16} evaluated={o.evaluated} inserted={o.inserted}")


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

    # Health checks candle freshness/gaps for whichever symbols tidemark
    # run/observe run would actually process (Phase 6, Merge 3) - never
    # an explicit override here, since there is no operator-given
    # --symbols for a scheduled health check to honor. If the database
    # itself is unreachable/uninitialized this raises before check_
    # database gets a chance to report it properly; falling back to
    # TIDEMARK_SYMBOLS here is always a safe list to check against, and
    # the real failure still surfaces via the database check itself.
    try:
        resolved_symbols = resolve_symbols(
            store,
            settings.venue,
            None,
            settings.symbol_list(),
            now,
            staleness_threshold=dt.timedelta(hours=settings.universe_staleness_hours),
        ).symbols
    except Exception:
        resolved_symbols = settings.symbol_list()

    return run_all_checks(
        store=store,
        engine=engine,
        venue=settings.venue,
        symbols=resolved_symbols,
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
        "last_run_symbol_source": report.last_run_symbol_source,
        "last_run_symbol_source_snapshot_id": report.last_run_symbol_source_snapshot_id,
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
            _safe_echo(f"{check.name:<28} {check.status:<5} {check.detail}")

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


@universe_app.command("discover")
def universe_discover(
    quote_currency: str = typer.Option(
        "USDT", "--quote-currency", help="Quote currency to filter the venue listing to."
    ),
) -> None:
    """Refresh `market_registry` from the venue's current symbol listing.

    Lists active perpetual contracts via ccxt's unified `load_markets`
    (a separate, additive call from candle fetching — see
    `data/exchange.py`'s `list_perpetual_symbols` and ADR 0002).
    Currently-listed symbols are upserted ACTIVE; previously-registered
    symbols no longer listed are marked ABSENT_FROM_VENUE, never deleted.
    """
    settings = get_settings()
    store = _store(settings)
    exchange = ExchangeClient(venue=settings.venue)

    outcome: DiscoveryOutcome = run_discovery(store, exchange, settings.venue, quote_currency)

    typer.echo(f"discover {outcome.run_id}: {outcome.status}")
    typer.echo(f"  discovered (ACTIVE): {outcome.discovered}")
    typer.echo(f"  marked ABSENT_FROM_VENUE: {outcome.marked_absent}")


@universe_app.command("backfill")
def universe_backfill(
    days: int = typer.Option(
        DEFAULT_BACKFILL_DAYS,
        "--days",
        help="Daily-candle backfill depth in days; enough for the 30-day median plus margin.",
    ),
) -> None:
    """Backfill closed 1D candles for every ACTIVE `market_registry` symbol.

    Reuses the existing `data backfill` ingest path unchanged (per-symbol
    failure isolation; one symbol failing gives PARTIAL, not FAILED) for
    exactly one timeframe (1D) — the volume metric (Merge 2B) needs
    nothing else at this stage. Prints one line per symbol as it
    completes, since a full venue listing can be several hundred symbols
    and this can take a while.
    """
    settings = get_settings()
    store = _store(settings)
    exchange = ExchangeClient(venue=settings.venue)

    symbols = active_symbols(store, settings.venue)
    if not symbols:
        typer.echo("No ACTIVE registry symbols found. Run `tidemark universe discover` first.")
        return

    typer.echo(f"Backfilling {len(symbols)} ACTIVE symbol(s), {days} days of 1D candles...")

    def _report_progress(outcome: SymbolTimeframeOutcome) -> None:
        if outcome.error is not None:
            _safe_echo(f"  {outcome.symbol:<16} FAILED: {outcome.error}")
        else:
            r = outcome.result
            _safe_echo(
                f"  {outcome.symbol:<16} fetched={r.fetched} inserted={r.inserted} "
                f"duplicates={r.duplicates_skipped} rejected={r.rejected}"
            )

    outcome: UniverseBackfillOutcome = run_universe_backfill(
        store, exchange, settings.venue, days=days, on_outcome=_report_progress
    )

    typer.echo("")
    typer.echo(
        f"backfill {outcome.run_outcome.run_id}: {outcome.run_outcome.status} "
        f"({outcome.symbols_with_coverage_updated}/{outcome.symbols_attempted} "
        "symbols got candles)"
    )


@universe_app.command("registry")
def universe_registry() -> None:
    """List `market_registry` rows for the configured venue: status,
    candle coverage, and stored row counts.

    Read-only: reports what's stored, never computes anything. Merge 2B
    is what fills `section1_first_usable_at`.
    """
    settings = get_settings()
    store = _store(settings)
    rows = store.market_registry(settings.venue)

    if not rows:
        typer.echo("No registry rows found yet.")
        return

    header = (
        f"{'SYMBOL':<16} {'STATUS':<18} {'FIRST_CANDLE':<20} "
        f"{'LAST_CANDLE':<20} {'ROWS':<8} SECTION1_USABLE"
    )
    typer.echo(header)
    for row in rows:
        first_candle = row.first_candle_seen_at.isoformat() if row.first_candle_seen_at else "-"
        last_candle = row.last_candle_seen_at.isoformat() if row.last_candle_seen_at else "-"
        usable = row.section1_first_usable_at.isoformat() if row.section1_first_usable_at else "-"
        row_count = store.count_candles(settings.venue, row.symbol, "1d")
        _safe_echo(
            f"{row.symbol:<16} {row.status:<18} {first_candle:<20} {last_candle:<20} "
            f"{row_count:<8} {usable}"
        )


@universe_app.command("snapshots")
def universe_snapshots() -> None:
    """List `universe_snapshot` headers for the configured venue, newest first."""
    settings = get_settings()
    store = _store(settings)
    snapshots = store.universe_snapshots(settings.venue)

    if not snapshots:
        typer.echo("No snapshots found yet.")
        return

    header = (
        f"{'SNAPSHOT_ID':<36} {'SNAPSHOT_AT':<20} {'METHODOLOGY':<14} {'N_SELECTED':<10} PROVENANCE"
    )
    typer.echo(header)
    for snap in snapshots:
        typer.echo(
            f"{snap.snapshot_id:<36} {snap.snapshot_at.isoformat():<20} "
            f"{snap.methodology_version:<14} {snap.n_selected:<10} {snap.provenance}"
        )


@universe_app.command("show")
def universe_show(
    snapshot_id: str = typer.Option(..., "--snapshot-id", help="The snapshot to show."),
) -> None:
    """Print one snapshot's full ranking, selected symbols first.

    Every ranked symbol is shown, not only the selected ones - an
    excluded symbol's rank and exclusion_reason stay visible.
    """
    settings = get_settings()
    store = _store(settings)
    snapshot = store.universe_snapshot_by_id(snapshot_id)
    if snapshot is None:
        typer.echo(f"No snapshot found with id {snapshot_id!r}.")
        raise typer.Exit(code=1)

    typer.echo(
        f"{snapshot.snapshot_id}  venue={snapshot.venue}  "
        f"at={snapshot.snapshot_at.isoformat()}  methodology={snapshot.methodology_version}  "
        f"metric={snapshot.metric_name} ({snapshot.metric_window_days}d)  "
        f"k={snapshot.k}  n_selected={snapshot.n_selected}  provenance={snapshot.provenance}"
    )
    typer.echo("")

    rows = store.universe_snapshot_rows(snapshot_id)
    if not rows:
        typer.echo("No ranked rows found for this snapshot.")
        return

    ordered = sorted(rows, key=lambda r: (not r.selected, r.rank))
    header = (
        f"{'RANK':<6} {'SYMBOL':<16} {'METRIC_VALUE':<14} {'ASSET_CLASS':<18} "
        f"{'ELIGIBLE':<9} {'SELECTED':<9} EXCLUSION_REASON"
    )
    typer.echo(header)
    for row in ordered:
        metric = f"{row.metric_value:.2f}" if row.metric_value is not None else "-"
        reason = row.exclusion_reason or "-"
        eligible = "-" if row.eligible is None else str(row.eligible)
        asset_class = row.asset_class or "-"
        _safe_echo(
            f"{row.rank:<6} {row.symbol:<16} {metric:<14} {asset_class:<18} "
            f"{eligible:<9} {str(row.selected):<9} {reason}"
        )


@universe_app.command("snapshot")
def universe_snapshot_command(
    as_of: str | None = typer.Option(
        None,
        "--as-of",
        help=(
            "ISO timestamp; reconstructs a BACKFILLED snapshot as of that moment "
            "instead of a live FORWARD one. A BACKFILLED snapshot never touches "
            "the network and never updates market_registry's cached eligibility."
        ),
    ),
) -> None:
    """Generate and persist one universe snapshot (Phase 6, Merge 2B).

    Ranks every ACTIVE registry symbol by
    MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D, assesses Section 1 eligibility
    (the LOCKED v1.1 engine, unmodified) for the top K=50, and selects the
    top N=30 eligible by rank. Every ranked symbol gets a row, not only
    the selected 30 - see `universe show`.
    """
    settings = get_settings()
    store = _store(settings)
    as_of_dt = _parse_as_of(as_of)
    exchange = ExchangeClient(venue=settings.venue) if as_of_dt is None else None

    result = generate_universe_snapshot(store, exchange, settings.venue, as_of=as_of_dt)
    snapshot = result.snapshot

    typer.echo(
        f"snapshot {snapshot.snapshot_id}  provenance={snapshot.provenance}  "
        f"at={snapshot.snapshot_at.isoformat()}  ranked={len(result.rows)}  "
        f"n_selected={snapshot.n_selected}/{snapshot.k}"
    )
    typer.echo("")
    typer.echo("By exclusion_reason:")
    for reason, count in sorted(snapshot.counts_by_exclusion_reason.items()):
        typer.echo(f"  {reason:<36} {count}")


@universe_app.command("coverage")
def universe_coverage(
    snapshot_id: str | None = typer.Option(
        None, "--snapshot-id", help="Snapshot to report on; defaults to the latest for this venue."
    ),
) -> None:
    """Coverage report: symbols on venue, eligible, assessed, selected,
    data available, and counts by exclusion reason.
    """
    settings = get_settings()
    store = _store(settings)

    registry_rows = store.market_registry(settings.venue)
    active_count = sum(1 for r in registry_rows if r.status == "ACTIVE")
    absent_count = sum(1 for r in registry_rows if r.status == "ABSENT_FROM_VENUE")
    with_daily = sum(1 for r in registry_rows if r.first_candle_seen_at is not None)
    with_4h = sum(
        1 for r in registry_rows if store.count_candles(settings.venue, r.symbol, "4h") > 0
    )
    with_cached_eligibility = sum(
        1 for r in registry_rows if r.section1_first_usable_at is not None
    )

    typer.echo(f"Registry: {len(registry_rows)} symbol(s) on venue")
    typer.echo(f"  ACTIVE:                            {active_count}")
    typer.echo(f"  ABSENT_FROM_VENUE:                 {absent_count}")
    typer.echo(f"  with daily candle data:            {with_daily}")
    typer.echo(f"  with 4H candle data:               {with_4h}")
    typer.echo(f"  with cached Section 1 eligibility: {with_cached_eligibility}")
    typer.echo("")

    if snapshot_id is not None:
        snapshot = store.universe_snapshot_by_id(snapshot_id)
        if snapshot is None:
            typer.echo(f"No snapshot found with id {snapshot_id!r}.")
            raise typer.Exit(code=1)
    else:
        snapshots = store.universe_snapshots(settings.venue)
        snapshot = snapshots[0] if snapshots else None

    if snapshot is None:
        typer.echo("No snapshots found yet.")
        return

    rows = store.universe_snapshot_rows(snapshot.snapshot_id)
    # "Assessed" means actually reached Section 1 eligibility (PART B step
    # 4). Not `rank <= K`: UNIV-08 scopes the assessment set to CRYPTO
    # candidates only, so a crypto symbol's raw rank can exceed K (pushed
    # down by non-crypto symbols ranked above it) while still having been
    # assessed, and a non-crypto symbol's raw rank can be <= K without
    # ever reaching this step. Not `eligible is not None` either - a
    # symbol excluded pre-assessment (NOT_ASSESSED, INSUFFICIENT_VOLUME_
    # HISTORY, or any UNIV-08 domain reason) also carries a non-NULL
    # `eligible=False` for the domain ones. "Assessed" is precisely: not
    # excluded for one of those five pre-assessment reasons.
    _not_assessed_reasons = {
        NOT_ASSESSED,
        INSUFFICIENT_VOLUME_HISTORY,
        NON_CRYPTO_UNDERLYING,
        NON_ELIGIBLE_INDEX,
        UNKNOWN_UNDERLYING_TYPE,
    }
    assessed = sum(1 for r in rows if r.exclusion_reason not in _not_assessed_reasons)
    typer.echo(
        f"Snapshot {snapshot.snapshot_id} ({snapshot.provenance}, "
        f"at={snapshot.snapshot_at.isoformat()}):"
    )
    typer.echo(f"  ranked:               {len(rows)}")
    typer.echo(f"  assessed (top-{snapshot.k} crypto candidates): {assessed}")
    typer.echo(f"  eligible:             {sum(1 for r in rows if r.eligible is True)}")
    typer.echo(f"  selected:             {sum(1 for r in rows if r.selected)}")
    typer.echo("")
    typer.echo("By exclusion_reason:")
    for reason, count in sorted(snapshot.counts_by_exclusion_reason.items()):
        typer.echo(f"  {reason:<36} {count}")


@universe_app.command("sync")
def universe_sync_command(
    days: int = typer.Option(
        DEFAULT_SYNC_DAYS,
        "--days",
        help="Backfill depth in days for every synced timeframe.",
    ),
) -> None:
    """Backfill 1H/4H/1D/1W for the SELECTED symbols of the latest
    universe snapshot only (Phase 6, Merge 3).

    Prepares full candle coverage for `tidemark run`/`tidemark observe
    run` before they switch to reading the snapshot's selection. Reuses
    the existing chunked-upsert backfill path unmodified (idempotent -
    re-running with the same --days inserts zero new duplicates); never
    fetches a symbol outside the selection.
    """
    settings = get_settings()
    store = _store(settings)
    exchange = ExchangeClient(venue=settings.venue)

    symbols = selected_symbols(store, settings.venue)
    if not symbols:
        typer.echo(
            "No universe snapshot with selected symbols found. "
            "Run `tidemark universe snapshot` first."
        )
        return

    typer.echo(
        f"Syncing {len(symbols)} selected symbol(s) across "
        f"{', '.join(SYNC_TIMEFRAMES)} ({days} days)..."
    )

    def _report_progress(outcome: SymbolTimeframeOutcome) -> None:
        if outcome.error is not None:
            _safe_echo(f"  {outcome.symbol:<16} {outcome.timeframe:<5} FAILED: {outcome.error}")
        else:
            r = outcome.result
            _safe_echo(
                f"  {outcome.symbol:<16} {outcome.timeframe:<5} fetched={r.fetched} "
                f"inserted={r.inserted} duplicates={r.duplicates_skipped} rejected={r.rejected}"
            )

    outcome = run_universe_sync(
        store, exchange, settings.venue, days=days, on_outcome=_report_progress
    )

    typer.echo("")
    typer.echo(f"sync {outcome.run_id}: {outcome.status}")


def _coinalyze_client(settings: Settings) -> CoinalyzeClient:
    return CoinalyzeClient(api_key=settings.coinalyze_api_key)


def _fmt_metric_line(name: str, metric) -> str:
    if metric.status != OK:
        return f"{name}: UNAVAILABLE ({metric.status}: {metric.reason})"
    if getattr(metric, "is_point_in_time", False):
        return (
            f"{name}: {metric.value} {metric.unit}  (live, updated {metric.updated_at.isoformat()})"
        )
    return (
        f"{name}: {metric.value} {metric.unit}  "
        f"(period {metric.period_start.isoformat()} to {metric.period_close.isoformat()})"
    )


def _render_market_intel_snapshot(snapshot: MarketIntelSnapshot) -> str:
    lines = [
        f"{snapshot.symbol} ({snapshot.coinalyze_symbol})",
        f"generated_at: {snapshot.generated_at.isoformat()}",
        f"market_status: {snapshot.market_status}",
        "",
        _fmt_metric_line("Open interest", snapshot.open_interest),
        _fmt_metric_line("Open interest change", snapshot.open_interest_change),
        _fmt_metric_line("Funding rate", snapshot.funding_rate),
        _fmt_metric_line("Predicted funding rate", snapshot.predicted_funding_rate),
    ]

    ls = snapshot.long_short_ratio
    if ls.status != OK:
        lines.append(f"Long/short ratio: UNAVAILABLE ({ls.status}: {ls.reason})")
    else:
        lines.append(
            f"Long/short ratio: {ls.ratio}  "
            f"(long {ls.long_pct}{ls.percent_unit} / short {ls.short_pct}{ls.percent_unit})  "
            f"(period {ls.period_start.isoformat()} to {ls.period_close.isoformat()})"
        )

    liq = snapshot.liquidations
    if liq.status != OK:
        lines.append(f"Liquidations: UNAVAILABLE ({liq.status}: {liq.reason})")
    else:
        lines.append(
            f"Liquidations: long={liq.long_usd} {liq.unit} / short={liq.short_usd} {liq.unit}  "
            f"(period {liq.period_start.isoformat()} to {liq.period_close.isoformat()})"
        )

    lines.append(_fmt_metric_line("Futures volume", snapshot.futures_volume))
    lines.append(_fmt_metric_line("Buy volume", snapshot.buy_volume))
    lines.append(_fmt_metric_line("Sell volume", snapshot.sell_volume))
    return "\n".join(lines)


def _metric_to_dict(metric) -> dict:
    payload = dataclasses.asdict(metric)
    for key in ("updated_at", "period_start", "period_close"):
        if key in payload and payload[key] is not None:
            payload[key] = payload[key].isoformat()
    return payload


def _market_intel_snapshot_to_dict(snapshot: MarketIntelSnapshot) -> dict:
    return {
        "symbol": snapshot.symbol,
        "coinalyze_symbol": snapshot.coinalyze_symbol,
        "market_status": snapshot.market_status,
        "generated_at": snapshot.generated_at.isoformat(),
        "open_interest": _metric_to_dict(snapshot.open_interest),
        "open_interest_change": _metric_to_dict(snapshot.open_interest_change),
        "funding_rate": _metric_to_dict(snapshot.funding_rate),
        "predicted_funding_rate": _metric_to_dict(snapshot.predicted_funding_rate),
        "long_short_ratio": _metric_to_dict(snapshot.long_short_ratio),
        "liquidations": _metric_to_dict(snapshot.liquidations),
        "futures_volume": _metric_to_dict(snapshot.futures_volume),
        "buy_volume": _metric_to_dict(snapshot.buy_volume),
        "sell_volume": _metric_to_dict(snapshot.sell_volume),
    }


@intel_app.command("market")
def intel_market(
    symbol: str = typer.Option(
        ..., "--symbol", help="ccxt unified perpetual symbol, e.g. BTC/USDT:USDT"
    ),
    json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Print one symbol's normalized Coinalyze derivatives snapshot.

    Raw data only: every metric with its value, unit, the period it
    covers (or its own live-update timestamp for a point-in-time
    reading), or UNAVAILABLE with the reason
    (MARKET_NOT_FOUND/NO_DATA). No Telegram, no trading recommendation,
    no bias output - see docs/adr/0011-market-intelligence-layer.md.
    """
    settings = get_settings()
    try:
        client = _coinalyze_client(settings)
    except MissingApiKeyError:
        typer.echo("TIDEMARK_COINALYZE_API_KEY is not set. See .env.example.")
        raise typer.Exit(code=1) from None

    cache = FutureMarketsCache(client)
    now = dt.datetime.now(dt.UTC)

    try:
        snapshot = fetch_market_intel(client, cache, symbol, settings.venue, now)
    except UnsupportedVenueError as exc:
        typer.echo(
            f"No Coinalyze exchange-code mapping for venue {exc.venue!r}. "
            "See docs/adr/0011-market-intelligence-layer.md."
        )
        raise typer.Exit(code=1) from None
    except RateLimitedError as exc:
        typer.echo(f"Coinalyze rate limited this request: {exc}")
        raise typer.Exit(code=1) from None
    except CoinalyzeHttpError as exc:
        typer.echo(f"Coinalyze returned HTTP {exc.status_code}: {exc.detail}")
        raise typer.Exit(code=1) from None
    except CoinalyzeConnectionError as exc:
        typer.echo(f"Could not reach Coinalyze: {exc.detail}")
        raise typer.Exit(code=1) from None

    if json:
        typer.echo(json_module.dumps(_market_intel_snapshot_to_dict(snapshot), indent=2))
    else:
        _safe_echo(_render_market_intel_snapshot(snapshot))

    if snapshot.market_status != OK:
        raise typer.Exit(code=1)


def _telegram_bot_client(settings: Settings) -> TelegramBotClient:
    return TelegramBotClient(bot_token=settings.telegram_bot_token)


@intel_app.command("bot")
def intel_bot(
    once: bool = typer.Option(
        False, "--once", help="Process any pending updates once and exit (for testing)."
    ),
) -> None:
    """Run the /coin Telegram long-polling bot.

    Read-only: replies with Merge 1 market snapshots only - no Telegram
    alert this bot could send resembles a trading recommendation. Any
    chat other than TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID is silently
    ignored (no reply, nothing that confirms the bot exists) and only
    logged (chat id and timestamp, never the message text). See
    docs/adr/0011-market-intelligence-layer.md.
    """
    settings = get_settings()

    if settings.telegram_allowed_chat_id is None:
        typer.echo("TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID is not set. See .env.example.")
        raise typer.Exit(code=1)

    try:
        telegram = _telegram_bot_client(settings)
    except MissingBotTokenError:
        typer.echo("TIDEMARK_TELEGRAM_BOT_TOKEN is not set. See .env.example.")
        raise typer.Exit(code=1) from None

    try:
        coinalyze = _coinalyze_client(settings)
    except MissingApiKeyError:
        typer.echo("TIDEMARK_COINALYZE_API_KEY is not set. See .env.example.")
        raise typer.Exit(code=1) from None

    cache = FutureMarketsCache(coinalyze)
    state = BotStateStore(settings.telegram_bot_offset_file)

    context_engine = make_universe_context_engine(settings.database_url)
    init_universe_context_store(context_engine)
    context_stale_after = dt.timedelta(hours=settings.universe_context_stale_after_hours)

    if once:
        try:
            outcome = run_once(
                telegram,
                coinalyze,
                cache,
                state,
                settings.telegram_allowed_chat_id,
                settings.venue,
                dt.datetime.now(dt.UTC),
                discard_backlog=True,
                poll_timeout=0,
                context_engine=context_engine,
                context_stale_after=context_stale_after,
            )
        except TelegramHttpError as exc:
            typer.echo(f"Telegram returned HTTP {exc.status_code}: {exc.detail}")
            raise typer.Exit(code=1) from None
        except TelegramConnectionError as exc:
            typer.echo(
                f"Could not reach Telegram: {exc.detail}. This is usually network "
                "filtering or a firewall, not a bad bot token."
            )
            raise typer.Exit(code=1) from None

        typer.echo(
            f"updates_seen={outcome.updates_seen} processed={outcome.processed} "
            f"discarded_stale={outcome.discarded_stale} unauthorized={outcome.unauthorized}"
        )
        return

    typer.echo("Starting the /coin Telegram bot (long polling). Ctrl+C to stop.")
    try:
        run_forever(
            telegram,
            coinalyze,
            cache,
            state,
            settings.telegram_allowed_chat_id,
            settings.venue,
            context_engine=context_engine,
            context_stale_after=context_stale_after,
        )
    except KeyboardInterrupt:
        typer.echo("Stopped.")
    except TelegramHttpError as exc:
        typer.echo(f"Telegram rejected the bot token (HTTP {exc.status_code}): {exc.detail}")
        raise typer.Exit(code=1) from None


def _input_to_dict(inp) -> dict:
    payload = dataclasses.asdict(inp)
    for key in ("period_start", "period_close"):
        if key in payload and payload[key] is not None:
            payload[key] = payload[key].isoformat()
    return payload


def _briefing_result_to_dict(result: BriefingResult, sent: bool) -> dict:
    cr = result.classification
    structure = result.structure
    return {
        "evaluated_at": result.evaluated_at.isoformat(),
        "classification": {
            "result": cr.result,
            "interpretation": cr.interpretation,
            "reason": cr.reason,
            "rulebook_version": cr.rulebook_version,
            "price": _input_to_dict(cr.price),
            "open_interest": _input_to_dict(cr.open_interest),
            "funding": _input_to_dict(cr.funding),
        },
        "structure": {
            "available": structure.available,
            "stale": structure.stale,
            "state": structure.state,
            "watch": structure.watch,
            "grade": structure.grade,
            "rule_version": structure.rule_version,
            "evaluated_at": (
                structure.evaluated_at.isoformat() if structure.evaluated_at is not None else None
            ),
        },
        "should_send": result.should_send,
        "send_reason": result.send_reason,
        "sent": sent,
        "message": result.message,
    }


@intel_app.command("briefing")
def intel_briefing(
    send: bool = typer.Option(
        False, "--send", help="Actually deliver via Telegram if this evaluation decides to send."
    ),
    json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Evaluate the hourly BTC derivatives-context briefing.

    Classifies the latest closed-1H price/OI/funding reading against
    docs/rulebook/derivatives-context-v0.1.md (D1-D6 or NO_MATCH), shows
    BTC's stored Section 1 structure alongside it (read-only, never
    recomputed - UNAVAILABLE if there is no record or it's stale), and
    records the evaluation - sent or not - in market_intel's own table.
    Without --send, nothing is ever delivered to Telegram; the
    evaluation still runs and is still recorded, for testing. Raw data
    only: no trade direction, no bias line, no BTC dominance. See
    docs/adr/0011-market-intelligence-layer.md.
    """
    settings = get_settings()
    try:
        coinalyze = _coinalyze_client(settings)
    except MissingApiKeyError:
        typer.echo("TIDEMARK_COINALYZE_API_KEY is not set. See .env.example.")
        raise typer.Exit(code=1) from None

    cache = FutureMarketsCache(coinalyze)
    evaluation_engine = make_evaluation_engine(settings.database_url)
    init_evaluation_store(evaluation_engine)
    now = dt.datetime.now(dt.UTC)

    try:
        result = evaluate_briefing(
            coinalyze, cache, evaluation_engine, settings.database_url, settings.venue, now
        )
    except UnsupportedVenueError as exc:
        typer.echo(
            f"No Coinalyze exchange-code mapping for venue {exc.venue!r}. "
            "See docs/adr/0011-market-intelligence-layer.md."
        )
        raise typer.Exit(code=1) from None
    except RateLimitedError as exc:
        typer.echo(f"Coinalyze rate limited this request: {exc}")
        raise typer.Exit(code=1) from None
    except CoinalyzeHttpError as exc:
        typer.echo(f"Coinalyze returned HTTP {exc.status_code}: {exc.detail}")
        raise typer.Exit(code=1) from None
    except CoinalyzeConnectionError as exc:
        typer.echo(f"Could not reach Coinalyze: {exc.detail}")
        raise typer.Exit(code=1) from None

    sent = False
    if send and result.should_send:
        if settings.telegram_allowed_chat_id is None:
            typer.echo("TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID is not set. See .env.example.")
            raise typer.Exit(code=1)
        try:
            telegram = _telegram_bot_client(settings)
        except MissingBotTokenError:
            typer.echo("TIDEMARK_TELEGRAM_BOT_TOKEN is not set. See .env.example.")
            raise typer.Exit(code=1) from None
        try:
            telegram.send_message(settings.telegram_allowed_chat_id, result.message)
            sent = True
            mark_sent(evaluation_engine, result, dt.datetime.now(dt.UTC))
        except TelegramHttpError as exc:
            typer.echo(f"Telegram returned HTTP {exc.status_code}: {exc.detail}")
            raise typer.Exit(code=1) from None
        except TelegramConnectionError as exc:
            typer.echo(
                f"Could not reach Telegram: {exc.detail}. This is usually network "
                "filtering or a firewall, not a bad bot token."
            )
            raise typer.Exit(code=1) from None

    if json:
        typer.echo(json_module.dumps(_briefing_result_to_dict(result, sent), indent=2))
    else:
        _safe_echo(result.message)
        typer.echo("")
        typer.echo(f"should_send={result.should_send} reason={result.send_reason} sent={sent}")


def _metric_cell(metric) -> str:
    if metric.status != OK:
        return "UNAVAILABLE"
    return f"{metric.value:.6g}"


def _render_distributions(measurement: DistributionsMeasurement) -> str:
    lines = [
        f"universe snapshot: {measurement.snapshot_id or 'N/A'}",
        f"source: {measurement.source}",
        f"period: {measurement.period.start.isoformat()} to {measurement.period.close.isoformat()}",
        f"generated_at: {measurement.generated_at.isoformat()}",
        f"elapsed: {measurement.elapsed_seconds:.1f}s",
        "",
        f"{'rank':>4}  {'symbol':<20}  {'l/s ratio':>12}  {'funding %':>12}  "
        f"{'oi 1h chg %':>12}  {'buy/sell ratio':>15}",
    ]
    for row in measurement.rows:
        lines.append(
            f"{row.rank:>4}  {row.ccxt_symbol:<20}  {_metric_cell(row.long_short_ratio):>12}  "
            f"{_metric_cell(row.funding_rate):>12}  {_metric_cell(row.oi_change_pct):>12}  "
            f"{_metric_cell(row.buy_sell_ratio):>15}"
        )

    lines.append("")
    lines.append("summary:")
    for summary in measurement.summaries:
        if summary.n == 0:
            lines.append(f"  {summary.name}: n=0 unavailable={summary.unavailable_count}")
        else:
            lines.append(
                f"  {summary.name}: n={summary.n} min={summary.min:.6g} p25={summary.p25:.6g} "
                f"median={summary.median:.6g} p75={summary.p75:.6g} max={summary.max:.6g} "
                f"unavailable={summary.unavailable_count}"
            )

    if measurement.skipped_symbols:
        lines.append("")
        lines.append(f"not fetched ({len(measurement.skipped_symbols)}, budget exhausted):")
        lines.append("  " + ", ".join(measurement.skipped_symbols))

    return "\n".join(lines)


def _closed_period_metric_to_dict(metric) -> dict:
    return {
        "status": metric.status,
        "value": metric.value,
        "unit": metric.unit,
        "period_start": metric.period_start.isoformat() if metric.period_start else None,
        "period_close": metric.period_close.isoformat() if metric.period_close else None,
        "reason": metric.reason,
    }


def _symbol_metrics_to_dict(row: SymbolMetrics) -> dict:
    return {
        "rank": row.rank,
        "ccxt_symbol": row.ccxt_symbol,
        "coinalyze_symbol": row.coinalyze_symbol,
        "long_short_ratio": _closed_period_metric_to_dict(row.long_short_ratio),
        "funding_rate": _closed_period_metric_to_dict(row.funding_rate),
        "oi_change_pct": _closed_period_metric_to_dict(row.oi_change_pct),
        "buy_sell_ratio": _closed_period_metric_to_dict(row.buy_sell_ratio),
    }


def _metric_summary_to_dict(summary: MetricSummary) -> dict:
    return {
        "name": summary.name,
        "n": summary.n,
        "min": summary.min,
        "p25": summary.p25,
        "median": summary.median,
        "p75": summary.p75,
        "max": summary.max,
        "unavailable_count": summary.unavailable_count,
    }


def _distributions_to_dict(measurement: DistributionsMeasurement) -> dict:
    return {
        "generated_at": measurement.generated_at.isoformat(),
        "period_start": measurement.period.start.isoformat(),
        "period_close": measurement.period.close.isoformat(),
        "source": measurement.source,
        "snapshot_id": measurement.snapshot_id,
        "elapsed_seconds": measurement.elapsed_seconds,
        "rows": [_symbol_metrics_to_dict(row) for row in measurement.rows],
        "summaries": [_metric_summary_to_dict(s) for s in measurement.summaries],
        "skipped_symbols": measurement.skipped_symbols,
    }


@intel_app.command("distributions")
def intel_distributions(
    json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    symbols: str | None = typer.Option(
        None,
        "--symbols",
        help="Comma-separated ccxt unified perpetual symbols; overrides the universe snapshot.",
    ),
) -> None:
    """Measure long/short ratio, funding rate, 1H open-interest change,
    and buy/sell volume ratio across the universe - a per-symbol table
    plus n/min/p25/median/p75/max per metric. Read-only measurement
    only: no interpretation, no labels, no trading recommendation - see
    docs/adr/0011-market-intelligence-layer.md.
    """
    settings = get_settings()
    try:
        client = _coinalyze_client(settings)
    except MissingApiKeyError:
        typer.echo("TIDEMARK_COINALYZE_API_KEY is not set. See .env.example.")
        raise typer.Exit(code=1) from None

    if symbols:
        ccxt_symbols = [s.strip() for s in symbols.split(",") if s.strip()]
        source = "EXPLICIT"
        snapshot_id = None
    else:
        snapshot_id, ccxt_symbols = read_latest_selected_symbols(
            settings.database_url, settings.venue
        )
        source = "SNAPSHOT"
        if not ccxt_symbols:
            typer.echo(
                f"No universe snapshot with selected symbols exists for venue={settings.venue!r}. "
                "Run `tidemark universe snapshot` first, or pass --symbols."
            )
            raise typer.Exit(code=1)

    cache = FutureMarketsCache(client)
    now = dt.datetime.now(dt.UTC)

    try:
        measurement = measure_distributions(
            client, cache, ccxt_symbols, settings.venue, now, source, snapshot_id
        )
    except UnsupportedVenueError as exc:
        typer.echo(
            f"No Coinalyze exchange-code mapping for venue {exc.venue!r}. "
            "See docs/adr/0011-market-intelligence-layer.md."
        )
        raise typer.Exit(code=1) from None
    except CoinalyzeHttpError as exc:
        typer.echo(f"Coinalyze returned HTTP {exc.status_code}: {exc.detail}")
        raise typer.Exit(code=1) from None
    except CoinalyzeConnectionError as exc:
        typer.echo(f"Could not reach Coinalyze: {exc.detail}")
        raise typer.Exit(code=1) from None

    if json:
        typer.echo(json_module.dumps(_distributions_to_dict(measurement), indent=2))
    else:
        _safe_echo(_render_distributions(measurement))


def _metric_summary_to_fields(summary: MetricSummary) -> MetricSummaryFields:
    return MetricSummaryFields(
        n=summary.n,
        min=summary.min,
        p25=summary.p25,
        median=summary.median,
        p75=summary.p75,
        max=summary.max,
        unavailable_count=summary.unavailable_count,
    )


@intel_app.command("refresh-context")
def intel_refresh_context() -> None:
    """Run the same measurement as `intel distributions` over the latest
    universe snapshot and cache its per-metric summary as one row in
    `universe_context_cache`, for /coin to read without a live universe
    scan. The only writer of that table - `intel distributions` stays
    read-only and unchanged. See docs/adr/0011-market-intelligence-layer.md.
    """
    settings = get_settings()
    try:
        client = _coinalyze_client(settings)
    except MissingApiKeyError:
        typer.echo("TIDEMARK_COINALYZE_API_KEY is not set. See .env.example.")
        raise typer.Exit(code=1) from None

    snapshot_id, ccxt_symbols = read_latest_selected_symbols(settings.database_url, settings.venue)
    if not ccxt_symbols:
        typer.echo(
            f"No universe snapshot with selected symbols exists for venue={settings.venue!r}. "
            "Run `tidemark universe snapshot` first."
        )
        raise typer.Exit(code=1)

    cache = FutureMarketsCache(client)
    now = dt.datetime.now(dt.UTC)

    try:
        measurement = measure_distributions(
            client, cache, ccxt_symbols, settings.venue, now, "SNAPSHOT", snapshot_id
        )
    except UnsupportedVenueError as exc:
        typer.echo(
            f"No Coinalyze exchange-code mapping for venue {exc.venue!r}. "
            "See docs/adr/0011-market-intelligence-layer.md."
        )
        raise typer.Exit(code=1) from None
    except CoinalyzeHttpError as exc:
        typer.echo(f"Coinalyze returned HTTP {exc.status_code}: {exc.detail}")
        raise typer.Exit(code=1) from None
    except CoinalyzeConnectionError as exc:
        typer.echo(f"Could not reach Coinalyze: {exc.detail}")
        raise typer.Exit(code=1) from None

    context_engine = make_universe_context_engine(settings.database_url)
    init_universe_context_store(context_engine)
    metrics = {s.name: _metric_summary_to_fields(s) for s in measurement.summaries}
    record_universe_context(
        context_engine,
        UniverseContextRecord(
            computed_at=now,
            universe_snapshot_id=snapshot_id,
            period_start=measurement.period.start,
            period_end=measurement.period.close,
            metrics=metrics,
        ),
    )

    typer.echo(
        f"universe_snapshot_id={snapshot_id}  "
        f"period={measurement.period.start.isoformat()} to {measurement.period.close.isoformat()}  "
        f"computed_at={now.isoformat()}  "
        f"elapsed={measurement.elapsed_seconds:.1f}s  "
        f"skipped={len(measurement.skipped_symbols)}"
    )
    typer.echo("Wrote 1 row to universe_context_cache.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
