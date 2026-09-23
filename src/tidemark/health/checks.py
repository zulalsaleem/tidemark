"""Health checks: proves the system actually ran.

Tidemark runs unattended and Telegram is silent unless state changes, so
silence is ambiguous — it could mean nothing changed, or it could mean
the system died. These checks make silence trustworthy by inspecting
what's actually in the database: is it reachable, is the data fresh, did
the last run succeed, is the journal still being written, are there
gaps, and is Telegram even configured.

Pure functions, no I/O beyond reading the database (via `TidemarkStore`/
`Engine`) and already-loaded settings — no writes, no network calls, no
sending. `now` is always passed in rather than read from the clock
internally, so every check is testable with a frozen time.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import Engine, inspect

from tidemark.data.models import Base, Run
from tidemark.data.store import RUNNING_STATUS, TidemarkStore
from tidemark.data.timeframes import TIMEFRAME_DURATIONS

# --- Status levels -----------------------------------------------------------

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

_STATUS_SEVERITY = {OK: 0, WARN: 1, FAIL: 2}

EXIT_CODES = {OK: 0, WARN: 1, FAIL: 2}

# --- Thresholds (PART A: one named-constant block, nothing scattered) -------

# Candle freshness: age measured in multiples of the timeframe's own
# interval (a 4h candle is "fresh" for longer, in absolute time, than a
# 5m candle).
CANDLE_FRESHNESS_OK_MULTIPLE = 1.5
CANDLE_FRESHNESS_WARN_MULTIPLE = 3.0

# A RUNNING row older than this never reached finish_run - the process
# that started it almost certainly crashed. Matches the existing STALE
# threshold in `cli.py`'s `data status`.
STALE_RUNNING_THRESHOLD = dt.timedelta(hours=2)

# If the most recent run of a command completed longer ago than this,
# the unattended cycle has been silent for two missed 4H cycles.
LAST_RUN_COMPLETION_FAIL_THRESHOLD = dt.timedelta(hours=8)

# Journal activity: age of the single newest journal row, across every
# asset.
JOURNAL_ACTIVITY_WARN_THRESHOLD = dt.timedelta(hours=8)
JOURNAL_ACTIVITY_FAIL_THRESHOLD = dt.timedelta(hours=24)

# Gap count per (symbol, timeframe): 0 is OK, 1-5 is WARN, above that FAIL.
GAPS_WARN_MAX = 5

# Only the timeframes Section 1 (and this phase's pipeline) actually use.
# 5m/15m/1h are part of the stored timeframe set for future sections but
# nothing in this codebase ingests them on a schedule yet, so checking
# their freshness/gaps here would just be permanent, meaningless WARN/FAIL
# noise rather than a signal about whether Tidemark is healthy.
HEALTH_TIMEFRAMES: tuple[str, ...] = ("4h", "1d", "1w")


@dataclass(frozen=True)
class CheckResult:
    """One check's outcome: a status plus a human-readable detail."""

    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class HealthReport:
    """The full health picture: every individual check, the worst status
    among them, and the summary data the heartbeat message renders.
    """

    status: str
    generated_at: dt.datetime
    rule_version: str
    checks: list[CheckResult] = field(default_factory=list)
    last_candle_symbol: str | None = None
    last_candle_timeframe: str | None = None
    last_candle_close_time: dt.datetime | None = None
    last_evaluation_recorded_at: dt.datetime | None = None
    last_evaluation_state: str | None = None
    last_evaluation_watch: str | None = None
    last_run_status: str | None = None
    total_gaps: int = 0
    journal_count: int = 0

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]


def worst_status(statuses: list[str]) -> str:
    """The worst of a list of statuses; OK if the list is empty."""
    if not statuses:
        return OK
    return max(statuses, key=lambda s: _STATUS_SEVERITY[s])


def format_age(age: dt.timedelta) -> str:
    """Render a timedelta as a short human string, e.g. "2h", "45m", "3d 2h"."""
    total_seconds = int(age.total_seconds())
    if total_seconds < 0:
        return "0m"
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


# --- Individual checks --------------------------------------------------


def check_database(engine: Engine) -> CheckResult:
    """The database file is reachable and every expected table exists."""
    try:
        existing = set(inspect(engine).get_table_names())
    except Exception as exc:
        return CheckResult("database", FAIL, f"database unreachable: {exc}")

    expected = set(Base.metadata.tables.keys())
    missing = expected - existing
    if missing:
        return CheckResult("database", FAIL, f"missing table(s): {', '.join(sorted(missing))}")
    return CheckResult("database", OK, f"{len(existing)} tables present")


def check_candle_freshness(
    store: TidemarkStore, venue: str, symbol: str, timeframe: str, now: dt.datetime
) -> CheckResult:
    """Age of the newest stored candle for one symbol/timeframe, measured
    against that timeframe's own interval. No data at all is WARN, not
    FAIL - it may simply not be ingested for this symbol yet.
    """
    name = f"candles:{symbol} {timeframe}"
    latest = store.latest_candle(venue, symbol, timeframe)
    if latest is None:
        return CheckResult(name, WARN, f"{symbol} {timeframe}: no candles stored")

    duration = TIMEFRAME_DURATIONS[timeframe]
    age = now - latest.close_time
    detail = f"{symbol} {timeframe}: newest candle {format_age(age)} old"
    if age <= duration * CANDLE_FRESHNESS_OK_MULTIPLE:
        return CheckResult(name, OK, detail)
    if age <= duration * CANDLE_FRESHNESS_WARN_MULTIPLE:
        return CheckResult(name, WARN, detail)
    return CheckResult(name, FAIL, detail)


def check_last_run(command: str, latest_for_command: Run | None, now: dt.datetime) -> CheckResult:
    """The most recent run row for one command.

    FAIL if it's FAILED, WARN if PARTIAL, FAIL if a RUNNING row is older
    than the stale threshold (a crashed run), FAIL if the most recent
    completion is older than the completion threshold (two missed 4H
    cycles). A command that's currently RUNNING and not yet stale is OK -
    that's live evidence the system is active right now.
    """
    name = f"last_run:{command}"
    run = latest_for_command
    if run is None:
        return CheckResult(name, FAIL, f"{command}: no run recorded")

    if run.status == "FAILED":
        return CheckResult(
            name, FAIL, f"{command}: last run FAILED (started {run.started_at.isoformat()})"
        )
    if run.status == "PARTIAL":
        return CheckResult(
            name, WARN, f"{command}: last run PARTIAL (started {run.started_at.isoformat()})"
        )
    if run.status == RUNNING_STATUS:
        age = now - run.started_at
        if age > STALE_RUNNING_THRESHOLD:
            return CheckResult(
                name, FAIL, f"{command}: RUNNING for {format_age(age)} (likely crashed)"
            )
        return CheckResult(name, OK, f"{command}: currently running ({format_age(age)})")

    # COMPLETED
    age = now - run.finished_at
    detail = f"{command}: last completed {format_age(age)} ago"
    if age > LAST_RUN_COMPLETION_FAIL_THRESHOLD:
        return CheckResult(name, FAIL, detail)
    return CheckResult(name, OK, detail)


def check_journal_activity(store: TidemarkStore, now: dt.datetime) -> CheckResult:
    """Age of the single newest journal row, across every asset."""
    latest = store.latest_journal_entry_overall()
    if latest is None:
        return CheckResult("journal_activity", FAIL, "no journal entries recorded")

    age = now - latest.recorded_at
    detail = f"newest journal entry {format_age(age)} old"
    if age <= JOURNAL_ACTIVITY_WARN_THRESHOLD:
        return CheckResult("journal_activity", OK, detail)
    if age <= JOURNAL_ACTIVITY_FAIL_THRESHOLD:
        return CheckResult("journal_activity", WARN, detail)
    return CheckResult("journal_activity", FAIL, detail)


def check_gaps(store: TidemarkStore, venue: str, symbol: str, timeframe: str) -> CheckResult:
    """Count of missing candles for one symbol/timeframe, via the
    existing gap-detection logic (`TidemarkStore.find_gaps`).
    """
    name = f"gaps:{symbol} {timeframe}"
    count = len(store.find_gaps(venue, symbol, timeframe))
    detail = f"{symbol} {timeframe}: {count} gap(s)" if count else f"{symbol} {timeframe}: no gaps"
    if count == 0:
        return CheckResult(name, OK, detail)
    if count <= GAPS_WARN_MAX:
        return CheckResult(name, WARN, detail)
    return CheckResult(name, FAIL, detail)


def check_telegram_config(is_configured: bool) -> CheckResult:
    """Whether Telegram credentials are present. Never sends anything -
    the caller passes in `TelegramNotifier.is_configured`, which is a
    plain property read, not a network call.
    """
    if is_configured:
        return CheckResult("telegram_config", OK, "credentials present")
    return CheckResult(
        "telegram_config",
        WARN,
        "TIDEMARK_TELEGRAM_BOT_TOKEN / TIDEMARK_TELEGRAM_CHAT_ID missing",
    )


# --- Orchestration ------------------------------------------------------


def run_all_checks(
    store: TidemarkStore,
    engine: Engine,
    venue: str,
    symbols: list[str],
    is_telegram_configured: bool,
    rule_version: str,
    now: dt.datetime,
) -> HealthReport:
    """Run every check for the configured symbol universe and fold them
    into one report. `HEALTH_TIMEFRAMES` (not the full stored timeframe
    set) is what freshness/gaps are checked against - see its docstring.

    If the database itself is unreachable or missing tables, every other
    check would either crash (querying a table that doesn't exist) or
    just redundantly report the same underlying problem - so a FAILing
    database check short-circuits the rest and is returned alone.
    """
    database_check = check_database(engine)
    if database_check.status == FAIL:
        return HealthReport(
            status=FAIL,
            generated_at=now,
            rule_version=rule_version,
            checks=[database_check],
        )

    checks: list[CheckResult] = [database_check]

    for symbol in symbols:
        for timeframe in HEALTH_TIMEFRAMES:
            checks.append(check_candle_freshness(store, venue, symbol, timeframe, now))
            checks.append(check_gaps(store, venue, symbol, timeframe))

    latest_runs = store.latest_runs_by_command()
    if not latest_runs:
        checks.append(CheckResult("last_run", FAIL, "no runs recorded for any command"))
    else:
        for command in sorted(latest_runs):
            checks.append(check_last_run(command, latest_runs[command], now))

    checks.append(check_journal_activity(store, now))
    checks.append(check_telegram_config(is_telegram_configured))

    overall = worst_status([c.status for c in checks])

    # Summary data for the heartbeat message - the freshest 4H candle and
    # the newest journal entry across every monitored symbol, plus the
    # single most recent run and total gap count.
    last_candle_symbol = last_candle_timeframe = None
    last_candle_close_time: dt.datetime | None = None
    for symbol in symbols:
        candle = store.latest_candle(venue, symbol, "4h")
        if candle is not None and (
            last_candle_close_time is None or candle.close_time > last_candle_close_time
        ):
            last_candle_close_time = candle.close_time
            last_candle_symbol = symbol
            last_candle_timeframe = "4h"

    latest_journal = store.latest_journal_entry_overall()
    total_gaps = sum(
        len(store.find_gaps(venue, symbol, timeframe))
        for symbol in symbols
        for timeframe in HEALTH_TIMEFRAMES
    )
    latest_run = store.latest_run()

    return HealthReport(
        status=overall,
        generated_at=now,
        rule_version=rule_version,
        checks=checks,
        last_candle_symbol=last_candle_symbol,
        last_candle_timeframe=last_candle_timeframe,
        last_candle_close_time=last_candle_close_time,
        last_evaluation_recorded_at=(
            latest_journal.recorded_at if latest_journal is not None else None
        ),
        last_evaluation_state=(latest_journal.state if latest_journal is not None else None),
        last_evaluation_watch=(latest_journal.watch if latest_journal is not None else None),
        last_run_status=(latest_run.status if latest_run is not None else None),
        total_gaps=total_gaps,
        journal_count=store.count_journal_entries(),
    )
