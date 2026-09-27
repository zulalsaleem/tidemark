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
