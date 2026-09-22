# CLAUDE.md

Guidance for Claude Code (and any other agent) working in this repository.

## What Tidemark is

A rule-based market-structure copilot for cryptocurrency markets. It
evaluates a human-written rulebook against closed candles and sends
read-only alerts to Telegram. A human makes every trading decision. The
system never places orders, never holds private keys, and never has
exchange trading permissions. Read-only market data only.

## Standing rules

1. **Never invent, tune, or "improve" a trading rule.** Rules come only
   from `docs/rulebook/`. If something is undefined there, mark it
   `NOT_DEFINED` and ask instead of guessing.
2. **Closed candles only.** No future information in any calculation.
3. **Every structural point stores `formed_at` and `confirmed_at`.**
4. **Every emitted record stores the rulebook version that produced it.**
5. **Secrets come from environment variables and are never logged.**
6. **"No setups found" is a successful run, not a failure.**

## Working with the rulebook

- The rulebook is the single source of truth for strategy logic. Code
  implements what the rulebook says; it never adds to it.
- Rulebook versions are immutable. A change creates a new version file;
  an old version is never edited in place.
- If a rulebook section is marked `DRAFT`, `PENDING`, or `NOT_DEFINED`,
  the corresponding code path must not guess a behavior — it should
  raise/flag rather than silently pick a default.

## Stack

Python 3.11, `uv` for dependency management, SQLite via SQLAlchemy,
pandas for candle handling, typer for the CLI, pytest for tests, ruff
for lint and format, pydantic-settings for configuration.

## Conventions

- Conventional Commits for all commit messages.
- Prefer several small, logically separated commits over one large one.
- Do not push to remote without explicit confirmation.
