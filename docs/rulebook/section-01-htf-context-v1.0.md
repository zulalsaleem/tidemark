SCALPING RULEBOOK v1.0 — SECTION 1: HTF CONTEXT
Registered: 2026-09-16 | Source: own trading experience | Status: LOCKED

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
the mirror.

EXCLUDED FROM v1.0: trendlines and patterns (NOT_DEFINED)

OUTPUT RECORD (recalculated at every 4H close, consumed by Section 2)
  asset, evaluated_at, rule_version, state, watch, grade, reason_code,
  active_levels[], fib{anchor_start, anchor_end, valid_from,
  invalidated_at}, swings_used[]{formed_at, confirmed_at}
