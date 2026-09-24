FOR GROK REVIEW

PROJECT STATE

Strategy A (ZEC/USDT 5m mean reversion) is frozen research-only.
Economic scorecard over 3 x 90d:
- 412 trades
- net PnL -20.353 USDT
- expectancy -0.0494/trade
- PF 0.877
- trail-hit +134.613 USDT
- trail-never -154.966 USDT
- 2 of 3 blocks negative
No paper/live deployment.

Filter B (4h confirmation after 24h low) is closed FAIL and may not be retuned.

Momentum proxy pre-screen is closed:
- ZEC/BTC 5m breakout overlap ±3 bars: 50.82%
- ZEC-on-BTC breakout-day R²: 0.2649
- ZEC idiosyncratic breakout share ±3: 40.90%
Verdict: ZEC_B_PREREGISTRATION_ELIGIBLE.
The diagnostic 20-bar event is not Strategy B.

CURRENT QUESTION

Review TEST-SPEC-002 — ZEC TSMOM B v1 before the first official evaluation.

LOCKED SPEC

Market: ZEC/USDT spot
Execution feed: 5m
Signal timeframe: fully closed 1h UTC bars
Max open positions: 1
Stake: 50 USDT
No leverage

Signal:
- N = 24
- range_high = max high of previous 24 fully closed 1h bars
- range_low = min low of previous 24 fully closed 1h bars
- long if signal 1h close > range_high
- short if signal 1h close < range_low
- signal candle excluded from range
- entry at open of first 5m candle after signal-hour close
- no entry inside signal hour
- no volume/RSI/Bollinger/rebound/BTC filter

ATR:
- Wilder ATR(24) on fully closed 1h bars
- TR uses standard max(high-low, |high-prev close|, |low-prev close|)
- latest fully closed ATR remains active until next 1h close

Exit:
- initial stop = 2 x ATR against entry
- chandelier:
  long: peak 5m high since entry - 2 x latest closed 1h ATR
  short: trough 5m low since entry + 2 x latest closed 1h ATR
- stop never loosens
- no timeout
- no opposite-breakout exit
- no Strategy A trail or stop

5m intrabar execution:
- active stop for a 5m candle is frozen before that candle begins
- gap through stop exits at 5m open
- otherwise stop touch exits at stop price
- current candle extrema may only tighten stop for the next 5m candle
- no retrospective same-candle tightening

End-of-test:
- if position is open at fold end, exit at final 5m close
- exit_reason=end_of_test
- same costs
- included in n, PnL, expectancy and PF
- if final 5m close is invalid, run is invalid

Costs:
- same stored fee + spread + impact snapshot as Strategy A
- long: R*(1-sell_cost)/(1+buy_cost)-1
- short: 1-R*(1+buy_cost)/(1-sell_cost)
- no funding, borrow charge, leverage or maker rebate

OFFICIAL EVALUATION DATA

Historical Sep-Nov 2025 fold was rejected before any B strategy metrics because OKX ZEC/USDT spot existed only from 2025-11-24 12:00 UTC.

Official forward fold is locked:
2026-09-24 12:00:00.000 UTC
to
2026-12-23 11:59:59.999 UTC

No partial-fold PASS/FAIL is allowed.
Runner has a hard guard preventing official snapshot/evaluation before fold end.

PASS / FAIL

If <20 closed trades:
INSUFFICIENT_SAMPLE

Otherwise PASS only if all:
1. net expectancy/trade > 0
2. PF >= 1.10
3. if both long and short have trades, both side net PnLs > 0
4. same-direction BTC 24x1h breakout overlap within ±1h < 60%

Else FAIL.

BTC overlap is diagnostic only, not an entry filter.

IMPLEMENTATION DETAILS TO REVIEW

Synthetic smoke currently passes:
- UTC 1h aggregation
- current signal candle excluded from breakout range
- Wilder ATR(24)
- long/short transaction costs
- entry on next 5m open
- end_of_test accounting
- no retroactive intrabar stop
- invalid final close rejection

YOU MAY CHALLENGE

- any look-ahead or intrabar ordering flaw
- ATR timing semantics
- long/short accounting symmetry
- signal-to-entry timing
- stop execution assumptions
- sample sufficiency logic
- PASS/FAIL logic
- BTC-overlap diagnostic definition
- whether any implementation ambiguity remains before the forward fold completes

DO NOT PROPOSE AFTER RESULTS

- different N
- different ATR period
- different ATR multiple
- volume or ATR-expansion filters
- BTC/ETH entry filters
- long-only or short-only variants
- timeout
- Strategy A exit logic
- parameter grid
- using Dec 2025-Sep 2026 for B PASS/FAIL
- changing the forward fold after results begin accumulating

REQUEST

Review this only for methodological or implementation flaws before the official run.
Do not optimize parameters and do not infer thresholds from previous evaluation results.
Return:
1. blocking flaws that must be fixed before the run,
2. non-blocking caveats,
3. whether the test is clean enough to remain locked as written.
