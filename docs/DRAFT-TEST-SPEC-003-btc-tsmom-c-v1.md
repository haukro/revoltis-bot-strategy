# TEST-SPEC-003 — BTC TSMOM C v1

Status: **DRAFT FOR GROK REVIEW — NOT LOCKED, NOT RUN**

Purpose: cross-market replication of the already specified TSMOM mechanism on BTC/USDT, not parameter optimization.

## 0. Research question

Does the same 1h time-series momentum mechanism specified for ZEC B v1 retain positive expectancy after costs on a more liquid crypto market, BTC/USDT, without retuning N, ATR period, or ATR multiple?

This test is intended to distinguish a potentially portable TSMOM mechanism from a ZEC-specific effect.

## 1. Market and execution

| Item | C v1 draft |
|---|---|
| Market | BTC/USDT spot on OKX |
| Execution feed | 5m |
| Signal timeframe | fully closed 1h UTC candles |
| Max open positions | 1 |
| Gross entry notional | 50 USDT before costs |
| Leverage | none |
| Pyramiding | none |

Signals while a position is already open are ignored for entry and do not reverse or modify the open position.

## 2. Signal — replication, not retuning

Use only fully closed 1h UTC candles.

Locked candidate parameters copied unchanged from TEST-SPEC-002:

- breakout lookback N = 24
- ATR period = 24
- ATR multiple = 2.0

For signal candle t:

```
range_high = max(high) of previous 24 fully closed 1h candles
range_low  = min(low)  of previous 24 fully closed 1h candles
```

Signal candle t is excluded from the breakout range.

Long:

```
close_t > range_high
```

Short:

```
close_t < range_low
```

If both conditions were somehow true for the same candle, discard the signal.

No volume, RSI, Bollinger, ATR-expansion, ETH, ZEC, regime, funding, or other entry filter.

## 3. Entry execution

The 1h signal bar is represented as [H, H+1h), with stored close_time = H+1h-1 ms.

Required entry is the exact 5m candle with:

```
open_time = signal_1h.close_time + 1 ms
```

This is the next UTC hour boundary.

If that exact 5m candle is missing, the official run is invalid.

No delayed fallback fill and no fill inside the signal hour.

## 4. ATR

Wilder ATR(24) on fully closed 1h UTC candles.

True range:

```
TR_t = max(
    high_t - low_t,
    abs(high_t - close_{t-1}),
    abs(low_t - close_{t-1})
)
```

Initialization:

- SMA of first 24 TR observations

Then:

```
ATR_t = ((23 * ATR_{t-1}) + TR_t) / 24
```

At entry, ATR includes the fully closed signal candle's TR.

The following incomplete 1h candle is excluded.

After entry, ATR updates only when another 1h candle fully closes.

## 5. Exit

No Strategy A exit logic.

No timeout.

No opposite-breakout exit.

### 5.1 Initial stop

Long:

```
entry_price - 2 * ATR
```

Short:

```
entry_price + 2 * ATR
```

### 5.2 Chandelier

Long:

```
candidate = highest completed 5m high since entry - 2 * latest closed 1h ATR
stop = max(previous_stop, candidate)
```

Short:

```
candidate = lowest completed 5m low since entry + 2 * latest closed 1h ATR
stop = min(previous_stop, candidate)
```

Stop never loosens.

### 5.3 Intrabar ordering

For each 5m candle, active stop is fixed before that candle begins using only:

- latest fully closed 1h ATR
- extrema through the previous completed 5m candle

Then:

Long:
- if 5m open < active stop, exit at open
- else if 5m low <= active stop, exit at stop

Short:
- if 5m open > active stop, exit at open
- else if 5m high >= active stop, exit at stop

If the position survives, that candle's high/low may tighten the stop only for the next 5m candle.

Same-bar stop-out after entry open is allowed.

## 6. Costs — BTC-specific frozen snapshot

Do **not** reuse the ZEC spread/impact snapshot.

Proposed BTC/USDT frozen taker snapshot at 50 USDT notional:

- source: OKX current book
- book_ts = `1790275677755`
- fee_rate = `0.001`
- entry_cost_rate = `0.0010005926681029335`
- exit_cost_rate = `0.0010005926681029335`
- half_spread = `5.926681029335338e-07`
- buy_impact = `0.0`
- sell_impact = `0.0`
- thin_L1 = false
- basis = `current_book_snapshot_not_historical_l2`

Long net return:

```
R = exit_price / entry_price
R * (1 - sell_cost) / (1 + buy_cost) - 1
```

Short research-mark return:

```
1 - R * (1 + buy_cost) / (1 - sell_cost)
```

Shorts are research marks on BTC/USDT spot prints. No borrow-free executability is claimed. No perpetual/funding/margin substitution.

## 7. Proposed clean evaluation fold

Candidate official fold:

**2025-09-02 00:00:00.000 UTC → 2025-11-30 23:59:59.999 UTC**

Exactly 90 days.

Pre-review certification:

- no stored optimizer/evaluation run in this project has an explicit start before 2025-12-01
- repository search found no prior BTC/USDT TEST-SPEC-003 evaluation on this interval
- Dec 2025–Sep 2026 is considered contaminated for this replication because BTC was already inspected in the ZEC momentum pre-screen
- no BTC TSMOM performance metric from the proposed Sep–Nov 2025 fold has been viewed

Before lock/run, data availability and contiguous 5m coverage must be certified without computing Strategy C metrics.

Pre-fold BTC bars may seed range and ATR only.

No trade may have entry_time before fold_start.

Signals whose exact required entry is after fold_end are not taken.

If a position is open at fold end:

- exit at final 5m close
- exit_reason = end_of_test
- same frozen costs
- include in n, net PnL, expectancy and PF
- no final-tick chandelier update

Invalid/missing final close => run invalid.

## 8. ETH overlap — diagnostic only

Unlike ZEC B v1, BTC C v1 has **no cross-market overlap PASS gate**.

Reason: the purpose is replication of the TSMOM mechanism on BTC, not proving that BTC is independent of another dominant crypto market.

Still report ETH overlap descriptively:

- ETH/USDT spot on OKX
- fully closed 1h UTC
- same N=24 close-breakout definition
- same-direction breakout on T-1h, T, or T+1h
- numerator = overlapping taken BTC trades
- denominator = taken BTC trades with complete ETH data at all 3 timestamps
- report missing ETH-overlap trades and completeness

ETH overlap cannot change PASS / FAIL.

## 9. Sample sufficiency

If fewer than 20 closed trades:

**INSUFFICIENT_SAMPLE**

No PASS / FAIL.

## 10. PASS / FAIL

If n >= 20, PASS only if all:

1. net expectancy per trade > 0
2. profit factor >= 1.10
3. if both long and short sides have at least one closed trade, both long net PnL and short net PnL must be > 0

Otherwise:

**FAIL**

Profit-factor bookkeeping:

- gross_loss = 0 and gross_profit > 0 => PF = +infinity and condition 2 passes
- gross_loss = 0 and gross_profit = 0 => PF undefined and condition 2 fails

No discretionary near-pass.

## 11. Required report

| Metric | BTC C v1 |
|---|---:|
| 1h signals | |
| Closed trades | |
| Net PnL | |
| Net expectancy / trade | |
| Profit factor | |
| Net PnL long | |
| Net PnL short | |
| Long trades | |
| Short trades | |
| Max DD | |
| Time in market | |
| Average hold | |
| ETH same-direction overlap ±1h | |
| ETH overlap completeness | |
| B&H return | |
| B&H max DD | |

Verdict exactly one of:

- PASS
- FAIL
- INSUFFICIENT_SAMPLE

## 12. Forbidden

Do not:

- tune N for BTC
- tune ATR period
- tune ATR multiple
- add volume or volatility filter
- add ETH/ZEC entry filter
- add long-only or short-only variant
- add timeout
- add opposite-breakout exit
- reuse Strategy A exits
- grid parameters
- rank multiple BTC variants and keep the best
- use Dec 2025–Sep 2026 for PASS/FAIL
- change the official fold after results are seen
- change the BTC cost snapshot after the test is locked
- use this run to alter the still-running ZEC B v1

If C v1 fails, a replacement hypothesis requires a new preregistration and a new unused fold.
