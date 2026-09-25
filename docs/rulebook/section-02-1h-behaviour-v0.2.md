SCALPING RULEBOOK v0.2 — SECTION 2: 1H BEHAVIOUR
Registered: 2026-09-24 | Source: replay of section-02-v0.1-baseline.md | Status: PROVISIONAL — OBSERVATION ONLY

CHANGE FROM v0.1
  Approved via docs/rulebook/section-02-v0.2-justification.md. Exactly one
  variable changes: what ends a Section 2 session (the old TERMINAL STATES
  / HTF_CONTEXT_INVALIDATED rule below). Nothing else in this document
  differs from section-02-1h-behaviour-v0.1.md — same N=2 fractal swing
  confirmation, same R1/R2/R3 reaction-tier definitions, same 12-candle
  reaction expiry, same INTERACTION/zone reuse of Section 1, same Section 1
  rules. v0.1 is unchanged and unedited; it remains a separate, immutable
  file, and existing v0.1 observations keep rule_version
  "section-02-v0.1" and are never rewritten under this version.

  Old rule (v0.1): a session ends the instant the pinned
  (state, watch, grade) tuple stops matching exactly what it was at
  session start. A grade change alone (A <-> B) ends the session, even
  when the WATCH is otherwise unchanged and price hasn't left the level.

  New rule (v0.2): a session ends when, and only when, ANY of:
    - the WATCH direction changes (LONG_WATCH <-> SHORT_WATCH), or
    - the WATCH disappears (Section 1 no longer reports a WATCH — watch
      becomes WAIT), or
    - the level identity changes: the currently held major level's price
      (of the matching role) falls OUTSIDE the ORIGINAL level's zone —
      the same zone_low/zone_high Section 1 computed for the level this
      session pinned at its start. No new tolerance is introduced; this
      reuses Section 1's existing zone exactly as INTERACTION already
      does. If no major level of the matching role holds at all anymore,
      that also ends the session (there is nothing left to test
      containment against).
  A grade change alone (A <-> B) does NOT end a session. Section 2's own
  pinned level (the one INTERACTION and Stage A/B test against) is never
  reassigned mid-session — it stays exactly what it was at session start,
  for the life of the session, exactly as v0.1 already did. Only the
  session-ending test itself reads the newest Section 1 record's currently
  held level, to check whether it still falls inside the original zone.

  Grade becomes data, not a session-ending switch. Every session records
  grade_at_start (the grade at the first evaluation of the session) and a
  grade_history: a list of (evaluated_at, grade) entries, one per grade
  change observed during the session (empty if the grade never changed).
  This makes "do A-grade sessions resolve differently from B-grade
  sessions?" a question answerable from real data later, instead of
  unanswerable by construction (v0.1 could never keep more than one grade
  on record for a single session, since any change ended it).

  rule_version becomes "section-02-v0.2" for every record produced under
  this document. `tidemark replay --rule-version section-02-v0.2` runs
  this version's session-termination rule against the same replay engine
  and the same pinned data snapshot as v0.1, so the two can be compared
  directly (see docs/replay/section-02-v0.2-report.md).

PURPOSE
  Section 2 v0.2 is a MEASUREMENT layer, not a signal layer. It observes
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

  HTF_CONTEXT_INVALIDATED  [CHANGED FROM v0.1 — see CHANGE FROM v0.1]
    The session ends when, and only when, ANY of:
      - the WATCH direction changes (LONG_WATCH <-> SHORT_WATCH), or
      - the WATCH disappears (watch becomes WAIT), or
      - the level identity changes: the currently held major level of the
        matching role no longer falls inside the ORIGINAL level's zone
        that this session pinned at its start (no new tolerance; reuses
        Section 1's zone, per INTERACTION above), including the case
        where no major level of that role holds at all anymore.
    A grade change alone (A <-> B) does NOT end a session — grade is
    recorded (grade_at_start, grade_history) rather than gating
    continuation. Section 2 is subordinate to Section 1 and never
    continues past it — this ends the observation outright, not just the
    current reaction. See OPEN QUESTIONS (4), now resolved by this
    version for the grade component; the level-identity definition itself
    remains the one judgment call flagged in section-02-v0.2-
    justification.md, Part 2.

  CONTINUATION_CANDIDATE_NOT_EVALUATED
    Path B (continuation — a WATCH holding without ever producing a
    reaction tier) is NOT implemented in v0.2. When Section 1 shows a
    WATCH and 1H shows interaction without any reaction tier matching,
    this state is recorded so the frequency of the case is measurable
    for a future v1.0 decision. See OPEN QUESTIONS (2).

STATES
  NO_HTF_CONTEXT · NO_INTERACTION · REACTION_DETECTED ·
  NO_STRUCTURAL_REFERENCE · CONTINUATION_CANDIDATE_NOT_EVALUATED ·
  BULLISH_STRUCTURE_CHANGE · BEARISH_STRUCTURE_CHANGE · HANDOFF_TO_15M ·
  SUPPORT_FAILURE · RESISTANCE_FAILURE · STAND_DOWN ·
  HTF_CONTEXT_INVALIDATED · REACTION_EXPIRED

EXCLUDED FROM v0.2
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
  section_1_grade, section_1_level_price, session_started_at,
  grade_at_start, grade_history[]{evaluated_at, grade},
  interaction_detected, reaction_tier, reaction_condition_matched,
  reaction_started_at, structure_reference_price,
  structure_reference_confirmed_at, structure_change, failure, expiry,
  state, reason_code, swings_used[]{formed_at, confirmed_at}

  grade_at_start and grade_history are new in v0.2 (see CHANGE FROM v0.1).
  session_started_at is bookkeeping — the evaluated_at of this session's
  first row — that makes a session's boundary recoverable from the flat
  row list now that grade no longer participates in it.

OPEN QUESTIONS
  1. Whether R1 alone should ever qualify as worth recording distinctly,
     or whether it's too weak a signal to be useful even as a measured
     tier once real data comes in.
  2. Path B (continuation: a WATCH that holds without ever producing a
     reaction tier) is undefined. v0.2 only measures how often this
     happens (CONTINUATION_CANDIDATE_NOT_EVALUATED); it does not define
     what, if anything, should happen when it does.
  3. INTERACTION reuses Section 1's holding definition and its zone
     bounds rather than adding a second tolerance — this is a v0.1
     design choice, carried over unchanged into v0.2, not a settled
     rule. Whether Section 2 should eventually have its own interaction
     tolerance, independent of Section 1's 0.25 × ATR, is open.
  4. v0.1's open question ("how long a reaction survives a Section 1
     context change") is resolved for the grade component by this
     version: a grade change alone no longer ends a session. What
     remains open is the level-identity definition itself — testing
     containment against the *original* level's zone (section-02-v0.2-
     justification.md, Part 2) is one reasonable way to decide "is this
     still the same level", chosen because it is directly reusable from
     Section 1 without a new tolerance, but it is a judgment call the
     rulebook author approved for this version, not a re-statement of an
     existing rule. A future version could revisit it (e.g. testing
     against the *current* zone instead, or requiring an exact price
     match) if evidence suggests it draws the line in the wrong place.
