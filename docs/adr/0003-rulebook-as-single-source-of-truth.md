# 3. Rulebook as single source of truth

Date: 2026-09-22

## Status

Accepted

## Context

Section 1 (`docs/rulebook/section-01-htf-context-v1.0.md`) is LOCKED and
now has a full engine behind it: `core/atr.py`, `core/swings.py`,
`core/levels.py`, `core/fib.py`, and `context/htf.py`'s state machine and
decision matrix. Writing that engine required turning prose parameters
and a plain-text decision matrix into code, and it would have been easy —
maybe even helpful, in the moment — to round a threshold, add a
convenience default, or "fix" an edge case the rulebook doesn't cover.
That temptation only grows once the engine is live against real data.

## Decision

Engine code encodes the rulebook; it never extends or corrects it.
Concretely:

- Every threshold, formula, and matrix row in `core/` and `context/htf.py`
  traces to a specific line in the rulebook. Where a piece of behavior
  was genuinely undefined (e.g. the STRUCTURE_BROKEN_BULL/_BEAR naming
  direction, or the reason codes for matrix rows 4-7, which the rulebook
  doesn't spell out), the code comment says so explicitly and picks the
  most literal reading rather than inventing new trading logic.
- A rulebook version file, once registered, is never edited to fix a
  behavior. If real usage shows Section 1 needs to change — a different
  fractal N, a different zone tolerance, a new matrix row — that is a new
  rulebook version file (e.g. `section-01-htf-context-v1.1.md`), authored
  by a human, not a code patch against v1.0.
- `context/htf.py`'s `RULE_VERSION` always names the exact rulebook
  version it implements (`"section-01-v1.0"`), and every emitted
  `ContextRecord` carries it, so a later behavior change is always
  attributable to a specific rulebook version rather than "whatever the
  code did that week."
- If a future change to Section 1's *behavior* is needed, it lands as: (1)
  a new rulebook version file, (2) an engine change that targets that new
  version, (3) the old version file and its historical `ContextRecord`
  rows left untouched.

## Consequences

- Engine changes that aren't traceable to a rulebook version bump should
  be treated as a bug, not a feature — including by an agent working in
  this repo.
- Backtests and historical `ContextRecord` rows stay interpretable: a
  record's `rule_version` tells you exactly which rulebook text produced
  it, even after Section 1 has moved to v1.1, v1.2, etc.
- This is slower than "just tune the parameter in code." That's
  deliberate — Section 1 governs real alerts a human acts on, and
  silent, undocumented behavior drift is a worse failure mode than a
  slower change process.
