# 1. Record architecture decisions

Date: 2026-09-17

## Status

Accepted

## Context

Tidemark's behavior is governed by two things: the rulebook
(`docs/rulebook/`), which is the source of truth for *strategy* decisions,
and the codebase's own architectural decisions — stack choices, module
boundaries, data model shape, and constraints like "closed candles only."
These need a durable, dated record separate from the rulebook, since they
are engineering decisions rather than trading rules.

## Decision

We will use Architecture Decision Records (ADRs), stored under
`docs/adr/`, one file per decision, numbered sequentially
(`NNNN-title-in-kebab-case.md`). Each ADR records the context, the
decision, and its consequences at the time it was made. Superseding a
decision means adding a new ADR that references the old one, not editing
the old one in place — the same immutability principle the rulebook uses
for its own versions.

## Consequences

- Engineering decisions (stack, module boundaries, storage choice, CI
  setup) get the same auditable, append-only treatment as trading rules.
- Future contributors (human or agent) can see *why* a structural choice
  was made without archaeology through commit history.
- This is ADR 0001 by convention (recording the decision to use ADRs at
  all); subsequent decisions start at 0002.
