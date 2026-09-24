# 7. Section 2 ships as observation, not signal

Date: 2026-09-23

## Status

Accepted

## Context

Section 1 (4H HTF context) is locked and drives real Telegram alerts:
it tells a human when price is at a major level worth watching. The
rulebook author's next request was for something that watches what 1H
price actually *does* once it gets there — does it react, does
structure shift, does the level fail. That is naturally a precursor to
a second, faster signal layer (an eventual handoff to 15M execution
timing).

But unlike Section 1, Section 2 arrived with named open questions
still unresolved: whether R1 alone is ever worth acting on, what
"continuation" (a WATCH that holds without ever producing a reaction)
should mean, whether Section 2 needs its own interaction tolerance
instead of reusing Section 1's, and how strictly a Section 1 context
change should end an in-progress reaction. Turning any of those into a
real signal now would mean the code — not the rulebook author — decided
the answers, which is exactly what the standing rules forbid: rules
come from `docs/rulebook/`, and an undefined behavior gets marked
`NOT_DEFINED`, never guessed.

## Decision

Section 2 v0.1 ships as a **measurement layer**, gated by a new
rulebook status: `PROVISIONAL — OBSERVATION ONLY` (`docs/rulebook/
README.md`), distinct from `LOCKED` (implementable, drives real
signals) and `DRAFT`/`PENDING` (not implementable at all). A
`PROVISIONAL — OBSERVATION ONLY` section is implementable, but only as
observation: it may read data and journal what it sees, and must never
drive a Telegram alert, a change-detector input, or any other trading
output.

Concretely, `context/mtf.py` implements every rule in
`section-02-1h-behaviour-v0.1.md` faithfully — reaction tiers,
structure confirmation, zone failure, expiry, context invalidation —
but stops at recording an observed state. `HANDOFF_TO_15M` is a
terminal state that nothing consumes. There is no entry, stop, target,
or R:R anywhere in the module, and `journal/observe_pipeline.py` (the
orchestrator behind `tidemark observe run`) takes no `TelegramNotifier`
parameter at all, unlike `journal/pipeline.py` — the parameter simply
does not exist, so there is no argument to accidentally wire up later.
Section 2 writes only to its own `observations` table; it never writes
to the Section 1 `journal_entries` table, never calls
`journal/changes.py`, and never imports `notify/telegram.py`.

The four open questions are recorded in the rulebook doc rather than
resolved in code: R1-alone qualification (open question 1), Path B /
continuation (open question 2, measured via
`CONTINUATION_CANDIDATE_NOT_EVALUATED` rather than implemented),
reusing Section 1's interaction tolerance instead of a second one (open
question 3), and how strictly a Section 1 context change should end an
in-progress reaction (open question 4). None of them block observation
— they block turning observation into a signal.

## Consequences

- Section 2 can run in production today, accumulating real evidence,
  without anyone having to decide the open questions first or risk
  shipping a guessed rule as if it were the rulebook author's own.
- The `observations` table is the evidence base a future v1.0 decision
  would be made from. `tidemark observe stats` — counts by state,
  reason_code, and reaction tier — is the review tool: it can answer,
  from real data, how often R1 alone would have mattered, how often
  `CONTINUATION_CANDIDATE_NOT_EVALUATED` actually happens, and how
  often `HTF_CONTEXT_INVALIDATED` cuts off a reaction that later would
  have confirmed.
- What would justify a v1.0 signal layer: enough observed
  `BULLISH_STRUCTURE_CHANGE`/`BEARISH_STRUCTURE_CHANGE` rows to judge
  whether they're a meaningfully better entry timing signal than
  Section 1 alone, a low enough `CONTINUATION_CANDIDATE_NOT_EVALUATED`
  rate that ignoring Path B wasn't discarding most of the real cases,
  and the rulebook author actually answering the four open questions
  from that evidence rather than from intuition alone. Until then, this
  stays observation.
- The cost is the same one ADR 0005 accepted for the Section 1
  journal: an extra table and a write on every 1H close under a WATCH,
  including every no-reaction row, because the negative cases are what
  make the eventual positive rate statistically meaningful.
