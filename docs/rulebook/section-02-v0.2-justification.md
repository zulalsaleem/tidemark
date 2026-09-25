SECTION 2 v0.2 — JUSTIFICATION
Prepared: 2026-09-24 | Status: PROPOSAL — NOT APPROVED, NOT IMPLEMENTED

This document proposes one change to Section 2's session-termination rule
and requests the rulebook author's approval before any code is written.
Nothing in this document is implemented. `context/mtf.py` and
`docs/rulebook/section-02-1h-behaviour-v0.1.md` are unchanged; v0.1 remains
the only implemented and locked-in-code version of Section 2.

All numbers below are from the corrected v0.1 baseline,
`docs/replay/section-02-v0.1-baseline.md` (row hash
`018f246a810905f025022e1bc0b01c2a8f5777185d01fef3f6f9e79d7a04520c`,
BTC/ETH/SOL/XRP/DOGE, 2026-03-28 to 2026-09-24, 476 Section 2 sessions).

---

1. THE PROBLEM

Of 476 Section 2 sessions in the baseline, 366 ended in
`HTF_CONTEXT_INVALIDATED`. Of those, 48 ended on a grade-only change —
Section 1's `(state, watch)` pair was unchanged; only `grade` moved
between A and B.

Grade is not a measure of whether Section 2 is still watching the same
thing. Per `section-01-htf-context-v1.1.md`'s decision matrix, grade A vs
B is decided by whether price is *also* sitting inside the Fibonacci
retracement zone on top of already holding a major support/resistance
level (`holds_major_support/resistance AND price_in_fib_zone` → A;
`holds_major_support/resistance` alone → B). It says nothing about
whether price has left the level, whether the level itself changed, or
whether the WATCH direction flipped. A grade flip can happen while price
is doing nothing structurally different at the level Section 2 is
observing: same level, same direction, same ongoing interaction — yet
Section 2 today treats it exactly like a full context change, discarding
whatever reaction tier or structural reference had been building and
starting an entirely new session from the next 1H candle.

48 of 476 sessions (10.1%) ended this way. That is not price action — it
is Section 2's session boundary reacting to a Section 1 field that was
never meant to describe continuity at the level.

(Note on the number: an earlier draft of this document reported 39,
described as "gated on each session's actual classified outcome" via
Table 2's own `group_sessions`/`_classify_session`. That figure is not
reproducible from the replay code and should be treated as wrong. Table
2's session grouping cannot see this event at all: `group_sessions`
closes a session on a time gap or on a row whose own `state` is
`HTF_CONTEXT_INVALIDATED` — it has no per-session field recording
*which* Section 1 field changed to cause that row, so no query over
Table 2's output can recover "grade-only" as a category. 48 is measured
a different way — directly at `mtf.py`'s `_session_ended`, instrumented
to record why each `True` result fired (watch direction changed, watch
disappeared, or — for v0.1 specifically — grade changed with state and
watch unchanged) and counted before any session grouping happens. That
count sums exactly to the corrected baseline's 476 sessions (427 watch-
disappeared + 1 watch-direction-changed + 48 grade-only), which is the
same kind of direct verification this section should have used the first
time instead of asserting a number attributed to a report code path that
cannot produce it.)

---

2. THE CHANGE — exactly one variable

Redefine what ends a Section 2 session. A session ends when, and only
when:

  - the WATCH direction changes (LONG_WATCH ↔ SHORT_WATCH), or
  - the WATCH disappears (watch becomes WAIT), or
  - the Section 1 level identity changes.

A grade change alone does NOT end a session.

**Level identity (requires approval — not settled):** the session
continues while the currently held major level's price falls within the
*original* level's zone — the same `zone_low`/`zone_high` Section 1
computed when the session began, with no new tolerance introduced. If the
currently held major level's price falls outside that original zone, the
session ends.

This is the one part of this proposal that is a judgment call rather than
a direct reading of an existing rule, and it is flagged as such
deliberately. Section 1 recomputes its level set from swing clusters at
every 4H close, so the "same" level can drift by a small amount from one
evaluation to the next even when nothing meaningful has changed at it;
testing containment against the *original* zone (rather than, say,
requiring the exact same price, or comparing to the *current* zone) is
one reasonable way to say "still the same level," but it is a choice, not
a re-statement of a rule already written down. It needs the rulebook
author's explicit sign-off before it is implemented, exactly like
Section 1's RULE 1.7a tie-break needed one.

**Outcome: not approved for this version.** A draft implementing this
level-identity clause alongside the grade change was measured against
the same pinned snapshot and instrumented at `_session_ended` directly:
it fired 73 times, replacing the 48 grade-only terminations it also
removed — net +25 sessions, not the decrease Part 4 predicts, and not a
number attributable to grade removal alone (Part 5's own "isolating one
variable" requirement applies to this document's own proposal, not only
to hypothetical future changes). v0.2 as implemented is grade-only: the
bullet above ("the Section 1 level identity changes") is NOT part of the
implemented rule. Level identity is deferred to a v0.3 proposal, to be
measured on its own against this v0.2 baseline. See docs/rulebook/
section-02-1h-behaviour-v0.2.md's "DEFERRED TO v0.3" section and OPEN
QUESTIONS (4).

Everything else about session termination is unchanged: `mtf.py`'s own
comparison already treats `state` and `watch` correctly (a `state` change
without a `watch` change essentially cannot happen under Section 1's
decision matrix — `watch` is derived from `state` — so this proposal does
not need a separate rule for `state` alone). Only `grade` is removed from
the comparison, and the level-identity check above is added as an
explicit, independent condition in its place (level identity is currently
implicit in `watch` + `grade` together, since a level change without a
watch/grade change is not currently possible to observe — removing grade
from the comparison makes it possible, hence the need to state the
condition explicitly).

---

3. GRADE BECOMES DATA, NOT A SWITCH

Grade stops being part of what ends a session and becomes something a
session simply records: `grade_at_start`, plus a grade history — every
`(grade, changed_at)` transition observed while the session is open.

This is what makes "do A-grade sessions resolve differently from B-grade
sessions?" an answerable question later, from real data, instead of an
unanswerable one. Under v0.1, a grade change always ends the session it
happened in, so a session's *recorded* grade is only ever whatever grade
was current at whatever arbitrary moment the session happened to end —
not evidence of anything about how the setup behaved. Under this
proposal, a session can span a B→A upgrade (or the reverse) and still be
one continuous observation, with both grades on the record and the
resolution (structure change, failure, or otherwise) attributable to the
whole session rather than to whichever grade happened to be current when
an unrelated field forced a reset.

---

4. PREDICTIONS — written before any rerun

If this change is implemented and replayed against the same 180-day
snapshot, the following should hold. These are falsifiable predictions,
not claims:

  - **Session count falls.** Some v0.1 sessions that were split apart
    purely by a grade-only change should merge back into single v0.2
    sessions.
  - **Grade-only terminations drop to zero, by construction.** Grade no
    longer participates in the termination test, so no session can end
    "because of grade" anymore — this one is not really a prediction, it
    follows directly from the change.
  - **Sessions live longer, in 1H candles.** v0.1's corrected baseline
    median session length is 5.0 candles (max 25, ALL row). v0.2 sessions
    should show a higher median, since a session is no longer cut short
    mid-reaction by a grade flip.
  - **More sessions reach a structural reference.** v0.1's `Table 3`
    shows `NO_STRUCTURAL_REFERENCE` at 32.61% of all evaluations (ALL
    row). With sessions running longer and reactions no longer discarded
    mid-flight by a grade change, a higher share of reactions should get
    the extra 1H candles needed to find a confirmed swing before their
    session ends for a real reason.

**What would count as v0.2 NOT helping:** in v0.1's corrected baseline,
structure changes and level failures together account for 110 of 476
sessions (13 + 3 + 50 + 44 = 110, ≈ 23.1%) — the share of sessions that
actually resolve into a decisive outcome. If that proportion stays
essentially the same under v0.2, the change did not improve measurement —
it only merged and relabelled session boundaries. Fewer, longer sessions
that still resolve at the same rate would mean the grade-only
terminations were cosmetic noise in the session *count*, not a real
distortion of what Section 2 was measuring — a legitimate, useful thing
to learn, but the opposite of what this proposal predicts.

---

5. WHAT IS NOT CHANGING, AND WHY

Not touched by this proposal, at all:

  - Fractal swing confirmation, N = 2.
  - The R1/R2/R3 reaction-tier definitions (zone-touch conditions,
    engulfing/close-beyond/range-≥1.5×ATR sub-conditions for R2, the
    reclaim condition for R3).
  - The 12-candle reaction expiry.
  - Every Section 1 rule and parameter: the decision matrix, ATR(14),
    level clustering distance, Fib zone bounds, RULE 1.7a's dynamic
    level role.

This proposal changes exactly one thing: what ends a Section 2 session.
If anything else changed at the same time — loosening expiry, widening a
reaction-tier condition, adjusting a Section 1 threshold — a difference
in v0.2's numbers could not be attributed to the session-boundary fix
specifically. It could equally be the other change, or an interaction
between the two. Isolating one variable is what makes the predictions in
Part 4 falsifiable at all.

---

6. TESTING ON THE SAME DATA

The problem in Part 1 was found by replaying this 180-day snapshot. If
this proposal is implemented, its correctness would first be checked by
replaying the *same* snapshot. That is a real methodological weakness —
tuning a fix to the data that revealed the problem, then declaring
victory on that same data, proves very little on its own.

It is acceptable here specifically because this is a **logic correction**,
not **parameter tuning**. Nothing in Part 2 adjusts a threshold, a ratio,
or a numeric constant to make these particular 180 days look better —
there is no number in the proposed rule that was chosen by looking at
these results (the one open item, level identity, is a definition, not a
tuned value, and is explicitly flagged for separate approval). The change
is a correction to make Section 2's session boundary match what the
rulebook already says Section 2 is watching — a WATCH, at a level — which
a grade field never encoded in the first place.

The real test is forward, not this replay: once v0.2 is implemented and
running live, its actual observation numbers must resemble what this
replay predicts. If live v0.2 data diverges materially from Part 4's
predictions, that is itself informative — either an assumption in this
proposal was wrong, or these 180 days were not representative — and
either way it is a reason to revisit this document, not to keep it as
written.

---

7. SAMPLE-SIZE CAVEAT

16 total structure changes (13 bullish, 3 bearish) is far too few to
conclude anything about which reaction tier, or which grade, "works."

R1 preceded 12 of the 16 (R2 preceded 1, R3 preceded 3). That looks like
a strong signal for R1 until it's set against the base rate: across all
476 sessions, R1 is also by far the most common tier ever reached — 183
of the 341 sessions that reached any tier at all (≈54%), versus 62 for R2
and 96 for R3. R1 preceding most structure changes is close to what plain
frequency alone would predict; it is not, on this sample, evidence that
R1 is a *better* predictor of a structure change than R2 or R3.

The same caution applies to grade: 11 of the 16 structure-change sessions
started at grade B, 5 at grade A. That is exactly the kind of question
Part 3's grade history is meant to make answerable — but with 16 events
total, it is a data point to keep, not a conclusion to act on. Any read
of "grade X resolves better" or "tier Y resolves better" needs materially
more structure-change events than this dataset has produced, from v0.2
itself, before it means anything.

---

8. RESULTS — measured against the same pinned snapshot, grade-only v0.2

v0.2 was implemented grade-only (level identity deferred — see the
"Outcome" note under Part 2) and replayed against the same 476-session
v0.1 baseline this document is built on. Numbers below are ALL-row totals
from both replays.

| | v0.1 | v0.2 (grade-only) |
| --- | ---: | ---: |
| Sessions | 476 | 428 |
| STRUCTURE_CHANGE_LONG / SHORT | 13 / 3 | 13 / 5 |
| LEVEL_FAILURE_SUPPORT / RESISTANCE | 50 / 44 | 50 / 43 |
| HTF_CONTEXT_INVALIDATED | 366 | 317 |
| STILL_OPEN_AT_END_OF_DATA | 0 | 0 |
| Median session length (1H candles) | 5.0 | 5.0 |
| Max session length | 25 | 33 |
| NO_STRUCTURAL_REFERENCE (Table 3 share) | 32.61% | 33.42% |

**Predictions 1 and 2: confirmed.** Session count fell from 476 to 428 —
a drop of exactly 48, matching the 48 grade-only terminations measured
directly at `_session_ended` (Part 1's corrected count), not an
approximate figure. Grade-only terminations are zero by construction:
`_session_ended`'s v0.2 branch does not read `grade` at all.

**Predictions 3 and 4: NOT confirmed.** Median session length is
unchanged (5.0 → 5.0 candles) — only the tail moved (max 25 → 33).
`NO_STRUCTURAL_REFERENCE`'s share of Table 3 evaluations got slightly
*worse* (32.61% → 33.42%), not better. Structure changes are essentially
flat (13 bullish / 3 bearish → 13 / 5) — no material increase in
sessions reaching a structural reference.

**The falsification condition (Part 4) was flawed.** As written, it
tested resolved-sessions ÷ total-sessions staying "essentially the same."
But v0.2 mechanically shrinks the denominator (fewer sessions, by
construction, per predictions 1/2) without touching the numerator by the
same logic — so the ratio moving is not evidence of anything. It did
move: 110/476 = 23.11% → 111/428 = 25.93%. Judged on the correct test —
absolute counts, not a ratio the change itself moves — resolved sessions
went 110 → 111. That is flat. v0.2 did not improve measurement by the
metric that matters.

**Conclusion.** v0.2 is kept as a correctness fix: grade should never
have been able to terminate an observation, since Section 1's grade field
was never meant to describe continuity at a level (Part 1). It is not
kept as an improvement to Section 2's measurement — session count
dropping and the resolution *rate* rising are both mechanical
consequences of removing 48 terminations, not evidence that Section 2 is
now better at finding structural references or level failures. Whatever
limits how often a reaction reaches a confirmed structural change is not
the grade field; it lies elsewhere in Section 2 (the 12-candle reaction
expiry, the R1/R2/R3 tier definitions, or the WATCH duration Section 1
itself produces are candidates a future justification would need to test
one at a time, per Part 5's own rule).

**Note for future justifications.** A falsification condition must use a
metric the change being tested does not itself mechanically move. A ratio
whose denominator the change directly shrinks or grows is not a valid
falsification test for that change — this document's own Part 4 violated
its own Part 5 principle ("isolating one variable is what makes the
predictions... falsifiable") by picking a metric coupled to the variable
being changed. Absolute counts, not rates built from the population the
change resizes, are the correct comparison.

---

APPROVAL

Grade-only termination removal is approved and implemented as
`section-02-v0.2` (docs/rulebook/section-02-1h-behaviour-v0.2.md):
`context/mtf.py` implements both v0.1 and v0.2 side by side, selected by
`rule_version`, and v0.1 is unedited and still available for replay.

Part 2's level-identity definition is NOT approved and NOT implemented —
see the "Outcome" note under Part 2. It remains a judgment call, not a
restatement of an existing rule, and needs its own single-variable
justification document (a v0.3 proposal) before any code implements it.
