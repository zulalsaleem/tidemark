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
  context/mtf.py   Section 2 — 1H behavior (DRAFT, not yet active)
        |
        v
  journal/records.py   append-only observation log (every run, incl. "no setups")
        |
        v
  notify/telegram.py   read-only alert  ->  Telegram
```

Persistence (SQLite via SQLAlchemy) sits alongside this pipeline in
`data/store.py`, holding closed candles, rejected candles, run records,
and emitted context records. Swings, levels, and Fib legs are pure
calculations recomputed from stored candles at every evaluation rather
than persisted separately. Market data comes from `data/exchange.py`, a
ccxt-backed client with no exchange credentials — see
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

## Project status

**Phase 1 + Phase 2 — data layer and Section 1 HTF context engine.** The
market-data pipeline is implemented: a ccxt-backed exchange client
(public data only, no credentials, closed candles only), idempotent
SQLite storage with per-row sanity checks, rejected-candle recording, gap
detection, and run bookkeeping, and `tidemark data backfill/update/gaps/
status` CLI commands. Section 1 of the rulebook (HTF context, locked at
v1.0) is fully implemented on top of that: ATR(14), fractal swing
detection, horizontal levels (swing clusters + previous day/week
high/low), Fibonacci retracement legs, and the Section 1 state machine
and 9-row decision matrix, all recalculated at every 4H close and
persisted idempotently. `tidemark context evaluate/history/explain` drive
the engine from the CLI, including `--as-of` for point-in-time
reproduction. Section 2 (1H behavior) is still in draft and
unimplemented. Telegram alert delivery and the end-to-end `run` pipeline
are not yet wired up.

See [docs/architecture.md](docs/architecture.md) for module responsibilities
and [docs/adr/](docs/adr/) for architecture decision records.
