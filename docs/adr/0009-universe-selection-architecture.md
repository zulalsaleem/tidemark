# 9. Universe selection architecture

Date: 2026-09-25

## Status

Accepted

## Context

Tidemark has watched a fixed, manually-configured set of five symbols
(`TIDEMARK_SYMBOLS`) since Phase 1. Phase 6 adds a universe-selection
layer: a repeatable methodology for choosing which symbols Tidemark
evaluates, instead of a person editing an environment variable. The Phase
6 preflight inspection (read-only, no code changes) surfaced the
constraints this ADR is written against:

- No market/universe registry of any kind exists today. The symbol list
  is pure configuration, read by every pipeline via
  `settings.symbol_list()`.
- `data/exchange.py`'s venue-agnostic contract (ADR 0002: any ccxt venue
  id exposing unified `fetch_ohlcv` works unchanged) is something a
  universe layer could easily break by reaching for exchange-specific
  data it doesn't already store.
- Section 1's warm-up requirement (2 confirmed swing highs AND 2 confirmed
  swing lows to exit `INSUFFICIENT_STRUCTURE` — `context/htf.py`,
  `_compute_bias`) is price-action-dependent and has no upper bound
  derivable from the rulebook's parameters. A calendar-day eligibility
  rule ("N days of history") would be an invented threshold, which
  CLAUDE.md's standing rules forbid inventing.
- Section 2 v0.2 (`section-02-v0.2-justification.md`) already established
  that a session must end only on conditions that actually describe
  whether price is still at the level — not on conditions unrelated to
  that question. A universe layer that can unilaterally kill an in-flight
  Section 2 session repeats exactly the mistake v0.2 corrected, for a
  different reason (venue selection instead of grade).

This ADR records the architecture Merge 1 lays the schema for. Merge 1 is
additive-only: new tables, new read-only store methods, new read-only CLI
commands. No selection methodology, no eligibility computation, and no
snapshot generation logic exists yet — that is Merge 2.

## Decision

### Three-layer separation

Universe selection is split into three layers, each with its own table,
because collapsing them loses information a human will need later:

1. **`market_registry`** — what the venue has ever contained. One row per
   `(venue, symbol)`, tracking when candle data was first/last seen for
   it and when it was first/last seen in the venue's own listed-symbol
   set, plus its `status` (`ACTIVE` / `STALE` / `ABSENT_FROM_VENUE`).
   This is a factual record of the venue, not a decision.
2. **`universe_snapshot`** / **`universe_snapshot_row`** — what the
   methodology selected, at a point in time. One snapshot header per
   selection run, one row per ranked symbol (every ranked symbol, not
   only the selected N) — so an excluded symbol's rank and
   `exclusion_reason` are recoverable, not just the survivors.
3. **`observation_record`** (existing: `journal_entries` / `observations`)
   — what Tidemark actually evaluated. Unchanged by this merge.

The reason for keeping these three separate, rather than one table with
a "selected" flag: **"not selected" must never be indistinguishable from
"no data existed."** A symbol absent from a given day's
`universe_snapshot_row` set could mean it existed and lost on the metric,
or that Tidemark had no candle data for it at all. `market_registry`
existing independently of `universe_snapshot` is what keeps those two
cases distinguishable — a snapshot without a registry could not tell them
apart, and a registry without a snapshot could not tell you what was ever
actually selected.

`TIDEMARK_SYMBOLS` is **not deleted or replaced** by this merge. It
remains the manual/development override every pipeline (`journal/
pipeline.py`, `journal/observe_pipeline.py`, `health/checks.py`, every
`data`/`context`/`journal`/`observe` CLI command) reads via
`settings.symbol_list()`. Wiring the universe layer's output into that
symbol source is an explicit future decision, not something this merge
does silently — see the hard constraint in the Phase 6 Merge 1 brief and
PART E's regression test.

### UNIV-01 — Eligibility is not a calendar-history requirement

An asset becomes eligible once Section 1 has **demonstrably exited
`INSUFFICIENT_STRUCTURE` at least once** on stored data — not after any
fixed number of days. This is recorded as
`market_registry.section1_first_usable_at`.

This directly encodes the Phase 6 preflight finding: Section 1's bias
computation (`context/htf.py`, `_compute_bias`) requires 2 confirmed
swing highs **and** 2 confirmed swing lows before it can produce anything
other than `INSUFFICIENT_STRUCTURE`. Fractal swings are sparse and
price-dependent (`core/swings.py`, `find_swings` — a strict local
maximum/minimum over `fractal_n=2` candles on each side); a sustained
one-directional move can go arbitrarily long without an opposing pivot.
No day-count is derivable from the rulebook's own parameters (ATR
period, fractal N, the 120-candle lookback) — those bound *inputs* being
available, not the bias state actually resolving. Treating "N days since
first candle" as an eligibility rule would be inventing a threshold the
rulebook does not supply, which is exactly what CLAUDE.md's standing
rules forbid.

Eligibility is explicitly **not** "has produced a trade," "has produced a
`LONG_WATCH`/`SHORT_WATCH`," or any signal-quality bar — it is the
minimal, factual claim that Section 1's state machine has produced a
real (non-`INSUFFICIENT_STRUCTURE`) answer at least once, so a universe
methodology has something other than silence to reason about.

`section1_first_usable_at` must be computed with the locked Section 1
v1.1 logic, including RULE 1.7a (dynamic level role), and must be
lookahead-safe — see UNIVERSE_AS_OF_INVARIANT below. Merge 1 adds the
column, nullable; Merge 2 fills it.

### UNIV-02 / UNIV-07 — Volume metric is derived, not exchange-reported

The selection metric is `MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D`: for each
closed daily candle, `base_volume × daily_close`, taking the median over
the trailing 30 closed daily candles. Its `metric_name` is recorded with
type **`DERIVED`**, explicitly distinguished from `EXCHANGE_REPORTED`.

This is derived rather than exchange-reported because of the Phase 6
preflight's Part 2 finding: ccxt's unified `fetch_ohlcv` truncates
Binance's raw kline response to six fields
(`[timestamp, open, high, low, close, volume]`) and discards quote-asset
volume, which sits at raw index 7 of Binance's own kline row. Obtaining
it would require a venue-specific raw endpoint call (e.g.
`fapiPublicGetKlines` for `binanceusdm`), which breaks ADR 0002's
venue-agnostic contract for `data/exchange.py` — "any ccxt exchange id
that exposes `fetch_ohlcv` for unified perpetual symbols... works without
changes here." That tradeoff was rejected. `base_volume × close` is
computable entirely from candles already stored today, with zero changes
to `data/exchange.py` or the `Candle` schema.

Should a future decision replace the derived metric with an
exchange-reported one, that is a **methodology version change**
(a new `methodology_version` value on `universe_snapshot`, coexisting
with prior snapshots), never a silent swap of what an existing
`metric_name` means.

### UNIV-03 — N = 30 is architectural, not tuned

The snapshot size, N = 30, is fixed and frozen, chosen on operational
grounds (server CPU headroom for the hourly observation cycle across
however many symbols are selected) — not swept or optimized for
performance. It is recorded on `universe_snapshot.n_selected` per
snapshot so it is auditable, but it is not a parameter Merge 2 (or any
future merge) is expected to search over.

### UNIV-04 — Daily refresh, independent of the hourly cycle

Universe selection refreshes once per day at 00:00 UTC, on its own
schedule, independent of the hourly Section 1/Section 2 observation
cycle. Nothing about this merge's schema requires the two schedules to
coordinate — a snapshot's `snapshot_at` is just another UTC timestamp
`observation_record` rows can be compared against via "latest snapshot at
or before T."

### UNIV-05 — Universe exit never kills an in-flight session

Exiting the universe prevents a symbol from opening a **new** Section 1
watch. It never terminates an **in-flight Section 2 session**. A session
already running when its symbol exits the universe is flagged
`universe_exit_during_session` rather than being forcibly ended.

This mirrors the finding that drove `section-02-1h-behaviour-v0.2.md`:
v0.1's grade-only termination ended sessions on a variable unrelated to
whether price was still at the level, and v0.2 corrected that by
narrowing session-ending conditions to only `WATCH` direction changes or
`WATCH` disappearing — not grade, and (per the v0.2 draft that was
measured and rejected) not level identity either. A universe-driven kill
switch would reintroduce the same category of error the v0.2 fix
addressed, just triggered by an unrelated system (universe selection)
instead of an unrelated Section 1 field (grade). Section 2 stays
subordinate to Section 1's own `HTF_CONTEXT_INVALIDATED` rule and nothing
else, per `section-02-1h-behaviour-v0.2.md`'s own terminal-states section.

### UNIV-06 — Backfilled snapshots are marked and never used for performance claims

A snapshot can be reconstructed retroactively from historical candle data
(`provenance = BACKFILLED`), but a backfilled snapshot is never valid
evidence for a performance claim: the venue's *current* symbol list
contains only survivors — a symbol that was delisted, migrated, or
otherwise vanished from the venue before today is invisible to a
backfill built from what the venue lists now, which silently removes
exactly the failure cases a performance claim would need to see
(survivorship bias). Snapshots generated going forward
(`provenance = FORWARD`) carry no such bias and are clean from the very
first one.

### UNIVERSE_AS_OF_INVARIANT — mandatory Phase 6 completion gate

For every field knowable as of time T:

```
generate(T, database truncated at T) == generate_as_of(T, full database)
```

Computing a snapshot (or any of its fields, including
`section1_first_usable_at`) using only data available as of T must
produce exactly what a later, fuller database would compute for that
same T. This is the same look-ahead guard `context/htf.py` and
`context/mtf.py` already rely on (truncating inputs to an "as of" point
is the only thing allowed to change their output). `section1_first_usable_at`
in particular must be computed by replaying the locked Section 1 v1.1
state machine — including RULE 1.7a's dynamic level-role logic — over
candles truncated at the check time, not by asking "did Section 1 ever
exit `INSUFFICIENT_STRUCTURE` as of *today*" and stamping that answer
onto an earlier timestamp.

This invariant is not verified by Merge 1 (there is no generation logic
yet to verify). It is recorded here as a gate Merge 2 and Merge 3 must
each be checked against before Phase 6 is considered complete.

## Addendum: Merge 2A — venue discovery and daily candle backfill

Merge 2A populates `market_registry` for real, via two separate steps:

- **Discovery** (`data/discover.py`, `ExchangeClient.list_perpetual_symbols`)
  lists the venue's active USDT-quoted perpetual contracts through ccxt's
  unified `load_markets` — a second, separate read-only call from
  `fetch_closed_candles`, filtering on ccxt's own unified `type`, `quote`,
  and `active` fields. This is exactly as venue-agnostic as candle
  fetching already is: `load_markets` is part of ccxt's unified market
  structure, not a venue-specific raw endpoint, so it works unchanged
  against any ccxt venue id the same way `fetch_ohlcv` does (ADR 0002).
  `data/exchange.py`'s candle-fetch path (`fetch_closed_candles`,
  `_fetch_with_retry`) is untouched by this addition.
- **Backfill** (`data/universe_backfill.py`) reuses `data/ingest.py`'s
  existing backfill path unmodified in its fetch behavior (same chunked
  upsert, same per-symbol/timeframe failure isolation and
  COMPLETED/PARTIAL/FAILED status), restricted to the `1d` timeframe and
  to symbols with registry `status = ACTIVE`. `run_backfill`/`run_update`
  gained one optional, backward-compatible parameter (`on_outcome`, a
  per-symbol progress callback) so a caller backfilling several hundred
  symbols can report progress; every existing caller that omits it is
  unaffected.

This required one schema amendment to Merge 1's original definition:
`market_registry.first_candle_seen_at`/`last_candle_seen_at` become
nullable (were `NOT NULL`). Merge 1 shipped these as required because its
schema assumed a registry row would always carry candle data; Merge 2A's
two-phase discover-then-backfill design means a freshly-discovered symbol
legitimately has a registry row before any candle has ever been fetched
for it. This is not a migration in the sense CLAUDE.md's "no migration
tooling" constraint is guarding against — nothing had ever written to
`market_registry` before Merge 2A (Merge 1 shipped schema and read-only
plumbing only), so there is no existing data to reconcile; a fresh
`create_all` picks up the corrected nullability directly. Two new,
narrower store methods (`record_market_listing`, `record_candle_coverage`)
replace `upsert_market_registry_row` for these write paths specifically,
because that method overwrites every field unconditionally and would let
discovery clobber a prior backfill's candle coverage, or vice versa.

No selection, eligibility, or metric computation exists after Merge 2A —
`section1_first_usable_at`, `MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D`, and
ranking are still entirely Merge 2B's, per UNIV-01 and UNIV-02/07 above.

## Consequences

- Two additional append-only tables (`universe_snapshot`,
  `universe_snapshot_row`) plus one upsertable registry table
  (`market_registry`) exist after this merge, all created by the
  existing `init_db`/`create_all` — no migration tooling was added or is
  needed, since every new column is either on a brand-new table or is
  nullable-by-construction (`section1_first_usable_at`,
  `section1_eligibility_checked_at`, and — from Merge 2A —
  `first_candle_seen_at`/`last_candle_seen_at`).
- `Candle` is untouched: no new column, no schema risk to the one table
  every pipeline already depends on. `data/exchange.py`'s candle-fetch
  path is untouched (Merge 2A only adds a second, separate
  `list_perpetual_symbols` call alongside it); quote volume is derived
  at read time from stored `close`/`volume`, never ingested.
- `TIDEMARK_SYMBOLS` / `settings.symbol_list()` remain the only symbol
  source any pipeline reads. This is true after Merge 2A as well, even
  though `market_registry` is now populated with potentially hundreds of
  symbols via `tidemark universe discover`/`backfill` — those symbols
  are inert to every existing pipeline until a future, explicit merge
  wires selection in. `tidemark run`, `tidemark observe run`, and
  `tidemark health check` behave identically before and after both
  merges.
- Merge 2B (eligibility computation, `section1_first_usable_at`, the
  derived volume metric) and Merge 3 (snapshot generation, refresh
  scheduling, and — as its own explicit, separately-reviewed decision —
  wiring a snapshot's selection into what the pipelines actually
  evaluate) are each their own change, checked against
  UNIVERSE_AS_OF_INVARIANT before being considered done.
