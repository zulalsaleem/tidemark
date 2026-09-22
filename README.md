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
`data/store.py`, holding candles, swings, levels, and emitted context
records.

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
cp .env.example .env           # fill in Telegram/exchange config
uv run tidemark --help         # verify the CLI is wired up
uv run pytest                  # run the test suite
uv run ruff check .            # lint
```

## Project status

**Phase 0 — repository skeleton.** Tooling, CI, data models, and
documentation are in place. Strategy logic (indicators, swing/level
detection, rulebook evaluation, data fetching, Telegram sending) is not
yet implemented — modules raise `NotImplementedError` where that logic
will go. Section 1 of the rulebook (HTF context) is locked at v1.0;
Section 2 (1H behavior) is still in draft.

See [docs/architecture.md](docs/architecture.md) for module responsibilities
and [docs/adr/](docs/adr/) for architecture decision records.
