# Result: ZEC momentum proxy pre-screen

Status: **CLOSED — ZEC_B_PREREGISTRATION_ELIGIBLE**

This document records the outcome of the diagnostic pre-screen defined in:

- `docs/preregistered-zec-momentum-proxy-prescreen.md`
- preregistration commit: `0dcd9bab922438f805053af5b54974091f86a2f7`

The diagnostic did not define or backtest Strategy B.

## Result run

- run id: `1893afa3-a6b2-434f-a94a-99876c025831`
- symbols: ZEC/USDT, BTC/USDT, ETH/USDT
- source timeframe: 5m
- secondary timeframe: 1h aggregated from the same 5m feed
- blocks: the same A/B/C 90-day windows as Strategy A
- diagnostic breakout event: close above prior 20-bar high
- Strategy B PnL: not computed
- grid: none

## Hard routing inputs

| Metric | Result | Locked threshold |
|---|---:|---:|
| ZEC/BTC 5m breakout overlap, ±3 bars | **50.82%** | route away if >= 60% |
| ZEC-on-BTC 5m breakout-day R² | **0.2649** | route away if >= 0.50 |
| ZEC 5m idiosyncratic breakout share, ±3 bars | **40.90%** | eligible if >= 40% |

Final hard verdict:

**ZEC_B_PREREGISTRATION_ELIGIBLE**

The idiosyncratic-share threshold was cleared narrowly, by about 0.90 percentage points.

## Pooled raw-return correlations

| Pair | 5m | 1h |
|---|---:|---:|
| ZEC-BTC | 0.5140 | 0.4972 |
| ZEC-ETH | 0.5202 | 0.5072 |
| BTC-ETH | 0.8818 | 0.8884 |

ZEC therefore carries a meaningful shared crypto factor, but the contemporaneous BTC factor does not explain a majority of ZEC 5m variance on ZEC-breakout days.

## Pooled breakout overlap

| Metric | 5m | 1h |
|---|---:|---:|
| ZEC breakout count | 3,650 | 274 |
| BTC same-bar given ZEC | 26.66% | 21.90% |
| ETH same-bar given ZEC | 24.08% | 22.99% |
| BTC ±1 given ZEC | 38.93% | 32.48% |
| ETH ±1 given ZEC | 35.84% | 33.94% |
| BTC ±3 given ZEC | 50.82% | 45.26% |
| ETH ±3 given ZEC | 48.08% | 48.18% |
| ZEC idiosyncratic ±1 | 53.89% | 58.03% |
| ZEC idiosyncratic ±3 | **40.90%** | **43.43%** |

## BTC beta on ZEC-breakout UTC days

- beta ZEC on BTC: **1.424**
- contemporaneous correlation: **0.515**
- R²: **0.2649**
- breakout UTC days: 271
- aligned 5m return observations: 77,389

The beta above 1 indicates amplified crypto-market sensitivity, but only about 26.5% of the contemporaneous ZEC 5m return variance is explained by BTC under this diagnostic definition.

## Descriptive +1h raw follow-through

This section is descriptive only and does not alter the hard routing verdict.

### 5m breakout events

| Group | N | Mean +1h | Median +1h | Positive share |
|---|---:|---:|---:|---:|
| Idiosyncratic ±3 | 1,493 | -0.007% | -0.186% | 42.67% |
| Shared ±3 | 2,157 | +0.088% | -0.099% | 45.85% |

### 1h breakout events

| Group | N | Mean +1h | Median +1h | Positive share |
|---|---:|---:|---:|---:|
| Idiosyncratic ±3 | 119 | -0.014% | -0.371% | 39.50% |
| Shared ±3 | 155 | +0.378% | +0.007% | 50.32% |

The raw diagnostic event therefore does **not** itself show attractive idiosyncratic +1h follow-through. This does not change the preregistered routing verdict because no follow-through cutoff was locked.

It also means that the diagnostic 20-bar-high event must not be promoted directly into Strategy B.

## Block stability

5m ZEC/BTC ±3 overlap by block:

- A: 52.59%
- B: 44.88%
- C: 55.14%

5m idiosyncratic ±3 share by block:

- A: 40.44%
- B: 45.77%
- C: 36.34%

5m breakout-day BTC R² by block:

- A: 0.3172
- B: 0.2202
- C: 0.2978

The pooled eligibility result is not driven by a single block, although block C falls below the 40% idiosyncratic-share threshold.

## Research boundary

This pre-screen is closed.

Do not:

- treat 20 bars as a Strategy B parameter because it appeared here
- tune a breakout lookback on these results
- add a post-hoc BTC overlap cutoff
- turn the descriptive +1h follow-through into a threshold after seeing it
- build a regime router before Strategy B exists and survives costs independently

The permitted next step is only:

**write a separate preregistration for one Strategy B v1 from first principles, then test it through the same cost/WF/holdout discipline.**
