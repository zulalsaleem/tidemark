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
  journal/records.py   EVALUATION -> JOURNAL
                        append-only: one row per evaluation, incl. every WAIT
        |
        v
  journal/changes.py   JOURNAL -> CHANGE DETECTOR
                        pure function; previous journal row + current
                        evaluation -> an alert reason, or None
        |
        v (only when a reason was returned)
  enrich/alerts.py     JOURNAL -> TELEGRAM   (separate command, own timer)
                        `tidemark alert enrich`: composes the alert from
                        the journal row + market context, then sends it
```

The journal is the complete research record; Telegram is a filtered
notification layer on top of it. They are never coupled — a Telegram
failure never prevents or rolls back a journal write, and the journal
write always happens first. See
[docs/adr/0005-journal-and-alert-separation.md](docs/adr/0005-journal-and-alert-separation.md).

`context/mtf.py` (Section 2, 1H behavior) runs as a separate,
parallel measurement pipeline, gated by Section 1's WATCH state:

```
context/htf.py journal (Section 1, read-only input)
        |
        v
  context/mtf.py   Section 2 — 1H behavior, evaluated at every 1H close
                    while the latest Section 1 record is a WATCH
        |
        v
  journal/observe_pipeline.py -> observations table
                        append-only: one row per 1H close under an
                        active WATCH, including every no-reaction row
```

It never writes to the Section 1 journal, never calls
`journal/changes.py`, and never calls `notify/telegram.py` — see
[docs/adr/0007-section-2-observation-only.md](docs/adr/0007-section-2-observation-only.md).

Persistence (SQLite via SQLAlchemy) sits alongside this pipeline in
`data/store.py`, holding closed candles, rejected candles, run records,
emitted context records, and journal rows. Swings, levels, and Fib legs
are pure calculations recomputed from stored candles at every evaluation
rather than persisted separately. Market data comes from
`data/exchange.py`, a ccxt-backed client with no exchange credentials —
see
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
cp .env.example .env           # fill in Telegram config (market data needs no credentials);
                                # add TIDEMARK_COINALYZE_API_KEY only if you want `tidemark intel`
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

# Evaluate Section 1 and journal the result - see "Using the run pipeline"
# below. `context history`/`explain` are read-only inspection of what that
# journaled: there is no standalone evaluate/persist command (removed as
# redundant with `tidemark replay`'s point-in-time reproduction - see
# docs/adr/0011-market-intelligence-layer.md's "Addendum: removing
# context_records").
uv run tidemark run --symbols BTC/USDT:USDT

# Past evaluations, and a human-readable breakdown of the latest one.
uv run tidemark context history --symbol BTC/USDT:USDT
uv run tidemark context explain --symbol BTC/USDT:USDT
```

## Using the run pipeline

```bash
# Evaluate Section 1 for each symbol, journal the result (a no-op if this
# 4H candle is already journaled), and record the change detector's alert
# reason if it finds one. It sends nothing: `tidemark alert enrich` does.
# One symbol failing gives PARTIAL, not FAILED.
uv run tidemark run --symbols BTC/USDT:USDT

# The full research record for a symbol, newest first — every evaluation,
# including every WAIT.
uv run tidemark journal list --symbol BTC/USDT:USDT

# Alerts the enricher actually sent, with their send time (ADR 0012).
uv run tidemark journal alerts

# Send one fixed message to prove TIDEMARK_TELEGRAM_BOT_TOKEN /
# TIDEMARK_TELEGRAM_CHAT_ID work. Writes nothing to the journal.
uv run tidemark notify test
```

## Health check and heartbeat

Telegram is silent unless state changes, so silence is ambiguous — it
could mean nothing changed, or it could mean the system died. `health`
makes silence trustworthy by inspecting what's actually in the database:
is it reachable, is candle data fresh, did the last run succeed, is the
journal still being written, are there gaps, and is Telegram even
configured. See
[docs/adr/0006-health-check-design.md](docs/adr/0006-health-check-design.md)
for why the thresholds are what they are.

```bash
# Every check, human-readable. Exit code is 0/1/2 for OK/WARN/FAIL, so
# systemd (or any monitor) can treat a degraded state as a failure.
uv run tidemark health check

# Same checks, machine-readable — for future monitoring.
uv run tidemark health check --json

# Run the checks and send one Telegram summary. The only `health`
# command that sends anything; never writes to the journal, since a
# heartbeat is a system-status message, not a research observation.
uv run tidemark health heartbeat
```

## Using Section 2 observation

Section 2 v0.1 is a **measurement layer, not a signal layer** — see
[docs/rulebook/section-02-1h-behaviour-v0.1.md](docs/rulebook/section-02-1h-behaviour-v0.1.md)
(status: `PROVISIONAL — OBSERVATION ONLY`). It never computes an entry,
stop, target, or R:R, never sends a Telegram alert, and never feeds
15M — `HANDOFF_TO_15M` is recorded as an observed state only.

```bash
# Backfill closed 1H candles too - Section 2 needs them alongside 4H/1D/1W.
uv run tidemark data backfill --symbols BTC/USDT:USDT --timeframes 4h,1d,1w,1h --days 180

# Evaluate Section 2 for each symbol and journal every 1H observation row
# (only while that symbol is under an active Section 1 WATCH). No alerts.
uv run tidemark observe run --symbols BTC/USDT:USDT

# Past observations for a symbol, newest first.
uv run tidemark observe list --symbol BTC/USDT:USDT

# Counts by state, reason_code, and reaction tier - the review tool for
# deciding whether v1.0 is ever warranted.
uv run tidemark observe stats
```

## Replaying Section 1 and Section 2

`tidemark replay` is a read-only, point-in-time replay over already-stored
candles — it never writes to the context, journal, or observation tables.
It reports Section 1 per evaluation, Section 2 per session, and Section 2
per evaluation as three separate tables that are never summed together
(mixing those units the wrong way is exactly what produced an earlier,
invalid ad-hoc comparison — see
[docs/adr/0008-replay-as-a-repo-command.md](docs/adr/0008-replay-as-a-repo-command.md)).
It also records a hash over the candle rows it used, so a later replay
against different history says so plainly instead of being silently
compared.

```bash
# Report against every symbol's full stored history.
uv run tidemark replay --rule-version section-02-v0.1

# Limit to specific symbols and/or the last N days.
uv run tidemark replay --rule-version section-02-v0.1 --symbols BTC/USDT:USDT --days 90
```

The frozen v0.1 baseline this produced is committed at
[docs/replay/section-02-v0.1-baseline.md](docs/replay/section-02-v0.1-baseline.md)
— what a future v0.2 replay is compared against.

## Querying the evidence archive

`tidemark evidence` is a read-only, frozen-metric query over the
persisted `observations`/`journal_entries` archive — it never
re-evaluates the rulebook (that's `tidemark replay`) and never writes
anything. It exists to build an evidence base toward Section 2's four
open questions (SEC2-01 through SEC2-04 — see
[docs/rulebook/section-02-1h-behaviour-v0.2.md](docs/rulebook/section-02-1h-behaviour-v0.2.md)'s
OPEN QUESTIONS) without answering any of them: every count it reports
is raw ("sessions containing R1", "occurrences of
`CONTINUATION_CANDIDATE_NOT_EVALUATED`"), never a judgment of validity
or success. See
[docs/adr/0010-evidence-command.md](docs/adr/0010-evidence-command.md)
for the full design, including why its session-grouping logic is a
deliberately independent copy of `tidemark replay`'s rather than a
shared import.

```bash
# Full report: archive coverage, Section 2 sessions, SEC2-01..04 raw
# evidence, a regime proxy, and data-sufficiency verdicts - each also
# broken out by week and month.
uv run tidemark evidence

# Narrow to a symbol and/or a date range.
uv run tidemark evidence --symbol BTC/USDT:USDT --from 2026-09-01 --to 2026-09-30

# Machine-readable output.
uv run tidemark evidence --json
```

**It refuses full output by default on a young archive** (spanning
fewer than 14 days): only the coverage summary and the sufficiency
verdicts print, and the command exits non-zero, so a single short
regime can never be mistaken for general evidence.
`--allow-insufficient` prints the full report anyway, headed
`PRELIMINARY — ... not valid for Section 2 conclusions`. Data
sufficiency (`INSUFFICIENT`/`EMERGING`/`SUFFICIENT`) is about whether a
question can be *investigated at all* from the fixed thresholds in
`data/evidence.py` — never what its answer would be.

## Universe selection (Phase 6)

A universe-selection layer is being added on top of the fixed,
manually-configured `TIDEMARK_SYMBOLS` list, split into three separate
tables so "not selected" can never be confused with "no data existed" —
see [docs/adr/0009-universe-selection-architecture.md](docs/adr/0009-universe-selection-architecture.md):
`market_registry` (what the venue has ever contained), `universe_snapshot`
/ `universe_snapshot_row` (what a methodology selected, every ranked
symbol recorded, not only the winners), and the existing journal/
observation tables (what Tidemark actually evaluated).

Merge 2A discovered the venue's symbol catalog and backfilled daily
candles for it. Merge 2B (this one) computes the metric, assesses
eligibility, and generates the snapshot itself:

```bash
# Refresh market_registry from the venue's live listing: active
# USDT-quoted perpetuals become ACTIVE, previously-registered symbols no
# longer listed become ABSENT_FROM_VENUE (never deleted).
uv run tidemark universe discover

# Backfill 1D candles for every ACTIVE registry symbol - a separate step
# from Section 1/2's 4H/1H/1D/1W backfill, and independent of it. Prints
# progress per symbol; a full venue listing can be several hundred
# symbols and take a while.
uv run tidemark universe backfill --days 120

# Generate a snapshot: rank every ACTIVE symbol by
# MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D, backfill 4H candles for the top
# K=50 if not already stored, assess Section 1 eligibility (the LOCKED
# v1.1 engine, unmodified) for those 50, and select the top N=30
# eligible by rank.
uv run tidemark universe snapshot

# The same, reconstructed as of a past timestamp instead of live - never
# touches the network, never updates market_registry's eligibility cache.
uv run tidemark universe snapshot --as-of 2026-09-01T00:00:00+00:00

# Market-registry rows: status, candle coverage, and stored row counts.
uv run tidemark universe registry

# Universe snapshot headers, newest first.
uv run tidemark universe snapshots

# One snapshot's full ranking - selected symbols first, excluded symbols
# shown too with their exclusion_reason. A rank below K=50 shows
# eligible as "-" (NULL): NOT_ASSESSED, never a reported failure.
uv run tidemark universe show --snapshot-id <id>

# Coverage report: symbols on venue, eligible, assessed, selected, data
# available, and counts by exclusion reason.
uv run tidemark universe coverage

# Backfill 1H/4H/1D/1W - every timeframe the observer needs - for the
# currently SELECTED symbols only. Idempotent; safe to re-run.
uv run tidemark universe sync --days 180
```

Discovery uses ccxt's unified `load_markets` — a second, separate,
venue-agnostic read-only call alongside candle fetching (never a
venue-specific raw endpoint); both backfill commands reuse the existing
`data backfill` ingest path unmodified. All read-only commands report an
empty database gracefully ("No ... found yet.") rather than erroring.

**Symbol source resolution (Phase 6, Merge 3).** `tidemark run`,
`tidemark observe run`, and `tidemark health check` now default to the
SELECTED symbols of the latest valid universe snapshot for the configured
venue, in this order: an explicit `--symbols` flag always wins; else the
latest snapshot, if one exists and is less than
`TIDEMARK_UNIVERSE_STALENESS_HOURS` (default 48) old; else
`TIDEMARK_SYMBOLS` as a last-resort fallback, with a warning printed
naming which source was used. `TIDEMARK_SYMBOLS` is not deleted — it
remains available as a manual/development override. Every run records
which source it used (and the snapshot id, when one was used) on its
`runs` row, so a journal entry can always be traced back to the universe
that produced it. See
[docs/adr/0009-universe-selection-architecture.md](docs/adr/0009-universe-selection-architecture.md)'s
Merge 3 section for the full design.

**UNIV-08 — asset-class domain constraint.** The first real snapshot
selected 14 of 30 symbols (47%) from outside cryptocurrency: tokenised
equities and commodities (MSTR, TSLA-style single stocks, gold, oil...).
A full-venue scan found 202 of 727 ACTIVE symbols (27.8%) are non-crypto.
Tidemark's rulebook is validated for crypto market structure only, so
`universe snapshot` now excludes any symbol whose Binance
`underlyingType` (read from the same `load_markets()` response discovery
already consumes — no new call) isn't `COIN`: `INDEX` (crypto-basket
products like BTCDOM) is excluded as `NON_ELIGIBLE_INDEX`, every other
known TradFi type as `NON_CRYPTO_UNDERLYING`, and anything the classifier
doesn't recognize fails closed to `UNKNOWN_UNDERLYING_TYPE` rather than
being guessed. `universe show` now displays each row's `ASSET_CLASS`.
Classification is captured once at discovery time and never
overwritten — a later venue-metadata change can never retroactively
alter what an already-generated snapshot's classification meant, the
same UNIVERSE_AS_OF_INVARIANT already applied to candle data. The
pre-UNIV-08 snapshot (`methodology_version="universe-v1"`) is untouched,
append-only, and kept as engineering data only; every snapshot from this
point on is tagged `"universe-v2"`. Ranking, N, and K are unchanged —
this is a domain eligibility correction, not a ranking change.

## Live market intelligence (Coinalyze)

A live, read-only derivatives-data layer — strictly separate from the
research engine above (Section 1/2, the journal, universe selection).
It never influences a rulebook evaluation, and it never writes to
`observations`, `journal_entries`, `context_records`, or `candles`. See
[docs/adr/0011-market-intelligence-layer.md](docs/adr/0011-market-intelligence-layer.md).

```bash
# Requires TIDEMARK_COINALYZE_API_KEY in .env (free key: see .env.example).
uv run tidemark intel market --symbol BTC/USDT:USDT
uv run tidemark intel market --symbol BTC/USDT:USDT --json
```

Prints one symbol's open interest, OI change, current and predicted
funding rate, long/short ratio, liquidations, futures volume, and
buy/sell volume — each with its own unit and the exact closed period it
covers (or its own live-update timestamp for a point-in-time reading).
Coinalyze does not truncate to closed periods on its own, so this always
computes and clamps to the latest fully-elapsed window itself, never the
in-progress one. A symbol Coinalyze doesn't list prints `MARKET_NOT_FOUND`
with a non-zero exit; a symbol that's listed but has no data flowing for
a given metric prints `NO_DATA` for that metric only — never a fabricated
zero. Every figure is Binance-only (ADR 0002's canonical venue) and, where
Coinalyze supports it, USD-converted via Coinalyze's own undocumented
methodology. Raw data only: no interpretation, no bias, no trading
recommendation, and BTC dominance is not available from this source at
all (see the ADR for what sourcing it would require).

### The /coin Telegram bot

A long-polling bot that answers `/coin <SYMBOL>` with the same Merge 1
snapshot, on demand, for any Binance USDT-M perpetual — not only the
30-symbol research universe. Read-only in every sense: no orders, no
exchange credentials, no signing. Reuses `TIDEMARK_TELEGRAM_BOT_TOKEN`
(the same bot the Section 1/2 alert path uses, if configured) but is
otherwise fully independent of it — its own client, its own message
renderer, its own text.

```bash
# In addition to TIDEMARK_COINALYZE_API_KEY and TIDEMARK_TELEGRAM_BOT_TOKEN:
# TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID=<your numeric chat id>   # get it from @userinfobot

uv run tidemark intel bot          # long-running foreground process, Ctrl+C to stop
uv run tidemark intel bot --once   # process any pending updates once and exit (for testing)
```

**Authorization is by numeric chat ID only** — never username or
display name, both of which are attacker-controlled. Any chat other
than `TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID` is silently ignored: no reply,
nothing that confirms the bot exists, only a log line recording the
chat id and timestamp (never the message text). See
[docs/adr/0011](docs/adr/0011-market-intelligence-layer.md#addendum-merge-2--the-coin-telegram-bot)
for the full reasoning, including why long polling was chosen over a
webhook, and why a missing bot token or Coinalyze key exits cleanly
rather than crashing.

On startup, any backlog older than a few minutes is discarded (so a
restart never answers a question asked hours ago), and the Telegram
`getUpdates` offset is persisted to a small JSON file
(`TIDEMARK_TELEGRAM_BOT_OFFSET_FILE`, unrelated to `tidemark.db`) so a
restart never replays an already-answered message or skips a new one. A
network failure or Telegram outage backs off and retries rather than
exiting; an invalid bot token (HTTP 401/403) exits immediately instead
of retrying forever against a guaranteed failure.

**Running it unattended (systemd example).** This is illustrative, not
prescriptive — it assumes nothing about your server beyond systemd
being available, and you should adjust the user, paths, and Python
invocation to match your actual deployment:

```ini
# /etc/systemd/system/tidemark-coin-bot.service
[Unit]
Description=Tidemark /coin Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tidemark
WorkingDirectory=/opt/tidemark
EnvironmentFile=/opt/tidemark/.env
ExecStart=/opt/tidemark/.venv/bin/tidemark intel bot
Restart=always
RestartSec=5
MemoryMax=256M
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/tidemark

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tidemark-coin-bot
journalctl -u tidemark-coin-bot -f   # tail the logs
```

`Restart=always` covers the case this bot's own backoff loop can't:
the process being killed outright (OOM, a host reboot, a manual stop).
`EnvironmentFile` keeps every secret (`TIDEMARK_TELEGRAM_BOT_TOKEN`,
`TIDEMARK_COINALYZE_API_KEY`, `TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID`) out
of the unit file itself and out of `systemctl status`/`ps` output.
`MemoryMax` is a sane ceiling for a single long-polling HTTP client
process, not a measured requirement — size it to your host.

### Hourly BTC briefing

Classifies BTC's latest closed-1H price/OI/funding reading against
[docs/rulebook/derivatives-context-v0.1.md](docs/rulebook/derivatives-context-v0.1.md)
(one of six defined combinations, D1-D6, or `NO_MATCH`) and shows it
alongside BTC's stored Section 1 structure — two independent sections,
two independent sources. Alerts only on a real change, never on a fixed
schedule: see the
[ADR's Merge 3 addendum](docs/adr/0011-market-intelligence-layer.md#addendum-merge-3--the-hourly-btc-briefing)
for exactly what counts as a change.

```bash
uv run tidemark intel briefing            # evaluate, print, record - never sends
uv run tidemark intel briefing --json     # machine-readable output
uv run tidemark intel briefing --send     # also deliver via Telegram if the evaluation decides to
```

Without `--send`, nothing is ever delivered to Telegram — the
evaluation still runs and is still recorded in `market_intel`'s own
table, so repeated dry runs are safe for testing. `--send` only gates
delivery: the evaluation itself, and the decision of whether it's worth
sending, happen unconditionally either way.

Every evaluation is recorded, sent or not, in `market_intel_evaluations`
— its own table, never `journal_entries`, `observations`, or
`context_records`. Two independent triggers decide whether an evaluation
actually sends:

- the classification differs from the **immediately prior evaluation**
  (sent or not);
- BTC's stored Section 1 `(state, watch)` differs from what the **last
  SENT briefing** carried — a different baseline on purpose, since the
  point is "what you were last told is now stale," not "something
  changed between two evaluations nobody saw."

`NO_MATCH` never sends, under any circumstance — the rulebook defines no
interpretation for those readings, so there's nothing true to alert on.
BTC's structure section reads `journal_entries` read-only (never
recomputed, never written to — the table `tidemark run` actually writes
Section 1 results to) and renders `UNAVAILABLE` if there's no result for
BTC or the latest one is stale (see the ADR for the exact threshold) —
it never falls back to computing structure itself.

**Running it hourly (systemd timer example).** Same caveats as the bot
example above — illustrative, not prescriptive:

```ini
# /etc/systemd/system/tidemark-briefing.service
[Unit]
Description=Tidemark hourly BTC briefing
After=network-online.target

[Service]
Type=oneshot
User=tidemark
WorkingDirectory=/opt/tidemark
EnvironmentFile=/opt/tidemark/.env
ExecStart=/opt/tidemark/.venv/bin/tidemark intel briefing --send
```

```ini
# /etc/systemd/system/tidemark-briefing.timer
[Unit]
Description=Run the Tidemark BTC briefing hourly

[Timer]
# 5 minutes past the hour: gives the Section 1 4H-close evaluation and
# the Section 2 hourly observer (see "Using the run pipeline" and "Using
# Section 2 observation" above) a head start, so the briefing's
# structure section reads a journal_entries row from THIS hour rather
# than racing it.
OnCalendar=*-*-* *:05:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tidemark-briefing.timer
systemctl list-timers tidemark-briefing.timer   # confirm the schedule
journalctl -u tidemark-briefing -f              # tail the logs
```

`Type=oneshot` (not a long-running process, unlike the bot) matches
`tidemark intel briefing`'s own shape: it evaluates once and exits.
`Persistent=true` catches up on a missed run after downtime rather than
silently skipping an hour. The `:05` offset is a suggestion, not a
requirement — it only matters if you also run `tidemark run`/
`tidemark observe run` on their own schedules and want the briefing to
read a fresh Section 1 record rather than an hour-old one.

### Measuring metric distributions

Before any coin-context rulebook threshold is chosen for long/short
ratio, funding, OI change, or buy/sell imbalance, `tidemark intel
distributions` measures what those metrics actually look like across
the universe, so a threshold like "elevated" is chosen from real numbers
rather than guessed. It is a measurement tool, not a rule: it prints raw
values and n/min/p25/median/p75/max per metric, never a label,
interpretation, or trading recommendation.

```bash
uv run tidemark intel distributions                              # the latest universe snapshot
uv run tidemark intel distributions --json                       # machine-readable output
uv run tidemark intel distributions --symbols BTC/USDT:USDT,ETH/USDT:USDT  # ad-hoc override
```

Symbols default to the SELECTED rows of the latest universe snapshot for
the configured venue — the same source `tidemark run` uses, never
`TIDEMARK_SYMBOLS` — in rank order. `--symbols` overrides that for an
ad-hoc measurement. Every value is either a number for a fully-closed 1H
period (the same clamping `/coin` and the hourly briefing use) or
`UNAVAILABLE`, never a fabricated zero; a symbol's `UNAVAILABLE` metrics
are excluded from that metric's `n` and counted in its own
`unavailable` total instead.

Coinalyze's 40 calls/minute budget is shared across the whole run —
roughly 4 call-units per symbol across the four metrics — so the command
paces itself against the observed call rate and still honors `Retry-
After` on a 429. If the budget runs out mid-run, it reports which
symbols were not fetched rather than failing the whole run, and always
reports how long it took.

### Universe context for /coin

`/coin` can show each metric's current value beside the universe's own
median and p75 — strictly numbers, no labels, no comparison words
("elevated", "above", etc.) — read from a small cache
(`universe_context_cache`) rather than ever running a live ~189-second
universe scan inside a chat reply.

```bash
uv run tidemark intel refresh-context   # runs the distributions measurement, writes one cache row
```

`refresh-context` is the only writer of that cache; `intel distributions`
itself stays fully read-only and unchanged. Run it on a schedule (4x
daily is the suggested default — see the ADR's addendum for why hourly
isn't worth it: a cross-sectional median across ~30 symbols moves slowly,
and a refresh costs the same ~120 Coinalyze call-units `intel
distributions` costs). `/coin` reads only the most recently cached row —
a fast local read, never a live fetch of the whole universe. If that row
is older than `TIDEMARK_UNIVERSE_CONTEXT_STALE_AFTER_HOURS` (default 12),
`/coin` still shows it, with its age stated plainly (e.g. "14h old") —
never silently hidden. If the cache has never been refreshed at all,
`/coin` omits the universe lines entirely and renders exactly as it
always did — a missing cache must never break the bot.

**Running it on a schedule (systemd timer example):**

```ini
# /etc/systemd/system/tidemark-refresh-context.service
[Unit]
Description=Tidemark /coin universe context refresh
After=network-online.target

[Service]
Type=oneshot
User=tidemark
WorkingDirectory=/opt/tidemark
EnvironmentFile=/opt/tidemark/.env
ExecStart=/opt/tidemark/.venv/bin/tidemark intel refresh-context
```

```ini
# /etc/systemd/system/tidemark-refresh-context.timer
[Unit]
Description=Refresh Tidemark's /coin universe context cache 4x daily

[Timer]
OnCalendar=*-*-* 00,06,12,18:15:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tidemark-refresh-context.timer
systemctl list-timers tidemark-refresh-context.timer   # confirm the schedule
journalctl -u tidemark-refresh-context -f              # tail the logs
```

Same `Type=oneshot`/`Persistent=true` reasoning as the hourly briefing
timer above: it runs once and exits, and a missed run is caught up on
rather than silently skipped. The `:15` offset just avoids the top of
the hour if other scheduled jobs also land there — not a requirement.

### Position flow: /coin's own classification

`/coin` classifies price and open interest moving together over one
closed 1H window into one of nine mechanical states — `LONG_BUILDUP`,
`SHORT_COVERING`, `QUIET`, and so on — against its own, independently
versioned rulebook,
[docs/rulebook/position-flow-v0.1.md](docs/rulebook/position-flow-v0.1.md).
This is entirely separate from
[derivatives-context-v0.1.md](docs/rulebook/derivatives-context-v0.1.md)
(the hourly BTC briefing's own classifier) — different document,
different classifier module, neither reads or depends on the other, and
works for any Binance USDT-M perpetual, not only BTC. No extra
Coinalyze calls: both inputs (price change, OI change — both as
percentages over the closed 1H window) are derived from OHLCV/
open-interest-history buckets `/coin` already fetches for its other
lines.

Every `/coin` reply now has three layers:

- **FACTS** — every existing raw metric, unchanged.
- **POSITION FLOW** — the classified state (or `NO_MATCH` with a reason,
  if price or OI change is unavailable), the price/OI inputs that
  produced it, and the rulebook version.
- **SUPPORTING CONTEXT** — funding, long/short ratio, buy/sell flow, and
  liquidations stated as plain directional facts (`POSITIVE`,
  `LONG-BIASED`, `BUYING > SELLING`, `SHORT > LONG`) — never a label
  like `BULLISH`/`CROWDED`/`STRONG`, which no rulebook here defines.
  These are NOT inputs to the classification — position-flow-v0.1 reads
  only price and open interest.

Still not a trade signal: no entries, stops, targets, or R:R, ever.

## Alert enricher (`tidemark alert enrich`)

`tidemark run` journals and decides; it sends nothing. The Telegram
WATCH alert is composed and sent by a separate command, which reads the
journal entries it has not yet processed. The full design is in
[docs/adr/0012-alert-enricher.md](docs/adr/0012-alert-enricher.md).

```bash
uv run tidemark alert enrich --dry-run   # the default: compose and print, write nothing
uv run tidemark alert enrich --send      # send, and advance the durable cursor
```

- **Cursor.** A checkpoint in market_intel's own tables records the last
  journal entry processed. It advances only after a successful send, so a
  failed run is retried by the next one. Each sent alert is recorded
  against its journal entry, so a re-run never sends it twice.
- **First run.** With no checkpoint, the enricher starts at the latest
  journal entry, sends nothing for history, and says so.
- **Outages.** If Coinalyze is unreachable, the alert still sends, with
  the market context marked `UNAVAILABLE`. The Section 1 facts never
  depend on Coinalyze.
- **Nothing is written to research tables.** Its only writes are the two
  cursor tables, and only on a send.

### Scheduling it (systemd timer, example only)

The research run, the enricher, and their timers are separate units. The
example below runs the enricher at :08 past each 4H boundary. Every path,
user, and file here is a placeholder: replace them with your own. Nothing
in this repository assumes a particular server.

```ini
# /etc/systemd/system/tidemark-alert-enrich.service
[Unit]
Description=Tidemark alert enricher (compose and send Section 1 WATCH alerts)
After=network-online.target

[Service]
Type=oneshot
User=<service-user>
WorkingDirectory=<path-to-tidemark-checkout>
EnvironmentFile=<path-to-tidemark-checkout>/.env
ExecStart=<path-to-tidemark-checkout>/.venv/bin/tidemark alert enrich --send
```

```ini
# /etc/systemd/system/tidemark-alert-enrich.timer
[Unit]
Description=Run the Tidemark alert enricher at :08 after each 4H boundary

[Timer]
OnCalendar=*-*-* 00/4:08:00
Persistent=true

[Install]
WantedBy=timers.target
```

Check the calendar expression before enabling it, with
`systemd-analyze calendar "*-*-* 00/4:08:00"`. Then enable the timer with
`systemctl enable --now tidemark-alert-enrich.timer`.

`Type=oneshot` means systemd never overlaps a run with itself, which the
single-writer assumption in ADR 0012 depends on. `Persistent=true` makes a
run missed during downtime fire at the next start; the cursor then picks
up everything it missed in order.

## Project status

**Phase 1 + 2 + 3 + 4B — data layer, Section 1 HTF context engine, the
journal/alert pipeline, and health/heartbeat.** The market-data pipeline
is implemented: a ccxt-backed exchange client (public data only, no
credentials, closed candles only), idempotent SQLite storage with
per-row sanity checks, rejected-candle recording, gap detection, and run
bookkeeping, and `tidemark data backfill/update/gaps/status` CLI
commands. Section 1 of the rulebook (HTF context, locked at v1.1) is
fully implemented on top of that: ATR(14), fractal swing detection,
horizontal levels (swing clusters + previous day/week high/low),
Fibonacci retracement legs, and the Section 1 state machine and 9-row
decision matrix, all recalculated at every 4H close. `tidemark run` ties
it together: evaluate -> append to the journal (the complete research
record, one row per evaluation, including every WAIT) -> a pure change
detector -> a filtered, read-only Telegram alert on change only.
`tidemark health check/heartbeat` proves the unattended system is
actually alive, independent of whether anything alert-worthy has
happened. `tidemark context evaluate/history/explain` still drove the
engine standalone, including `--as-of` for point-in-time reproduction.
(`context evaluate` was later removed as redundant with `tidemark
replay`'s point-in-time reproduction - see "Removing context_records"
below.)

**Phase 5 — Section 2 (1H behavior), observation-only.** Implemented as
a measurement layer (rulebook status `PROVISIONAL — OBSERVATION ONLY`,
v0.1): reaction tiers (R1/R2/R3), higher-low/lower-high-then-close
structure confirmation, zone-close failure, 12-candle reaction expiry,
and `HTF_CONTEXT_INVALIDATED` on any Section 1 change, all replayed
deterministically over closed 1H candles. `tidemark observe
run/list/stats` journal and review it. It produces no trading output
of any kind and is fully decoupled from the Section 1 journal, the
change detector, and Telegram — see
[docs/adr/0007-section-2-observation-only.md](docs/adr/0007-section-2-observation-only.md).

**Phase 5B — the replay command and the frozen v0.1 baseline.**
`tidemark replay` re-evaluates Section 1 and Section 2 point-in-time
over stored candles with no writes, reporting per-evaluation and
per-session counts as separate, never-merged tables — see
[docs/adr/0008-replay-as-a-repo-command.md](docs/adr/0008-replay-as-a-repo-command.md)
and the committed
[docs/replay/section-02-v0.1-baseline.md](docs/replay/section-02-v0.1-baseline.md).

**Phase 6, Merge 1 — universe registry and snapshot schema.** Additive
only: three new tables (`market_registry`, `universe_snapshot`,
`universe_snapshot_row`), registry upserts, atomic
snapshot-header-plus-rows writes, and read-only `tidemark universe
registry/snapshots/show` CLI commands — see
[docs/adr/0009-universe-selection-architecture.md](docs/adr/0009-universe-selection-architecture.md).
No selection or eligibility computation exists yet, `Candle` gained no
column, and every pipeline's symbol source is still `TIDEMARK_SYMBOLS`.

**Phase 6, Merge 2A — venue discovery and daily candle backfill.**
`tidemark universe discover` lists the venue's active USDT-quoted
perpetuals via ccxt's unified `load_markets` (a second, venue-agnostic,
read-only call, added alongside `data/exchange.py`'s candle fetching
without changing it) and syncs `market_registry`, marking symbols no
longer listed ABSENT_FROM_VENUE rather than deleting them.
`tidemark universe backfill` then backfills `1d` candles for every ACTIVE
symbol via the unmodified `data backfill` ingest path (same per-symbol
failure isolation, same PARTIAL-on-partial-failure), and records what
actually landed back onto each registry row. `market_registry`'s
`first_candle_seen_at`/`last_candle_seen_at` became nullable (amended
from Merge 1, before anything had ever written to the table) so a
freshly-discovered symbol is a valid row before its first backfill.
Still no eligibility, metric, or ranking logic, and every pipeline's
symbol source remains exactly `TIDEMARK_SYMBOLS`.

**Phase 6, Merge 2B — the volume metric, eligibility, and snapshot
generation.** `tidemark universe snapshot` computes
`MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D` for every ACTIVE registry symbol
(`data/universe_metric.py`), ranks them (deterministic, symbol-name
tie-break), backfills 4H candles for the top K=50 if not already stored,
and assesses Section 1 eligibility for those 50 by replaying the LOCKED
v1.1 engine unmodified (`data/universe_eligibility.py`, via
`replay.report.replay_section1`) — never a calendar-history requirement,
per UNIV-01. The top N=30 eligible by rank are selected; every ranked
symbol gets a row regardless. Rows below K are `NOT_ASSESSED` with
`eligible = NULL`, deliberately distinct from an assessed-and-failed
`eligible = False` (`UniverseSnapshotRow.eligible` and
`UniverseSnapshot.k` were amended/added the same way Merge 2A amended
`market_registry` — nothing had written to these tables in production
yet). Omitting `--as-of` runs live (FORWARD): it may backfill 4H data and
caches a found `section1_first_usable_at` onto `market_registry` so a
later run never re-replays a symbol's whole history. Giving `--as-of`
reconstructs a past snapshot (BACKFILLED): it never touches the network
and never writes that cache, verified by `UNIVERSE_AS_OF_INVARIANT`
tests across several timestamps
(`tests/data/test_universe_snapshot.py`). `tidemark universe coverage`
reports symbols on venue, eligible, assessed, selected, data available,
and counts by exclusion reason. Every pipeline's symbol source remains
exactly `TIDEMARK_SYMBOLS` — verified by both a store-level and a
CLI-level regression test, even after a real snapshot has been
generated. See
[docs/adr/0009-universe-selection-architecture.md](docs/adr/0009-universe-selection-architecture.md)'s
Merge 2B addendum for the full design, including the rank-first order
and the listing-status-as-of limitation.

**Phase 6, UNIV-08 — asset-class domain constraint.** A live inspection
found 202 of 727 ACTIVE registry symbols (27.8%), and 14 of the first
snapshot's 30 selections (47%), were non-crypto — tokenised equities,
commodities, FX, and pre-IPO synthetics that Section 1's structural
rules have never been validated against. `data/asset_class.py` classifies
every symbol from Binance's `underlyingType` field alone (`COIN` →
`CRYPTO`; `INDEX` → `NON_ELIGIBLE_INDEX`; a known TradFi type →
`NON_CRYPTO`; anything unrecognized → `UNKNOWN`, fail closed, never
guessed) — read from the same `load_markets()` response
`data/discover.py` already consumes, zero new API calls. Captured once at
first discovery (`record_classification`, never-moved semantics
mirroring `first_seen_in_venue_list_at`) and persisted on
`market_registry`; `generate_universe_snapshot` reads only that
persisted value, never a live call, so a later reclassification can
never alter a past snapshot — UNIVERSE_AS_OF_INVARIANT applied to asset
class. Carried onto `UniverseSnapshotRow` too, so every row is
self-auditing. Ranking, N=30, and K=50 are unchanged. The pre-UNIV-08
snapshot is untouched (append-only, `methodology_version="universe-v1"`
forever); `data/universe_snapshot.py`'s methodology version moved to
`"universe-v2"` so the two are trivially distinguishable. See
[docs/adr/0009](docs/adr/0009-universe-selection-architecture.md)'s
UNIV-08 section for the full inspection evidence and design.

**Phase 6, Merge 3 — the observer reads the universe snapshot.**
`tidemark run`, `tidemark observe run`, and `tidemark health check` now
default to the SELECTED symbols of the latest valid universe snapshot for
the configured venue instead of `TIDEMARK_SYMBOLS`
(`data/symbol_source.py`, `resolve_symbols`): explicit `--symbols` still
wins outright, and `TIDEMARK_SYMBOLS` remains as a last-resort fallback
when no snapshot exists, the latest one is older than
`TIDEMARK_UNIVERSE_STALENESS_HOURS` (default 48), or it selected nothing
— each case logs a warning naming the reason. A missing or stale snapshot
is never a crash. Every `tidemark run`/`tidemark observe run` execution
records which source it used, and the snapshot id when one was used, on
its `runs` row (`symbol_source`/`symbol_source_snapshot_id`, added via a
manual `ALTER TABLE` against the real database, the same treatment
UNIV-08 gave its registry columns) — every journal entry is traceable
back to the universe that produced it. `tidemark health check` gained
`universe_freshness` (OK/WARN/FAIL on the snapshot's age) and
`symbol_source` (WARN when the last run fell back to `TIDEMARK_SYMBOLS`)
checks. `tidemark universe sync --days N` backfills 1H/4H/1D/1W for the
selected symbols only, reusing the existing chunked upsert, idempotent.
No Section 1/2 rule, the ranking methodology, N, or K changed — see
[docs/adr/0009](docs/adr/0009-universe-selection-architecture.md)'s
Merge 3 section for the full resolution order and fallback rule.

**Phase 7 — the plumbing check and `tidemark evidence`.** A read-only
inspection of the live archive (observation start/end, universe
coverage, ingestion gaps, evaluation failures, duplicates, and a
pipeline-ordering gap it found) preceded a new, permanent command:
`tidemark evidence` queries the persisted `observations`/
`journal_entries` archive for raw evidence toward Section 2's four open
questions, without re-simulating anything and without answering any of
them. It refuses full output on an archive spanning fewer than 14 days
(`--allow-insufficient` overrides, marked preliminary), reports a data-
sufficiency verdict per question, and breaks every count out by week
and month alongside a Section 1 regime proxy — see
[docs/adr/0010-evidence-command.md](docs/adr/0010-evidence-command.md).
No Section 1/2 rule, parameter, or threshold changed.

**Phase 8, Merge 1 — the market intelligence layer.** A read-only
inspection of Coinalyze's derivatives API (auth, rate limits, symbol
mapping including a CJK-ticker meme coin, closed-period behavior, and
what's genuinely unavailable — BTC dominance) preceded a new, fully
isolated package, `market_intel/`, and one new command,
`tidemark intel market [--json]`. Enforced by a static-analysis test,
not discipline: it never imports the research engine, is never imported
by it, and never writes to any research table. Computes its own
closed-period boundary rather than trusting Coinalyze's `to=now` (which
returns an in-progress bucket), and distinguishes a symbol Coinalyze
doesn't list (`MARKET_NOT_FOUND`) from one that's listed but has no data
flowing for a metric (`NO_DATA`) — never a fabricated zero. Raw data
only: no Telegram, no scheduling, no bias, no trading recommendation.
`docs/rulebook/derivatives-context-v0.1.md` records six price/OI/funding
interpretations as a `PROVISIONAL`, unwired document only — see
[docs/adr/0011-market-intelligence-layer.md](docs/adr/0011-market-intelligence-layer.md).

**Phase 8, Merge 2 — the /coin Telegram bot.** A long-polling bot
(`tidemark intel bot [--once]`) answering `/coin <SYMBOL>` with the
Merge 1 snapshot, for any Binance USDT-M perpetual. Authorization is by
numeric chat ID only (`TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID`) — never
username or display name; an unauthorized chat is silently ignored (no
reply, only a logged chat id and timestamp, never the message text).
Its own independent Telegram client and message renderer, not shared
with `notify.telegram`'s Section 1/2 alert path, so a change to either
can never silently affect the other. Persists its `getUpdates` offset
to a flat JSON file (no relationship to `tidemark.db`) so a restart
never replays or skips a message, and discards a startup backlog older
than a few minutes. Reuses Merge 1's rate-limit tracking so a burst of
`/coin` requests replies "rate limited" instead of failing silently.
Survives a Telegram outage with exponential backoff; exits immediately
on an invalid bot token rather than retrying forever. Still raw data
only — see the
[ADR's Merge 2 addendum](docs/adr/0011-market-intelligence-layer.md#addendum-merge-2--the-coin-telegram-bot).

**Phase 8, Merge 3 — the hourly BTC briefing.** The rulebook
(`docs/rulebook/derivatives-context-v0.1.md`) was updated with exact
price/OI/funding thresholds, six stable identifiers (D1-D6), and a
`NO_MATCH` definition for the other twelve of eighteen possible
combinations — in its own commit, before any classifier code, per
CLAUDE.md's standing rule that strategy logic comes only from the
rulebook. `tidemark intel briefing [--send] [--json]` classifies BTC's
closed-1H reading against it and shows BTC's stored Section 1 structure
alongside it, via a single narrow, explicitly-permitted exception to the
isolation boundary: a read-only `journal_entries` lookup — the table
`tidemark run` actually writes Section 1 results to, not `context_records`
(a table only the standalone `tidemark context evaluate` command writes;
an earlier version of this read that table instead, until an audit of a
live deployment found it was always empty — see the ADR) —
(`context_read.py`) that imports only `tidemark.data.models` — never
`tidemark.context`, never `tidemark.data.store` (whose transitive
`tidemark.data.exchange` import the import-boundary test now closes off
by allowlist rather than blocklist), and never writes anything. Alerts
only on a real classification change (compared against the immediately
prior evaluation) or a meaningful Section 1 structural change (compared
against the last SENT briefing specifically) — `NO_MATCH` never alerts.
Every hourly evaluation, sent or not, is recorded in its own table,
`market_intel_evaluations`, idempotent per hour with the same narrow
`sent`/`send_reason`-only update exception `JournalEntry` already uses.
See the
[ADR's Merge 3 addendum](docs/adr/0011-market-intelligence-layer.md#addendum-merge-3--the-hourly-btc-briefing).

**Removing `context_records`.** The `context_records` table and its
`ContextRecord` ORM model are gone entirely - not merely unread.
`ContextRecord` is now a plain `@dataclass` (`htf.evaluate`'s in-memory
Section 1 output shape; unchanged everywhere it's used - only its
persistence is gone). `tidemark context evaluate` (its only writer, and
by then already unused in production) is removed as redundant with
`tidemark replay`'s existing point-in-time, side-effect-free inspection;
`context history`/`explain` are kept, repointed at `journal_entries` -
same output format, only the source changed. The lesson: a table that
exists but receives no writes is a trap for anything that queries it by
name. See the
[ADR's "Addendum: removing context_records"](docs/adr/0011-market-intelligence-layer.md#addendum-removing-context_records)
for the full report and the documented (manual, one-time) migration
procedure for dropping the table from an existing database.

See [docs/architecture.md](docs/architecture.md) for module responsibilities
and [docs/adr/](docs/adr/) for architecture decision records.
