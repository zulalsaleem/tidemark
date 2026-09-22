# 2. Canonical market-data venue

Date: 2026-09-22

## Status

Accepted

## Context

Rulebook Section 1 defines structure (swings, levels, ATR) as a function
of one continuous price/volume history per symbol. Different exchanges
quote perpetual futures at slightly different prices, with different
funding-driven basis, different liquidation cascades, and different
candle boundaries around low-liquidity periods. If Tidemark fetched some
history for a symbol from one venue and the rest from another — or
compared structure computed on one venue's candles against a level formed
on another's — the resulting swings, levels, and ATR values would be
internally inconsistent in ways that have nothing to do with market
structure and everything to do with data-source noise.

Tidemark also needs one client implementation that can point at more
than one venue (for redundancy, or to move the canonical venue later)
without a code change per venue.

## Decision

- Binance USDT-M perpetual futures (ccxt id `binanceusdm`) is the
  canonical venue by default, because it is the deepest and most liquid
  USDT-M perpetual venue for the symbols in `TIDEMARK_SYMBOLS`, which
  keeps candle data closest to a clean read of aggregate market
  structure.
- Venue is configuration (`TIDEMARK_VENUE`), not code. `data/exchange.py`
  is built on ccxt and works unchanged against any ccxt venue id that
  exposes unified-symbol OHLCV, including `bitget` and `mexc`.
- Mixing venues within one symbol's stored history is not allowed. All
  candles for a given `(symbol, timeframe)` in the `candles` table come
  from a single venue at a time; the `venue` column exists precisely so
  this can be enforced and audited, not so histories can be blended.
- Switching the canonical venue for a symbol is an explicit operational
  decision (e.g. re-backfilling under the new venue), not something the
  ingestion code decides automatically.

## Consequences

- Structure computed in later phases (swings, levels, ATR, Section 1
  evaluation) is always computed from one internally consistent price
  history per symbol.
- Adding or trialing a second venue requires no code change to
  `data/exchange.py` — only a configuration change and, if adopted, a
  fresh backfill for the affected symbols.
- Nothing in the data layer silently merges or averages candles across
  venues; if that is ever wanted, it needs its own ADR and its own
  rulebook basis, not a quiet default.
