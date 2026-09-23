"""Tidemark CLI entrypoint.

Read-only copilot: the CLI can evaluate the rulebook and emit alerts, but
it never places orders and never touches exchange trading permissions.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import typer

from tidemark import __version__
from tidemark.config.settings import get_settings
from tidemark.context import htf
from tidemark.core.atr import atr as compute_atr
from tidemark.data.exchange import ExchangeClient
from tidemark.data.models import ContextRecord
from tidemark.data.store import TidemarkStore, create_store_engine, init_db

app = typer.Typer(
    name="tidemark",
    help=(
        "Tidemark: a rule-based market-structure copilot for crypto markets. "
        "Read-only. Never places orders."
    ),
    no_args_is_help=True,
)

data_app = typer.Typer(help="Market data: backfill closed candles from the exchange.")
context_app = typer.Typer(help="Section 1 HTF context: evaluate, history, explain.")
app.add_typer(data_app, name="data")
app.add_typer(context_app, name="context")

_BACKFILL_PAGE_SIZE = 1000


def _store() -> TidemarkStore:
    settings = get_settings()
    engine = create_store_engine(settings.database_url)
    init_db(engine)
    return TidemarkStore(engine)


def _exchange_client() -> ExchangeClient:
    settings = get_settings()
    api_key = settings.exchange_api_key.get_secret_value() if settings.exchange_api_key else None
    return ExchangeClient(
        base_url=settings.exchange_base_url,
        api_key=api_key,
        exchange_id=settings.exchange_id,
    )


def _parse_as_of(value: str | None) -> dt.datetime | None:
    if value is None:
        return None
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


@app.command()
def run() -> None:
    """Evaluate the rulebook against closed candles and emit alerts.

    Not implemented in Phase 0 — this is a repository/tooling skeleton
    only. Strategy logic lands in a later phase.
    """
    raise NotImplementedError("Pipeline execution is not implemented in Phase 0.")


@app.command()
def version() -> None:
    """Print the installed Tidemark version."""
    typer.echo(__version__)


@data_app.command("backfill")
def data_backfill(
    symbols: str = typer.Option(..., help="Comma-separated symbols, e.g. BTC/USDT:USDT"),
    timeframes: str = typer.Option(..., help="Comma-separated timeframes, e.g. 4h,1d,1w"),
    days: int = typer.Option(..., help="How many days of closed-candle history to fetch"),
) -> None:
    """Backfill closed candles for one or more symbols/timeframes."""
    store = _store()
    client = _exchange_client()
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    timeframe_list = [t.strip() for t in timeframes.split(",") if t.strip()]
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)

    for symbol in symbol_list:
        for timeframe in timeframe_list:
            cursor: dt.datetime | None = since
            total = 0
            while True:
                batch = client.fetch_closed_candles(
                    symbol, timeframe, limit=_BACKFILL_PAGE_SIZE, since=cursor
                )
                if not batch:
                    break
                store.save_candles(batch)
                total += len(batch)
                next_cursor = batch[-1].close_time
                if cursor is not None and next_cursor <= cursor:
                    break
                cursor = next_cursor
                if len(batch) < _BACKFILL_PAGE_SIZE:
                    break
            typer.echo(f"{symbol} {timeframe}: {total} candles")


def _load_context_candles(
    store: TidemarkStore, symbol: str, as_of: dt.datetime | None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    candles_4h = store.get_candles(symbol, "4h", as_of=as_of)
    candles_1d = store.get_candles(symbol, "1d", as_of=as_of)
    candles_1w = store.get_candles(symbol, "1w", as_of=as_of)
    return candles_4h, candles_1d, candles_1w


def _evaluate_symbol(store: TidemarkStore, symbol: str, as_of: dt.datetime | None) -> ContextRecord:
    candles_4h, candles_1d, candles_1w = _load_context_candles(store, symbol, as_of)
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
    store = _store()
    record = _evaluate_symbol(store, symbol, _parse_as_of(as_of))
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
    store = _store()
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
    store = _store()
    record = store.latest_context_record(symbol)
    if record is None:
        typer.echo(
            f"No context record for {symbol} yet. "
            f"Run `tidemark context evaluate --symbol {symbol}` first."
        )
        raise typer.Exit(code=1)

    grade = record.grade or "-"
    typer.echo(f"{symbol} — Section 1 HTF Context ({record.rule_version})")
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
            f"  {level['kind']:10} {level['price']:.2f}  "
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
