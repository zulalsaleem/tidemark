DERIVATIVES CONTEXT v0.1
Registered: 2026-09-27 | Updated: 2026-09-27
Status: PROVISIONAL | Version: v0.1 | Window: CLOSED_1H

PURPOSE
  This document records six price/open-interest/funding-rate combinations,
  a plain-language interpretation of each, and the exact thresholds a
  classifier must use to assign one. Phase 8, Merge 3 (see
  docs/adr/0011-market-intelligence-layer.md) is the wiring merge this
  document's original PURPOSE text anticipated: `market_intel`'s
  classifier reads its thresholds from THIS document and defines none of
  its own. Any future change to a threshold, an identifier, or an
  interpretation's wording is a new version of this file (v0.2, ...),
  never a silent edit — the same rule every other rulebook document
  follows.

STATUS
  PROVISIONAL. Nothing here has been measured against real outcomes.
  These are descriptive labels for a price/OI/funding combination, not a
  claim that the combination predicts anything. None of the six rows
  below is a trade direction, an entry condition, or a signal.

WINDOW: CLOSED_1H
  Every input (price, open interest, funding) is read from the last
  fully closed 1-hour period — never an in-progress, still-accumulating
  one. This is `market_intel/clamping.py`'s `closed_period` boundary,
  the same mechanism `tidemark intel market`/`/coin` already use (see
  ADR 0011). "The current reading" always means this closed 1H reading;
  "the previous reading" (used only by D5/D6, below) means the 1H period
  immediately before it.

INPUTS AND THRESHOLDS

  Price — the percentage change in Coinalyze's own close price over the
  closed 1H window ((close - open) / open x 100):
    UP      if change > +0.25%
    DOWN    if change < -0.25%
    FLAT    otherwise — INCLUDING exactly +0.25% and exactly -0.25%

  Open interest — the percentage change in open interest over the same
  closed 1H window ((close - open) / open x 100):
    UP      if change > +0.5%
    DOWN    if change < -0.5%
    FLAT    otherwise — INCLUDING exactly +0.5% and exactly -0.5%

  Funding — the closed 1H funding rate reading:
    POSITIVE  if value > 0
    NEGATIVE  if value < 0
  A reading of EXACTLY 0.0 is undefined by this document (neither
  POSITIVE nor NEGATIVE) and classifies as NO_MATCH, with a reason - see
  NO_MATCH below. This document does not define a FLAT band for funding.

  Rising/falling (used only by D5/D6, never by D1-D4) — the current
  closed 1H funding reading compared with the previous closed 1H
  reading:
    RISING    if current > previous
    FALLING   if current < previous
  Equal readings are neither rising nor falling ("UNCHANGED") and do not
  match D5 or D6 - see NO_MATCH below.

  BOUNDARY VALUES BELONG TO FLAT. Every price/OI condition above uses a
  strict inequality ("exceeds"/"is below"), never "at or beyond" — a
  reading sitting exactly on a threshold is always FLAT, never nudged
  into UP/DOWN by convention. Concretely: exactly +0.25% is FLAT,
  +0.251% is UP; exactly -0.5% is FLAT, -0.501% is DOWN.

  If any input needed for a combination is missing or reports a non-OK
  status (UNAVAILABLE, NO_DATA, MARKET_NOT_FOUND), the classifier does
  not guess: the result is NO_MATCH with a reason naming which input was
  unavailable.

THE SIX COMBINATIONS
  D1-D4 classify funding by SIGN (POSITIVE/NEGATIVE); D5-D6 classify it
  by TREND (RISING/FALLING) instead, since price is FLAT in both and
  sign alone says nothing about direction of change. This is why the
  combinatorial space below is exactly 3 (price) x 3 (OI) x 2 (funding,
  whichever axis applies) = 18, with six defined and twelve NO_MATCH -
  see NO_MATCH.

  D1 — price up, OI up, funding positive
     -> long participation increasing. New money is entering on the long
        side; existing shorts are not the ones driving the move.

  D2 — price up, OI down, funding positive
     -> possible short covering. Price is rising while open interest
        shrinks, consistent with short positions closing rather than new
        longs opening; funding staying positive says longs still hold a
        net premium.

  D3 — price down, OI up, funding negative
     -> short positioning increasing. New money is entering on the short
        side as price falls; funding flipping negative says shorts are
        now paying longs to hold the position.

  D4 — price down, OI down, funding positive
     -> longs being flushed/deleveraged. Price falling while OI shrinks
        and funding is still positive is consistent with long positions
        being forcibly or voluntarily closed, not fresh shorts opening.

  D5 — price flat, OI up, funding rising
     -> position buildup, breakout risk. Price is not moving but
        positioning is growing and funding is climbing — read as
        increasing directional pressure without resolution yet, a
        condition worth watching rather than a signal to act on.

  D6 — price flat, OI down, funding falling
     -> position reduction. Participants are closing positions on both
        sides without a directional resolution; read as de-risking, not
        as a setup.

NO_MATCH
  Price has 3 states (UP/DOWN/FLAT). OI has 3 (UP/DOWN/FLAT). Funding
  contributes 2 states per combination - SIGN (POSITIVE/NEGATIVE) when
  price is UP or DOWN, TREND (RISING/FALLING) when price is FLAT. That
  is 3 x 3 x 2 = 18 possible combinations in total (6 for price=UP, 6 for
  price=DOWN, 6 for price=FLAT). This document defines six of them
  (D1-D6, above). The other twelve are NO_MATCH:
    price=UP:   (UP,UP,NEGATIVE), (UP,DOWN,NEGATIVE),
                (UP,FLAT,POSITIVE), (UP,FLAT,NEGATIVE)
    price=DOWN: (DOWN,UP,POSITIVE), (DOWN,DOWN,NEGATIVE),
                (DOWN,FLAT,POSITIVE), (DOWN,FLAT,NEGATIVE)
    price=FLAT: (FLAT,UP,FALLING), (FLAT,DOWN,RISING),
                (FLAT,FLAT,RISING), (FLAT,FLAT,FALLING)

  NO_MATCH is a real, first-class classification result — never forced
  into the nearest-sounding D1-D6, and never omitted from a record.
  NO_MATCH NEVER triggers an alert, regardless of whether it differs
  from the previous evaluation's classification: this document gives no
  interpretation for those twelve combinations (or for a funding reading
  of exactly 0.0, or an UNCHANGED funding trend when price is FLAT), so
  there is nothing for a briefing to say about them beyond "no defined
  interpretation applies right now."

  A missing or UNAVAILABLE input is also NO_MATCH, with a reason naming
  which input was missing — never a guessed classification.

WHAT THIS DOCUMENT DOES NOT DO
  - It does not rank D1-D6 against each other, does not combine them with
    Section 1/2 state beyond displaying both side by side, and does not
    produce a trade direction, entry, stop, target, or R:R under any
    circumstance.
  - It says nothing about BTC dominance, which is unavailable from
    Coinalyze entirely (see ADR 0011) and is out of scope for this
    document.
  - It leaves no threshold undefined as of this version. A classifier
    encountering a case this document doesn't cover must still treat it
    as NO_MATCH with a reason, per CLAUDE.md's standing rule against
    guessing undefined rulebook behavior — never invent a new threshold
    or a seventh combination to cover a gap.

OPEN QUESTIONS
  1. Funding EXACTLY UNCHANGED between the current and the previous
     closed 1H reading is neither RISING nor FALLING, so it cannot
     satisfy D5 or D6 even when price is FLAT and OI is UP or DOWN — the
     result is NO_MATCH (see NO_MATCH above). This is not a defect: v0.1
     already defines UNCHANGED as unmatched, deliberately, the same way
     it leaves a funding reading of exactly 0.0 unmatched for D1-D4. It
     is recorded here because it was an observed gap, not a hypothetical
     one — it occurred on this classifier's first live run (2026-09-27,
     BTC/USDT:USDT, price FLAT, OI UP, funding unchanged at 0.001051%
     across both the current and previous closed period). Whether a v0.2
     should define a genuine "funding unchanged" case for D5/D6 (a
     seventh and eighth combination, or a redefinition of D5/D6 to admit
     it) is open and would need its own justification, the same way any
     other rulebook threshold or combination change does — it is not
     decided by this one observation, and no threshold or classifier
     behavior changes as a result of recording it here.

CHANGE PROCESS
  This is a rulebook document like any other under docs/rulebook/: a
  change creates a new version file (v0.2, ...); this file is never
  edited in place once another merge depends on it. This version (v0.1)
  is now depended upon by `market_intel`'s classifier (Phase 8, Merge 3)
  — any future threshold or interpretation change ships as a new version
  file, with its own classifier change reviewed against it, never a
  silent edit of what v0.1 already means.
