# Rulebook

This directory is the single source of truth for Tidemark's strategy
logic. Code implements what these documents say; it never adds to them,
tunes them, or infers behavior they don't specify.

## How it works

- **The rulebook is human-authored.** Rules come from the trader's own
  documented experience, not from the model. If a document doesn't define
  something, the corresponding code marks it `NOT_DEFINED` and the correct
  response is to ask, not to guess.
- **Version numbers are immutable.** Once a section is registered at a
  version (e.g. `v1.0`), that file's content is never edited in place.
- **Changes create a new version rather than editing an old one.** A
  revision to a locked section is written as a new file (e.g.
  `section-01-htf-context-v1.1.md`), leaving the prior version intact for
  history and for any code/records still referencing it.
- **Each version records its registration date and source** — when it was
  written down and where it came from (e.g. "own trading experience").
- **A section's status matters.** `LOCKED` sections are implementable as
  written and drive real signals. `DRAFT` / `EXTRACTION IN PROGRESS`
  sections are not yet ready for implementation — states or parameters
  marked `PENDING` must not be guessed at. `PROVISIONAL — OBSERVATION
  ONLY` sections are implementable, but only as measurement: they may
  read data and journal what they observe, and must never drive a
  Telegram alert, a change-detector input, or any other trading output.

## Current sections

| Section | File | Status |
| --- | --- | --- |
| 1 — HTF Context (4H) | [section-01-htf-context-v1.1.md](section-01-htf-context-v1.1.md) (current); [v1.0](section-01-htf-context-v1.0.md) (superseded, kept for history) | LOCKED |
| 2 — 1H Behaviour | [section-02-1h-behaviour-v0.2.md](section-02-1h-behaviour-v0.2.md) (current); [v0.1](section-02-1h-behaviour-v0.1.md) (superseded, kept for history) | PROVISIONAL — OBSERVATION ONLY |

## Live observation version

`tidemark observe run` (the live Section 2 pipeline) switched from
`section-02-v0.1` to `section-02-v0.2` (grade-only session termination —
see [section-02-v0.2-justification.md](section-02-v0.2-justification.md))
on 2026-09-25. `tidemark replay` continues to support both versions via
`--rule-version`, for comparison against the pinned baseline. Observation
rows already collected under v0.1 keep `rule_version` `"section-02-v0.1"`
and are never rewritten — the two versions coexist in the `observations`
table, distinguished by that field.

See [open-questions.md](open-questions.md) for behavior the current
rulebook doesn't define, surfaced by running the engine — none of it is
decided, and code does not guess at an answer while an entry is open.
