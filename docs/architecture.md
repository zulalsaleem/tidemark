# Architecture

## Pipeline

```
                 +----------------------------+
                 |  exchange (read-only data) |
                 +--------------+-------------+
                                |  closed candles only
                                v
                 +----------------------------+
                 |  data/store.py (SQLite)     |
                 |  candles, swings, levels,   |
                 |  context records            |
                 +--------------+-------------+
                                |
                                v
        +---------------------------------------------+
        |  core/                                        |
        |  atr.py -> swings.py -> levels.py -> fib.py    |
        +----------------------+------------------------+
                                |
                                v
                 +----------------------------+
                 |  context/htf.py             |
                 |  Section 1 (4H, LOCKED v1.0)|
                 |  recalculated at every       |
                 |  4H close                    |
                 +--------------+-------------+
                                |  output record
                                v
                 +----------------------------+
                 |  context/mtf.py             |
                 |  Section 2 (1H, DRAFT)      |
                 +--------------+-------------+
                                |
                                v
                 +----------------------------+
                 |  journal/records.py         |
                 |  append-only observation     |
                 |  log (every run, incl.       |
                 |  "no setups found")          |
                 +--------------+-------------+
                                |
                                v
                 +----------------------------+
                 |  notify/telegram.py         |
                 |  read-only alert            |
                 +----------------------------+
```

## Module responsibilities

| Module | Responsibility |
| --- | --- |
| `config/settings.py` | Load configuration from environment variables (via `.env` in development). No secrets in code; secret fields are `SecretStr` and never logged. |
| `data/models.py` | SQLAlchemy ORM models: `Candle`, `Swing`, `Level`, `ContextRecord`, `JournalEntry`. Structural points carry `formed_at`/`confirmed_at`; emitted records carry `rule_version`. |
| `data/store.py` | SQLite persistence: engine/session setup and CRUD against the above models. |
| `data/exchange.py` | Read-only market-data client. Fetches closed candles only; no trading endpoints are ever called. |
| `core/atr.py` | ATR(14) on 4H — the distance unit used throughout Section 1. |
| `core/swings.py` | Fractal swing detection (`formed_at`/`confirmed_at`). |
| `core/levels.py` | Swing clustering into levels/zones; previous day/week high & low. |
| `core/fib.py` | Retracement leg detection and the 0.500-0.786 Fib zone. |
| `context/htf.py` | Section 1 — evaluates the locked v1.0 decision matrix against 4H closes. |
| `context/mtf.py` | Section 2 — 1H behavior. Currently DRAFT/PENDING; not yet active. |
| `journal/records.py` | Append-only log of every evaluation run, including runs where no setup is found. |
| `notify/telegram.py` | Sends read-only alerts to Telegram. No order-placement code path exists anywhere in this project. |
| `cli.py` | Typer entrypoint wiring the above together. |

## Design constraints (see `CLAUDE.md` for the full list)

- Closed candles only, everywhere — no calculation may see future information.
- Strategy logic is derived only from `docs/rulebook/`; undefined behavior is
  marked `NOT_DEFINED`, never guessed.
- Every emitted `ContextRecord` carries the `rule_version` that produced it.
- Secrets are read from the environment and never logged.
