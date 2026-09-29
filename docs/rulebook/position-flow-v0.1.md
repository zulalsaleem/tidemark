POSITION FLOW v0.1
Registered: 2026-09-29 | Updated: 2026-09-29
Status: PROVISIONAL | Version: v0.1 | Window: CLOSED_1H

PURPOSE
  This document defines a nine-state classification of how price and open
  interest moved together over one closed 1H window, for any Binance
  USDT-M perpetual `/coin` can be asked about — not only BTC and not only
  the 30-symbol research universe. It is a SECOND, independently
  versioned rulebook, entirely separate from
  docs/rulebook/derivatives-context-v0.1.md: that document (D1-D6/
  NO_MATCH) is read only by the hourly BTC briefing's classifier;
  this document is read only by `/coin`'s classifier
  (`position_flow_classifier.py`). Neither document modifies, supersedes,
  or is read by the other's code path. This mirrors CLAUDE.md's standing
  rule that code implements what a rulebook says and never adds to it —
  two questions, two documents, two classifiers.

STATUS
  PROVISIONAL. Nothing here has been measured against real outcomes.
  Every state below is a mechanical description of price and open
  interest moving together, not a claim that the combination predicts
  anything, and not a trade direction, entry condition, or signal.

WINDOW: CLOSED_1H
  Both inputs are read from the last fully closed 1-hour period — never
  an in-progress, still-accumulating one. This is `market_intel/
  clamping.py`'s `closed_period` boundary, the same mechanism `tidemark
  intel market`/`/coin`/the hourly briefing already use (see ADR 0011).

INPUTS AND THRESHOLDS

  Price — the percentage change in Coinalyze's own close price over the
  closed 1H window ((close - open) / open x 100):
    UP      if change > +0.25%
    DOWN    if change < -0.25%
    FLAT    otherwise — INCLUDING exactly +0.25% and exactly -0.25%

  Open interest — the percentage change in open interest over the same
  closed 1H window ((close - open) / open x 100):
    UP      if change > +0.25%
    DOWN    if change < -0.25%
    FLAT    otherwise — INCLUDING exactly +0.25% and exactly -0.25%

  BOUNDARY VALUES BELONG TO FLAT. Both conditions above use a strict
  inequality ("exceeds"/"is below"), never "at or beyond" — a reading
  sitting exactly on a threshold is always FLAT, never nudged into UP/
  DOWN by convention. Concretely: exactly +0.25% is FLAT, +0.250001% is
  UP; exactly -0.25% is FLAT, -0.250001% is DOWN.

  Funding, long/short ratio, liquidations, and buy/sell volume flow are
  NOT inputs to this classification. They may be shown alongside the
  result as supporting context, unrelated numbers describing the same
  period — this document defines no combination, threshold, or
  interpretation involving any of them, and the classifier reads none of
  them for its own decision.

  If price change or open interest change is missing or reports a
  non-OK status (NO_DATA, MARKET_NOT_FOUND), the classifier does not
  guess: the result is NO_MATCH with a reason naming which input was
  unavailable. Never a substituted zero, never a classification from
  whichever input is available.

THE NINE STATES
  Price has 3 states (UP/DOWN/FLAT). Open interest has 3 (UP/DOWN/FLAT).
  That is 3 x 3 = 9 combinations, and this document defines all nine —
  unlike derivatives-context-v0.1, there is no undefined combination
  here; NO_MATCH occurs only for a missing/unavailable input, never for
  an input combination this document fails to cover.

  UP   / UP    -> LONG_BUILDUP
     Price rose while open interest rose. Mechanically: positions grew
     while price moved up over this window. This does NOT mean a
     bullish trade, a buy signal, or that the positions being added are
     net long — only that more contracts existed at the close of this
     window than at its open, alongside a price increase.

  UP   / FLAT  -> PRICE_RISE_NO_OI_EXPANSION
     Price rose while open interest did not meaningfully change.

  UP   / DOWN  -> SHORT_COVERING
     Price rose while open interest fell. Mechanically consistent with
     positions closing (of either side) during a price increase — this
     document does not determine which side closed, only that fewer
     contracts existed at the close of the window than at its open.

  FLAT / UP    -> NEW_PARTICIPATION
     Price did not meaningfully change while open interest rose.

  FLAT / FLAT  -> QUIET
     Neither price nor open interest meaningfully changed.

  FLAT / DOWN  -> DELEVERAGING
     Price did not meaningfully change while open interest fell.

  DOWN / UP    -> SHORT_BUILDUP
     Price fell while open interest rose. This does NOT mean a bearish
     trade, a sell signal, or that the positions being added are net
     short — only that more contracts existed at the close of this
     window than at its open, alongside a price decrease.

  DOWN / FLAT  -> PRICE_FALL_NO_OI_EXPANSION
     Price fell while open interest did not meaningfully change.

  DOWN / DOWN  -> LONG_UNWIND
     Price fell while open interest fell. Mechanically consistent with
     positions closing (of either side) during a price decrease — this
     document does not determine which side closed.

  Every state name describes what price and open interest did, together,
  over one closed window — never what a trader should do next.

NO_MATCH
  A missing or non-OK-status price or open-interest input is NO_MATCH,
  with a reason naming which input was unavailable. There is no other
  NO_MATCH path in this document: every (price, OI) combination is one
  of the nine states above.

WHAT THIS DOCUMENT DOES NOT DO
  - It does not use funding, long/short ratio, liquidations, or buy/sell
    volume as inputs to the classification, and it does not define any
    threshold or interpretation for them.
  - It does not rank the nine states against each other, does not assign
    directional bias (long/short, bullish/bearish) to any of them, and
    does not produce a trade direction, entry, stop, target, or R:R
    under any circumstance.
  - It does not modify, supersede, or depend on
    docs/rulebook/derivatives-context-v0.1.md, Section 1, Section 2, the
    research engine, or universe-selection logic in any way.
  - It leaves no threshold undefined as of this version. A classifier
    encountering a case this document doesn't cover must still treat it
    as NO_MATCH with a reason, per CLAUDE.md's standing rule against
    guessing undefined rulebook behavior.

CHANGE PROCESS
  This is a rulebook document like any other under docs/rulebook/: a
  change creates a new version file (v0.2, ...); this file is never
  edited in place once another merge depends on it. This version (v0.1)
  is depended upon by `position_flow_classifier.py` — any future
  threshold or state-definition change ships as a new version file, with
  its own classifier change reviewed against it, never a silent edit of
  what v0.1 already means.
