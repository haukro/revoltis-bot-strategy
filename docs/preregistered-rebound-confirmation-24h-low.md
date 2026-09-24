# Pre-registered test: rebound confirmation vs 24h low

Status: **LOCKED BEFORE EVALUATION**

This document defines the next unseen evaluation test for the ZEC strategy.
The discovery datasets must not be used to tune any threshold or interpretation in this test.

## 1. Hypothesis

The mean-reversion entry fails mainly when the signal appears close to a fresh 24h low without a confirmed rebound.

A completed 4h bullish higher-low candle after the latest 24h low may separate continuing selloffs from entries that have a realistic chance of reaching the existing trailing-profit activation level.

This is the only hypothesis tested here.

## 2. Frozen baseline

The trading system remains unchanged except for the single candidate entry gate defined below.

| Item | Locked value |
|---|---|
| Pair | ZEC/USDT |
| Strategy timeframe | 5m |
| Strategy direction | spot long-only |
| Entry signal | existing entry logic, unchanged |
| Stop loss | 6% |
| Trailing start | +1.6% |
| Trailing distance | 0.3% |
| Max no-trail timeout | 4h, unchanged |
| Fees / cost model | unchanged |
| Trend filter | none |
| 1h / 4h pre-entry drop filter | forbidden |
| Percent-above-low threshold | forbidden |
| range_pos threshold | forbidden |
| Parameter grid | forbidden |

The 4h value refers to the existing max-no-trail timeout and to the confirmation candle timeframe.
The strategy itself continues to run on **5m** bars.

## 3. Discovery data excluded from evaluation

The following already-inspected datasets are discovery / prior research only and must not decide PASS / FAIL for this test:

- 2026-03-27 17:15:00 UTC to 2026-06-25 17:14:59.999 UTC
- 2026-06-25 17:15:00 UTC to 2026-09-23 17:14:59.999 UTC

No threshold may be derived from their winner / loser separation.

## 4. Evaluation dataset

The evaluation dataset is fixed before results are viewed:

**2025-12-27 17:15:00 UTC to 2026-03-27 17:14:59.999 UTC**

It is the immediately preceding non-overlapping 90-day block.

Baseline and candidate must use exactly the same candles, fees, cost model, WF / holdout procedure and benchmark rules.

## 5. Two branches

### Baseline

The frozen 5m ZEC baseline with the existing 4h max-no-trail timeout and no new entry filter.

### Candidate

Exactly the same strategy plus **Filter B** only.

No other parameter may differ.

## 6. Filter B: 4h rebound confirmation after the current 24h low

A 5m entry signal is allowed only if a completed 4h rebound-confirmation candle exists.

### 6.1 4h candle alignment

4h candles use fixed UTC boundaries:

- 00:00–03:59:59.999
- 04:00–07:59:59.999
- 08:00–11:59:59.999
- 12:00–15:59:59.999
- 16:00–19:59:59.999
- 20:00–23:59:59.999

Only fully closed 4h candles may be used.

The 4h candle containing the current 5m signal / entry is not eligible.

### 6.2 Current 24h low

For each 5m signal:

1. Take the last **6 fully closed 4h candles** before the 4h candle containing the signal.
2. 24h_low is the minimum low among those 6 candles.
3. If multiple 4h candles have exactly the same minimum low, the **most recent** one is the low-origin candle.
4. No data from the current incomplete 4h candle or from the future may be used.

### 6.3 Confirmation condition

After the low-origin candle and before the 5m signal, there must be at least one fully closed 4h candle satisfying both:

- low > 24h_low
- close > open

If no such candle exists:

- candidate taken = 0
- skip_reason = no_rebound_confirm

There is no percentage cutoff, no minimum distance above the low and no range-position threshold.

## 7. Per-signal logging

Log the following fields for every baseline signal and its corresponding candidate decision:

- timestamp
- taken
- skip_reason = none / no_rebound_confirm
- entry_price
- trail_hit
- outcome = win / loss
- mae_pct
- mae_before_trail_pct
- hit_minus_1pct
- time_to_trail_min
- time_to_minus_1pct_min
- pre_entry_1h_ret
- pre_entry_4h_ret
- rv_24bars
- range_pos_24h
- pct_above_24h_low
- bars_since_24h_low

range_pos_24h, pct_above_24h_low, pre-entry returns and realized volatility are diagnostics only.
They must not enter Filter B or the PASS / FAIL decision.

## 8. Primary metrics

Calculate separately for baseline and candidate on the same evaluation fold:

1. Win rate among trades where trail was **not** reached
2. Share of taken trades that reached trail
3. Win rate among trades that reached trail
4. Number of taken trades

Also report total baseline signals and candidate skip rate.

## 9. Secondary diagnostics

These are descriptive only and cannot change the verdict:

- overall win rate
- average MAE
- average MAE of losing trades
- median time to trail
- median time to -1%
- share reaching -1% before trail
- average range_pos_24h for winners vs losses
- average pct_above_24h_low for winners vs losses
- pre-entry 1h / 4h return for winners vs losses
- PnL
- expectancy
- maximum drawdown
- B&H return
- B&H maximum drawdown

## 10. Sample sufficiency

Before PASS / FAIL is evaluated:

- If the **baseline** has fewer than **20 trades in the trail-not-reached group**, verdict = **INSUFFICIENT_SAMPLE**.
- No PASS / FAIL conclusion is allowed from a smaller baseline denominator.
- This rule is only a statistical sufficiency guard and is not a strategy parameter.

## 11. PASS / FAIL criteria

If the sample is sufficient, the candidate passes only if **all four** conditions are true:

1. Win rate in the **trail-not-reached** group improves by at least **+15 percentage points** versus baseline on the same fold.
2. Trail-reached share falls by **no more than 10 percentage points** versus baseline.
3. Win rate among trail-reached trades remains **>= 90%**.
4. Taken trade count falls by **no more than 35%** versus baseline.

Otherwise verdict = **FAIL**.

There is no partial pass, almost-passed status, discretionary override or post-hoc threshold adjustment.

## 12. Required report table

Fill only after the unseen fold has completed.

| Metric | Baseline | Candidate | Delta |
|---|---:|---:|---:|
| Total signals |  |  |  |
| Taken trades |  |  |  |
| Skip rate |  |  |  |
| Overall WR |  |  |  |
| Trail reached share |  |  |  |
| WR if trail reached |  |  |  |
| WR if trail not reached |  |  |  |
| -1% before trail |  |  |  |
| Median time -> trail |  |  |  |
| Median time -> -1% |  |  |  |
| Avg MAE |  |  |  |
| Avg MAE losses |  |  |  |

Final verdict must be exactly one of:

- **PASS**
- **FAIL**
- **INSUFFICIENT_SAMPLE**

Below the table, add only two interpretation sentences:

1. Whether Filter B removed mainly the trail-not-reached group.
2. Whether it materially reduced the share of trades that reach trail.

No new hypothesis may be introduced in the same report.

## 13. Forbidden until verdict

Until this test produces its locked verdict, do not:

- change the 24h-low definition
- add range_pos > 0.33
- add any percentage threshold above the low
- change stop loss
- change trailing start or distance
- change the existing 4h max-no-trail timeout
- filter on 1h / 4h pre-entry return
- add a trend filter
- tune thresholds using this evaluation fold
- run a parameter grid
- substitute another symbol or date range
- alter PASS / FAIL thresholds after results are viewed

If the candidate fails, the next step is not to tune Filter B on this fold.
Filter B is rejected for this preregistered test, or a completely new hypothesis must be preregistered on another unseen fold.
