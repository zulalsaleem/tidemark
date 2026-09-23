# Open rulebook questions

Behavior the current rulebook doesn't define, surfaced by implementing or
running the engine against real data. Nothing here is decided — each entry
is a question for the rulebook author to resolve in a future version.
Code must not guess at an answer while an entry is open.

## Section 1 — near-duplicate levels from different sources

**Observed:** running `context explain` against real BTC/USDT:USDT data
produced two separate `active_levels` entries sitting within ~50 of each
other, with overlapping zones:

```
resistance 81983.03  zone[81731.19,82234.88]  touches=3  major  source=swing_cluster
resistance 81933.90  zone[81682.05,82185.75]  touches=1  major  source=prev_week_high
```

(81,983.03 from a swing cluster, 81,933.90 from the previous week's high;
81,983.03 − 81,933.90 ≈ 49.)

**Question:** Section 1 defines how a *swing cluster* becomes a level
(`>= 2 confirmed swing points within 0.5 x ATR of each other`) and how
*previous day/week high/low* become levels, as two independent
mechanisms. It does not say whether two levels from *different sources*
that land close together (or whose zones overlap) should be merged,
deduplicated, or left as separate entries. Currently they are left
separate — `core/levels.py` clusters swing highs and swing lows against
each other, but never compares a swing-cluster level against a
prev-day/prev-week level.

**Why it matters:** near-duplicate levels double-count confluence at
what a human would likely read as a single price zone, which could
affect how "holds major support/resistance" reads in the `explain`
output, and how many active levels get reported. Resolving this needs a
rule: a distance/zone-overlap threshold for merging, which source wins
if merged (or how a merged level's `touches`/`is_major` combine), or an
explicit decision that near-duplicates are intentional and should stay
separate.

**Status:** not merged, not changed. `core/levels.py` behavior is
unchanged pending a rulebook decision.
