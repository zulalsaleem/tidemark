This is the corrected v0.1 baseline. It supersedes
[section-02-v0.1-baseline-SUPERSEDED.md](section-02-v0.1-baseline-SUPERSEDED.md),
which had two bugs in `replay/report.py`'s Table 2 (per-session) grouping
and classification — see [ADR 0008](../adr/0008-replay-as-a-repo-command.md)
for the full account. In short:

- **Bug 1** (session grouping): a session's closing `HTF_CONTEXT_INVALIDATED`
  row was being split off into its own phantom session instead of closing
  the session it ended, because that row's own Section 1 fields reflect
  the *new* post-change record, not the ending session's pin. Fixed in
  `group_sessions`. Session count: 903 → 476.
- **Bug 2** (structure-change direction): every session ending in
  `HANDOFF_TO_15M` — the shared echo for both bullish and bearish
  confirmations — was labelled `STRUCTURE_CHANGE_LONG` regardless of its
  actual direction, because the classifier never read the row's own
  `structure_change` field. Fixed in `_classify_session`. Direction split:
  16 long / 0 short → 13 bullish / 3 bearish.

The **data snapshot below is unchanged** from the superseded report — same
row hash (`018f246a810905f025022e1bc0b01c2a8f5777185d01fef3f6f9e79d7a04520c`),
same candles. Only the Table 2 grouping/classification code changed;
Table 1 and Table 3 were already correct and are numerically identical to
the superseded report.

---

# Section section-02-v0.1 replay

- command: `tidemark replay --rule-version section-02-v0.1`
- rule_version: `section-02-v0.1`
- generated_at: 2026-09-24T14:35:28.347166+00:00

## Data snapshot

- venue: `binanceusdm`
- symbols: `BTC/USDT:USDT`, `ETH/USDT:USDT`, `SOL/USDT:USDT`, `XRP/USDT:USDT`, `DOGE/USDT:USDT`
- timeframes: `4h`, `1d`, `1w`, `1h`
- row hash (sha256 over venue/symbol/timeframe/open_time/OHLCV): `018f246a810905f025022e1bc0b01c2a8f5777185d01fef3f6f9e79d7a04520c`

| Symbol | Timeframe | Rows | First open_time | Last open_time |
| --- | --- | ---: | --- | --- |
| BTC/USDT:USDT | 4h | 1079 | 2026-03-28T08:00:00+00:00 | 2026-09-24T00:00:00+00:00 |
| BTC/USDT:USDT | 1d | 179 | 2026-03-29T00:00:00+00:00 | 2026-09-23T00:00:00+00:00 |
| BTC/USDT:USDT | 1w | 25 | 2026-03-30T00:00:00+00:00 | 2026-09-14T00:00:00+00:00 |
| BTC/USDT:USDT | 1h | 4319 | 2026-03-28T05:00:00+00:00 | 2026-09-24T03:00:00+00:00 |
| ETH/USDT:USDT | 4h | 1079 | 2026-03-28T08:00:00+00:00 | 2026-09-24T00:00:00+00:00 |
| ETH/USDT:USDT | 1d | 179 | 2026-03-29T00:00:00+00:00 | 2026-09-23T00:00:00+00:00 |
| ETH/USDT:USDT | 1w | 25 | 2026-03-30T00:00:00+00:00 | 2026-09-14T00:00:00+00:00 |
| ETH/USDT:USDT | 1h | 4319 | 2026-03-28T05:00:00+00:00 | 2026-09-24T03:00:00+00:00 |
| SOL/USDT:USDT | 4h | 1079 | 2026-03-28T08:00:00+00:00 | 2026-09-24T00:00:00+00:00 |
| SOL/USDT:USDT | 1d | 179 | 2026-03-29T00:00:00+00:00 | 2026-09-23T00:00:00+00:00 |
| SOL/USDT:USDT | 1w | 25 | 2026-03-30T00:00:00+00:00 | 2026-09-14T00:00:00+00:00 |
| SOL/USDT:USDT | 1h | 4319 | 2026-03-28T05:00:00+00:00 | 2026-09-24T03:00:00+00:00 |
| XRP/USDT:USDT | 4h | 1079 | 2026-03-28T08:00:00+00:00 | 2026-09-24T00:00:00+00:00 |
| XRP/USDT:USDT | 1d | 179 | 2026-03-29T00:00:00+00:00 | 2026-09-23T00:00:00+00:00 |
| XRP/USDT:USDT | 1w | 25 | 2026-03-30T00:00:00+00:00 | 2026-09-14T00:00:00+00:00 |
| XRP/USDT:USDT | 1h | 4319 | 2026-03-28T05:00:00+00:00 | 2026-09-24T03:00:00+00:00 |
| DOGE/USDT:USDT | 4h | 1079 | 2026-03-28T08:00:00+00:00 | 2026-09-24T00:00:00+00:00 |
| DOGE/USDT:USDT | 1d | 179 | 2026-03-29T00:00:00+00:00 | 2026-09-23T00:00:00+00:00 |
| DOGE/USDT:USDT | 1w | 25 | 2026-03-30T00:00:00+00:00 | 2026-09-14T00:00:00+00:00 |
| DOGE/USDT:USDT | 1h | 4319 | 2026-03-28T05:00:00+00:00 | 2026-09-24T03:00:00+00:00 |

## Table 1 — Section 1, per evaluation

One row of this table's counts per 4H close, point-in-time (as-of that close, no look-ahead). `ALL` is every symbol combined.

### BTC/USDT:USDT

Total evaluations: 1079

| State | Count | % |
| --- | ---: | ---: |
| INSUFFICIENT_STRUCTURE | 16 | 1.48% |
| BULLISH | 313 | 29.01% |
| BEARISH | 202 | 18.72% |
| NEUTRAL | 418 | 38.74% |
| STRUCTURE_BROKEN_BULL | 58 | 5.38% |
| STRUCTURE_BROKEN_BEAR | 72 | 6.67% |

| Reason code | Count | % |
| --- | ---: | ---: |
| NOT_ENOUGH_SWINGS | 16 | 1.48% |
| STRUCTURE_BROKEN | 130 | 12.05% |
| NEUTRAL_STRUCTURE | 418 | 38.74% |
| MAJOR_SUPPORT_FIB | 14 | 1.30% |
| MAJOR_SUPPORT | 72 | 6.67% |
| MAJOR_RESISTANCE_FIB | 21 | 1.95% |
| MAJOR_RESISTANCE | 52 | 4.82% |
| FIB_ONLY | 52 | 4.82% |
| NOT_IN_ZONE | 304 | 28.17% |

| Watch | Grade A | Grade B |
| --- | ---: | ---: |
| LONG_WATCH | 14 | 72 |
| SHORT_WATCH | 21 | 52 |

WATCH episodes: 94  |  median length (4H candles): 1.0  |  max length: 5

### ETH/USDT:USDT

Total evaluations: 1079

| State | Count | % |
| --- | ---: | ---: |
| INSUFFICIENT_STRUCTURE | 18 | 1.67% |
| BULLISH | 315 | 29.19% |
| BEARISH | 217 | 20.11% |
| NEUTRAL | 419 | 38.83% |
| STRUCTURE_BROKEN_BULL | 47 | 4.36% |
| STRUCTURE_BROKEN_BEAR | 63 | 5.84% |

| Reason code | Count | % |
| --- | ---: | ---: |
| NOT_ENOUGH_SWINGS | 18 | 1.67% |
| STRUCTURE_BROKEN | 110 | 10.19% |
| NEUTRAL_STRUCTURE | 419 | 38.83% |
| MAJOR_SUPPORT_FIB | 34 | 3.15% |
| MAJOR_SUPPORT | 58 | 5.38% |
| MAJOR_RESISTANCE_FIB | 21 | 1.95% |
| MAJOR_RESISTANCE | 40 | 3.71% |
| FIB_ONLY | 96 | 8.90% |
| NOT_IN_ZONE | 283 | 26.23% |

| Watch | Grade A | Grade B |
| --- | ---: | ---: |
| LONG_WATCH | 34 | 58 |
| SHORT_WATCH | 21 | 40 |

WATCH episodes: 91  |  median length (4H candles): 1  |  max length: 6

### SOL/USDT:USDT

Total evaluations: 1079

| State | Count | % |
| --- | ---: | ---: |
| INSUFFICIENT_STRUCTURE | 18 | 1.67% |
| BULLISH | 273 | 25.30% |
| BEARISH | 242 | 22.43% |
| NEUTRAL | 373 | 34.57% |
| STRUCTURE_BROKEN_BULL | 86 | 7.97% |
| STRUCTURE_BROKEN_BEAR | 87 | 8.06% |

| Reason code | Count | % |
| --- | ---: | ---: |
| NOT_ENOUGH_SWINGS | 18 | 1.67% |
| STRUCTURE_BROKEN | 173 | 16.03% |
| NEUTRAL_STRUCTURE | 373 | 34.57% |
| MAJOR_SUPPORT_FIB | 12 | 1.11% |
| MAJOR_SUPPORT | 51 | 4.73% |
| MAJOR_RESISTANCE_FIB | 26 | 2.41% |
| MAJOR_RESISTANCE | 55 | 5.10% |
| FIB_ONLY | 80 | 7.41% |
| NOT_IN_ZONE | 291 | 26.97% |

| Watch | Grade A | Grade B |
| --- | ---: | ---: |
| LONG_WATCH | 12 | 51 |
| SHORT_WATCH | 26 | 55 |

WATCH episodes: 78  |  median length (4H candles): 1.5  |  max length: 8

### XRP/USDT:USDT

Total evaluations: 1079

| State | Count | % |
| --- | ---: | ---: |
| INSUFFICIENT_STRUCTURE | 18 | 1.67% |
| BULLISH | 233 | 21.59% |
| BEARISH | 275 | 25.49% |
| NEUTRAL | 357 | 33.09% |
| STRUCTURE_BROKEN_BULL | 114 | 10.57% |
| STRUCTURE_BROKEN_BEAR | 82 | 7.60% |

| Reason code | Count | % |
| --- | ---: | ---: |
| NOT_ENOUGH_SWINGS | 18 | 1.67% |
| STRUCTURE_BROKEN | 196 | 18.16% |
| NEUTRAL_STRUCTURE | 357 | 33.09% |
| MAJOR_SUPPORT_FIB | 6 | 0.56% |
| MAJOR_SUPPORT | 59 | 5.47% |
| MAJOR_RESISTANCE_FIB | 13 | 1.20% |
| MAJOR_RESISTANCE | 68 | 6.30% |
| FIB_ONLY | 79 | 7.32% |
| NOT_IN_ZONE | 283 | 26.23% |

| Watch | Grade A | Grade B |
| --- | ---: | ---: |
| LONG_WATCH | 6 | 59 |
| SHORT_WATCH | 13 | 68 |

WATCH episodes: 78  |  median length (4H candles): 1.0  |  max length: 6

### DOGE/USDT:USDT

Total evaluations: 1079

| State | Count | % |
| --- | ---: | ---: |
| INSUFFICIENT_STRUCTURE | 25 | 2.32% |
| BULLISH | 230 | 21.32% |
| BEARISH | 307 | 28.45% |
| NEUTRAL | 345 | 31.97% |
| STRUCTURE_BROKEN_BULL | 92 | 8.53% |
| STRUCTURE_BROKEN_BEAR | 80 | 7.41% |

| Reason code | Count | % |
| --- | ---: | ---: |
| NOT_ENOUGH_SWINGS | 25 | 2.32% |
| STRUCTURE_BROKEN | 172 | 15.94% |
| NEUTRAL_STRUCTURE | 345 | 31.97% |
| MAJOR_SUPPORT_FIB | 15 | 1.39% |
| MAJOR_SUPPORT | 50 | 4.63% |
| MAJOR_RESISTANCE_FIB | 20 | 1.85% |
| MAJOR_RESISTANCE | 65 | 6.02% |
| FIB_ONLY | 95 | 8.80% |
| NOT_IN_ZONE | 292 | 27.06% |

| Watch | Grade A | Grade B |
| --- | ---: | ---: |
| LONG_WATCH | 15 | 50 |
| SHORT_WATCH | 20 | 65 |

WATCH episodes: 86  |  median length (4H candles): 1.0  |  max length: 6

### ALL

Total evaluations: 5395

| State | Count | % |
| --- | ---: | ---: |
| INSUFFICIENT_STRUCTURE | 95 | 1.76% |
| BULLISH | 1364 | 25.28% |
| BEARISH | 1243 | 23.04% |
| NEUTRAL | 1912 | 35.44% |
| STRUCTURE_BROKEN_BULL | 397 | 7.36% |
| STRUCTURE_BROKEN_BEAR | 384 | 7.12% |

| Reason code | Count | % |
| --- | ---: | ---: |
| NOT_ENOUGH_SWINGS | 95 | 1.76% |
| STRUCTURE_BROKEN | 781 | 14.48% |
| NEUTRAL_STRUCTURE | 1912 | 35.44% |
| MAJOR_SUPPORT_FIB | 81 | 1.50% |
| MAJOR_SUPPORT | 290 | 5.38% |
| MAJOR_RESISTANCE_FIB | 101 | 1.87% |
| MAJOR_RESISTANCE | 280 | 5.19% |
| FIB_ONLY | 402 | 7.45% |
| NOT_IN_ZONE | 1453 | 26.93% |

| Watch | Grade A | Grade B |
| --- | ---: | ---: |
| LONG_WATCH | 81 | 290 |
| SHORT_WATCH | 101 | 280 |

WATCH episodes: 427  |  median length (4H candles): 1  |  max length: 8

## Table 2 — Section 2, per SESSION

A session is one contiguous run under a single pinned Section 1 (state, watch, grade). Every session ends in exactly one terminal outcome; each symbol's outcome counts sum to that symbol's session count (see the **sum** row). `ALL` is every symbol combined.

### BTC/USDT:USDT

Sessions: 103  |  with any interaction: 79  |  median length (1H candles): 5  |  max length: 21

| Terminal outcome | Count |
| --- | ---: |
| STRUCTURE_CHANGE_LONG | 4 |
| STRUCTURE_CHANGE_SHORT | 0 |
| LEVEL_FAILURE_SUPPORT | 16 |
| LEVEL_FAILURE_RESISTANCE | 9 |
| REACTION_EXPIRED | 0 |
| HTF_CONTEXT_INVALIDATED | 74 |
| STILL_OPEN_AT_END_OF_DATA | 0 |
| **sum** | **103** |

| Highest reaction tier reached | Sessions |
| --- | ---: |
| R1 | 44 |
| R2 | 9 |
| R3 | 24 |
| none | 26 |

Why sessions ending in HTF_CONTEXT_INVALIDATED actually ended (74 sessions):
| Reason | Count |
| --- | ---: |
| Grade-only change | 8 |
| State/watch change | 66 |

No-reaction sessions, split by interaction (26 sessions):
| Interaction | Count |
| --- | ---: |
| With interaction | 2 |
| Without interaction | 24 |

### ETH/USDT:USDT

Sessions: 102  |  with any interaction: 77  |  median length (1H candles): 5.0  |  max length: 25

| Terminal outcome | Count |
| --- | ---: |
| STRUCTURE_CHANGE_LONG | 5 |
| STRUCTURE_CHANGE_SHORT | 0 |
| LEVEL_FAILURE_SUPPORT | 11 |
| LEVEL_FAILURE_RESISTANCE | 8 |
| REACTION_EXPIRED | 0 |
| HTF_CONTEXT_INVALIDATED | 78 |
| STILL_OPEN_AT_END_OF_DATA | 0 |
| **sum** | **102** |

| Highest reaction tier reached | Sessions |
| --- | ---: |
| R1 | 40 |
| R2 | 14 |
| R3 | 17 |
| none | 31 |

Why sessions ending in HTF_CONTEXT_INVALIDATED actually ended (78 sessions):
| Reason | Count |
| --- | ---: |
| Grade-only change | 9 |
| State/watch change | 69 |

No-reaction sessions, split by interaction (31 sessions):
| Interaction | Count |
| --- | ---: |
| With interaction | 6 |
| Without interaction | 25 |

### SOL/USDT:USDT

Sessions: 90  |  with any interaction: 70  |  median length (1H candles): 5.0  |  max length: 17

| Terminal outcome | Count |
| --- | ---: |
| STRUCTURE_CHANGE_LONG | 1 |
| STRUCTURE_CHANGE_SHORT | 1 |
| LEVEL_FAILURE_SUPPORT | 5 |
| LEVEL_FAILURE_RESISTANCE | 10 |
| REACTION_EXPIRED | 0 |
| HTF_CONTEXT_INVALIDATED | 73 |
| STILL_OPEN_AT_END_OF_DATA | 0 |
| **sum** | **90** |

| Highest reaction tier reached | Sessions |
| --- | ---: |
| R1 | 35 |
| R2 | 15 |
| R3 | 19 |
| none | 21 |

Why sessions ending in HTF_CONTEXT_INVALIDATED actually ended (73 sessions):
| Reason | Count |
| --- | ---: |
| Grade-only change | 9 |
| State/watch change | 64 |

No-reaction sessions, split by interaction (21 sessions):
| Interaction | Count |
| --- | ---: |
| With interaction | 1 |
| Without interaction | 20 |

### XRP/USDT:USDT

Sessions: 82  |  with any interaction: 59  |  median length (1H candles): 5.0  |  max length: 21

| Terminal outcome | Count |
| --- | ---: |
| STRUCTURE_CHANGE_LONG | 2 |
| STRUCTURE_CHANGE_SHORT | 1 |
| LEVEL_FAILURE_SUPPORT | 6 |
| LEVEL_FAILURE_RESISTANCE | 7 |
| REACTION_EXPIRED | 0 |
| HTF_CONTEXT_INVALIDATED | 66 |
| STILL_OPEN_AT_END_OF_DATA | 0 |
| **sum** | **82** |

| Highest reaction tier reached | Sessions |
| --- | ---: |
| R1 | 23 |
| R2 | 17 |
| R3 | 15 |
| none | 27 |

Why sessions ending in HTF_CONTEXT_INVALIDATED actually ended (66 sessions):
| Reason | Count |
| --- | ---: |
| Grade-only change | 3 |
| State/watch change | 63 |

No-reaction sessions, split by interaction (27 sessions):
| Interaction | Count |
| --- | ---: |
| With interaction | 4 |
| Without interaction | 23 |

### DOGE/USDT:USDT

Sessions: 99  |  with any interaction: 72  |  median length (1H candles): 5  |  max length: 25

| Terminal outcome | Count |
| --- | ---: |
| STRUCTURE_CHANGE_LONG | 1 |
| STRUCTURE_CHANGE_SHORT | 1 |
| LEVEL_FAILURE_SUPPORT | 12 |
| LEVEL_FAILURE_RESISTANCE | 10 |
| REACTION_EXPIRED | 0 |
| HTF_CONTEXT_INVALIDATED | 75 |
| STILL_OPEN_AT_END_OF_DATA | 0 |
| **sum** | **99** |

| Highest reaction tier reached | Sessions |
| --- | ---: |
| R1 | 41 |
| R2 | 7 |
| R3 | 21 |
| none | 30 |

Why sessions ending in HTF_CONTEXT_INVALIDATED actually ended (75 sessions):
| Reason | Count |
| --- | ---: |
| Grade-only change | 10 |
| State/watch change | 65 |

No-reaction sessions, split by interaction (30 sessions):
| Interaction | Count |
| --- | ---: |
| With interaction | 3 |
| Without interaction | 27 |

### ALL

Sessions: 476  |  with any interaction: 357  |  median length (1H candles): 5.0  |  max length: 25

| Terminal outcome | Count |
| --- | ---: |
| STRUCTURE_CHANGE_LONG | 13 |
| STRUCTURE_CHANGE_SHORT | 3 |
| LEVEL_FAILURE_SUPPORT | 50 |
| LEVEL_FAILURE_RESISTANCE | 44 |
| REACTION_EXPIRED | 0 |
| HTF_CONTEXT_INVALIDATED | 366 |
| STILL_OPEN_AT_END_OF_DATA | 0 |
| **sum** | **476** |

| Highest reaction tier reached | Sessions |
| --- | ---: |
| R1 | 183 |
| R2 | 62 |
| R3 | 96 |
| none | 135 |

Why sessions ending in HTF_CONTEXT_INVALIDATED actually ended (366 sessions):
| Reason | Count |
| --- | ---: |
| Grade-only change | 39 |
| State/watch change | 327 |

No-reaction sessions, split by interaction (135 sessions):
| Interaction | Count |
| --- | ---: |
| With interaction | 16 |
| Without interaction | 119 |

### Structure-change sessions - detail

Grade Section 1 held at session start, and the reaction tier that preceded the confirming close, for every structure-change session across all symbols.

| Symbol | Session start | Trigger | Direction | Grade at start | Reaction tier |
| --- | --- | --- | --- | --- | --- |
| BTC/USDT:USDT | 2026-04-15T12:00:00+00:00 | 2026-04-15T20:00:00+00:00 | BULLISH_STRUCTURE_CHANGE | B | R1 |
| BTC/USDT:USDT | 2026-06-12T08:00:00+00:00 | 2026-06-12T17:00:00+00:00 | BULLISH_STRUCTURE_CHANGE | B | R1 |
| BTC/USDT:USDT | 2026-08-26T16:00:00+00:00 | 2026-08-26T23:00:00+00:00 | BULLISH_STRUCTURE_CHANGE | A | R3 |
| BTC/USDT:USDT | 2026-09-05T04:00:00+00:00 | 2026-09-05T16:00:00+00:00 | BULLISH_STRUCTURE_CHANGE | B | R1 |
| DOGE/USDT:USDT | 2026-06-13T01:00:00+00:00 | 2026-06-13T08:00:00+00:00 | BULLISH_STRUCTURE_CHANGE | A | R1 |
| DOGE/USDT:USDT | 2026-07-17T20:00:00+00:00 | 2026-07-18T06:00:00+00:00 | BEARISH_STRUCTURE_CHANGE | B | R1 |
| ETH/USDT:USDT | 2026-04-24T04:00:00+00:00 | 2026-04-24T12:00:00+00:00 | BULLISH_STRUCTURE_CHANGE | A | R1 |
| ETH/USDT:USDT | 2026-05-02T08:00:00+00:00 | 2026-05-02T14:00:00+00:00 | BULLISH_STRUCTURE_CHANGE | B | R1 |
| ETH/USDT:USDT | 2026-05-10T08:00:00+00:00 | 2026-05-10T16:00:00+00:00 | BULLISH_STRUCTURE_CHANGE | B | R1 |
| ETH/USDT:USDT | 2026-05-26T04:00:00+00:00 | 2026-05-26T11:00:00+00:00 | BULLISH_STRUCTURE_CHANGE | A | R1 |
| ETH/USDT:USDT | 2026-08-08T00:00:00+00:00 | 2026-08-08T11:00:00+00:00 | BULLISH_STRUCTURE_CHANGE | A | R1 |
| SOL/USDT:USDT | 2026-05-20T20:00:00+00:00 | 2026-05-21T01:00:00+00:00 | BULLISH_STRUCTURE_CHANGE | B | R1 |
| SOL/USDT:USDT | 2026-07-13T08:00:00+00:00 | 2026-07-13T18:00:00+00:00 | BEARISH_STRUCTURE_CHANGE | B | R3 |
| XRP/USDT:USDT | 2026-06-18T04:00:00+00:00 | 2026-06-18T14:00:00+00:00 | BEARISH_STRUCTURE_CHANGE | B | R3 |
| XRP/USDT:USDT | 2026-07-26T12:00:00+00:00 | 2026-07-26T17:00:00+00:00 | BULLISH_STRUCTURE_CHANGE | B | R1 |
| XRP/USDT:USDT | 2026-09-18T08:00:00+00:00 | 2026-09-18T14:00:00+00:00 | BULLISH_STRUCTURE_CHANGE | B | R2 |

## Table 3 — Section 2, per EVALUATION

Row-level state counts - these are **evaluation counts, not outcomes**. A single session shows up as many rows here (one per 1H close), which is why `NO_STRUCTURAL_REFERENCE` in this table is not comparable to any count in Table 2. `ALL` is every symbol combined.

### BTC/USDT:USDT

Total evaluations (1H closes under an active WATCH): 730

| State | Count | % |
| --- | ---: | ---: |
| NO_INTERACTION | 145 | 19.86% |
| REACTION_DETECTED | 166 | 22.74% |
| NO_STRUCTURAL_REFERENCE | 241 | 33.01% |
| CONTINUATION_CANDIDATE_NOT_EVALUATED | 6 | 0.82% |
| BULLISH_STRUCTURE_CHANGE | 4 | 0.55% |
| BEARISH_STRUCTURE_CHANGE | 0 | 0.00% |
| HANDOFF_TO_15M | 8 | 1.10% |
| SUPPORT_FAILURE | 16 | 2.19% |
| RESISTANCE_FAILURE | 9 | 1.23% |
| STAND_DOWN | 27 | 3.70% |
| HTF_CONTEXT_INVALIDATED | 103 | 14.11% |
| REACTION_EXPIRED | 5 | 0.68% |

### ETH/USDT:USDT

Total evaluations (1H closes under an active WATCH): 703

| State | Count | % |
| --- | ---: | ---: |
| NO_INTERACTION | 155 | 22.05% |
| REACTION_DETECTED | 141 | 20.06% |
| NO_STRUCTURAL_REFERENCE | 209 | 29.73% |
| CONTINUATION_CANDIDATE_NOT_EVALUATED | 14 | 1.99% |
| BULLISH_STRUCTURE_CHANGE | 5 | 0.71% |
| BEARISH_STRUCTURE_CHANGE | 0 | 0.00% |
| HANDOFF_TO_15M | 19 | 2.70% |
| SUPPORT_FAILURE | 11 | 1.56% |
| RESISTANCE_FAILURE | 8 | 1.14% |
| STAND_DOWN | 36 | 5.12% |
| HTF_CONTEXT_INVALIDATED | 102 | 14.51% |
| REACTION_EXPIRED | 3 | 0.43% |

### SOL/USDT:USDT

Total evaluations (1H closes under an active WATCH): 654

| State | Count | % |
| --- | ---: | ---: |
| NO_INTERACTION | 151 | 23.09% |
| REACTION_DETECTED | 117 | 17.89% |
| NO_STRUCTURAL_REFERENCE | 239 | 36.54% |
| CONTINUATION_CANDIDATE_NOT_EVALUATED | 1 | 0.15% |
| BULLISH_STRUCTURE_CHANGE | 1 | 0.15% |
| BEARISH_STRUCTURE_CHANGE | 1 | 0.15% |
| HANDOFF_TO_15M | 11 | 1.68% |
| SUPPORT_FAILURE | 5 | 0.76% |
| RESISTANCE_FAILURE | 10 | 1.53% |
| STAND_DOWN | 28 | 4.28% |
| HTF_CONTEXT_INVALIDATED | 90 | 13.76% |
| REACTION_EXPIRED | 0 | 0.00% |

### XRP/USDT:USDT

Total evaluations (1H closes under an active WATCH): 662

| State | Count | % |
| --- | ---: | ---: |
| NO_INTERACTION | 205 | 30.97% |
| REACTION_DETECTED | 121 | 18.28% |
| NO_STRUCTURAL_REFERENCE | 193 | 29.15% |
| CONTINUATION_CANDIDATE_NOT_EVALUATED | 9 | 1.36% |
| BULLISH_STRUCTURE_CHANGE | 2 | 0.30% |
| BEARISH_STRUCTURE_CHANGE | 1 | 0.15% |
| HANDOFF_TO_15M | 20 | 3.02% |
| SUPPORT_FAILURE | 6 | 0.91% |
| RESISTANCE_FAILURE | 7 | 1.06% |
| STAND_DOWN | 13 | 1.96% |
| HTF_CONTEXT_INVALIDATED | 82 | 12.39% |
| REACTION_EXPIRED | 3 | 0.45% |

### DOGE/USDT:USDT

Total evaluations (1H closes under an active WATCH): 686

| State | Count | % |
| --- | ---: | ---: |
| NO_INTERACTION | 153 | 22.30% |
| REACTION_DETECTED | 132 | 19.24% |
| NO_STRUCTURAL_REFERENCE | 238 | 34.69% |
| CONTINUATION_CANDIDATE_NOT_EVALUATED | 3 | 0.44% |
| BULLISH_STRUCTURE_CHANGE | 1 | 0.15% |
| BEARISH_STRUCTURE_CHANGE | 1 | 0.15% |
| HANDOFF_TO_15M | 4 | 0.58% |
| SUPPORT_FAILURE | 12 | 1.75% |
| RESISTANCE_FAILURE | 10 | 1.46% |
| STAND_DOWN | 30 | 4.37% |
| HTF_CONTEXT_INVALIDATED | 99 | 14.43% |
| REACTION_EXPIRED | 3 | 0.44% |

### ALL

Total evaluations (1H closes under an active WATCH): 3435

| State | Count | % |
| --- | ---: | ---: |
| NO_INTERACTION | 809 | 23.55% |
| REACTION_DETECTED | 677 | 19.71% |
| NO_STRUCTURAL_REFERENCE | 1120 | 32.61% |
| CONTINUATION_CANDIDATE_NOT_EVALUATED | 33 | 0.96% |
| BULLISH_STRUCTURE_CHANGE | 13 | 0.38% |
| BEARISH_STRUCTURE_CHANGE | 3 | 0.09% |
| HANDOFF_TO_15M | 62 | 1.80% |
| SUPPORT_FAILURE | 50 | 1.46% |
| RESISTANCE_FAILURE | 44 | 1.28% |
| STAND_DOWN | 134 | 3.90% |
| HTF_CONTEXT_INVALIDATED | 476 | 13.86% |
| REACTION_EXPIRED | 14 | 0.41% |
