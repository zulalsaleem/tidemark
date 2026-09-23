# 5. Journal and alert separation

Date: 2026-09-23

## Status

Accepted

## Context

Phase 3 needed two things that look similar but serve different
purposes: a durable record of every Section 1 evaluation, and a way to
tell a human when something worth their attention just happened. It
would have been simple to fold these into one step — evaluate, decide
whether it's alert-worthy, and if so write a row and send a message. But
that couples two things that fail independently and serve different
audiences:

- The **journal** is research data. Its value is completeness — every
  evaluation, including every WAIT, at the candle it was evaluated for.
  A gap in it (a skipped WAIT, a row lost because a network call failed)
  quietly corrupts anything later built on top of it: backtesting the
  matrix's real-world hit rate, auditing why a watch opened or closed, or
  just trusting that "no alert" actually meant "nothing changed" and not
  "the write failed."
- **Telegram** is a notification layer for a human who cannot and should
  not be shown every 4H evaluation for every symbol — most of them are
  WAIT with nothing new to say. It is also the one part of this pipeline
  that talks to a third-party network service, which means it is the one
  part most likely to fail transiently for reasons that have nothing to
  do with whether the evaluation itself was good.

## Decision

The pipeline is four stages, each with a single job, and each output
feeds the next: **evaluation -> journal -> change detector -> Telegram**
(`context/htf.py` -> `journal/records.py` -> `journal/changes.py` ->
`notify/telegram.py`, orchestrated by `journal/pipeline.py`).

- The journal write happens for *every* evaluation, unconditionally, and
  happens *before* anything downstream runs. It is append-only: a
  repeat of an already-journaled (asset, evaluated_at, rule_version) is a
  no-op, never a second row and never an update — the two fields that
  are the sole exception, `alert_sent`/`alert_reason`, are set once,
  after the fact, once the outcome of the steps below is known.
- The change detector (`journal/changes.py`) is a pure function: given
  the previous journal row and the current evaluation, it decides
  whether this is worth alerting on and returns a reason or `None`. It
  performs no I/O, so it can never itself fail, retry, or need a network
  connection — testing it needs no store, no mock server, nothing.
- Telegram sending only happens if the change detector returned a
  reason, and only *after* the journal row already exists. Its failure
  modes (missing credentials, a dropped connection, a Telegram outage)
  are caught, logged, and recorded as `alert_sent=false` with the
  attempted `alert_reason` — never raised, and never a reason to touch
  the journal row's evaluation fields.

Concretely: `journal/pipeline.py`'s per-symbol flow is
`evaluate -> save_journal_entry -> detect_change -> send_alert ->
record_alert_outcome`, in that order, and a failure at any step from
`detect_change` onward cannot undo the `save_journal_entry` that already
committed.

## Consequences

- The journal is trustworthy as a complete research record independent
  of whether Telegram was ever configured, reachable, or working that
  day. A backtest or audit reading `journal_entries` never needs to
  account for missing rows caused by notification failures.
- Telegram can go down, get misconfigured, or be rate-limited without
  ever affecting whether an evaluation gets recorded — the worst case is
  a human doesn't get pinged, not that the research record has a hole in
  it.
- `journal/changes.py` needing no I/O makes its test suite exhaustive
  and fast (every transition in the rulebook's PART B, plus every
  "never alert" case) without touching a database or a mock server.
- The cost is an extra table and an extra write per evaluation compared
  to a single evaluate-and-maybe-alert step. That cost is deliberate:
  the "no setups found" runs are exactly the runs a research record
  needs most, since they're what makes the alert-worthy runs
  statistically meaningful rather than a self-selected highlight reel.
- One transition PART B names but doesn't spell out:
  `STRUCTURE_BROKEN_BULL -> STRUCTURE_BROKEN_BEAR` (and the mirror) is
  reported as `STRUCTURE_BROKEN`, not `STRUCTURE_RESOLVED`. Structure
  never stopped being broken in that transition, it just broke in the
  other direction, so `STRUCTURE_RESOLVED` ("no longer broken") would
  misdescribe it; "any state -> STRUCTURE_BROKEN_BULL/_BEAR" is read as
  naming which structure is broken right now. See the comment on that
  branch in `journal/changes.py::detect_change`.
