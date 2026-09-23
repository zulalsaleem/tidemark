# Tidemark

Tidemark is a rule-based market-structure copilot for cryptocurrency
markets. It evaluates a human-written rulebook against closed candles and
sends read-only alerts to Telegram.

**A human makes every trading decision.** Tidemark never places orders,
never holds private keys, and never has exchange trading permissions. It
only reads market data.

## Read-only / no-execution boundary

This is a hard boundary, not a configuration option:

- Tidemark only ever fetches *closed* candles — no live/in-progress
  candles, and no calculation ever uses future information.
- Tidemark only ever sends messages *to* Telegram. It has no order-placement
  code path, anywhere, in any module.
- Tidemark holds no exchange API secret with trading scope, and no private
  keys of any kind.
- Every alert is informational. The human reading it decides what, if
  anything, to do.

## Pipeline

```
closed candles (exchange, read-only)
        |
        v
  core/ ATR, swings, levels, fib   (pure calculations)
        |
        v
  context/htf.py   Section 1 — 4H context, evaluated at every 4H close
        |
        v
  journal/records.py   EVALUATION -> JOURNAL
                        append-only: one row per evaluation, incl. every WAIT
        |
        v
  journal/changes.py   JOURNAL -> CHANGE DETECTOR
                        pure function; previous journal row + current
                        evaluation -> an alert reason, or None
        |
        v (only when a reason was returned)
  notify/telegram.py   CHANGE DETECTOR -> TELEGRAM
                        read-only, filtered alert
```

The journal is the complete research record; Telegram is a filtered
notification layer on top of it. They are never coupled — a Telegram
failure never prevents or rolls back a journal write, and the journal
write always happens first. See
[docs/adr/0005-journal-and-alert-separation.md](docs/adr/0005-journal-and-alert-separation.md).
`context/mtf.py` (Section 2, 1H behavior) is still DRAFT and not wired
into this pipeline.

Persistence (SQLite via SQLAlchemy) sits alongside this pipeline in
`data/store.py`, holding closed candles, rejected candles, run records,
emitted context records, and journal rows. Swings, levels, and Fib legs
are pure calculations recomputed from stored candles at every evaluation
rather than persisted separately. Market data comes from
`data/exchange.py`, a ccxt-backed client with no exchange credentials —
see
[docs/adr/0002-canonical-market-data-venue.md](docs/adr/0002-canonical-market-data-venue.md)
for the canonical-venue rationale.

## How the rulebook works

The rulebook (`docs/rulebook/`) is the single source of truth for strategy
logic — code implements what it says and nothing else. See
[docs/rulebook/README.md](docs/rulebook/README.md) for how versions,
registration, and locking work. In short:

- Rulebook files are human-authored.
- Version numbers are immutable — a change creates a new version file, it
  never edits an old one in place.
- If something isn't defined in the rulebook, the code marks it
  `NOT_DEFINED` rather than guessing.

## Setup

Requires Python 3.11 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                        # install dependencies
cp .env.example .env           # fill in Telegram config (market data needs no credentials)
uv run tidemark --help         # verify the CLI is wired up
uv run pytest                  # run the test suite
uv run ruff check .            # lint

# fetch and inspect market data (public, read-only)
uv run tidemark data backfill --symbols BTC/USDT:USDT --timeframes 4h --days 7
uv run tidemark data status
uv run tidemark data gaps
```

## Using Section 1

> **PowerShell users:** quote comma-separated `--symbols`/`--timeframes`
> values (`--timeframes "4h,1d,1w"`), or repeat the flag instead
> (`--timeframes 4h --timeframes 1d --timeframes 1w`). Left unquoted,
> PowerShell parses a bare comma list as an array-literal expression
> before the process even starts, and a token like `1d` matches its
> decimal-literal-with-suffix grammar (`d` = `System.Decimal`) — so it
> silently becomes the number `1`, not the string `"1d"`. Bash is
> unaffected.

```bash
# Backfill closed 4H/1D/1W candles for a symbol (read-only, no API key needed
# for public OHLCV on the default exchange).
uv run tidemark data backfill --symbols BTC/USDT:USDT --timeframes 4h,1d,1w --days 180

# Evaluate Section 1's decision matrix against the latest closed 4H candle
# and persist the result.
uv run tidemark context evaluate --symbol BTC/USDT:USDT

# Reproduce exactly what Section 1 would have output as of an earlier 4H
# close, using only data available at that moment.
uv run tidemark context evaluate --symbol BTC/USDT:USDT --as-of 2026-06-01T00:00:00+00:00

# Past evaluations, and a human-readable breakdown of the latest one.
uv run tidemark context history --symbol BTC/USDT:USDT
uv run tidemark context explain --symbol BTC/USDT:USDT
```

## Using the run pipeline

```bash
# Evaluate Section 1 for each symbol, journal the result (a no-op if this
# 4H candle is already journaled), and send a Telegram alert only if the
# change detector finds a reason to. One symbol failing gives PARTIAL,
# not FAILED.
uv run tidemark run --symbols BTC/USDT:USDT

# The full research record for a symbol, newest first — every evaluation,
# including every WAIT.
uv run tidemark journal list --symbol BTC/USDT:USDT

# Only the rows where an alert actually went out, across all symbols.
uv run tidemark journal alerts

# Send one fixed message to prove TIDEMARK_TELEGRAM_BOT_TOKEN /
# TIDEMARK_TELEGRAM_CHAT_ID work. Writes nothing to the journal.
uv run tidemark notify test
```

## Health check and heartbeat

Telegram is silent unless state changes, so silence is ambiguous — it
could mean nothing changed, or it could mean the system died. `health`
makes silence trustworthy by inspecting what's actually in the database:
is it reachable, is candle data fresh, did the last run succeed, is the
journal still being written, are there gaps, and is Telegram even
configured. See
[docs/adr/0006-health-check-design.md](docs/adr/0006-health-check-design.md)
for why the thresholds are what they are.

```bash
# Every check, human-readable. Exit code is 0/1/2 for OK/WARN/FAIL, so
# systemd (or any monitor) can treat a degraded state as a failure.
uv run tidemark health check

# Same checks, machine-readable — for future monitoring.
uv run tidemark health check --json

# Run the checks and send one Telegram summary. The only `health`
# command that sends anything; never writes to the journal, since a
# heartbeat is a system-status message, not a research observation.
uv run tidemark health heartbeat
```

## Project status

**Phase 1 + 2 + 3 + 4B — data layer, Section 1 HTF context engine, the
journal/alert pipeline, and health/heartbeat.** The market-data pipeline
is implemented: a ccxt-backed exchange client (public data only, no
credentials, closed candles only), idempotent SQLite storage with
per-row sanity checks, rejected-candle recording, gap detection, and run
bookkeeping, and `tidemark data backfill/update/gaps/status` CLI
commands. Section 1 of the rulebook (HTF context, locked at v1.1) is
fully implemented on top of that: ATR(14), fractal swing detection,
horizontal levels (swing clusters + previous day/week high/low),
Fibonacci retracement legs, and the Section 1 state machine and 9-row
decision matrix, all recalculated at every 4H close. `tidemark run` ties
it together: evaluate -> append to the journal (the complete research
record, one row per evaluation, including every WAIT) -> a pure change
detector -> a filtered, read-only Telegram alert on change only.
`tidemark health check/heartbeat` proves the unattended system is
actually alive, independent of whether anything alert-worthy has
happened. `tidemark context evaluate/history/explain` still drive the
engine standalone, including `--as-of` for point-in-time reproduction.
Section 2 (1H behavior) is still in draft and unimplemented.

See [docs/architecture.md](docs/architecture.md) for module responsibilities
and [docs/adr/](docs/adr/) for architecture decision records.
