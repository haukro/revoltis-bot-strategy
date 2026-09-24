# Result — TEST-SPEC-003 BTC TSMOM C v1

Status: **CLOSED — FAIL**

This document records the single official evaluation of TEST-SPEC-003.

## Audit trail

- Spec: `docs/TEST-SPEC-003-btc-tsmom-c-v1.md`
- Locked spec merge commit: `75e4ab2d90c061ae63d45b0739f132c6400cf94e`
- Implementation merge commit: `d85431002b1e9cc33ce1b505b908641c65a01102`
- BTC raw snapshot id: `cf1cd0f6-b202-4793-9811-a38434e51dc0`
- ETH raw snapshot id: `04d1b7b7-e45b-4e2a-8507-017ca806cdc1`
- Continuity certificate id: `cf2f5e42-95c2-4bf3-9172-e55333463989`
- Official evaluation run id: `758d536a-c0a8-4146-9703-295755413cc5`
- Official run count for TEST-SPEC-003: **1**
- Grid: none
- Strategy B modified: **false**

## Continuity certificate

The certificate was produced before the official Strategy C performance run.

BTC execution data:

- 5m bars: **26,208 / 26,208**
- 1h bars: **2,184 / 2,184**
- contiguous 5m: **true**
- eligible 1h signals checked for exact entry availability: **197**
- missing required entry bars: **0**
- final 5m close valid: **true**
- execution data pass: **true**

ETH diagnostic data:

- 5m bars: **26,220 / 26,220**
- 1h bars: **2,185 / 2,185**
- contiguous 5m: **true**
- eligible BTC signals missing complete ETH ±1h window: **0**
- diagnostic completeness: **true**

The continuity certificate computed no Strategy C PnL, expectancy, PF, drawdown or PASS/FAIL metric.

## Official fold

`2025-09-02 00:00:00.000 UTC → 2025-11-30 23:59:59.999 UTC`

## Frozen strategy

- Market: BTC/USDT spot on OKX
- Execution feed: 5m
- Signal timeframe: fully closed 1h UTC
- Breakout N: 24
- Wilder ATR period: 24
- ATR multiple: 2.0
- Long + short
- Max open positions: 1
- Gross entry notional: 50 USDT
- No leverage
- No timeout
- No opposite-breakout exit
- No volume / volatility / ETH / ZEC entry filter
- ETH overlap: diagnostic only

## Frozen BTC cost snapshot

- book_ts: `1790275677755`
- taker fee: 0.001 per side
- entry_cost_rate: `0.0010005926681029335`
- exit_cost_rate: `0.0010005926681029335`
- half_spread: `5.926681029335338e-07`
- buy impact: 0
- sell impact: 0
- basis: current_book_snapshot_not_historical_l2

Shorts remain research marks on spot prints, not proof of borrow-free spot-short executability.

## Official result

| Metric | BTC C v1 |
|---|---:|
| 1h signals | 197 |
| Closed trades | **92** |
| Net PnL | **-10.417843 USDT** |
| Net expectancy / trade | **-0.113237 USDT** |
| Profit factor | **0.5797** |
| Net PnL long | **-7.929850 USDT** |
| Net PnL short | **-2.487993 USDT** |
| Long trades | 49 |
| Short trades | 43 |
| Win rate | 34.78% |
| Max DD | **12.73%** |
| Time in market | 31.95% |
| Average hold | 450.05 min |
| ETH same-direction overlap ±1h | 56.52% |
| ETH overlap completeness | 100% |
| B&H return | -17.36% |
| B&H max DD | 35.75% |

Exit reasons:

- chandelier_stop: 90
- chandelier_stop_gap: 2
- end_of_test: 0

## Preregistered PASS conditions

| Condition | Result |
|---|---|
| Net expectancy > 0 | **FAIL** |
| PF >= 1.10 | **FAIL** |
| Both active sides profitable | **FAIL** |

Official verdict:

# **FAIL**

The strategy had sufficient sample size, so this is not an INSUFFICIENT_SAMPLE result.

## Interpretation

The exact ZEC TSMOM prescription did **not** replicate profitably on BTC in this clean 90-day fold after the frozen BTC transaction-cost model.

Both long and short books were independently negative, so the failure is not explained by only one side of the market.

ETH overlap was 56.52% with complete diagnostic data, but ETH overlap was not a PASS gate and does not alter the verdict.

## Research boundary after FAIL

Do not use this fold to:

- tune N
- tune ATR period
- tune ATR multiple
- create a long-only or short-only rescue
- add volume / volatility / ETH / ZEC filters
- add timeout or opposite-breakout exits
- rank nearby BTC variants
- reinterpret this result as a near-pass

TEST-SPEC-003 is closed.

Any future BTC strategy requires a new hypothesis, preregistration, and unused evaluation fold.

The result must not be used to alter the still-running frozen TEST-SPEC-002 ZEC forward evaluation.


## Final family-level follow-up after Grok review

The raw N=24 / Wilder ATR(24) / 2x ATR TSMOM prescription will **not** be replicated across additional coins such as ETH or SOL after the BTC FAIL.

Reason:

- deciding to add more coins only after seeing BTC fail would extend the hypothesis family post hoc
- BTC was the intended benchmark replication market and produced a sufficient 92-trade sample with both long and short sides negative
- additional same-prescription altcoin tests would risk turning replication into coin selection
- the still-running ZEC TEST-SPEC-002 remains the only open test of this exact prescription

This does not claim that time-series momentum can never work in crypto. It closes only this exact research branch: 1h N=24 breakout with Wilder ATR(24) and 2x ATR stop/chandelier replicated coin-by-coin.

Any future research must either:

- wait for the frozen ZEC forward result, or
- preregister a genuinely different economic edge family rather than another coin with the same prescription.
