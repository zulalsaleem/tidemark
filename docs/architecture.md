# Architecture

## Pipeline

```
                 +----------------------------+
                 |  data/exchange.py (ccxt)    |
                 |  public data, no creds      |
                 +--------------+-------------+
                                |  closed candles only
                                v
                 +----------------------------+
                 |  data/ingest.py             |
                 |  fetch + upsert + run log   |
                 +--------------+-------------+
                                |
                                v
                 +----------------------------+
                 |  data/store.py (SQLite)     |
                 |  candles, rejected_candles, |
                 |  runs, swings, levels,      |
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
| `config/settings.py` | Load configuration from environment variables (via `.env` in development). No secrets in code; secret fields are `SecretStr` and never logged; no field may hold an exchange credential. |
| `data/models.py` | SQLAlchemy ORM models: `Candle`, `RejectedCandle`, `Run`, `RunSymbolStat`, `Swing`, `Level`, `ContextRecord`, `JournalEntry`. A `UTCDateTime` type keeps every stored timestamp UTC-aware despite SQLite having no native timezone type. Structural points carry `formed_at`/`confirmed_at`; emitted records carry `rule_version`. |
| `data/store.py` | SQLite persistence: idempotent candle upserts (`UNIQUE(venue, symbol, timeframe, open_time)`), per-row sanity checks with rejection recording, gap detection, and run bookkeeping. |
| `data/exchange.py` | ccxt-backed market-data client. Venue is configuration (default `binanceusdm`; also works with `bitget`, `mexc`, ... unchanged). Constructed with no credentials — `apiKey`/`secret` are asserted empty. Fetches closed candles only, with bounded-retry backoff on transient network errors. |
| `data/timeframes.py` | The stored timeframe set (5m, 15m, 1h, 4h, 1d, 1w) and their durations — the single source of truth shared by the exchange client, store, and CLI. |
| `data/ingest.py` | Orchestrates exchange fetch + store upsert + run recording for `backfill`/`update`. One symbol/timeframe failing never aborts the others; run status is COMPLETED/PARTIAL/FAILED. |
| `core/atr.py` | ATR(14) on 4H — the distance unit used throughout Section 1. |
| `core/swings.py` | Fractal swing detection (`formed_at`/`confirmed_at`). |
| `core/levels.py` | Swing clustering into levels/zones; previous day/week high & low. |
| `core/fib.py` | Retracement leg detection and the 0.500-0.786 Fib zone. |
| `context/htf.py` | Section 1 — evaluates the locked v1.0 decision matrix against 4H closes. |
| `context/mtf.py` | Section 2 — 1H behavior. Currently DRAFT/PENDING; not yet active. |
| `journal/records.py` | Append-only log of every evaluation run, including runs where no setup is found. |
| `notify/telegram.py` | Sends read-only alerts to Telegram. No order-placement code path exists anywhere in this project. |
| `cli.py` | Typer entrypoint wiring the above together. |

## TODO

- Symbol universe: the default set (`TIDEMARK_SYMBOLS`) is BTC, ETH, SOL,
  XRP, and DOGE perpetuals. The rule for selecting a larger universe is
  `NOT_DEFINED` in `docs/rulebook/` — do not invent one. Ask the rulebook
  author before expanding beyond the configured list.

## Design constraints (see `CLAUDE.md` for the full list)

- Closed candles only, everywhere — no calculation may see future information.
- Strategy logic is derived only from `docs/rulebook/`; undefined behavior is
  marked `NOT_DEFINED`, never guessed.
- Every emitted `ContextRecord` carries the `rule_version` that produced it.
- Secrets are read from the environment and never logged.
