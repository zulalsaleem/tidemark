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
                 |  runs, context records      |
                 |  (swings/levels/fib have     |
                 |  ORM tables but are          |
                 |  recomputed each evaluation) |
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
                 |  Section 1 (4H, LOCKED v1.1)|
                 |  recalculated at every       |
                 |  4H close                    |
                 +--------------+-------------+
                                |  ContextRecord (every evaluation)
                                v
                 +----------------------------+
                 |  journal/records.py         |
                 |  EVALUATION -> JOURNAL       |
                 |  append-only: one row per    |
                 |  evaluation, incl. every WAIT|
                 +--------------+-------------+
                                |  previous row + current evaluation
                                v
                 +----------------------------+
                 |  journal/changes.py         |
                 |  JOURNAL -> CHANGE DETECTOR  |
                 |  pure function -> reason|None|
                 +--------------+-------------+
                                |  only when a reason was returned
                                v
                 +----------------------------+
                 |  notify/telegram.py         |
                 |  CHANGE DETECTOR -> TELEGRAM |
                 |  read-only, filtered alert   |
                 +----------------------------+

                 +----------------------------+
                 |  health/checks.py           |
                 |  reads data/store.py only    |
                 |  (own side branch, not in    |
                 |  the evaluate/journal/alert  |
                 |  flow above) -> `health check`|
                 |  or, via notify/telegram.py's |
                 |  build_heartbeat_message,     |
                 |  `health heartbeat`           |
                 +----------------------------+

                 +----------------------------+
                 |  context/mtf.py             |
                 |  Section 2 (1H, PROVISIONAL |
                 |  - OBSERVATION ONLY v0.1)    |
                 |  reads journal/records.py's  |
                 |  output as-of each 1H close; |
                 |  own side branch, not in the |
                 |  evaluate/journal/alert flow |
                 |  above - no path back into it|
                 +--------------+-------------+
                                |  ObservationResult (every 1H close under WATCH)
                                v
                 +----------------------------+
                 |  journal/observe_pipeline.py|
                 |  -> data/store.py            |
                 |  observations table          |
                 |  (append-only; no notifier   |
                 |  parameter exists at all)    |
                 +----------------------------+
```

`journal/pipeline.py` orchestrates the last three stages for `tidemark
run`, mirroring `data/ingest.py`'s per-symbol failure isolation and
COMPLETED/PARTIAL/FAILED run status. See
[ADR 0005](adr/0005-journal-and-alert-separation.md) for why the journal
and Telegram stages are never coupled, and
[ADR 0006](adr/0006-health-check-design.md) for why `health/checks.py`
is a separate read-only side branch rather than part of that pipeline.

`context/mtf.py` (Section 2, 1H behavior) is a second, parallel side
branch: it reads Section 1's journal as input (the latest row as of
each 1H close) but has no path back into the evaluate/journal/change-
detector/Telegram flow above. `journal/observe_pipeline.py` orchestrates
`tidemark observe run` the same way `journal/pipeline.py` orchestrates
`tidemark run`, with one deliberate difference: it takes no notifier
parameter at all, so there is no code path by which it could reach
Telegram. See
[ADR 0007](adr/0007-section-2-observation-only.md) for why Section 2
ships as measurement rather than signal.

## Module responsibilities

| Module | Responsibility |
| --- | --- |
| `config/settings.py` | Load configuration from environment variables (via `.env` in development). No secrets in code; secret fields are `SecretStr` and never logged; no field may hold an exchange credential. |
| `data/models.py` | SQLAlchemy ORM models: `Candle`, `RejectedCandle`, `Run`, `RunSymbolStat`, `Swing`, `Level`, `ContextRecord`, `JournalEntry`, `Observation`, `MarketRegistry`, `UniverseSnapshot`, `UniverseSnapshotRow` (Phase 6, Merge 1 — schema only, see [ADR 0009](adr/0009-universe-selection-architecture.md)). A `UTCDateTime` type keeps every stored timestamp UTC-aware despite SQLite having no native timezone type. Structural points carry `formed_at`/`confirmed_at`; emitted records carry `rule_version`. `Candle` gained no column for this merge. |
| `data/store.py` | SQLite persistence: idempotent candle upserts (`UNIQUE(venue, symbol, timeframe, open_time)`), per-row sanity checks with rejection recording, gap detection, run bookkeeping, idempotent context-record upserts (keyed on asset + evaluated_at + rule_version), append-only journal writes (`UNIQUE(asset, evaluated_at, rule_version)` — a repeat is a no-op, never a second row or an update) plus the one narrow exception, `record_alert_outcome`, append-only observation writes (`UNIQUE(asset, evaluated_at, rule_version)`, same no-op-on-repeat contract), idempotent `market_registry` upserts (keyed on venue + symbol), and atomic `universe_snapshot`/`universe_snapshot_row` writes (`UNIQUE(venue, snapshot_at, methodology_version)` — unlike the journal/observation tables, a duplicate here raises rather than no-ops). No selection/eligibility logic lives here yet. |
| `data/exchange.py` | ccxt-backed market-data client. Venue is configuration (default `binanceusdm`; also works with `bitget`, `mexc`, ... unchanged). Constructed with no credentials — `apiKey`/`secret` are asserted empty. Fetches closed candles only, with bounded-retry backoff on transient network errors. |
| `data/timeframes.py` | The stored timeframe set (5m, 15m, 1h, 4h, 1d, 1w) and their durations — the single source of truth shared by the exchange client, store, and CLI. |
| `data/ingest.py` | Orchestrates exchange fetch + store upsert + run recording for `backfill`/`update`. One symbol/timeframe failing never aborts the others; run status is COMPLETED/PARTIAL/FAILED. |
| `core/atr.py` | ATR(14) on 4H via Wilder's smoothing — the distance unit used throughout Section 1. |
| `core/swings.py` | Fractal swing detection (N=2), each swing storing `formed_at`/`confirmed_at` separately; `confirmed_swings_as_of` is the only way downstream code sees a swing. |
| `core/levels.py` | Swing clustering into support/resistance levels/zones (0.5x ATR cluster distance, 120-candle lookback); previous day/week high & low. |
| `core/fib.py` | Retracement leg detection (min 2x ATR), the 0.500-0.786 Fib zone, and invalidation on a 4H close beyond the leg start. |
| `context/htf.py` | Section 1 — full state machine (bias, break, broken-state persistence) and the 9-row decision matrix against 4H closes; see [ADR 0003](adr/0003-rulebook-as-single-source-of-truth.md). Level role (support/resistance) is computed fresh each evaluation via `core.levels.level_role`, per v1.1's RULE 1.7a — see [ADR 0004](adr/0004-dynamic-level-role.md). |
| `context/mtf.py` | Section 2 — 1H behavior (rulebook status `PROVISIONAL — OBSERVATION ONLY`, v0.1). A full deterministic replay over closed 1H candles, gated by the as-of Section 1 WATCH record: reaction tiers R1/R2/R3 (Stage A), higher-low/lower-high-then-close structure confirmation (Stage B), zone-close failure, 12-candle reaction expiry, and `HTF_CONTEXT_INVALIDATED` on any Section 1 state/watch/grade change. Reuses Section 1's zone bounds and holding definition rather than a second tolerance. Produces measurement rows only — see [ADR 0007](adr/0007-section-2-observation-only.md). |
| `journal/records.py` | Builds the append-only journal row (`JournalEntry`) from an evaluated `ContextRecord`. One row per evaluation, including every WAIT — "no setups found" is a successful run, not a failure. |
| `journal/changes.py` | Pure change detector: previous journal row + current evaluation -> an alert reason or `None`. No I/O. See [ADR 0005](adr/0005-journal-and-alert-separation.md) for why this stays decoupled from the journal write and from Telegram. |
| `journal/pipeline.py` | Orchestrates `tidemark run`: evaluate -> journal -> change detector -> Telegram, per symbol, with per-symbol failure isolation and the run lifecycle (COMPLETED/PARTIAL/FAILED), mirroring `data/ingest.py`'s pattern. |
| `journal/observe_pipeline.py` | Orchestrates `tidemark observe run`: evaluate Section 2 -> journal every returned row, per symbol, with the same per-symbol failure isolation and run lifecycle as `journal/pipeline.py`. Takes no notifier parameter at all — there is no code path by which this could reach Telegram. |
| `notify/telegram.py` | Sends read-only, send-only alerts to Telegram (no polling/webhook/commands). Builds the fixed alert message shape and the heartbeat summary shape (`build_heartbeat_message`), with bounded retry on transient network errors; a failure or missing credentials is logged and skipped, never raised. Classifies a failed send as a connection failure (never reached Telegram) vs an HTTP error response (`TelegramSendError`), so `notify test`/callers can report which. No order-placement code path exists anywhere in this project. |
| `health/checks.py` | Pure health checks reading only `data/store.py`: database reachability/schema, candle freshness, last run per command, journal activity, gap counts, Telegram config presence. Never sends anything itself — see [ADR 0006](adr/0006-health-check-design.md). |
| `cli.py` | Typer entrypoint: `data backfill/update/gaps/status` manage market data; `context evaluate` (with optional `--as-of`)/`history`/`explain` drive Section 1 standalone; `run` drives the full journal/alert pipeline; `journal list`/`alerts` read the research record; `notify test` proves Telegram credentials work without touching the journal; `health check` (human-readable or `--json`, exit 0/1/2 for OK/WARN/FAIL) and `health heartbeat` (the only `health` command that sends, and never journals) prove the unattended system is alive; `observe run`/`list`/`stats` drive Section 2 - measurement only, no alerts; `universe registry`/`snapshots`/`show` (Phase 6, Merge 1) are read-only inspection of the new universe tables - they report an empty database gracefully rather than erroring, and take no `--symbols` flag of their own. |

## Universe selection (Phase 6)

Phase 6 adds a universe-selection layer on top of the pipeline above,
built as three separate tables so "not selected" can never be confused
with "no data existed" — see
[ADR 0009](adr/0009-universe-selection-architecture.md) for the full
rationale:

1. **`market_registry`** — what the venue has ever contained per
   `(venue, symbol)`: when candle data and venue listing were first/last
   seen, and `status` (ACTIVE / STALE / ABSENT_FROM_VENUE). A factual
   record, not a decision.
2. **`universe_snapshot`** / **`universe_snapshot_row`** — what a
   selection methodology chose, at a point in time. Every ranked symbol
   is stored, selected or not, with its `exclusion_reason` if excluded.
3. **Observation records** (`journal_entries` / `observations`, existing,
   unchanged) — what Tidemark actually evaluated.

`TIDEMARK_SYMBOLS` / `settings.symbol_list()` remains the only symbol
source every pipeline (`tidemark run`, `tidemark observe run`,
`tidemark health check`) reads. This layer does not wire into that yet.

The work is sequenced as three merges:

- **Merge 1 (this one)** — schema for all three new tables, read/write
  store methods, and read-only `tidemark universe registry/snapshots/show`
  CLI commands. No selection or eligibility logic exists yet.
- **Merge 2** — eligibility computation (UNIV-01: Section 1 demonstrably
  exiting `INSUFFICIENT_STRUCTURE` at least once, never a calendar-history
  requirement) and the derived quote-volume metric
  (`MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D` — UNIV-02/07), populating
  `market_registry`'s `section1_first_usable_at`.
- **Merge 3** — snapshot generation, the daily refresh schedule
  (UNIV-04), and — as its own explicit, separately-reviewed decision —
  wiring a snapshot's selection into what the pipelines actually
  evaluate.

Every merge is checked against `UNIVERSE_AS_OF_INVARIANT` (ADR 0009):
generating a snapshot from data truncated at time T must equal generating
it as-of T from the full database, for every field knowable at T — the
same look-ahead guard `context/htf.py` and `context/mtf.py` already rely
on.

## TODO

- Symbol universe: the default set (`TIDEMARK_SYMBOLS`) is BTC, ETH, SOL,
  XRP, and DOGE perpetuals. The rule for selecting a larger universe is
  `NOT_DEFINED` in `docs/rulebook/` — do not invent one. Ask the rulebook
  author before expanding beyond the configured list. The universe-
  selection layer above (Phase 6) is scaffolding for eventually answering
  this; Merge 1 alone does not answer it or change `TIDEMARK_SYMBOLS`.

## Design constraints (see `CLAUDE.md` for the full list)

- Closed candles only, everywhere — no calculation may see future information.
- Strategy logic is derived only from `docs/rulebook/`; undefined behavior is
  marked `NOT_DEFINED`, never guessed.
- Every emitted `ContextRecord` carries the `rule_version` that produced it.
- Secrets are read from the environment and never logged.
- The journal and Telegram are never coupled: a Telegram failure must never
  prevent or roll back a journal write. The journal write always happens
  first and is committed before the change detector or Telegram are ever
  invoked.
- `health check` never sends anything and a Telegram failure never affects
  its exit code; `health heartbeat` is the only health command that sends,
  and it never writes to the journal — a heartbeat is a system-status
  message, not a research observation.
