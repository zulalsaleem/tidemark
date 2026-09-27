DERIVATIVES CONTEXT v0.1
Registered: 2026-09-27 | Status: PROVISIONAL — NOT WIRED TO ANY CODE

PURPOSE
  This document records six price/open-interest/funding-rate combinations
  and a plain-language interpretation of each. It exists so that, if
  Tidemark ever surfaces derivatives context to a human, the
  interpretation attached to each combination is written down and
  versioned first — never invented ad hoc inside a rendering function.

  No code reads this file yet. `tidemark intel market` (see
  docs/adr/0011-market-intelligence-layer.md) prints raw open interest,
  OI change, funding rate, predicted funding rate, long/short ratio, and
  liquidations with their values, units, and periods — and nothing else.
  It does not classify, does not reference this document, and does not
  produce any of the six labels below. Wiring this document to output is
  a later merge, requiring its own approval, exactly like a new rulebook
  section requires for Section 1/2.

STATUS
  PROVISIONAL. Nothing here has been measured against real outcomes.
  These are descriptive labels for a price/OI/funding combination, not a
  claim that the combination predicts anything. None of the six rows
  below is a trade direction, an entry condition, or a signal.

INPUTS
  - Price direction over the observation window: up / down / flat.
  - Open interest change over the same window (`open_interest_change` in
    `tidemark intel market`'s output): up / down.
  - Funding rate direction or level over the same window: positive
    (longs pay shorts) / negative (shorts pay longs) / rising / falling.
  All three must be read from the same closed period — see
  docs/adr/0011's closed-period clamping rule. Comparing a price move
  from one window against an OI change from a different window is not a
  valid use of this document.

THE SIX COMBINATIONS

  1. price up, OI up, funding positive
     -> long participation increasing. New money is entering on the long
        side; existing shorts are not the ones driving the move.

  2. price up, OI down, funding positive
     -> possible short covering. Price is rising while open interest
        shrinks, consistent with short positions closing rather than new
        longs opening; funding staying positive says longs still hold a
        net premium.

  3. price down, OI up, funding negative
     -> short positioning increasing. New money is entering on the short
        side as price falls; funding flipping negative says shorts are
        now paying longs to hold the position.

  4. price down, OI down, funding positive
     -> longs being flushed/deleveraged. Price falling while OI shrinks
        and funding is still positive is consistent with long positions
        being forcibly or voluntarily closed, not fresh shorts opening.

  5. price flat, OI up, funding rising
     -> position buildup, breakout risk. Price is not moving but
        positioning is growing and funding is climbing — read as
        increasing directional pressure without resolution yet, a
        condition worth watching rather than a signal to act on.

  6. price flat, OI down, funding falling
     -> position reduction. Participants are closing positions on both
        sides without a directional resolution; read as de-risking, not
        as a setup.

WHAT THIS DOCUMENT DOES NOT DO
  - It does not define thresholds for "up," "down," "flat," "rising," or
    "falling." Those are NOT_DEFINED — a future merge that wires this
    document to output must define them explicitly, versioned, before
    any code can implement this table. Guessing a threshold here would
    violate CLAUDE.md's standing rule against inventing rulebook
    behavior.
  - It does not rank the six combinations against each other, does not
    combine them with Section 1/2 state, and does not produce a trade
    direction, entry, stop, target, or R:R under any circumstance.
  - It says nothing about BTC dominance, which is unavailable from
    Coinalyze entirely (see ADR 0011) and is out of scope for this
    document.

CHANGE PROCESS
  This is a rulebook document like any other under docs/rulebook/: a
  change creates a new version file (v0.2, ...); this file is never
  edited in place once another merge depends on it. Turning this table
  into actual output — deciding thresholds, deciding what "the
  observation window" means in wall-clock terms, deciding how it
  interacts with Section 1/2 — is a separate, later merge requiring its
  own approval and its own ADR.
