# 11. Market intelligence layer (Coinalyze derivatives data)

Date: 2026-09-27

## Status

Accepted

## Context

Section 1/2 (the research engine: `data/exchange.py`, `context/`,
`journal/`, `replay/`) evaluates the rulebook against closed Binance
USDT-M candles and nothing else — that is the whole point of ADR 0002:
one internally consistent price history per symbol, never blended with
another source. There is a separate, real need for a human to glance at
live derivatives context (open interest, funding, long/short
positioning, liquidations) alongside that structure — an hourly BTC
briefing and an ad-hoc `/coin <SYMBOL>` lookup are the eventual targets —
but none of that is price structure, and none of it may ever influence a
Section 1/2 evaluation.

A read-only inspection of Coinalyze's API (the practical option for this
data: free, documented, per-market rather than aggregated) found several
things load-bearing for how this had to be built, not just what it
returns:

- **Coinalyze does not truncate to closed periods.** A history request
  with `to=now` returns a bucket that is still accumulating — verified
  live at multiple points inside an hour. Anything built on top of this
  API that doesn't compute its own closed-period boundary would silently
  violate CLAUDE.md's "closed candles only" rule the first time someone
  ran it mid-candle.
- **`200 []` means three different things.** A symbol not listed at all,
  a symbol listed but with no data flowing (confirmed live: our
  universe's CJK-ticker meme coin, 龙虾/USDT:USDT, is listed in
  `/future-markets` but returns an empty array for open interest,
  funding rate, and predicted funding rate), and a simply wrong symbol
  name all produce the identical empty-array response. Rendering any of
  these as `0` would be a fabricated number, not a missing one.
- **Symbol mapping is mechanical for our canonical venue, verified with
  the hard case.** Coinalyze identifies a market as
  `<BASE><QUOTE>_PERP.<EXCHANGE_CODE>`; for Binance (`A`) this is a
  direct concatenation of the ccxt unified symbol's own base/quote
  assets, confirmed against a real `/future-markets` listing including
  the CJK ticker above — Coinalyze preserves it verbatim, never
  romanizing it. This holds only for the venue ADR 0002 makes canonical;
  an unmapped venue must fail closed, not guess a code.
- **Every query is exchange-specific, not an aggregate.** The exchange
  code is embedded in the symbol, so `BTCUSDT_PERP.A` is Binance-only —
  Coinalyze never silently blends venues. This lines up with ADR 0002
  without any extra work: the natural single-symbol query already
  matches "Binance USDT-M canonical," but every number this layer prints
  is a Binance figure, not "the market," and must be labeled as such.
- **BTC dominance is not available from this source at all.** Coinalyze
  is a derivatives-data aggregator; it has no market-cap concept
  anywhere in its API surface. Sourcing dominance would mean a second,
  unrelated external API (CoinGecko's `/global`, CoinMarketCap's
  global-metrics, or similar) — out of scope for this merge and deferred
  until a rulebook decision says it's wanted.
- **USD conversion is Coinalyze's own, undocumented methodology.**
  `convert_to_usd=true` on open interest, its history, and liquidations
  normalizes across markets with different natural denominations
  (`BASE_ASSET`/`QUOTE_ASSET`/`CONTRACTS`), but Coinalyze does not
  document what spot price or timing it uses to do the conversion. This
  merge always passes `convert_to_usd=true` for consistency across
  symbols rather than reporting mixed denominations, and this ADR is
  where that tradeoff is recorded rather than left implicit in code.
- **The documented rate limit is 40 calls/minute per key, and each
  symbol in a comma-separated request spends one of those 40** — a
  6-endpoint pull for one symbol costs 6, not 1. During the inspection,
  this session repeatedly hit 429s that could not be attributed to this
  codebase or to any other process on the same machine, which is
  recorded here as an operational risk (the key's budget cannot be
  assumed private) rather than something this merge can resolve.

## Decision

- **New top-level package, `src/tidemark/market_intel/`,** with a hard
  isolation boundary enforced by a static-analysis test
  (`tests/market_intel/test_import_boundary.py`), not just left to
  discipline:
  - `market_intel/*.py` never imports `tidemark.data.exchange`,
    `tidemark.context`, `tidemark.journal`, or `tidemark.replay`.
  - `data/exchange.py`, and every file under `context/`, `journal/`, and
    `replay/`, never imports `tidemark.market_intel`.
  - It never writes to `observations`, `journal_entries`,
    `context_records`, `candles`, or any research table — it has no
    store dependency of any kind, only an HTTP client and plain
    dataclasses.
- **Closed-period clamping is computed locally, never trusted from the
  API.** `market_intel/clamping.py`'s `closed_period(now, interval)`
  returns the latest fully-elapsed `[start, close)` window for any of
  Coinalyze's interval strings; every period-based metric is fetched
  with `to = period.close - 1s` so the in-progress next bucket (whose
  `t` equals `period.close`) is excluded by construction. Every
  returned metric carries the exact period it describes (or its own
  `updated_at` for a point-in-time reading, marked
  `is_point_in_time=True`) — a caller can never mistake which window a
  value refers to. This merge fixes one interval, 1 hour, for every
  closed-period metric, for simplicity and because it matches the
  hourly-briefing use case this layer exists for; a future merge that
  wants per-metric intervals can revisit this without touching the
  clamping primitive itself.
- **`/future-markets` is cached (24h TTL) and used to distinguish
  `MARKET_NOT_FOUND` from `NO_DATA` from `OK`** — never inferred from an
  empty array alone. A symbol absent from the cached listing is
  `MARKET_NOT_FOUND` (short-circuits before any other network call). A
  symbol present but whose `has_long_short_ratio_data`/`has_ohlcv_data`/
  `has_buy_sell_data` flag is `False` is `NO_DATA` for exactly that
  metric, decided from the cache with no network call at all. Open
  interest, funding rate, predicted funding rate, OI change, and
  liquidations have no such flag on Coinalyze's side, so those are
  `NO_DATA` only when the live call itself comes back empty. No metric
  is ever rendered as `0` for a missing value — always a non-`OK` status
  with `value=None` and a reason.
- **Symbol mapping (`market_intel/symbols.py`) is a fixed
  venue-to-exchange-code table**, currently `{"binanceusdm": "A"}`, not a
  general lookup service. An unmapped venue raises `UnsupportedVenueError`
  rather than guessing.
- **`convert_to_usd=true` is always passed** where Coinalyze supports it
  (open interest, its history, liquidations) — see the tradeoff recorded
  above.
- **Rate limiting is tracked, not preemptively enforced**: the client
  records call timestamps for observability and logs a warning if usage
  looks like it's approaching the documented 40/minute, but the
  authoritative signal is Coinalyze's own 429. On a 429, it respects
  `Retry-After` (capped at a bounded maximum per attempt) and gives up
  with a typed `RateLimitedError` after a bounded number of retries —
  never an infinite retry loop. The API key is never logged.
- **Missing `TIDEMARK_COINALYZE_API_KEY` fails at client construction**,
  before any network attempt, so `tidemark intel market` reports it as a
  clean one-line message and a non-zero exit — not a traceback.
- **`tidemark intel market --symbol <SYM> [--json]`** is the only user
  surface this merge adds. It prints the normalized snapshot — every
  metric's value, unit, and window, or `UNAVAILABLE` with the reason —
  and nothing else: no Telegram, no scheduling, no trading
  recommendation, no bias output, no BTC dominance.
- **`docs/rulebook/derivatives-context-v0.1.md`** records six
  price/OI/funding interpretations as a `PROVISIONAL`, unwired document
  only — no code reads it. Turning it into actual output is an explicit
  later merge, per the rulebook's own standing rules (`NOT_DEFINED`
  behavior must be marked, never guessed): thresholds for "up" / "down"
  / "flat" / "rising" / "falling" are not defined anywhere yet.

## Consequences

- The research engine (Section 1/2, the journal, the universe-selection
  layer) is provably unaffected by this merge — the import-boundary test
  fails the build the moment either side of the boundary is crossed,
  rather than relying on code review to catch it.
- Every number `tidemark intel market` prints is Binance-only (matching
  ADR 0002's canonical venue) and USD-converted via Coinalyze's own
  undocumented methodology — both are stated in this ADR so a future
  reader isn't left to reverse-engineer either fact from the code.
- BTC dominance remains unavailable and deferred; if it's ever wanted, it
  needs a second data source, its own secret, its own rate-limit story,
  and — since the rulebook currently says nothing about it — likely its
  own rulebook decision before it's purely an engineering task.
- No trading recommendation, bias, or interpretation ships from this
  layer. `derivatives-context-v0.1.md`'s six combinations exist as a
  recorded, versioned starting point for a future merge to wire up
  deliberately — not as a shortcut that lets one get built without its
  own review.
- The unexplained rate-limit contention observed during the API
  inspection was not reproduced or resolved by this merge — it's an
  operational question (is this key shared with something outside this
  environment?) that should be settled before `tidemark intel market` is
  run on any kind of schedule.

## Addendum: Merge 2 — the /coin Telegram bot

Merge 1 gave a human a command to run by hand. The natural next surface
is a Telegram bot that answers `/coin <SYMBOL>` on demand, using exactly
the Merge 1 snapshot — still no interpretation, no bias, no trading
recommendation, and still no BTC dominance.

**Long polling, not a webhook.** A webhook needs a publicly reachable
HTTPS endpoint, TLS termination, and an inbound port — infrastructure
this project has no server story for and shouldn't need one for a
single-operator read-only bot. `getUpdates` long polling needs nothing
but outbound HTTPS, which every other piece of Tidemark already assumes
(ccxt, Coinalyze, the existing `notify.telegram` alert sender). The
tradeoff is a long-running foreground process instead of a request
handler — accepted, and addressed with `Restart=always` at the
process-supervision layer (see the systemd unit below) rather than by
building supervision into the bot itself.

**Authorization is a single numeric chat ID, from
`TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID`, checked against
`update.message.chat.id` only.** Never a username, never a display
name — Telegram lets any user set both to whatever they like, so
neither identifies who is actually messaging the bot. A chat ID is
assigned by Telegram itself and is not attacker-controlled. This merge
supports exactly one allowed chat, matching the env var's singular name
and this bot's single-operator scope; a future multi-chat allowlist
would be its own, explicitly-scoped change, not a quiet extension of
this one.

**An unauthorized chat gets total silence, not an error.** No reply,
no "you are not authorized" message, nothing that confirms a bot is
listening on the other end at all. The only trace is a log line
recording the chat ID and timestamp — never the message text, which
could contain anything an anonymous stranger chose to send. The
reasoning: a bot that replies "unauthorized" to a stranger has just
confirmed (a) that something is listening at this bot username and (b)
that it distinguishes callers, both of which are worth withholding for
free. Silence costs nothing and reveals nothing.

**Any Binance USDT-M perpetual is answerable, not only the 30-symbol
research universe.** `/coin` is a general lookup tool for a human, not
a Section 1/2 companion — restricting it to the research universe would
turn a reasonable question ("what's SOL's funding rate?") into an
arbitrary refusal for any symbol outside that list. The Merge 1
`/future-markets` cache is still the sole gatekeeper and still
distinguishes exactly the same three cases (`MARKET_NOT_FOUND`/
`NO_DATA`/`OK`) it always did — this merge doesn't add a second
symbol-validity notion, it just points the existing one at a wider set
of inputs.

**The bot never imports `notify.telegram`.** That module builds Section
1 alert text and therefore imports `tidemark.context.htf`,
`tidemark.data.models`, and `tidemark.journal.changes` — reusing it here
would transitively pull the research engine into `market_intel` even
though the import-boundary test only checks direct imports per file.
`market_intel/telegram_client.py` is a second, independent
implementation of just `getUpdates`/`sendMessage`, and
`market_intel/telegram_render.py` is a second, independent renderer —
the same "duplicate rather than couple" tradeoff ADR 0010 already made
for `evidence.py` versus `replay/report.py`'s session grouping, for the
same reason: a change made to the Section 1 alert format for Section
1's own reasons must never be able to silently change what `/coin`
renders, and vice versa.

**Rate limiting reuses Merge 1's tracking rather than adding a second
counter.** `CoinalyzeClient.calls_in_last_minute` is checked before
dispatching a `/coin` lookup (estimated at up to 7 call-units — see
`bot.py`), so a burst of requests replies "rate limited" instead of
attempting a call that would fail anyway; the client's own bounded-retry
`RateLimitedError` (Merge 1) is still caught as a fallback. Coinalyze's
40-calls/minute budget is shared across every consumer of the key —
`tidemark intel market` and this bot both draw from it — which matters
more now that the bot can be asked about any symbol at any time, not
just BTC/SOL by hand.

**The update offset is a flat JSON file, not a database row.** It has
no relationship to `tidemark.db` at all — a stronger form of "writes to
no research table" than merely using a separate table in the same
database would be. Written atomically (temp file + rename) so a crash
mid-write can't corrupt the last known-good offset. On startup, any
update older than a few minutes is discarded (offset still advances
past it) so a restart after downtime never answers a `/coin` asked
hours ago into a now-stale context.

**A network failure backs off and retries; a bad token does not.** A
connection failure or a Telegram 5xx is treated as transient and
retried with exponential backoff (capped), so the bot survives a
Telegram outage rather than exiting. A 401/403 means the configured bot
token is simply wrong — retrying cannot fix that, so `run_forever`
raises immediately rather than looping forever against a guaranteed
failure.

### Consequences (Merge 2)

- `tidemark intel bot` is a long-running foreground process; operators
  are expected to supervise it (see the systemd example in the README)
  rather than the bot supervising itself.
- Every `/coin` reply and every `/help`/`/start`/unknown-command reply is
  produced by `market_intel`'s own renderer and text constants — none of
  it is shared code with the Section 1/2 Telegram alert path, so a
  future change to either can never silently affect the other.
- An unauthorized user interacting with the bot leaves no trace visible
  to them and only a minimal trace (chat ID, timestamp) in Tidemark's
  own logs — by design, not by oversight.
- `docs/rulebook/derivatives-context-v0.1.md` remains unwired. Nothing
  in this bot reads it, classifies price/OI/funding combinations, or
  produces anything resembling a trade direction.

## Addendum: Merge 3 — the hourly BTC briefing

Merge 2 gave a human a command to ask on demand. Merge 3 is the first
thing in `market_intel` that runs unattended and decides for itself
whether to speak: an hourly evaluation that classifies BTC's closed-1H
price/OI/funding reading against `docs/rulebook/derivatives-context-
v0.1.md` and alerts only on a real change - never on a fixed schedule,
never on every tick.

**The rulebook was written before any classifier code, deliberately.**
`derivatives-context-v0.1.md` was updated with the exact thresholds,
D1-D6 identifiers, and the NO_MATCH definition in its own commit, ahead
of `derivatives_classifier.py`. This mirrors CLAUDE.md's standing rule
that strategy logic comes only from the rulebook: the classifier reads
`PRICE_UP_THRESHOLD_PCT`/`OI_UP_THRESHOLD_PCT`/etc. from that module,
but that module's own docstring states plainly that every value in it is
copied from the rulebook document, not invented here.

**The read-only `journal_entries` exception.** The briefing needs BTC's
stored Section 1 result to show alongside derivatives context, but
`market_intel` must never recompute Section 1 or import its logic. The
exception (`context_read.py`) is narrow by construction, not just by
convention:
- it imports only `tidemark.data.models.JournalEntry`, a plain ORM class
  with no imports of its own back into the research engine;
- it never imports `tidemark.data.store`, whose `TidemarkStore`
  transitively imports `tidemark.data.exchange` for a type hint - a gap
  a naive "just forbid `tidemark.data.exchange`" rule would have missed
  entirely, since `tidemark.data.store` was never itself on any
  forbidden list;
- it builds its own SQLAlchemy engine directly from a database URL
  string and never calls `init_db`, so it can never provision the
  research schema, only observe whatever is already there;
- it never writes anything, anywhere - there is no write path in this
  module at all.

  The import-boundary test was tightened from "`tidemark.data.exchange`
  is forbidden" to an allowlist - only `tidemark.data.models` may be
  imported under `tidemark.data`, and a second test asserts that only
  `context_read.py` actually does. This closes the `tidemark.data.store`
  gap and any other one like it, present or future, rather than growing
  the blocklist reactively each time a new transitive path is found.

  If no Section 1 result exists for BTC, or the latest one is more than
  `SECTION1_STALE_AFTER` (8 hours - two missed 4H cycles of grace, an
  operational judgment `market_intel` owns for its own display purposes,
  not a Section 1 parameter) old, the briefing's structure section
  renders `UNAVAILABLE` with a reason. It never falls back to computing
  structure itself - there is no code path by which it could, since
  Section 1's evaluation logic (`tidemark.context`) is never imported
  here at all.

  **Correction (post-launch):** this exception originally read
  `ContextRecord`/`context_records` instead. An operator reported that on
  a live deployment `context_records` was empty (never written to) while
  `journal_entries` held 125 real rows including BTC's. Tracing the code
  confirmed why: `context_records` is written only by the standalone
  `tidemark context evaluate` command (`cli.py`); `tidemark run` - what's
  actually scheduled in production - evaluates Section 1 via `htf.evaluate`
  and persists the result only as a `JournalEntry` (`journal/pipeline.py`),
  never as a `ContextRecord`. The briefing's structure section was
  therefore always `UNAVAILABLE` on any deployment where only `tidemark
  run` runs, and the structural-change alert trigger could never fire -
  not because no Section 1 result existed, but because this module was
  reading a table nothing in production writes to. Fixed to read
  `journal_entries` instead (`read_latest_journal_entry`); the isolation
  boundary is otherwise unchanged - still read-only, still
  `tidemark.data.models` only, still no `tidemark.context`, still no
  recomputation, still no write path. `context_records`/`ContextRecord`
  is not removed by this correction - whether the standalone `context
  evaluate`/`history`/`explain` trio (its only writer and readers) is
  worth keeping is a separate decision, not a correctness question this
  ADR resolves.

**Derivatives data never flows back into Section 1.** There is no
write path from `market_intel` into `journal_entries`, `context_records`,
`candles`, or any other research table - verified the same way the
import boundary is: statically, not by inspection. The information flow
across this boundary is one-way and read-only.

**State-change alerting, not a fixed schedule.** Every hourly evaluation
is recorded in `market_intel_evaluations` (`evaluation_store.py`) - its
own table, its own declarative Base, never `journal_entries`,
`observations`, or `context_records` - whether or not it triggers a
send. Two independent triggers decide whether to actually alert:
- the classification differs from the **immediately prior evaluation**
  (sent or not) - mirroring `journal.changes`'s own "compare against the
  previous recorded row" pattern, including its behavior on the very
  first evaluation ever: with nothing to compare against, it never
  alerts, exactly like a first Section 1 evaluation is journal-only;
- BTC's stored (state, watch) differs from what the **last SENT
  briefing** carried - a deliberately different baseline from the
  classification check. The point of this trigger is "what you were
  last actually told about structure is now stale," not "structure
  ticked between two evaluations nobody saw" - comparing against the
  last evaluation rather than the last sent one would make a transient,
  unsent structure change on an otherwise-quiet hour retroactively
  "count" against a future comparison it was never actually measured
  against.

**NO_MATCH never alerts, full stop - not "changed NO_MATCH", not
"NO_MATCH after a real D-match either."** The rulebook document defines
no interpretation for those cases; a briefing has nothing true to say
about them beyond "no defined interpretation applies right now," so it
says only that, and never sends it. This is stated in both the rulebook
document and the classifier's own tests, not left implicit in the
alerting code alone.

**Idempotent per hour, with one narrow update exception.** A repeat
`tidemark intel briefing` invocation within the same closed 1H window
never recomputes or overwrites its classification/inputs/structure
fields - mirroring `JournalEntry.alert_sent`/`alert_reason` exactly,
`MarketIntelEvaluation.sent`/`send_reason` are the only fields a second
write may change, letting a dry-run recorded as `sent=False` later be
corrected to `sent=True` once the same hour's briefing is actually
delivered via `--send`.

### Consequences (Merge 3)

- The hourly BTC briefing is provably one-directional: it can read one
  specific, already-computed Section 1 fact, and it can never write to
  or recompute anything the research engine owns. The import-boundary
  test fails the build the moment either constraint is violated.
- `market_intel_evaluations` is a complete, queryable history of every
  hourly evaluation this system has ever made - sent or not - which is
  what makes "did this actually change, and when" answerable later
  without re-deriving it from Coinalyze's own (short) history retention.
- `docs/rulebook/derivatives-context-v0.1.md` is now load-bearing: a
  future threshold or interpretation change is a new rulebook version
  with its own classifier change reviewed against it, never a quiet edit
  of what a running system has been alerting on.
- BTC dominance remains unavailable and out of scope; this briefing adds
  no new sourcing for it.

## Addendum: removing `context_records`

The prior addendum's "Correction (post-launch)" fixed `context_read.py`
to read `journal_entries` instead of `context_records`. This addendum
removes `context_records` and its `ContextRecord` ORM model entirely -
structural cleanup only; no Section 1/2 rule, parameter, threshold, or
engine logic changes.

**What was actually there, confirmed from the code before changing
anything:**
- The only writer of `context_records` was ever the standalone
  `tidemark context evaluate` command (`cli.py`). `tidemark run` - what
  actually runs in production - evaluates Section 1 via `htf.evaluate`
  and persists the result only as a `JournalEntry`
  (`journal.records.build_journal_entry` + `TidemarkStore.
  save_journal_entry`), never as a `ContextRecord` row.
- Every other consumer of the `ContextRecord` type
  (`context/htf.py`'s own construction, `journal/changes.py`'s
  `detect_change`, `journal/records.py`'s `build_journal_entry`,
  `notify/telegram.py`'s rendering, `replay/report.py`'s entire
  in-memory replay) used it purely as `htf.evaluate`'s in-memory return
  shape - never touching the table.
- Readers of the table: `context history`/`context explain` (the CLI's
  own read-only pair) and `market_intel`'s hourly briefing (fixed in the
  prior addendum). `context_records` was empty on the live deployment
  this was found on; `journal_entries` held the real history.

**Decision on `tidemark context evaluate`: removed, not converted to
write `JournalEntry`.** `tidemark replay` (ADR 0008) already provides
point-in-time, side-effect-free Section 1 inspection for any symbol -
`context evaluate`'s standalone-evaluate-and-persist function was
redundant with it. Teaching it to write into `journal_entries` instead
would have let ad hoc, manually-triggered evaluations land in the same
append-only table `tidemark run`'s real, scheduled evaluations populate,
with no way to tell them apart later - a data-hygiene risk, not a
feature worth preserving. It was already unused in practice (zero rows
on the live deployment), so removing it changes no observed production
behavior. `context history` and `context explain` are kept, repointed at
`journal_entries` (`TidemarkStore.journal_history`) - same output
format, only the source changed. `_evaluate_symbol`/`_load_context_
candles`/`_candles_to_frame` (helpers that existed only to support
`context evaluate`) were removed alongside it, along with the now-
redundant `tests/context/test_look_ahead_guard.py` - the same look-
ahead-safety property it proved for `_evaluate_symbol` is still proven,
for the one remaining point-in-time evaluation path, by `tests/replay/
test_report.py::test_replay_section1_look_ahead_guard`.

**What changed:**
- `data/models.py`: `ContextRecord` is now a plain `@dataclass`, not a
  `Base`-mapped ORM class - same field names, same shape, so every
  in-memory consumer above needed zero changes. It no longer has a
  `__tablename__`, an `id`, or any relationship to a table at all -
  removing the class from the ORM's metadata is what makes it
  impossible for `context_records` to be silently recreated by a future
  `init_db()`/`create_all()` call.
- `data/store.py`: `save_context_record`, `latest_context_record`, and
  `context_history` (the `ContextRecord`-table methods) are removed.
  `TidemarkStore.journal_history` (already existed, already used
  elsewhere) is what `context history`/`context explain` now call.
- `cli.py`: `context evaluate` is removed. `context history`/`context
  explain` read `journal_history`/`journal_history()[0]` instead of the
  removed methods; their rendering code is untouched, since
  `JournalEntry` carries the exact same fields `ContextRecord` did.

**Tests updated to seed via the real production write path
(`build_journal_entry` + `save_journal_entry`), never a bespoke row
insert** - a bespoke insert into the wrong table/method is exactly what
let the original `context_records` bug through undetected for as long
as it went unnoticed. This applies to `tests/test_cli.py`'s `context
history`/`context explain` tests (new: `test_context_history_shows_
data_written_by_the_production_path`, `test_context_explain_shows_data_
written_by_the_production_path`) and to the `market_intel` fixtures that
previously called the now-removed `save_context_record` directly.

### Migration: dropping `context_records` from an existing database

There is no migration tooling in this project - `init_db()` only ever
runs `Base.metadata.create_all()`, which creates missing tables and
never drops anything. Removing `ContextRecord` from the ORM stops a
*fresh* database from ever gaining a `context_records` table, but an
*existing* database file (any local dev copy, and the live server) keeps
the orphaned table until it is dropped explicitly. This is a manual,
one-time step - not something this change automates, and no
general-purpose migration framework was added to do it.

Run these two steps, in order, against the actual database file
(`sqlite3 tidemark.db`, or the path in `TIDEMARK_DATABASE_URL`):

```sql
-- Step 1: confirm nothing is lost. This MUST print 0 before proceeding -
-- do not skip it or assume the count from this document.
SELECT COUNT(*) FROM context_records;
```

```sql
-- Step 2: only after Step 1 printed 0.
DROP TABLE context_records;
```

Equivalently, from a shell:

```bash
sqlite3 tidemark.db "SELECT COUNT(*) FROM context_records;"   # must print 0
sqlite3 tidemark.db "DROP TABLE context_records;"
```

This touches `context_records` only. `candles`, `journal_entries`,
`observations`, `runs`, `run_symbol_stats`, `swings`, `levels`,
`market_registry`, `universe_snapshot`, `universe_snapshot_row`, and
`market_intel_evaluations` are all untouched by this migration and by
this entire change - verified by the test suite (`test_init_db_creates_
expected_tables` now asserts `context_records` is absent from a fresh
database; the `market_intel` isolation tests independently confirm
`market_intel`'s own writes never touch a research table).

### Consequences (removing `context_records`)

- A table that exists but receives no writes is a trap for anything that
  queries it by name - it looks like a legitimate, checkable data
  source right up until the moment someone reads it and silently gets
  nothing. Removing the table removes the trap; keeping
  `journal_entries` as the one place Section 1 results live removes the
  ambiguity about which table is authoritative.
- `context evaluate` is gone; `tidemark replay` is the standalone,
  side-effect-free way to inspect what Section 1 would say about a
  symbol, and `context history`/`context explain` remain the read-only
  way to inspect what it actually did say, in production.
- Every future consumer of a Section 1 result - inside `market_intel` or
  anywhere else - has exactly one table to query:
  `journal_entries`.

## Addendum: /coin formatting and universe context

### Phase 1 — the formatting-pass gap

An operator reported that live `/coin` output on the server still showed
raw floats (`2410227.0534883 USD`, `"base asset units"`) despite the
formatting pass (`$1.04B`, `597,404 SOL`, three-decimal percentages)
having been built and merged. Investigated before changing anything:

- **The formatter is correctly wired.** `bot.py`'s `_handle_coin` calls
  `telegram_render.render_snapshot`, and that function has always
  contained the full formatting pass - confirmed by reading the current
  source, not assumed. There is no second code path in `bot.py` that
  bypasses it.
- **There is a second renderer, but the bot never uses it.**
  `cli.py`'s `_render_market_intel_snapshot` is a deliberately separate,
  unformatted-by-design renderer for `tidemark intel market` (a terminal
  audience, not a chat) - see this ADR's own "duplicate rather than
  couple" reasoning above. It was checked and ruled out as the cause.
- **The actual gap is test coverage, not wiring.**
  `test_telegram_render.py` calls `render_snapshot` directly and
  thoroughly verifies formatting; `test_bot.py`'s existing `/coin` tests
  went through the real `run_once` -> `_handle_message` -> `_handle_coin`
  path but never asserted on a formatted substring - only that a symbol
  string or a fixed constant appeared. A hypothetical future regression
  in the wiring (someone swapping which render function `bot.py` calls)
  would not have been caught by the bot's own tests. Fixed by adding
  `test_coin_reply_through_the_real_bot_path_is_formatted_not_raw`,
  which reproduces the reported symptom exactly (a large open-interest
  value) and asserts the abbreviated form appears and the raw float does
  not - going through the real dispatch path, not `render_snapshot`
  directly.
- **This means the code in this repository was never the cause of what
  was observed on the server.** The most consistent explanation,
  supported by there being no CI/CD or deployment automation in this
  project (`ci.yml` runs lint/tests only, on push/PR to `main` - it has
  no deploy step, and no Dockerfile or deployment script exists
  anywhere in the repo), is that the running bot process on the server
  predates the formatting-pass commit and was never restarted against
  it. That is an operational fact about that specific deployment, not
  something this merge's code changes: merging to `main` has never been
  synonymous with "running in production" for this project, and this is
  the first time that gap actually mattered.

### Phase 2 — the universe context cache

`/coin` compares one symbol's current long/short ratio, funding rate, OI
1H % change, and buy/sell volume ratio against the universe's own
distribution - median and p75 - without ever computing that distribution
inline. A live `intel distributions` run costs ~189s and ~120 Coinalyze
call-units; a single-threaded, sequentially-processing bot (`run_once`'s
`for update in updates` loop, see the Merge 2 addendum above) would be
unresponsive to every chat, not just the requester, for the entire
duration of one such reply. So the distribution is computed on its own
schedule and cached; `/coin` only ever does a fast local read.

**`universe_context_cache`, a fourth market_intel table, own Base.**
Following `evaluation_store.py`'s own precedent exactly: a brand-new
declarative Base (`UniverseContextBase`), its own duplicated
`_UTCDateTime`, never `tidemark.data.models.Base`. Deliberately its own
Base rather than sharing `evaluation_store.MarketIntelBase` too, so each
market_intel storage module stays as independently self-contained as the
last - this is consistent with the codebase's repeated choice to
duplicate a small amount of code across differently-scoped consumers
rather than couple them (the same reasoning behind `telegram_render.py`
vs. `cli.py`'s renderer, and `briefing_data.py` vs. `service.py`).
Append-only, unlike `market_intel_evaluations`: `tidemark intel
refresh-context` is the only writer, always inserts, never updates or
de-duplicates - a refresh that runs twice in the same hour simply
produces two rows, and nothing about `/coin`'s read path depends on
there being only one row per period.

**Why the refresh is 4x daily by default, not hourly.** A cross-sectional
median/p75 across ~30 symbols moves slowly compared to any single
symbol's own minute-to-minute reading - it reflects the whole universe's
positioning, not one coin's latest tick, so refreshing it as often as
the hourly BTC briefing refreshes BTC's own structure would spend
Coinalyze budget (~120 call-units per refresh, the same measurement
`intel distributions` performs) without the comparison value changing
meaningfully between runs. Four refreshes a day (~480 call-units/day,
against 40/minute = up to 57,600/day) keeps the comparison recent enough
that a 12-hour default staleness threshold (`TIDEMARK_
UNIVERSE_CONTEXT_STALE_AFTER_HOURS`) comfortably covers one missed
scheduled run without `/coin` ever silently going stale.

**Why `universe_snapshot_id` is recorded, never joined.** Universe
membership changes daily (`tidemark universe snapshot`'s own selection
can add or drop symbols), so a median computed from one day's cohort is
not necessarily the same comparison a reader would get from a fresher
one. Recording the id as a plain string (not a foreign key back to
`universe_snapshot`) lets a cached row always be traced to exactly the
cohort it was computed from, while keeping `universe_context_store.py`
free of any dependency on `tidemark.data` at all - unlike
`universe_read.py`, this table needs no boundary exception, since it
never reads the research engine's own snapshot tables, only records the
id string `intel distributions`/`universe_read.py` already produced.

**Why /coin shows numbers, not a comparison.** "STRICTLY NUMBERS. No
labels, no colours, no comparison words" is enforced the same way the
rest of `market_intel`'s no-interpretation constraint is: a fixed
forbidden-word list checked against every rendered message in tests
(now extended with `elevated`, `crowded`, `high`, `low`, `above`,
`below`, `bullish`, `bearish`, `avoid`, `strong`, `weak`, `setup`). The
reasoning is the same as everywhere else in this ADR: Tidemark's whole
premise is that a human makes every trading decision, and a tool that
says "2.07 is above the median of 1.74" has already made half of that
judgment for the reader, even without ever saying "elevated." Showing
both numbers, unlabeled, side by side, leaves the comparison entirely to
the person reading it.

**OI 1H % change is fetched independently, not reused from
`open_interest_change`.** `MarketIntelSnapshot.open_interest_change`
(the value `/coin` has always shown) is an absolute USD difference, not
a percentage - a different quantity from what `intel distributions`
measures and the cache stores, not just a different format. Pairing a
USD "current" value with a percentage median/p75 under near-identical
labels would silently invite exactly the kind of misreading "numbers
only, no comparison words" is trying to avoid a different way - two
numbers that look comparable but aren't. `coin_universe_context.py`
fetches its own `open_interest_history` bucket and computes the
percentage directly instead, at a cost of one extra Coinalyze call per
`/coin` lookup (`ESTIMATED_CALL_COST_PER_COIN_LOOKUP` moved from 7 to
8) - not the ~120-call universe scan, a single extra call, matching
`service.py`'s own budget-consciousness rather than expanding it.

**Funding rate reuses the snapshot's existing LIVE value, a deliberate,
documented exception to that same reasoning.** Unlike OI, funding's
"current" and cached values share the same unit (`%`), so there is no
unit-mismatch risk - but they are still measured differently: `/coin`'s
`funding_rate` is Coinalyze's live point-in-time reading (no closed-
period concept exists for it on that endpoint - see this ADR's original
Merge 1 context), while the cache's `funding_rate` summary is the closed
1H reading `intel distributions` measures, the same choice
`briefing_data.py` made for the classifier. Fetching a second, closed-
period funding reading just for this one comparison would add yet
another Coinalyze call for a live-updating value that already changes
every few seconds regardless. This is recorded here as a conscious
tradeoff, not an oversight: the two numbers are both real, both
correctly labeled with their own timestamps (the live reading's own
"live, updated HH:MM" and the cache's own "As of HH:MM"), and never
conflated as the same instant.

**Long/short ratio and buy/sell ratio need no extra fetch at all.**
Long/short ratio is already the same closed-period quantity on both
sides (`/coin`'s existing `long_short_ratio.ratio` and the cache's
`long_short_ratio` summary both come from `/long-short-ratio-history`'s
`r` field). Buy/sell ratio is computed from the two volume values
`fetch_market_intel` already fetches for `/coin`'s existing "Buy
volume"/"Sell volume" lines (`buy_volume.value / sell_volume.value`) -
zero additional Coinalyze calls for either metric.

**A missing cache entirely omits every universe line; a missing current
value for one metric omits only that metric's lines.** These are two
different kinds of "nothing to show," handled at two different levels:
`gather_coin_universe_context` returns `None` outright if
`universe_context_cache` has never been refreshed (so `/coin` renders
exactly as it did before this addendum, with no universe content at
all), and returns `None` for one specific metric's `MetricContext` when
this particular symbol has no current value for it (e.g. a market
without `has_long_short_ratio_data`) even though the cache itself has
real data for the universe as a whole - never a fabricated current value
paired with a real median/p75, and never a median/p75 rendered as if
computed from zero symbols when the truth is "this symbol has none."

### Consequences (/coin formatting and universe context)

- Every claim in Phase 1's investigation was checked against the actual
  current source before any test was written - the formatter was never
  broken in this repository; the gap was coverage and, most likely,
  deployment lag on one specific server.
- `render_snapshot`'s signature gained one optional, defaulted parameter
  (`universe_context: CoinUniverseContext | None = None`); every
  existing caller and test that doesn't pass it renders byte-for-byte
  what it always rendered - verified by
  `test_none_universe_context_renders_exactly_as_before`.
- `ESTIMATED_CALL_COST_PER_COIN_LOOKUP` is now 8, not 7 - the one honest
  accounting change this addendum makes to `/coin`'s own Coinalyze
  budget, documented at its definition in `bot.py`.
- `universe_context_cache` has no relationship to `journal_entries`,
  `observations`, `context_records` (removed), `market_intel_
  evaluations`, or `universe_snapshot`/`universe_snapshot_row` beyond
  recording the latter's id as a plain string - verified the same way
  every other market_intel table boundary is verified in this project:
  a test asserting the table list a fresh engine produces.
- No rulebook exists for coin-context thresholds and this addendum adds
  none - `intel distributions`/`refresh-context`/`/coin`'s new lines
  measure and display only; choosing what counts as "elevated" for any
  of these four metrics remains a future, separate rulebook decision.
