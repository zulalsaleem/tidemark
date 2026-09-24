SCALPING RULEBOOK v0.1 — SECTION 2: 1H BEHAVIOUR
Registered: 2026-09-23 | Source: own trading experience | Status: PROVISIONAL — OBSERVATION ONLY

PURPOSE
  Section 2 v0.1 is a MEASUREMENT layer, not a signal layer. It observes
  what 1H price does at a 4H WATCH area and journals it. It produces no
  trading output of any kind: no entries, stop-losses, take-profits,
  R:R, or position sizing anywhere, no handoff to 15M (HANDOFF_TO_15M is
  recorded as an observed state only — nothing consumes it), and no
  Telegram alerts. It exists to build an evidence base that could
  eventually justify a v1.0 signal layer, or could show that none is
  warranted.

DATA
  Closed 1H candles only · UTC · no future information
  Distance unit for any 1H measurement: 1H ATR(14) [PROVISIONAL]
  1H swing fractal N = 2, same fractal method as Section 1 [PROVISIONAL]

ACTIVATION
  Section 2 evaluates an asset only when the latest Section 1 record for
  it is LONG_WATCH or SHORT_WATCH. Otherwise state = NO_HTF_CONTEXT.

INTERACTION
  INTERACTION reuses Section 1's existing holding definition and the
  level zone Section 1 already computed (level price ± 0.25 × ATR, on
  4H ATR). Section 2 does NOT define a second tolerance and does NOT
  stack another 0.25 × ATR on top. Section 1 is authoritative for WHERE
  a level is; Section 2 only asks what 1H price does once it gets there.
  See OPEN QUESTIONS (3).

PARAMETERS [ALL PROVISIONAL]
  1H swing fractal N .............. 2
  1H ATR period .................... 14
  R2 range threshold ............... 1.5 × 1H ATR
  Reaction expiry ................... 12 closed 1H candles with no
                                       confirmation and no failure

STAGE A — REACTION TIERS
  Evaluated on closed 1H candles, within interaction. Tiers are recorded,
  never ranked into a decision — all three enter REACTION_DETECTED. The
  tier, the triggering candle's close time, and which condition matched
  are all recorded.

  R1 BASIC
    The candle trades into the level zone and closes back away from its
    own extreme, on the correct side of the level price (not merely the
    zone edge).

  R2 STRONG
    R1, plus at least one of:
      - engulfing the prior candle's full range, or
      - a close beyond the prior candle's extreme in the reaction
        direction, or
      - a candle range >= 1.5 × 1H ATR.

  R3 RECLAIM
    The candle's extreme trades BEYOND the level price and the candle
    CLOSES back on the original side — a failed breakdown (long) or a
    failed breakout (short). See OPEN QUESTIONS (1): R3 does not require
    R1's within-zone condition, since a reclaim can wick past the zone
    entirely and still be a genuine reclaim.

STAGE B — STRUCTURE CONFIRMATION
  For a long reaction, the reference is the most recent CONFIRMED 1H
  swing high that FORMED AFTER the reaction started. For a short, the
  mirror (most recent confirmed swing low formed after the reaction).

  If no such confirmed reference exists, state = NO_STRUCTURAL_REFERENCE.
  A reference is never manufactured from range boundaries or any other
  source — only a genuine confirmed 1H fractal swing qualifies.

  Confirmation requires BOTH, in this order:
    1. a confirmed higher low after the reaction (long; mirror — a
       confirmed lower high — for short), THEN
    2. a 1H CLOSE beyond the reference swing.
  A wick beyond the reference is not a break; only a close counts. If
  the close-beyond happens before the higher low confirms, it does not
  count as confirmation.

  On confirmation, the triggering candle records state =
  BULLISH_STRUCTURE_CHANGE or BEARISH_STRUCTURE_CHANGE. The candle(s)
  after it record state = HANDOFF_TO_15M — terminal, observed only.
  Nothing in this codebase consumes it.

TERMINAL STATES
  SUPPORT_FAILURE / RESISTANCE_FAILURE
    A confirmed 1H CLOSE beyond the Section 1 level zone (past zone_low
    for a support setup, past zone_high for resistance) after
    interaction has occurred. The failing candle records state =
    SUPPORT_FAILURE or RESISTANCE_FAILURE; subsequent candles under the
    same observation record state = STAND_DOWN.

  REACTION_EXPIRED
    12 closed 1H candles pass after the reaction with no confirmation
    and no failure. Exactly 12 — not 11, not 13. The reaction clears
    after expiry; a new reaction may still be detected later in the
    same observation if Section 1's context hasn't changed.

  HTF_CONTEXT_INVALIDATED
    The Section 1 record for that asset is no longer the WATCH the
    observation began under (state, watch, and grade all pinned at
    observation start; any of the three changing invalidates it).
    Section 2 is subordinate to Section 1 and never continues past it —
    this ends the observation outright, not just the current reaction.
    See OPEN QUESTIONS (4).

  CONTINUATION_CANDIDATE_NOT_EVALUATED
    Path B (continuation — a WATCH holding without ever producing a
    reaction tier) is NOT implemented in v0.1. When Section 1 shows a
    WATCH and 1H shows interaction without any reaction tier matching,
    this state is recorded so the frequency of the case is measurable
    for a future v1.0 decision. See OPEN QUESTIONS (2).

STATES
  NO_HTF_CONTEXT · NO_INTERACTION · REACTION_DETECTED ·
  NO_STRUCTURAL_REFERENCE · CONTINUATION_CANDIDATE_NOT_EVALUATED ·
  BULLISH_STRUCTURE_CHANGE · BEARISH_STRUCTURE_CHANGE · HANDOFF_TO_15M ·
  SUPPORT_FAILURE · RESISTANCE_FAILURE · STAND_DOWN ·
  HTF_CONTEXT_INVALIDATED · REACTION_EXPIRED

EXCLUDED FROM v0.1
  Entries, stop-losses, take-profits, R:R, position sizing — all
  NOT_DEFINED and out of scope for a measurement layer.
  15M — HANDOFF_TO_15M is recorded, never consumed.
  Telegram alerts — Section 2 writes to the observation journal and
  nothing else. The change detector and notifier are untouched.
  Path B / continuation logic — NOT_DEFINED, see OPEN QUESTIONS (2).

OUTPUT RECORD (one row per 1H close per asset under an active WATCH,
recorded even when nothing happens — the negative cases are the
measurement)
  asset, evaluated_at, rule_version, section_1_state, section_1_watch,
  section_1_grade, section_1_level_price, interaction_detected,
  reaction_tier, reaction_condition_matched, reaction_started_at,
  structure_reference_price, structure_reference_confirmed_at,
  structure_change, failure, expiry, state, reason_code,
  swings_used[]{formed_at, confirmed_at}

OPEN QUESTIONS
  1. Whether R1 alone should ever qualify as worth recording distinctly,
     or whether it's too weak a signal to be useful even as a measured
     tier once real data comes in.
  2. Path B (continuation: a WATCH that holds without ever producing a
     reaction tier) is undefined. v0.1 only measures how often this
     happens (CONTINUATION_CANDIDATE_NOT_EVALUATED); it does not define
     what, if anything, should happen when it does.
  3. INTERACTION reuses Section 1's holding definition and its zone
     bounds rather than adding a second tolerance — this is a v0.1
     design choice, not a settled rule. Whether Section 2 should
     eventually have its own interaction tolerance, independent of
     Section 1's 0.25 × ATR, is open.
  4. How long a reaction survives a Section 1 context change is
     partially defined (HTF_CONTEXT_INVALIDATED ends the observation
     immediately on any state/watch/grade change) but whether that's
     too strict — e.g. whether a grade B→A change mid-reaction should
     really discard an otherwise-valid in-progress reaction — is open.
