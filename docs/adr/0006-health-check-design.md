# 6. Health check design

Date: 2026-09-23

## Status

Accepted

## Context

Phase 3 made Telegram a *filtered* notification layer on purpose (ADR
0005): it only speaks when state actually changes, so a human isn't
pinged for every "still NEUTRAL" evaluation. That design has a blind
spot once Tidemark runs unattended on a server: silence is now
ambiguous. It could mean the rulebook genuinely found nothing new to
say, or it could mean the process crashed, the exchange started
rejecting requests, the disk filled up, or the scheduled job stopped
firing three days ago. From the outside, a healthy quiet system and a
dead quiet system look identical.

A health check makes that silence trustworthy by inspecting what's
actually in the database — proof the system ran, not an assertion that
it did.

## Decision

`health/checks.py` is a set of pure functions that read only
`data/store.py` (via `TidemarkStore`/`Engine`) and already-loaded
settings, and never write, never call the network, and never send
anything. Each check returns OK/WARN/FAIL plus a human-readable detail;
the overall status is the worst individual status
(`health/checks.py::worst_status`). `tidemark health check` reports it
(human-readable or `--json`) with exit codes 0/1/2 so systemd or any
future monitor can treat a degraded state as a failure. `tidemark health
heartbeat` is the only command in this area that sends anything — it
runs the same checks and posts one Telegram summary.

### Why these six checks

- **Database** — everything else assumes the schema exists and the file
  is reachable. If it isn't, no other check's result means anything, so
  a failing database check short-circuits the rest (`run_all_checks`
  returns immediately) instead of letting five more checks crash trying
  to query tables that were never created.
- **Candle freshness** — the most direct evidence that data ingestion is
  still running. Measured in multiples of the timeframe's own interval,
  not a fixed wall-clock duration, because a 4H candle and a 1W candle
  have very different "normal" ages.
- **Last run per command** — freshness alone can't distinguish "ingestion
  is fine but `run` itself crashed" from "everything is fine"; the run
  lifecycle (already tracked for `backfill`/`update`/`run`) is the direct
  signal for that. The stale-RUNNING threshold (2h) is copied verbatim
  from the existing logic in `cli.py`'s `data status`, not reinvented.
- **Journal activity** — the journal is the complete research record
  (ADR 0005); if it has stopped growing, `run` has stopped completing
  successfully even if candle ingestion (a separate command) is still
  healthy.
- **Gaps** — reuses `TidemarkStore.find_gaps` exactly as `data gaps`
  already does. A handful of gaps can be an exchange hiccup; a lot of
  them means ingestion has been unreliable for a while.
- **Telegram config** — WARN, not FAIL: a misconfigured or newly
  installed instance can be legitimately healthy on the data side while
  nobody has wired up alerting yet. This never sends a message to check
  itself — it only reads whether `TelegramNotifier.is_configured` is
  true.

### Why these thresholds

- Candle freshness: OK up to 1.5x the interval (the next candle simply
  hasn't closed yet, or is running a little behind schedule), WARN up to
  3x, FAIL beyond. A timeframe with *no* data is WARN, not FAIL — it may
  legitimately not be backfilled for that symbol yet, which is a
  configuration gap, not a crash.
- Stale RUNNING: 2 hours, matching the pre-existing threshold in `data
  status` — one crashed-run definition across the whole CLI, not two
  that could quietly drift apart.
- No completed run in 8 hours: two missed 4H cycles. One missed cycle
  could be a transient blip; two in a row is a real pattern.
- Journal activity: WARN at 8h (one missed cycle), FAIL at 24h (a full
  day of silence from the research record, regardless of what candle
  ingestion is doing).
- Gaps: 0 is OK, 1-5 is WARN (worth knowing about, not yet alarming),
  above 5 is FAIL (ingestion has been unreliable, not just unlucky
  once).

All of the above live in one named-constant block at the top of
`health/checks.py`, not scattered through the check functions, so a
future threshold change is a one-line diff with an obvious blast radius.

### Why the heartbeat is not journalled

The journal (ADR 0005) is a complete research record of Section 1
evaluations — every 4H close, including every WAIT. A heartbeat isn't an
evaluation at all; it's a status report about the *process*, generated
on whatever cadence someone chooses to run `health heartbeat` (hourly
cron, systemd timer, a human running it by hand), completely decoupled
from the 4H candle cycle the journal is keyed on. Writing it to
`journal_entries` would give that table a second, incompatible meaning —
mixing "what did Section 1 conclude about the market" with "was the
process alive when someone last asked" — and would corrupt exactly the
kind of statistical analysis ADR 0005 explains the journal exists for
(comparing alert-worthy runs against the full population of WAITs). So
`health heartbeat` only ever reads the store and calls
`TelegramNotifier.send_text`; it never touches `TidemarkStore`'s journal
methods.

## Consequences

- A Telegram failure can never change `health check`'s exit code, since
  that command never attempts a send. It *can* affect `health
  heartbeat`'s exit code, since notifying is that command's one job —
  a failed send there returns exit code 2 regardless of the underlying
  health status, on top of printing the rendered message so a human
  running it by hand still sees the full report.
- Because every check takes `now` as a parameter instead of reading the
  clock internally, the entire suite is testable with a frozen time and
  fixture data — no real clock, no real network, anywhere in
  `tests/health/`.
- `HEALTH_TIMEFRAMES` (4h/1d/1w) is deliberately narrower than the full
  stored timeframe set (which also includes 5m/15m/1h for future
  sections). Checking freshness/gaps against timeframes nothing in this
  codebase ingests yet would just be permanent WARN/FAIL noise instead
  of a real signal — this list grows only when a section that actually
  uses a finer timeframe is implemented.
