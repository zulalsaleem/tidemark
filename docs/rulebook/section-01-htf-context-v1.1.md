SCALPING RULEBOOK v1.1 — SECTION 1: HTF CONTEXT
Registered: 2026-09-23 | Source: own trading experience | Status: LOCKED

CHANGE FROM v1.0
  RULE 1.7a — LEVEL ROLE CLASSIFICATION (new). A level's role (support or
  resistance) is now dynamic: evaluated fresh at every evaluation from the
  level price and the current 4H close, and never stored from the level's
  formation. In v1.0, a level's support/resistance label was fixed
  permanently at formation by its origin (a swing high or a previous
  day/week high always read as resistance; a swing low or previous
  day/week low always read as support). A level born from a swing high
  can now satisfy "holds major support" if price has since moved above
  it, and a level born from a swing low can now read as resistance if
  price has since moved below it — the decision matrix's "holds major
  support"/"holds major resistance" checks (rows 4-7) are unchanged
  mechanically (zone touch + close beyond the zone's near edge) but are
  now applied using this dynamic role instead of the level's static
  origin. This changes engine output for any evaluation where price has
  crossed a previously-formed level, so it is a new rule_version rather
  than a clarification of v1.0 — see
  docs/adr/0004-dynamic-level-role.md. All other rules, parameters,
  thresholds, and matrix rows are unchanged from v1.0.

DATA
  Closed candles only · UTC · 4H decides, 1H handled in Section 2
  Distance unit: ATR(14) on 4H

PARAMETERS
  Swing fractal N ................ 2  (usable 8h after formation)
  Level/zone tolerance ........... 0.25 × ATR
  Minimum Fib leg ................ 2 × ATR
  Horizontal lookback ............ 120 × 4H candles (~20 days)
  Swing cluster distance ......... 0.5 × ATR
  Major level .................... >=2 touches OR weekly high/low
  Fib zone ....................... 0.500-0.786 (log nearest level)
  Reaction test (log only) ....... 1 × ATR within 6 × 4H candles

STATES
  INSUFFICIENT_STRUCTURE · BULLISH · BEARISH · NEUTRAL
  STRUCTURE_BROKEN_BULL / _BEAR
    enter: 4H close beyond last confirmed HL / LH
    exit:  >=1 new swing formed after the break is confirmed,
           then Rule 1.3 is recalculated

DECISION MATRIX (first match wins)
  1. INSUFFICIENT_STRUCTURE                   -> WAIT   NOT_ENOUGH_SWINGS
  2. STRUCTURE_BROKEN_*                       -> WAIT   STRUCTURE_BROKEN
  3. NEUTRAL                                  -> WAIT   NEUTRAL_STRUCTURE
  4. BULLISH + holds major support + Fib zone -> LONG_WATCH  grade A
  5. BULLISH + holds major support            -> LONG_WATCH  grade B
  6. BEARISH + holds major resistance + Fib   -> SHORT_WATCH grade A
  7. BEARISH + holds major resistance         -> SHORT_WATCH grade B
  8. In Fib zone, no major level              -> WAIT   FIB_ONLY (logged)
  9. Anything else                            -> WAIT   NOT_IN_ZONE

"Holds major support" = 4H low touches the level zone (level ± 0.25 ATR)
AND the candle closes at or above the zone's lower edge. Resistance is
the mirror. As of v1.1, which levels are eligible to be treated as
"support" vs "resistance" for this test is decided by RULE 1.7a, not by
a level's origin.

RULE 1.7a — LEVEL ROLE CLASSIFICATION (new in v1.1)
  A level's role is dynamic and evaluated fresh at every evaluation.
  It is never stored from the level's formation.

    close > level_price   -> SUPPORT
    close < level_price   -> RESISTANCE
    close = level_price   -> SUPPORT (registered tie-break)

  The comparison point is the level price itself, never a zone boundary.
  Tolerance determines whether price is NEAR a level; it never determines
  which side of the level price is on.

  The level's origin (swing_high_cluster, swing_low_cluster, prev_day_high,
  prev_day_low, prev_week_high, prev_week_low) is a permanent factual
  property and is unchanged by this rule. Origin records where the level
  came from; role records how it acts right now.

EXCLUDED FROM v1.1: trendlines and patterns (NOT_DEFINED). Whether
near-duplicate levels from different origins should be merged is also
NOT_DEFINED — see docs/rulebook/open-questions.md.

OUTPUT RECORD (recalculated at every 4H close, consumed by Section 2)
  asset, evaluated_at, rule_version, state, watch, grade, reason_code,
  active_levels[], fib{anchor_start, anchor_end, valid_from,
  invalidated_at}, swings_used[]{formed_at, confirmed_at}

  As of v1.1, each active_levels[] entry also carries the level's
  dynamically-computed role (RULE 1.7a) alongside its permanent origin.
