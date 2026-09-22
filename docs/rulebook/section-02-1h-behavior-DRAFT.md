SCALPING RULEBOOK — SECTION 2: 1H BEHAVIOR
Status: EXTRACTION IN PROGRESS

This section is not registered and not locked. Nothing below is
implementable yet. States and parameters marked PENDING must not be
guessed, estimated, or defaulted by code or by an assistant working in
this repository — ask the rulebook author instead.

STATES (all PENDING)
  REJECTION                    PENDING
  LEVEL_FAILING                PENDING
  CONSOLIDATION                PENDING
  BULLISH_STRUCTURE_CHANGE     PENDING
  BEARISH_STRUCTURE_CHANGE     PENDING
  TREND_RESUMING                PENDING

OPEN PARAMETERS
  1. 1H swing fractal N .................... NOT_DEFINED
  2. Definition of "at/around the HTF area" on 1H ... NOT_DEFINED

NOTES
  Section 2 consumes the OUTPUT RECORD emitted by Section 1
  (docs/rulebook/section-01-htf-context-v1.0.md) but does not yet define
  how 1H price behavior is classified once that context is known. Do not
  implement `context/mtf.py` beyond a stub until this document is
  registered with a version, a date, and a source, and every PENDING
  state and NOT_DEFINED parameter above has been resolved.
