# 8. Replay as a repo command, not a scratchpad script

Date: 2026-09-24

## Status

Accepted

## Context

An earlier request for numbers on how Section 1 and Section 2 v0.1
actually behave was answered with a one-off Python script, written
outside the repository, run once, and reported as prose. Two problems
came out of that:

- **It wasn't reproducible.** The script existed only in a session
  scratchpad. Nobody else — not a future session, not the rulebook
  author, not v0.2's author — could re-run it, diff it against a later
  run, or even confirm it did what the summary said it did. A number
  nobody can regenerate isn't evidence, it's a claim.
- **It mixed two units.** Section 1 was counted per 4H evaluation.
  Section 2 was counted two different ways in the same script — per
  1H evaluation row for some figures, per WATCH "episode" for others —
  and the write-up then compared a session count against an episode
  count as if they were the same kind of thing. They aren't: a
  Section 1 WATCH episode is a run of consecutive 4H candles: a
  Section 2 session is a run bounded by Section 1's (state, watch,
  grade) tuple, which is a *stricter* boundary (a grade change alone
  ends a session but not a Section 1 episode) — see rulebook open
  question 4. Comparing the two counts directly produced a ratio that
  looked like a finding but was actually a units error.

## Decision

`tidemark replay` (`src/tidemark/replay/report.py` + `render.py`,
wired up in `cli.py`) replaces the scratchpad script. It is:

- **Read-only.** It only calls `TidemarkStore.get_candles` and the pure
  `htf.evaluate`/`mtf.evaluate` functions. It never calls
  `save_context_record`, `save_journal_entry`, or `save_observation` —
  enforced by `tests/replay/test_report.py`'s mock-based test, not just
  asserted in a docstring.
- **Point-in-time**, using the same truncation the existing look-ahead
  guard (`tests/context/test_look_ahead_guard.py`) already proves
  correct for Section 1: for the i-th 4H candle, only candles with
  `close_time <= that candle's own close_time` are visible. Section 2's
  own replay needs no extra truncation, since `mtf.evaluate` is already
  a from-the-start, no-look-ahead replay by construction.
- **Pinned to its input.** The report records exactly which candle rows
  it read — symbols, timeframes, row counts, first/last `open_time`,
  and a SHA-256 hash over every `(venue, symbol, timeframe, open_time,
  OHLCV)` row — so a later report can say plainly whether it ran
  against the same history as this one, rather than silently comparing
  two different datasets.
- **Deterministic.** The same store, given the same arguments, produces
  byte-identical report content (excluding the `generated_at` stamp,
  which is a wall-clock fact about the run, not part of the replay
  result — see `tests/replay/test_report.py::
  test_replay_report_is_deterministic_across_two_runs`).
- **Three tables, three units, never merged.** Table 1 counts Section 1
  evaluations (one row per 4H close). Table 2 counts Section 2
  *sessions* — every session ends in exactly one of seven mutually
  exclusive outcomes (`STRUCTURE_CHANGE_LONG`/`_SHORT`,
  `LEVEL_FAILURE_SUPPORT`/`_RESISTANCE`, `REACTION_EXPIRED`,
  `HTF_CONTEXT_INVALIDATED`, `STILL_OPEN_AT_END_OF_DATA`), and those
  counts are asserted to sum to the session count
  (`test_session_outcome_counts_sum_to_session_count`). Table 3 counts
  Section 2 *evaluations* (one row per 1H close under an active WATCH)
  — a different unit from Table 2's sessions, computed by a different
  function (`_build_table3`, not `_build_table2`) from a differently
  shaped input, and never added to or compared against a session count
  anywhere in the report's data model
  (`test_report_data_model_never_combines_table_totals`).

The generated report is committed to the repo at
`docs/replay/section-02-v0.1-baseline.md` — not regenerated silently,
not left in a scratchpad. It is the frozen v0.1 baseline: a future
`tidemark replay --rule-version section-02-v0.2` run is compared
against it explicitly, and only when both reports' snapshot hashes
match is that comparison meaningful.

## Consequences

- Anyone can reproduce the baseline exactly: `uv run tidemark data
  backfill ...` to the same point, then `uv run tidemark replay
  --rule-version section-02-v0.1`, and diff the output against the
  committed file.
- A future v0.2 replay against a different candle history is caught
  immediately by a mismatched snapshot hash, rather than producing a
  quietly-wrong comparison the way the scratchpad script's episode-vs-
  session mixing did.
- The cost is a small amount of report-building machinery
  (`SnapshotInfo`/`DataSnapshot`, three table-builder functions, a
  Markdown renderer) instead of an inline script. That cost buys
  something a script can't: a `pytest` suite that keeps the "sessions
  sum to outcomes" and "never writes" guarantees true as the codebase
  changes, instead of trusting that a one-off script happened to get
  it right the day it ran.
- This ADR and `tidemark replay` do not resolve any of Section 2's four
  open questions (see `docs/rulebook/section-02-1h-behaviour-v0.1.md`)
  and do not implement v0.2 — they build the instrument a future
  decision on those questions would be measured with.
