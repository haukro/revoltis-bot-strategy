# Pre-registered diagnostic: ZEC momentum proxy pre-screen

Status: **LOCKED BEFORE BTC/ETH DATA LOAD**

This document defines a diagnostic pre-screen only. It does not define Strategy B, does not backtest PnL, and does not change Strategy A.

## 1. Purpose

Determine whether a future 5m ZEC momentum/breakout Strategy B would likely represent an independent ZEC-specific edge, or mainly proxy the shared BTC/ETH crypto-momentum factor.

The diagnostic event `20-bar high` is not a Strategy B parameter and must not be carried into a later B specification automatically.

## 2. Frozen datasets

Use exactly the same three non-overlapping 90-day blocks as the Strategy A economic scorecard:

- A: 2025-12-27 17:15:00 UTC to 2026-03-27 17:14:59.999 UTC
- B: 2026-03-27 17:15:00 UTC to 2026-06-25 17:14:59.999 UTC
- C: 2026-06-25 17:15:00 UTC to 2026-09-23 17:14:59.999 UTC

Symbols:

- ZEC/USDT
- BTC/USDT
- ETH/USDT

No other symbol or date range may replace these after results are viewed.

## 3. Timeframes

Measure separately on:

- 5m
- 1h

The 1h series must be aggregated from the same aligned 5m feed using fixed UTC hour boundaries. Do not fetch a separately aligned 1h dataset if it could create timestamp differences.

## 4. Raw return correlation

For each block and timeframe, compute close-to-close log returns and Pearson correlation for:

- ZEC vs BTC
- ZEC vs ETH
- BTC vs ETH

Use only timestamps present for all three symbols.

Also report pooled correlations across all three blocks.

## 5. Diagnostic breakout event

For each symbol and timeframe, define:

`breakout_t = close_t > max(high_{t-20}, ..., high_{t-1})`

Rules:

- the current bar is excluded from the 20-bar high
- every qualifying bar is an event; no debounce or cooldown
- no volume, ATR, RSI, Bollinger, trail, timeout, stop, sizing or fee logic
- this event is diagnostic only

## 6. Shared-breakout metrics

Condition on every ZEC breakout event.

For BTC and ETH separately, compute:

- same-bar conditional probability:
  `P(asset breakout at t | ZEC breakout at t)`
- event within ±1 bar
- event within ±3 bars

At 5m:
- ±1 = ±5 minutes
- ±3 = ±15 minutes

At 1h:
- ±1 = ±1 hour
- ±3 = ±3 hours

## 7. Idiosyncratic ZEC breakout share

A ZEC breakout is idiosyncratic for a given window if neither BTC nor ETH has a breakout event in that window.

Report:

- idiosyncratic share for ±1 bar
- idiosyncratic share for ±3 bars

The primary idiosyncratic metric for routing is the ±3-bar result.

## 8. BTC beta / explained variation on ZEC-breakout days

This metric is defined only on 5m returns.

1. Identify UTC calendar days containing at least one ZEC 5m breakout event.
2. On those days, take all aligned 5m ZEC and BTC close-to-close log returns.
3. Fit simple OLS with intercept:
   `ZEC_return = alpha + beta * BTC_return + error`
4. Report:
   - beta
   - Pearson correlation
   - R²

For one-regressor OLS with intercept, R² is the squared contemporaneous Pearson correlation.

This is a diagnostic of shared crypto beta, not a trading signal.

## 9. Raw follow-through diagnostic

No PnL, costs or strategy exits are computed.

For every ZEC breakout event, report the raw close-to-close forward return over the next 1 hour:

- on 5m: close at t+12 bars versus event close
- on 1h: close at t+1 bar versus event close

Split only into:

- idiosyncratic ±3-bar ZEC breakouts
- ZEC breakouts with BTC and/or ETH event within ±3 bars

Report mean, median and positive-share.

This metric is descriptive. No numeric follow-through threshold is allowed to be invented after viewing the results.

## 10. Pre-screen routing verdict

The hard routing decision uses only the pre-registered overlap, R² and idiosyncratic-share thresholds below.

### Route away from a single-pair ZEC Strategy B

If either condition is true:

- ZEC/BTC breakout overlap is **>= 60%** using the 5m ±3-bar metric, or
- 5m ZEC-on-BTC breakout-day R² is **>= 0.50**

then verdict:

**DO_NOT_BUILD_ZEC_ONLY_B**

The next research direction is multi-market time-series momentum, or Strategy B is deferred.

### Eligible for a separately preregistered ZEC Strategy B

Only if all are true:

- ZEC/BTC 5m ±3-bar overlap < 60%
- breakout-day 5m R² < 0.50
- 5m idiosyncratic ZEC breakout share at ±3 bars >= 40%

then verdict:

**ZEC_B_PREREGISTRATION_ELIGIBLE**

The raw 1h follow-through comparison must be reported beside this verdict, but it cannot change the hard verdict because no additional follow-through cutoff has been preregistered.

Otherwise verdict:

**INCONCLUSIVE_FOR_ZEC_B**

No Strategy B specification may be inferred from an inconclusive result.

## 11. Required output

For each block A/B/C and pooled:

| Metric | 5m | 1h |
|---|---:|---:|
| Pearson ZEC-BTC | | |
| Pearson ZEC-ETH | | |
| Pearson BTC-ETH | | |
| ZEC breakout count | | |
| BTC same-bar given ZEC | | |
| ETH same-bar given ZEC | | |
| BTC ±1 given ZEC | | |
| ETH ±1 given ZEC | | |
| BTC ±3 given ZEC | | |
| ETH ±3 given ZEC | | |
| ZEC idiosyncratic ±1 | | |
| ZEC idiosyncratic ±3 | | |

Additional 5m-only fields:

- ZEC/BTC beta on ZEC-breakout UTC days
- ZEC/BTC R² on those days

Additional descriptive follow-through:

- idiosyncratic ±3 mean / median / positive share over +1h
- shared ±3 mean / median / positive share over +1h

Final verdict must be exactly one of:

- **DO_NOT_BUILD_ZEC_ONLY_B**
- **ZEC_B_PREREGISTRATION_ELIGIBLE**
- **INCONCLUSIVE_FOR_ZEC_B**

## 12. Forbidden until verdict

Do not:

- backtest Strategy B
- turn 20 bars into a Strategy B parameter
- add volume or ATR thresholds
- add a trail, stop or timeout
- use PnL for this diagnostic
- change overlap windows
- change 60%, 0.50 or 40% thresholds
- substitute another coin after seeing results
- search this output for a better breakout lookback
- use Strategy A performance to alter the pre-screen verdict
