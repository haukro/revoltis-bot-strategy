# Strategy A economic scorecard

Status: **RESEARCH-ONLY — NOT A LIVE CANDIDATE**

This document records the frozen economic scorecard for Strategy A.

## Frozen strategy

- Pair: ZEC/USDT
- Trading timeframe: 5m
- Stop loss: 6%
- Trailing start: 1.6%
- Trailing distance: 0.3%
- Max no-trail timeout: 4h
- Max open trades: 1
- Stake: 50 USDT
- Cost model: stored taker fee + half-spread + book impact snapshot
- Cost snapshot timestamp: 1790183884652
- Cost model basis: current_book_snapshot_not_historical_l2
- Optimizer: none
- Grid: none

Final scorecard run:
`b0fdcf56-b133-4ec4-92bc-e5b40423af5f`

## 90-day blocks

| Metric | A | B | C | Combined |
|---|---:|---:|---:|---:|
| Trades | 89 | 136 | 187 | 412 |
| Trades / 30d month | 29.67 | 45.33 | 62.33 | 45.78 |
| Net PnL USDT | -2.405 | -22.295 | +4.347 | **-20.353** |
| Net expectancy / trade | -0.0270 | -0.1639 | +0.0232 | **-0.0494** |
| Net profit factor | 0.926 | 0.697 | 1.073 | **0.877** |
| Trail-hit PnL | +27.602 | +49.455 | +57.556 | **+134.613** |
| Trail-never PnL | -30.007 | -71.750 | -53.209 | **-154.966** |
| Trail-hit share | 47.19% | 47.79% | 42.25% | 45.15% |
| Max DD | 8.59% | 26.86% | 10.83% | max block **26.86%** |
| Time in market | 11.42% | 17.75% | 26.44% | 18.53% |
| B&H return | -58.43% | +84.75% | +278.91% | — |
| B&H max DD | 66.51% | 62.65% | 22.58% | — |

Block A: 2025-12-27 17:15 UTC to 2026-03-27 17:14:59.999 UTC  
Block B: 2026-03-27 17:15 UTC to 2026-06-25 17:14:59.999 UTC  
Block C: 2026-06-25 17:15 UTC to 2026-09-23 17:14:59.999 UTC

## Economic conclusion

Across the three fixed 90-day blocks:

- total net PnL = **-20.353 USDT**
- net expectancy = **-0.0494 USDT per trade**
- net profit factor = **0.877**
- positive blocks = **1 / 3**
- negative blocks = **2 / 3**
- trail-hit branch = **+134.613 USDT**
- trail-never branch = **-154.966 USDT**

The trail-hit edge does **not** pay for the trail-never loss branch after the stored transaction-cost model.

Strategy A therefore remains a research baseline only. It must not be promoted to paper/live deployment on the basis of this scorecard.

## Boundary for future work

Do not reopen Strategy A through another 24h-low filter, trail grid, timeout grid or post-hoc threshold search.

Future alpha work must target a different independently specified edge or market mechanism.

Any future Strategy B must be evaluated with the same cost discipline before portfolio or router research.

## Cost-model caveat

The historical PnL is net of a stored current-book estimate for fee, spread and book impact. It is not reconstructed from historical L2 order books.

This caveat affects execution precision, but the scorecard verdict is based on the declared model consistently across all three blocks.
