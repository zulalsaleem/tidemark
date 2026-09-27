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

                 +----------------------------+
                 |  market_intel/               |
                 |  Coinalyze derivatives data   |
                 |  (Phase 8, Merge 1 + 2)       |
                 |  no connection to anything    |
                 |  above - reads HTTP, writes   |
                 |  nothing, imports nothing     |
                 |  from data/exchange.py,       |
                 |  context/, journal/, replay/  |
                 |  -> `tidemark intel market`   |
                 |  -> `tidemark intel bot`      |
                 |     (own Telegram client,     |
                 |     never notify/telegram.py) |
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

`market_intel/` (Phase 8) is a third, fully disconnected side branch:
unlike `context/mtf.py`, it doesn't even read the journal — it has no
dependency on the pipeline above at all, in either direction. See
[ADR 0011](adr/0011-market-intelligence-layer.md) for why this
isolation is enforced by a static-analysis test rather than left to
discipline.

## Module responsibilities

| Module | Responsibility |
| --- | --- |
| `config/settings.py` | Load configuration from environment variables (via `.env` in development). No secrets in code; secret fields are `SecretStr` and never logged; no field may hold an exchange credential. |
| `data/models.py` | SQLAlchemy ORM models: `Candle`, `RejectedCandle`, `Run`, `RunSymbolStat`, `Swing`, `Level`, `ContextRecord`, `JournalEntry`, `Observation`, `MarketRegistry`, `UniverseSnapshot`, `UniverseSnapshotRow` (Phase 6, see [ADR 0009](adr/0009-universe-selection-architecture.md)). A `UTCDateTime` type keeps every stored timestamp UTC-aware despite SQLite having no native timezone type. Structural points carry `formed_at`/`confirmed_at`; emitted records carry `rule_version`. `Candle` gained no column across any Phase 6 merge. `UniverseSnapshot.k` (assessment-set size) and `UniverseSnapshotRow.eligible`'s nullability (PART C: `NULL` means "not assessed," never coerced to `False`) were added/amended in Merge 2B, safe because nothing had written to either table before it. `MarketRegistry.underlying_type`/`asset_class`/`classification_source`/`classification_as_of`/`classification_methodology_version` (UNIV-08), mirrored onto `UniverseSnapshotRow`, were added via a manual `ALTER TABLE` against the real database — the first Phase 6 schema change to land on tables that already held real production rows (727 each), not empty ones. `Run.symbol_source`/`Run.symbol_source_snapshot_id` (Phase 6, Merge 3) record which symbol source a `run`/`observe` execution used (`EXPLICIT`/`SNAPSHOT`/`TIDEMARK_SYMBOLS_FALLBACK`, see `data/symbol_source.py`) and the snapshot id when one was used; nullable, unset by every other command, added the same way UNIV-08 added its columns — a manual `ALTER TABLE` against the real `runs` table, which already held real rows. |
| `data/store.py` | SQLite persistence: idempotent candle upserts (`UNIQUE(venue, symbol, timeframe, open_time)`), per-row sanity checks with rejection recording, gap detection, run bookkeeping (`start_run` takes optional `symbol_source`/`symbol_source_snapshot_id`, Phase 6 Merge 3), idempotent context-record upserts (keyed on asset + evaluated_at + rule_version), append-only journal writes (`UNIQUE(asset, evaluated_at, rule_version)` — a repeat is a no-op, never a second row or an update) plus the one narrow exception, `record_alert_outcome`, append-only observation writes (`UNIQUE(asset, evaluated_at, rule_version)`, same no-op-on-repeat contract), a full-row `market_registry` upsert (keyed on venue + symbol) plus narrow, field-scoped writes that never clobber each other — `record_market_listing` (listing fields), `record_candle_coverage` (candle-coverage fields), `record_section1_eligibility` (eligibility-cache fields, Merge 2B), `record_classification` (UNIV-08 — set-once, never overwritten, mirroring `first_seen_in_venue_list_at`) — `mark_absent_from_venue` (status only, never deletes a row), `count_rejected_candles` (Merge 2B, the `INVALID_OHLCV` eligibility check, `as_of`-filterable), and atomic `universe_snapshot`/`universe_snapshot_row` writes (`UNIQUE(venue, snapshot_at, methodology_version)` — unlike the journal/observation tables, a duplicate here raises rather than no-ops). |
| `data/exchange.py` | ccxt-backed market-data client. Venue is configuration (default `binanceusdm`; also works with `bitget`, `mexc`, ... unchanged). Constructed with no credentials — `apiKey`/`secret` are asserted empty. Fetches closed candles only, with bounded-retry backoff on transient network errors. `list_perpetual_symbols` (Phase 6, Merge 2A) is a second, separate read-only call — ccxt's unified `load_markets`, filtered to active/`swap`/quote-currency — added alongside candle fetching without changing it. `MarketListing.underlying_type` (UNIV-08) reads `market['info']['underlyingType']` from that same response — no new call. |
| `data/timeframes.py` | The stored timeframe set (5m, 15m, 1h, 4h, 1d, 1w) and their durations — the single source of truth shared by the exchange client, store, and CLI. |
| `data/ingest.py` | Orchestrates exchange fetch + store upsert + run recording for `backfill`/`update`. One symbol/timeframe failing never aborts the others; run status is COMPLETED/PARTIAL/FAILED. `run_backfill`/`run_update` take an optional `on_outcome` progress callback (Phase 6, Merge 2A) — backward compatible, existing callers unaffected; reused unmodified by both `data/universe_backfill.py` and `data/universe_snapshot.py`'s own 4H backfill step. |
| `data/discover.py` | (Phase 6, Merge 2A, PART A) `tidemark universe discover`'s orchestrator: lists the venue's active USDT perpetuals via `ExchangeClient.list_perpetual_symbols` and syncs `market_registry` — upserts every currently-listed symbol ACTIVE, marks any previously-registered symbol no longer listed ABSENT_FROM_VENUE (never deleted). One listing call, so COMPLETED/FAILED only, never PARTIAL. Also classifies each listing's asset class (UNIV-08) via `data/asset_class.py` and persists it once via `record_classification`. |
| `data/asset_class.py` | (Phase 6, UNIV-08) `classify_underlying_type`: pure, total mapping from Binance's raw `underlyingType` to `asset_class` (`CRYPTO`/`NON_CRYPTO`/`NON_ELIGIBLE_INDEX`/`UNKNOWN`) plus an `exclusion_reason`. Deterministic; ignores symbol name, price, volume, and `underlyingSubType` entirely. A `underlyingType` outside the enumerated known set fails closed to `UNKNOWN`, never silently folded into `NON_CRYPTO`. |
| `data/universe_backfill.py` | (Phase 6, Merge 2A, PART B) `tidemark universe backfill`'s orchestrator: backfills `1d` candles for every ACTIVE `market_registry` symbol via the unmodified `data/ingest.py` path, then records each symbol's actual stored candle range back onto its registry row via `record_candle_coverage`. No eligibility or metric computation. |
| `data/universe_metric.py` | (Phase 6, Merge 2B, PART A) `MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D`: `derived_quote_volume = base_volume × close` per closed daily candle, median over the trailing 30 that closed by `as_of` — never a partial window. Pure, no store access. Type `DERIVED`, per UNIV-02/07; changing the formula is a new `methodology_version`, never a silent redefinition. |
| `data/universe_eligibility.py` | (Phase 6, Merge 2B, PART C) `assess_section1_eligibility`: runs the LOCKED Section 1 v1.1 engine unmodified via `replay.report.replay_section1`, looking for the first evaluation that exits `INSUFFICIENT_STRUCTURE`. Three cheaper data-quality gates run first, in order: `INSUFFICIENT_4H_HISTORY` (< 14 4H candles — ATR(14)'s own mechanical floor), `INVALID_OHLCV` (any rejected 4H candle on record), `DATA_GAPS` (a gap in the 4H sequence). Pure, no store access — trusts its caller's already-truncated candle lists completely, which is what keeps it lookahead-safe by construction. |
| `data/universe_snapshot.py` | (Phase 6, Merge 2B, PARTS B/D; UNIV-08) `generate_universe_snapshot`: the rank-first orchestration fixed by PART B — rank every ACTIVE symbol by the metric (`rank_symbols`: symbols with no metric sort last, ties broken alphabetically), take the top K=50, backfill 4H for those 50 if missing (FORWARD only), assess eligibility for those 50, select the top N=30 eligible by rank. Every ranked symbol gets a row (`NOT_ASSESSED`/`eligible=NULL` below K). Before any of that, each symbol's *persisted* `market_registry` asset class is checked (UNIV-08) — only `CRYPTO` can reach the volume/rank/eligibility checks at all; a symbol already known non-crypto is also skipped by the 4H backfill step, though this changes no assessment-set membership, N, or K. FORWARD (`as_of=None`) may reach the exchange and caches a found `section1_first_usable_at` onto `market_registry`; BACKFILLED (`as_of` given) never touches the network and never writes that cache, and never reads live classification either — see [ADR 0009](adr/0009-universe-selection-architecture.md)'s Merge 2B and UNIV-08 sections. `METHODOLOGY_VERSION` moved to `"universe-v2"` for UNIV-08; the pre-UNIV-08 snapshot keeps `"universe-v1"` forever, untouched. |
| `data/symbol_source.py` | (Phase 6, Merge 3) `resolve_symbols`: the fixed resolution order every observer pipeline shares — explicit `--symbols` (`EXPLICIT`), else the latest `universe_snapshot` for the venue if it exists, is within the staleness threshold (`TIDEMARK_UNIVERSE_STALENESS_HOURS`, default 48h), and selected at least one symbol (`SNAPSHOT`, rank-ordered), else `settings.symbol_list()` (`TIDEMARK_SYMBOLS_FALLBACK`, with a warning naming the reason). Pure, store-backed logic; never raises — a missing/stale/empty snapshot always falls back rather than crashing the run. |
| `data/universe_sync.py` | (Phase 6, Merge 3) `run_universe_sync`/`selected_symbols`: backfills 1H/4H/1D/1W for the latest snapshot's currently SELECTED symbols only, reusing `data/ingest.py`'s `run_backfill` unmodified (same chunked upsert, same COMPLETED/PARTIAL/FAILED per-symbol isolation). Applies no staleness fallback of its own — it prepares candles for whatever the latest snapshot says; deciding whether that snapshot is still trustworthy is `resolve_symbols`'s job alone, at observation time. |
| `core/atr.py` | ATR(14) on 4H via Wilder's smoothing — the distance unit used throughout Section 1. |
| `core/swings.py` | Fractal swing detection (N=2), each swing storing `formed_at`/`confirmed_at` separately; `confirmed_swings_as_of` is the only way downstream code sees a swing. |
| `core/levels.py` | Swing clustering into support/resistance levels/zones (0.5x ATR cluster distance, 120-candle lookback); previous day/week high & low. |
| `core/fib.py` | Retracement leg detection (min 2x ATR), the 0.500-0.786 Fib zone, and invalidation on a 4H close beyond the leg start. |
| `context/htf.py` | Section 1 — full state machine (bias, break, broken-state persistence) and the 9-row decision matrix against 4H closes; see [ADR 0003](adr/0003-rulebook-as-single-source-of-truth.md). Level role (support/resistance) is computed fresh each evaluation via `core.levels.level_role`, per v1.1's RULE 1.7a — see [ADR 0004](adr/0004-dynamic-level-role.md). |
| `context/mtf.py` | Section 2 — 1H behavior (rulebook status `PROVISIONAL — OBSERVATION ONLY`, v0.1). A full deterministic replay over closed 1H candles, gated by the as-of Section 1 WATCH record: reaction tiers R1/R2/R3 (Stage A), higher-low/lower-high-then-close structure confirmation (Stage B), zone-close failure, 12-candle reaction expiry, and `HTF_CONTEXT_INVALIDATED` on any Section 1 state/watch/grade change. Reuses Section 1's zone bounds and holding definition rather than a second tolerance. Produces measurement rows only — see [ADR 0007](adr/0007-section-2-observation-only.md). |
| `journal/records.py` | Builds the append-only journal row (`JournalEntry`) from an evaluated `ContextRecord`. One row per evaluation, including every WAIT — "no setups found" is a successful run, not a failure. |
| `journal/changes.py` | Pure change detector: previous journal row + current evaluation -> an alert reason or `None`. No I/O. See [ADR 0005](adr/0005-journal-and-alert-separation.md) for why this stays decoupled from the journal write and from Telegram. |
| `journal/pipeline.py` | Orchestrates `tidemark run`: evaluate -> journal -> change detector -> Telegram, per symbol, with per-symbol failure isolation and the run lifecycle (COMPLETED/PARTIAL/FAILED), mirroring `data/ingest.py`'s pattern. `run_pipeline` takes optional `symbol_source`/`symbol_source_snapshot_id` (Phase 6, Merge 3), recorded on the run's row via `start_run` so the resolution the caller already made is traceable after the fact. |
| `journal/observe_pipeline.py` | Orchestrates `tidemark observe run`: evaluate Section 2 -> journal every returned row, per symbol, with the same per-symbol failure isolation and run lifecycle as `journal/pipeline.py`, and the same `symbol_source`/`symbol_source_snapshot_id` recording (Phase 6, Merge 3). Takes no notifier parameter at all — there is no code path by which this could reach Telegram. |
| `market_intel/client.py` | (Phase 8, Merge 1) `CoinalyzeClient`: raw HTTP wrapper over Coinalyze's public REST API (`urllib`, no new dependency — same pattern as `notify/telegram.py`). Tracks call timestamps for observability against the documented 40-calls/minute-per-key limit; on a 429 response, respects `Retry-After` with a bounded backoff and raises `RateLimitedError` rather than retrying indefinitely. Raises `MissingApiKeyError` at construction if `TIDEMARK_COINALYZE_API_KEY` is unset. |
| `market_intel/symbols.py` | (Phase 8, Merge 1) `to_coinalyze_symbol`: mechanical ccxt-to-Coinalyze symbol mapping for Binance (`<BASE><QUOTE>_PERP.A`), verified against a real `/future-markets` listing including a CJK-ticker meme coin in our universe — Unicode tickers are preserved verbatim. An unmapped venue raises `UnsupportedVenueError`. |
| `market_intel/clamping.py` | (Phase 8, Merge 1) `closed_period`: computes the latest fully-elapsed period for any Coinalyze interval, since Coinalyze itself does not truncate a history request to closed periods. Pure function, no I/O — see [ADR 0011](adr/0011-market-intelligence-layer.md). |
| `market_intel/future_markets.py` | (Phase 8, Merge 1) `FutureMarketsCache`: fetches and caches `/future-markets` (24h TTL) and is what lets the rest of the package distinguish `MARKET_NOT_FOUND` (not in the listing) from `NO_DATA` (listed, but the relevant `has_*_data` flag is false, or the live call comes back empty) from `OK`. |
| `market_intel/models.py` | (Phase 8, Merge 1) The normalized `MarketIntelSnapshot` and its per-metric dataclasses (`PointInTimeMetric`, `ClosedPeriodMetric`, `LongShortRatioMetric`, `LiquidationsMetric`). Every metric carries its own status, unit, and window (or point-in-time update timestamp); a missing value is always a non-`OK` status with `value=None`, never a rendered zero. |
| `market_intel/service.py` | (Phase 8, Merge 1) `fetch_market_intel`: orchestrates symbol mapping, future-markets validation, closed-period clamping, and the per-metric calls into one `MarketIntelSnapshot`. Always passes `convert_to_usd=true` where Coinalyze supports it; fixes a single 1-hour window for every closed-period metric. |
| `market_intel/telegram_client.py` | (Phase 8, Merge 2) `TelegramBotClient`: a second, independent Bot API client (`getUpdates`/`sendMessage` only) — deliberately not `notify/telegram.py`, which transitively imports `context`/`data.models`/`journal` to build Section 1/2 alert text. Bot token is `SecretStr`, passed only in the request URL (the Bot API's own auth shape), never logged. |
| `market_intel/telegram_render.py` | (Phase 8, Merge 2) `render_snapshot`: Telegram-formatted rendering of a `MarketIntelSnapshot` for `/coin`, with its own footer disclaimer. A second, independent renderer from `cli.py`'s own — the same "duplicate rather than couple" tradeoff ADR 0010 made for `evidence.py` versus `replay/report.py`. |
| `market_intel/bot_state.py` | (Phase 8, Merge 2) `BotStateStore`: persists the Telegram `getUpdates` offset to a flat JSON file (atomic write, temp file + rename) — no relationship to `tidemark.db` at all, so a restart never replays or skips a message. |
| `market_intel/bot.py` | (Phase 8, Merge 2) `run_once`/`run_forever`: long-polling loop, chat-ID authorization (`TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID` only — unauthorized chats are silently ignored and only logged, chat id and timestamp, never message text), `/coin`/`/help`/`/start` command dispatch, `normalize_coin_input` (accepts `SOL`/`$SOL`/`sol`/`SOL/USDT:USDT`), a startup-backlog discard (a few minutes), and exponential backoff on a Telegram outage (immediate raise on an unrecoverable 401/403). Reuses `CoinalyzeClient.calls_in_last_minute` to throttle a `/coin` burst before it can exhaust the documented 40/minute budget. |
| `notify/telegram.py` | Sends read-only, send-only alerts to Telegram (no polling/webhook/commands). Builds the fixed alert message shape and the heartbeat summary shape (`build_heartbeat_message`), with bounded retry on transient network errors; a failure or missing credentials is logged and skipped, never raised. Classifies a failed send as a connection failure (never reached Telegram) vs an HTTP error response (`TelegramSendError`), so `notify test`/callers can report which. No order-placement code path exists anywhere in this project. |
| `health/checks.py` | Pure health checks reading only `data/store.py`: database reachability/schema, candle freshness, last run per command, journal activity, gap counts, Telegram config presence, universe freshness (Phase 6, Merge 3 — `check_universe_freshness`: OK <48h, WARN <7d or no snapshot, FAIL beyond), and which symbol source the last `run` used (`check_symbol_source`: WARN on `TIDEMARK_SYMBOLS_FALLBACK`). Never sends anything itself — see [ADR 0006](adr/0006-health-check-design.md). |
| `cli.py` | Typer entrypoint: `data backfill/update/gaps/status` manage market data; `context evaluate` (with optional `--as-of`)/`history`/`explain` drive Section 1 standalone; `run` and `observe run` resolve their symbols via `data/symbol_source.py` (Phase 6, Merge 3 — explicit `--symbols`, else the latest valid universe snapshot, else `TIDEMARK_SYMBOLS`, printing which source fired) and drive the journal/observe pipelines; `journal list`/`alerts` read the research record; `notify test` proves Telegram credentials work without touching the journal; `health check` (human-readable or `--json`, exit 0/1/2 for OK/WARN/FAIL, now including universe freshness and symbol source) and `health heartbeat` (the only `health` command that sends, and never journals) prove the unattended system is alive; `universe discover` (Merge 2A) refreshes `market_registry` from the venue's live listing, `universe backfill [--days]` backfills 1D candles for every ACTIVE registry symbol and prints per-symbol progress, `universe snapshot [--as-of]` (Merge 2B; UNIV-08 domain check) generates and persists a snapshot, `universe sync [--days]` (Merge 3) backfills 1H/4H/1D/1W for the currently SELECTED symbols only, `universe coverage [--snapshot-id]` (Merge 2B) reports symbols on venue/eligible/assessed/selected/data-available and counts by exclusion reason, and `universe registry`/`snapshots`/`show` are read-only inspection (`registry` shows candle coverage and stored row counts; `show` prints every ranked symbol including its `ASSET_CLASS` (UNIV-08), `eligible=NULL` rendered as `-`) - all report an empty database gracefully rather than erroring; `intel market --symbol <SYM> [--json]` (Phase 8, Merge 1) prints one symbol's normalized Coinalyze snapshot — reports `TIDEMARK_COINALYZE_API_KEY` missing as a clean message and exit rather than a traceback, and a symbol Coinalyze doesn't list as `MARKET_NOT_FOUND` with a non-zero exit — touches no store table at all; `intel bot [--once]` (Phase 8, Merge 2) runs the `/coin` Telegram long-polling bot in the foreground (`--once` processes any pending updates and exits, for testing) — reports a missing `TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID`/`TIDEMARK_TELEGRAM_BOT_TOKEN`/`TIDEMARK_COINALYZE_API_KEY` as a clean message and exit, same as `intel market`. |

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

As of Merge 3, `tidemark run`, `tidemark observe run`, and
`tidemark health check` default to the SELECTED symbols of the latest
valid universe snapshot for the venue; `TIDEMARK_SYMBOLS` /
`settings.symbol_list()` remains available as an explicit fallback (see
`data/symbol_source.py` below and
[ADR 0009](adr/0009-universe-selection-architecture.md)'s Merge 3
section) rather than being deleted.

The work is sequenced as merges:

- **Merge 1** — schema for all three new tables, read/write store
  methods, and read-only `tidemark universe registry/snapshots/show` CLI
  commands. No selection or eligibility logic exists yet.
- **Merge 2A** — venue symbol discovery
  (`tidemark universe discover`, `data/discover.py`) via ccxt's unified
  `load_markets` — a second, additive, venue-agnostic call alongside
  `fetch_closed_candles`, never a venue-specific raw endpoint. Syncs
  `market_registry`: every currently-listed active USDT-quoted perpetual
  is upserted ACTIVE, and any previously-registered symbol no longer
  listed is marked ABSENT_FROM_VENUE (never deleted). Paired with daily
  candle backfill for every ACTIVE symbol
  (`tidemark universe backfill`, `data/universe_backfill.py`), which
  reuses `data/ingest.py`'s existing backfill path unmodified — same
  chunked upsert, same per-symbol failure isolation — restricted to the
  `1d` timeframe, then records each symbol's actual candle coverage back
  onto its registry row. Still no eligibility, metric, or ranking logic.
- **Merge 2B** — the metric, rank-first eligibility, and
  snapshot generation (`tidemark universe snapshot`,
  `data/universe_snapshot.py`). Rank-first order (PART B, fixed): rank
  every ACTIVE symbol by `MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D`
  (UNIV-02/07, `data/universe_metric.py`), take the top **K=50**
  (architectural headroom over N=30, never swept — same commitment
  UNIV-03 already made), backfill 4H for those 50 if missing, assess
  Section 1 eligibility for those 50 only (UNIV-01: exiting
  `INSUFFICIENT_STRUCTURE` at least once, via the LOCKED v1.1 engine
  unmodified — `data/universe_eligibility.py`), select the top 30
  eligible by rank. A row ranked below K is `NOT_ASSESSED` with
  `eligible = NULL` — deliberately distinct from an assessed-and-failed
  `eligible = False`. A FORWARD run (`--as-of` omitted) caches a found
  `section1_first_usable_at` onto `market_registry`; a BACKFILLED run
  (`--as-of` given) never touches the network and never writes that
  cache. `tidemark universe coverage` reports on the result.
- **UNIV-08** — asset-class domain constraint
  (`data/asset_class.py`). A live inspection found 202/727 ACTIVE
  symbols (27.8%) and 14/30 of the first snapshot's selections (47%)
  were non-crypto — tokenised equities, commodities, FX, pre-IPO
  synthetics. Classifies from Binance's `underlyingType` alone (`COIN` →
  `CRYPTO`; `INDEX` → `NON_ELIGIBLE_INDEX`; a known TradFi type →
  `NON_CRYPTO`; unrecognized → `UNKNOWN`, fail closed), read from the
  same `load_markets()` response discovery already consumes. Captured
  once at first discovery, persisted on `market_registry`, never
  overwritten; `generate_universe_snapshot` reads only that persisted
  value — UNIVERSE_AS_OF_INVARIANT applied to asset class. Ranking, N,
  and K unchanged. The pre-UNIV-08 snapshot is untouched
  (`methodology_version="universe-v1"` forever); every snapshot from
  here on is `"universe-v2"`.
- **Merge 3 (this one)** — the observer reads the universe snapshot
  (`data/symbol_source.py`, `data/universe_sync.py`). `tidemark run`,
  `tidemark observe run`, and `tidemark health check` resolve symbols in
  a fixed order: explicit `--symbols` (`EXPLICIT`), else the latest
  snapshot for the venue if it exists and is under
  `TIDEMARK_UNIVERSE_STALENESS_HOURS` (default 48h) old and selected at
  least one symbol (`SNAPSHOT`, rank-ordered), else `TIDEMARK_SYMBOLS`
  (`TIDEMARK_SYMBOLS_FALLBACK`, with a warning naming the reason). Never
  crashes over a missing/stale snapshot. Every run records which source
  it used, and the snapshot id when applicable, on its `runs` row
  (`symbol_source`/`symbol_source_snapshot_id`, added via a manual
  `ALTER TABLE` against the real database, same treatment as UNIV-08's
  registry columns) so a journal entry is always traceable to the
  universe that produced it. `health check` gained `universe_freshness`
  (OK/WARN/FAIL on snapshot age) and `symbol_source` (WARN on fallback)
  checks. `tidemark universe sync [--days]` backfills 1H/4H/1D/1W for the
  currently SELECTED symbols only, reusing `run_backfill` unmodified. No
  Section 1/2 rule, the ranking methodology, N, or K changed.

Every merge is checked against `UNIVERSE_AS_OF_INVARIANT` (ADR 0009):
generating a snapshot from data truncated at time T must equal generating
it as-of T from the full database, for every field knowable at T — the
same look-ahead guard `context/htf.py` and `context/mtf.py` already rely
on.

## Market intelligence layer (Phase 8)

A live, read-only derivatives-data layer, strictly separate from
everything above — see
[ADR 0011](adr/0011-market-intelligence-layer.md) for the full
rationale. `market_intel/` never imports `data/exchange.py`, `context/`,
`journal/`, or `replay/`, is never imported by them, and never writes to
`observations`, `journal_entries`, `context_records`, `candles`, or any
other research table — enforced by
`tests/market_intel/test_import_boundary.py`, a static-analysis test
that fails the build the moment either side of the boundary is crossed.

- **Merge 1** — the Coinalyze client, symbol mapping,
  future-markets cache/validation, closed-period clamping, and
  `tidemark intel market [--json]`. No Telegram, no scheduling, no BTC
  dominance (unavailable from Coinalyze at all), no trading
  recommendation, no bias output, no signal logic.
  `docs/rulebook/derivatives-context-v0.1.md` records six
  price/OI/funding interpretations as a `PROVISIONAL`, unwired
  document — no code reads it yet.
- **Merge 2 (this one)** — `tidemark intel bot [--once]`: a `/coin`
  Telegram long-polling bot answering with the Merge 1 snapshot, for
  any Binance USDT-M perpetual. Chat-ID-only authorization
  (`TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID`), silent ignore + minimal log for
  any other chat, its own independent Telegram client and renderer (not
  `notify/telegram.py`), a flat-JSON-file offset with no relationship to
  `tidemark.db`, a discarded startup backlog, and exponential backoff on
  a Telegram outage. Still no hourly briefing, no scheduling beyond the
  bot's own poll loop, no BTC dominance, no trading recommendation.

## TODO

- Symbol universe: `TIDEMARK_SYMBOLS` (default BTC, ETH, SOL, XRP, DOGE
  perpetuals) is no longer the primary symbol source as of Phase 6, Merge
  3 — see the Universe selection section above. It remains available as
  the manual/development/test fallback ADR 0009 always intended, per
  CLAUDE.md's own instruction not to delete it. The *methodology* for
  what a snapshot selects (N=30, K=50, the derived volume metric, the
  crypto-only domain constraint) is defined by Merges 2B/UNIV-08 above,
  not invented here.

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
