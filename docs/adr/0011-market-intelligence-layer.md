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

## Addendum: intel distributions

Before any coin-context rulebook (long/short ratio, funding, OI change,
buy/sell imbalance thresholds) can be written, someone has to look at
what those metrics actually look like across the universe - otherwise a
threshold like "elevated" or "crowded" is a guess, not a measurement.
`tidemark intel distributions` exists to answer exactly that question,
and nothing else: it fetches the same four closed-period metrics for
every symbol in the currently selected universe and prints raw values
plus n/min/p25/median/p75/max per metric. It produces no interpretation
of its own - no label, no flag, no bias - and it does not write a
rulebook; it is the measurement step that has to happen before one can
be written honestly.

**A second, narrow boundary exception, alongside `context_read.py`.**
Loading "the currently selected symbols from the latest universe
snapshot" means reading `UniverseSnapshot`/`UniverseSnapshotRow` -
tables the research engine's universe-selection pipeline
(`data/universe_snapshot.py`) writes. `universe_read.py` is the second
(and, by the same import-boundary test that now checks for exactly two
named files, still deliberately singular per concern) file permitted to
import `tidemark.data.models`, for this one read-only purpose. It
never imports `data.symbol_source` or `data.store`, and it never
reproduces `resolve_symbols`'s `TIDEMARK_SYMBOLS` fallback: a missing or
empty snapshot is returned as `(None, [])` for the caller to report
plainly, never silently substituted with a different universe than the
one requested.

**Funding here is the closed-period reading, not the live one.** `/coin`
and `intel market`'s funding metric is deliberately a live,
point-in-time value (Coinalyze has no closed-period funding "current"
concept in that sense). `intel distributions` instead reads
`funding_rate_history`'s closed 1H bucket, the same choice
`briefing_data.py` already made for the classifier - CLAUDE.md's "closed
candles only" rule applies to a measurement exactly as much as to a rule
evaluation, and a distribution built from a live value would describe a
different instant for every symbol depending on when the run happened
to reach it.

**Pacing is additive to the client's own reactive 429 handling.**
`CoinalyzeClient` already retries a 429 with bounded backoff honoring
`Retry-After`, but deliberately never preempts a call on its own
tracked call rate (see `calls_in_last_minute`'s docstring). A
distributions run is large enough (~30 symbols x up to 4 call-units =
up to 120, against the 40/minute budget) that relying on reactive
retries alone would mean routinely eating several 429s per run. `intel
distributions` adds its own proactive wait before each symbol's calls
when the tracked rate is within the reserved cost of that symbol's
worst case (4 call-units, even though a symbol missing long/short-ratio
or buy/sell data actually costs less) - a deliberately conservative,
never-under-reserving estimate that keeps the pre-call check simple.

**A rate-limit budget exhaustion is a partial result, never a crash.**
If the client's own bounded retries are exhausted mid-run
(`RateLimitedError`), every symbol not yet fetched is reported in
`skipped_symbols` and the run still returns its summary over whatever it
did fetch - "no setups found" is a successful run per CLAUDE.md, and the
same spirit applies here: an incomplete measurement, honestly reported
as incomplete, is still useful; a crash is not.

### Consequences (intel distributions)

- The import-boundary test now names exactly two files
  (`context_read.py`, `universe_read.py`) as the only permitted
  importers of `tidemark.data.models` - the allowlist stays as narrow as
  the number of genuine read-only needs `market_intel` actually has, not
  a general-purpose door into `tidemark.data`.
- This command informs threshold selection for a future coin-context
  rulebook version; it is not itself part of any rulebook and makes no
  claim about what a threshold should be. Choosing one from its output
  is a separate, deliberate step with its own rulebook change.
