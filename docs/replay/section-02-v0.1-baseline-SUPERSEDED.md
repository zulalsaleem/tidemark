# Section section-02-v0.1 replay

- command: `tidemark replay --rule-version section-02-v0.1`
- rule_version: `section-02-v0.1`
- generated_at: 2026-09-24T08:18:05.973108+00:00

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

Sessions: 197  |  with any interaction: 79  |  median length (1H candles): 4  |  max length: 20

| Terminal outcome | Count |
| --- | ---: |
| STRUCTURE_CHANGE_LONG | 4 |
| STRUCTURE_CHANGE_SHORT | 0 |
| LEVEL_FAILURE_SUPPORT | 16 |
| LEVEL_FAILURE_RESISTANCE | 9 |
| REACTION_EXPIRED | 0 |
| HTF_CONTEXT_INVALIDATED | 94 |
| STILL_OPEN_AT_END_OF_DATA | 74 |
| **sum** | **197** |

| Highest reaction tier reached | Sessions |
| --- | ---: |
| R1 | 44 |
| R2 | 9 |
| R3 | 24 |
| none | 120 |

### ETH/USDT:USDT

Sessions: 193  |  with any interaction: 77  |  median length (1H candles): 4  |  max length: 24

| Terminal outcome | Count |
| --- | ---: |
| STRUCTURE_CHANGE_LONG | 5 |
| STRUCTURE_CHANGE_SHORT | 0 |
| LEVEL_FAILURE_SUPPORT | 11 |
| LEVEL_FAILURE_RESISTANCE | 8 |
| REACTION_EXPIRED | 0 |
| HTF_CONTEXT_INVALIDATED | 91 |
| STILL_OPEN_AT_END_OF_DATA | 78 |
| **sum** | **193** |

| Highest reaction tier reached | Sessions |
| --- | ---: |
| R1 | 40 |
| R2 | 14 |
| R3 | 17 |
| none | 122 |

### SOL/USDT:USDT

Sessions: 168  |  with any interaction: 70  |  median length (1H candles): 4.0  |  max length: 16

| Terminal outcome | Count |
| --- | ---: |
| STRUCTURE_CHANGE_LONG | 2 |
| STRUCTURE_CHANGE_SHORT | 0 |
| LEVEL_FAILURE_SUPPORT | 5 |
| LEVEL_FAILURE_RESISTANCE | 10 |
| REACTION_EXPIRED | 0 |
| HTF_CONTEXT_INVALIDATED | 78 |
| STILL_OPEN_AT_END_OF_DATA | 73 |
| **sum** | **168** |

| Highest reaction tier reached | Sessions |
| --- | ---: |
| R1 | 35 |
| R2 | 15 |
| R3 | 19 |
| none | 99 |

### XRP/USDT:USDT

Sessions: 160  |  with any interaction: 59  |  median length (1H candles): 4.0  |  max length: 20

| Terminal outcome | Count |
| --- | ---: |
| STRUCTURE_CHANGE_LONG | 3 |
| STRUCTURE_CHANGE_SHORT | 0 |
| LEVEL_FAILURE_SUPPORT | 6 |
| LEVEL_FAILURE_RESISTANCE | 7 |
| REACTION_EXPIRED | 0 |
| HTF_CONTEXT_INVALIDATED | 78 |
| STILL_OPEN_AT_END_OF_DATA | 66 |
| **sum** | **160** |

| Highest reaction tier reached | Sessions |
| --- | ---: |
| R1 | 23 |
| R2 | 17 |
| R3 | 15 |
| none | 105 |

### DOGE/USDT:USDT

Sessions: 185  |  with any interaction: 72  |  median length (1H candles): 4  |  max length: 24

| Terminal outcome | Count |
| --- | ---: |
| STRUCTURE_CHANGE_LONG | 2 |
| STRUCTURE_CHANGE_SHORT | 0 |
| LEVEL_FAILURE_SUPPORT | 12 |
| LEVEL_FAILURE_RESISTANCE | 10 |
| REACTION_EXPIRED | 0 |
| HTF_CONTEXT_INVALIDATED | 86 |
| STILL_OPEN_AT_END_OF_DATA | 75 |
| **sum** | **185** |

| Highest reaction tier reached | Sessions |
| --- | ---: |
| R1 | 41 |
| R2 | 7 |
| R3 | 21 |
| none | 116 |

### ALL

Sessions: 903  |  with any interaction: 357  |  median length (1H candles): 4  |  max length: 24

| Terminal outcome | Count |
| --- | ---: |
| STRUCTURE_CHANGE_LONG | 16 |
| STRUCTURE_CHANGE_SHORT | 0 |
| LEVEL_FAILURE_SUPPORT | 50 |
| LEVEL_FAILURE_RESISTANCE | 44 |
| REACTION_EXPIRED | 0 |
| HTF_CONTEXT_INVALIDATED | 427 |
| STILL_OPEN_AT_END_OF_DATA | 366 |
| **sum** | **903** |

| Highest reaction tier reached | Sessions |
| --- | ---: |
| R1 | 183 |
| R2 | 62 |
| R3 | 96 |
| none | 562 |

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
