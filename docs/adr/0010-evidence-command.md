# 10. `tidemark evidence` — a frozen, read-only archive query, not a script

Date: 2026-09-27

## Status

Accepted

## Context

Phase 7 asks for something different from what `tidemark replay`
(ADR 0008) already gives us. Replay re-evaluates the rulebook,
point-in-time, over stored candles — it answers "what would Section
1/2 have said." The Section 2 observer (`docs/rulebook/
section-02-1h-behaviour-v0.2.md`) has now been running for hours, not
weeks, against a real, growing `observations` archive, and that
archive is what a future decision on SEC2-01 through SEC2-04 (the
rulebook's four open questions) will eventually be measured against.
What's needed is a way to ask the archive itself — "how many sessions
contain R1," "how often does `CONTINUATION_CANDIDATE_NOT_EVALUATED`
fire," "how many reactions actually reach the 12-candle boundary" —
without re-running anything, and to ask it the same way every time.

The Phase 7 read-only plumbing check (see the accompanying report)
found real problems with the archive as it stands today: a
pipeline-ordering gap where `observe run` executed before the `run`
that would have given it a WATCH to observe, and a schema drift where
the live `observations` table predated the v0.2 session-bookkeeping
columns the current model defines. Both are evidence that ad hoc
inspection of this archive is fragile and needs a fixed instrument, not
a fresh script each time.

## Decision

`tidemark evidence` (`data/evidence.py` + `data/evidence_render.py`,
wired up in `cli.py`) is a repo command, for the same reasons ADR 0008
gives for `tidemark replay`:

- **It queries, it never simulates.** `build_evidence_report` calls
  only `TidemarkStore.all_observations`/`all_journal_entries` — it
  never calls `htf.evaluate`/`mtf.evaluate`, never reads a `Candle`,
  and never writes anything. Running it twice against an unchanged
  archive produces byte-identical numbers (barring `generated_at`,
  a wall-clock fact about the run).

- **Its session grouping is its own frozen copy, not a shared import.**
  `evidence.py` re-implements the same session-boundary logic
  `replay/report.py` already has (`_group_sessions_v1`/
  `_group_sessions_v2`/`_classify_session`) rather than importing it.
  This is a deliberate divergence from the more obvious "just reuse
  `report.group_sessions`" approach: `tidemark replay` and `tidemark
  evidence` answer different questions on different cadences (a report
  regenerated on demand vs. a frozen metric compared across months),
  and coupling them would mean a change made for replay's own reasons —
  a bug fix, a new session-outcome bucket, a rendering tweak — could
  silently shift what an already-published evidence number means. Each
  module now owns its own copy of a small amount of logic in exchange
  for that independence.

- **Every metric is a raw count, never a judgment.** Section C
  (SEC2-01/R1) reports "sessions containing R1," "R1 followed by
  resolution," "unresolved open R1 sessions" — never "R1 is a useful
  signal." Section D (SEC2-02) reports occurrences of
  `CONTINUATION_CANDIDATE_NOT_EVALUATED` with their surrounding raw
  events, and nothing in the schema for those events has a field for a
  derived classification. This mirrors the rulebook's own stance
  (`section-02-1h-behaviour-v0.2.md`'s OPEN QUESTIONS): the code is not
  the place a rulebook question gets answered.

- **Metric definitions are frozen and documented**, so a run today
  means the same thing as a run three months from now: what counts as
  a "session," what "missing hourly ticks" means (a gap greater than
  one hour between two rows sharing the same `session_started_at`),
  what bucket a reaction cycle falls into relative to the fixed
  12-candle expiry, and what the sufficiency thresholds are (see
  below). These are documented in this ADR and in the CLI's own
  docstring/`--help` text, not left to be inferred from behavior.
  Changing any of them is a rulebook-adjacent decision, not a
  refactor — it should be treated the same way a rulebook version
  bump is: a new, clearly labeled version of the metric, not a silent
  edit of what an old number meant.

- **Data-sufficiency verdicts are evidentiary thresholds, not rulebook
  parameters.** `INSUFFICIENT`/`EMERGING`/`SUFFICIENT` are computed
  from three fixed constants (`MIN_ARCHIVE_SPAN_DAYS = 14`,
  `MIN_SUFFICIENCY_SYMBOLS = 3`, and a raw-count band of
  `<=4`/`5..29`/`>=30`). These decide whether a SEC2-0x question can
  be *investigated at all* — never what its answer would be — and they
  live in `data/evidence.py`, not `docs/rulebook/`, because they are
  process judgments about sample size, the same kind of judgment
  editorial review always makes about "is there enough data to look at
  this yet," not a trading rule CLAUDE.md's standing rules would
  forbid inventing.

- **It refuses by default on a young archive.** An archive spanning
  fewer than 14 days prints only the coverage summary and the
  sufficiency verdicts, then exits non-zero — a single short regime
  must never be mistaken for general evidence, and the tool should
  make that hard to do by accident rather than trusting every caller
  to remember it. `--allow-insufficient` overrides this and prints the
  full report with an explicit `PRELIMINARY — ... not valid for
  Section 2 conclusions` header, so a preliminary read is always
  labeled as such in the output itself, not only in a caller's memory.

- **Temporal coverage is mandatory, not optional.** Every count that
  matters (sessions, R1 occurrences, continuation candidates,
  terminations, reaction-cycle buckets) is also reported by week and by
  month, and a REGIME PROXY table reports the Section 1 state
  distribution the same way — explicitly labeled a proxy for market
  conditions, never a classification. This is the direct fix for the
  failure mode a short-lived archive invites: a single trending week
  producing a pile of `BULLISH`/`LONG_WATCH` sessions must be visible
  as one week's regime, not silently generalized into "what R1 does."

## Consequences

- `tidemark evidence [--from DATE] [--to DATE] [--symbol SYM] [--json]
  [--allow-insufficient]` is reproducible: the same archive state,
  given the same arguments, produces the same report every time, and
  the report says plainly which archive slice (symbols/date range) it
  covers.
- The evidence engine and the replay engine can each evolve on their
  own schedule. A future replay-only change (a new Table 2 outcome
  bucket, say) does not retroactively change what a previously
  published evidence number meant, and vice versa.
- The current archive (see the Phase 7 plumbing report) spans about
  three days and has zero Section 2 observation rows, so every SEC2-0x
  verdict is `INSUFFICIENT` today — by design, since the command's job
  right now is to prove the instrument works and say honestly that
  there isn't yet enough to investigate, not to produce a premature
  reading.
- This ADR, like ADR 0008 before it, resolves none of Section 2's open
  questions and adds no rule, parameter, or threshold to the rulebook.
  It builds the query a future proposal on those questions would be
  measured with.
