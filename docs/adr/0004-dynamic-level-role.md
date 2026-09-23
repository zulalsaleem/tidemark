# 4. Dynamic level role (Section 1 v1.1)

Date: 2026-09-23

## Status

Accepted

## Context

While reviewing `core/levels.py`'s support/resistance classification
(see the report given alongside this decision), it became clear that
v1.0 never actually defined how a level's role was decided — the
engine's original implementation *assumed* it: a level's support/
resistance label was fixed permanently at formation, derived entirely
from its origin. A swing high (or a previous day/week high) was always
`RESISTANCE`; a swing low (or a previous day/week low) was always
`SUPPORT`. This assumption was baked into `Level.kind` at construction
time in `core/levels.py` and read directly by `context/htf.py`'s
"holds major support"/"holds major resistance" checks.

Real BTC data surfaced why this matters: a level born from a swing high
can sit well below current price after a sustained move up, and a
human reading the chart would call that level support, not resistance
— it's exactly the kind of level price is likely to react to on a
pullback. The v1.0 engine could never classify it that way, because it
only ever asked "what did this level's origin say," not "what does
price relative to this level say."

## Decision

Section 1 v1.1 (`docs/rulebook/section-01-htf-context-v1.1.md`) adds
RULE 1.7a: a level's role is computed fresh at every evaluation from
the level's price and the current 4H close (`close > level_price` ->
SUPPORT, `close < level_price` -> RESISTANCE, `close = level_price` ->
SUPPORT by registered tie-break), and is never stored from the level's
formation.

This explicitly separates two concepts v1.0 conflated:

- **Origin** — a permanent fact about where a level came from
  (`swing_high_cluster`, `swing_low_cluster`, `prev_day_high`,
  `prev_day_low`, `prev_week_high`, `prev_week_low`). Unchanged by this
  rule; still set once at construction in `core/levels.py`.
- **Role** — support or resistance, evaluated fresh against the current
  close via the new `level_role()` function in `core/levels.py`, and
  used by `context/htf.py`'s "holds major support/resistance" checks
  and reported per-level in the output record instead of a static
  label.

`Level.kind` (still populated as before, from origin) is no longer read
anywhere in the decision-matrix logic or shown in the `active_levels[]`
output, specifically to prevent it being mistaken for role now that the
word "support"/"resistance" means something dynamic.

This is a new rule_version, not a same-version clarification, because
it changes engine output: the same stored candles can now produce a
different `watch`/`grade`/`reason_code` for a level that was previously
classified only by origin. `context/htf.py`'s `RULE_VERSION` becomes
`"section-01-v1.1"`. `docs/rulebook/section-01-htf-context-v1.0.md` is
left untouched and locked, as the accurate historical record of what
v1.0's engine actually produced — existing `ContextRecord` rows tagged
`section-01-v1.0` are never rewritten to reflect v1.1's behavior.

## Consequences

- A level's displayed role in `context explain` can now differ from
  what its origin alone would suggest — e.g. a `swing_high_cluster`
  level can display `role: support`. This is intended, not a bug.
- Any future code path that touches level classification must call
  `level_role(level, close)` fresh rather than caching or reading a
  static role from a `Level` object — caching it would silently
  reintroduce the v1.0 assumption this ADR retires.
- Old (`section-01-v1.0`) and new (`section-01-v1.1`) `ContextRecord`
  rows can coexist for the same asset and evaluated_at, since they key
  on `rule_version` too (see `data/store.py`). Comparing across the two
  must account for the behavior difference this ADR describes.
- `docs/rulebook/open-questions.md`'s near-duplicate-level question is
  unaffected and unresolved by this change — RULE 1.7a governs role for
  a level that already exists; it says nothing about whether two
  close-together levels from different origins should be merged.
