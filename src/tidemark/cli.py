"""Tidemark CLI entrypoint.

Read-only copilot: the CLI can fetch/store market data, evaluate the
rulebook, and emit alerts, but it never places orders and never touches
exchange trading permissions.
"""

from __future__ import annotations

import datetime as dt

import typer

from tidemark import __version__
from tidemark.config.settings import Settings, get_settings
from tidemark.data.exchange import ExchangeClient
from tidemark.data.ingest import RunOutcome, run_backfill, run_update
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.data.timeframes import TIMEFRAMES

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

STALE_RUNNING_THRESHOLD = dt.timedelta(hours=2)


@app.command()
def run() -> None:
    """Evaluate the rulebook against closed candles and emit alerts.

    Not implemented yet — rulebook evaluation lands in a later phase.
    """
    raise NotImplementedError("Rulebook evaluation is not implemented yet.")


@app.command()
def version() -> None:
    """Print the installed Tidemark version."""
    typer.echo(__version__)


def _parse_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
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
    symbols: str = typer.Option(
        None, "--symbols", help="Comma-separated symbols; defaults to TIDEMARK_SYMBOLS."
    ),
    timeframes: str = typer.Option(
        None, "--timeframes", help="Comma-separated timeframes; defaults to all stored timeframes."
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
    symbols: str = typer.Option(
        None, "--symbols", help="Comma-separated symbols; defaults to TIDEMARK_SYMBOLS."
    ),
    timeframes: str = typer.Option(
        None, "--timeframes", help="Comma-separated timeframes; defaults to all stored timeframes."
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
    symbols: str = typer.Option(
        None, "--symbols", help="Comma-separated symbols; defaults to TIDEMARK_SYMBOLS."
    ),
    timeframes: str = typer.Option(
        None, "--timeframes", help="Comma-separated timeframes; defaults to all stored timeframes."
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
    symbols: str = typer.Option(
        None, "--symbols", help="Comma-separated symbols; defaults to TIDEMARK_SYMBOLS."
    ),
    timeframes: str = typer.Option(
        None, "--timeframes", help="Comma-separated timeframes; defaults to all stored timeframes."
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
