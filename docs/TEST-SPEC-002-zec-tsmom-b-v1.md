# TEST-SPEC-002 — ZEC TSMOM B v1

Status: **LOCKED BEFORE FIRST RUN**

This specification defines Strategy B v1 before any Strategy B backtest is executed.

Strategy A remains research-only. No Strategy A entry filter, exit, trail, timeout, or 24h-low logic is reused.

## 0. Hypothesis

If a fully closed 1h ZEC candle closes above the maximum high, or below the minimum low, of the previous 24 fully closed 1h candles, short-horizon continuation has positive expectancy after transaction costs.

The intended edge is ZEC time-series momentum, not a BTC-proxy signal.

## 1. Frozen market and execution configuration

| Item | B v1 |
|---|---|
| Market | ZEC/USDT |
| Execution feed | 5m |
| Signal timeframe | fully closed 1h UTC candles |
| Cost model | same stored fee + spread + impact model as Strategy A |
| Max open positions | 1 |
| Notional | same as Strategy A: 50 USDT |
| Strategy A entry | forbidden |
| Strategy A trail 1.6/0.3 | forbidden |
| Strategy A stop loss 6% | forbidden |
| Strategy A max-no-trail 4h | forbidden |
| RSI / Bollinger / rebound / 24h-low | forbidden |
| 20-bar event from momentum pre-screen | forbidden |

No pyramiding.

A signal that occurs while a position is already open does not open an additional position and does not reverse the existing position.

## 2. Signal — one configuration

Use only fully closed 1h UTC candles.

The live/incomplete 1h candle must never enter signal calculations.

Locked lookback:

`N = 24`

For signal candle `t`:

```
range_high = max(high) of the previous 24 fully closed 1h candles
range_low  = min(low)  of the previous 24 fully closed 1h candles
```

The signal candle itself is excluded from `range_high` and `range_low`.

Long signal:

```
close_t > range_high
```

Short signal:

```
close_t < range_low
```

If both conditions are true for the same signal candle, discard the signal.

No volume, ATR-expansion, RSI, Bollinger, rebound, BTC, ETH, or other entry filter is permitted in B v1.

## 3. Entry execution

After a valid 1h signal closes:

- enter at the **open of the first 5m candle after the signal 1h close**
- never enter inside the signal hour
- do not delay entry to a later 5m candle
- do not alter the entry price using a confirmation rule

The 1h signal and ATR used for entry must be fully known before the first eligible 5m execution candle opens.

## 4. ATR definition

ATR uses fully closed 1h UTC candles only.

Locked ATR period:

`ATR period = 24`

True range:

```
TR_t = max(
    high_t - low_t,
    abs(high_t - close_{t-1}),
    abs(low_t - close_{t-1})
)
```

Wilder ATR:

- initialize with the arithmetic mean of the first 24 available TR observations
- after initialization:

```
ATR_t = ((23 * ATR_{t-1}) + TR_t) / 24
```

At entry, use the most recent ATR value available at the close of the signal 1h candle.

After entry, ATR may update only when another 1h candle fully closes.

Between 1h closes, the most recently completed ATR value remains in force.

## 5. Exit — independent trend exit

Locked ATR multiplier:

`K = 2.0`

No time-based timeout.

No Strategy A trail.

No Strategy A stop.

No opposite-breakout exit.

### 5.1 Initial stop

Long:

```
stop = entry_price - 2 * ATR
```

Short:

```
stop = entry_price + 2 * ATR
```

### 5.2 Chandelier stop

Track position extrema from entry using the 5m execution feed.

Long:

```
peak_high = highest observed 5m high since entry
candidate_stop = peak_high - 2 * latest_closed_1h_ATR
stop = max(previous_stop, candidate_stop)
```

Short:

```
trough_low = lowest observed 5m low since entry
candidate_stop = trough_low + 2 * latest_closed_1h_ATR
stop = min(previous_stop, candidate_stop)
```

The stop may move only in the profitable direction. It must never loosen.

### 5.3 5m stop execution

Stop conditions are evaluated on 5m OHLC only.

For a long position:

- if the 5m candle opens below the active stop, exit at the 5m open
- otherwise, if the 5m low reaches or crosses the active stop, exit at the stop price

For a short position:

- if the 5m candle opens above the active stop, exit at the 5m open
- otherwise, if the 5m high reaches or crosses the active stop, exit at the stop price

The stop active for a 5m candle must be based only on information available before or at that candle according to the rules above. No future 1h ATR or future extrema may be used.

The same stored fee + spread + impact cost model used for Strategy A is applied to both entry and exit.

## 6. Parameters that are not tuned

This test does not tune:

- N
- ATR period
- ATR multiplier
- volume filter
- ATR-expansion filter
- long-only or short-only variants
- BTC or ETH filters
- any Strategy A exit
- any time-based timeout

If B v1 fails, no replacement values are inferred from the failed run.

## 7. Contaminated descriptive blocks

The three blocks from December 2025 through September 2026 are contaminated for B because they informed Strategy A research, the momentum proxy pre-screen, or the ZEC regime discussion.

They may be used only for descriptive reporting.

They must not determine PASS / FAIL.

## 8. Official evaluation fold

The official evaluation fold is certified unused within the project before this specification is merged.

**2025-09-02 00:00:00.000 UTC → 2025-11-30 23:59:59.999 UTC**

This is exactly 90 days and ends before 2025-12-01.

Certification performed before lock:

- stored optimizer-run metadata contained no explicit `start_time`, `fixed_history_from_ms`, or `holdout_from_ms` before 2025-12-01
- repository search found no previously used pre-December-2025 evaluation interval
- no Strategy B result from this fold existed before this specification

The fold must not be replaced after results are viewed.

## 9. Primary metrics

On the official evaluation fold, after the locked transaction-cost model:

1. net expectancy per trade
2. profit factor
3. trade count
4. net PnL long
5. net PnL short
6. share of entries with a same-direction BTC 24×1h breakout within ±1 hour

The BTC overlap is diagnostic and is not an entry filter.

BTC breakout uses the same 24×1h breakout definition and direction as B:

- BTC long breakout for a ZEC long entry
- BTC short breakout for a ZEC short entry

The ±1 hour overlap window is locked before the first run.

## 10. Sample sufficiency

If the official evaluation fold produces fewer than 20 closed trades:

**INSUFFICIENT_SAMPLE**

No PASS / FAIL conclusion is allowed.

## 11. PASS / FAIL

If the sample is sufficient, B v1 passes only if all four conditions hold:

1. net expectancy per trade > 0
2. profit factor >= 1.10
3. if both long and short sides have at least one closed trade, then **both** long net PnL and short net PnL must be > 0; if one side has zero trades, this clause is not applied
4. same-direction BTC-overlap share < 60%

Otherwise:

**FAIL**

No partial pass and no discretionary override.

## 12. Secondary reporting

Report descriptively:

- total net PnL
- max drawdown
- time in market
- average holding time
- long trade count
- short trade count
- long expectancy
- short expectancy
- win rate overall / long / short
- B&H return and B&H max drawdown over the same evaluation fold
- same-direction BTC-overlap count and share

These fields do not add new PASS / FAIL rules.

## 13. Required report

| Metric | B v1 |
|---|---:|
| 1h signals | |
| Closed trades | |
| Net PnL | |
| Net expectancy / trade | |
| Profit factor | |
| Net PnL long | |
| Net PnL short | |
| Max DD | |
| Time in market | |
| BTC same-direction overlap ±1h | |
| Average hold | |

Final verdict must be exactly one of:

- **PASS**
- **FAIL**
- **INSUFFICIENT_SAMPLE**

Below the table, add only two interpretation sentences:

1. whether any positive result survived the locked transaction-cost model
2. whether entries were predominantly shared BTC crypto breakouts

## 14. Forbidden until verdict

Do not:

- run a grid over N in {12, 20, 24, 48}
- run a grid over ATR multiplier in {1.5, 2, 2.5, 3}
- import Strategy A exit logic
- add volume because the 5m pre-screen follow-through was weak
- add ATR expansion
- add BTC or ETH as an entry filter
- evaluate PASS / FAIL on Dec 2025–Sep 2026
- tune using the official fold
- change the official fold after results are viewed
- build an A+B router before B independently passes

If B v1 fails, a new Strategy B hypothesis or configuration requires a new preregistration and a new unused evaluation fold.
