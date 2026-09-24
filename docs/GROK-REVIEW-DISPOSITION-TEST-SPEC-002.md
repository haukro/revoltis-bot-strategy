# Grok Review Disposition — TEST-SPEC-002

Status: **PRE-RUN REVIEW APPLIED**

No official TEST-SPEC-002 evaluation result or official-fold Strategy B metric existed when this review was processed.

## ACCEPTED

### B2 — ATR window
Accepted.

- ATR(24) uses fully closed 1h bars.
- The signal bar's TR is included because the signal bar is fully closed at decision time.
- The following incomplete hour is excluded.
- Wilder initialization remains SMA of the first 24 TR observations, followed by standard Wilder recursion.

### B3 — BTC overlap definition
Accepted.

Locked implementation:

- BTC/USDT spot on OKX
- fully closed 1h UTC bars
- same N=24 close-breakout definition as ZEC
- BTC signal candle excluded from its own range
- same-direction BTC breakout at T-1h, T, or T+1h counts as overlap
- numerator = overlapping taken ZEC trades
- denominator = taken ZEC trades with complete BTC 1h data at all three required timestamps
- incomplete BTC overlap data drops the trade from both numerator and denominator
- dropped BTC-overlap trades >10% of taken trades => INSUFFICIENT_SAMPLE

### B4 — fold boundaries and warmup
Accepted.

- no entry before forward fold start
- only signals whose required entry lies inside the fold are eligible
- pre-fold history seeds indicators only
- ZEC warmup is exactly 24 closed 1h bars
- BTC warmup is exactly 25 closed 1h bars so T-1h overlap can be evaluated for the earliest possible ZEC trade
- end_of_test occurs only at fold end

### B5 — official verdict is one batch replay
Accepted.

- official PASS / FAIL is one replay after the fold ends
- raw snapshots are immutable once created
- hard endpoint guards prevent official snapshot/evaluation before fold end
- mid-fold UI or engine changes do not define the official result

### B6 — frozen cost snapshot
Accepted.

Frozen source:

- Strategy A source run: c4279991-66ae-44a2-9840-073c96bd8251
- book_ts: 1790183884652
- entry_cost_rate: 0.001086487119183424
- exit_cost_rate: 0.0010838075452430937

The official runner rejects the run if this snapshot does not match.

### B7 — spot shorts are research marks
Accepted.

- short PnL is simulated from OKX ZEC/USDT spot prints
- no borrow cost is claimed
- no perpetual, funding, margin, leverage, or alternate venue is silently substituted
- a PASS is a research verdict, not proof that borrow-free spot shorts are executable

## ACCEPTED WITH MODIFICATION / DISAGREEMENT

### B1 — entry 5m timestamp
Grok's intent was accepted: the fill must be the first 5m bar after the signal hour is fully closed.

However, the literal proposed timestamp was not accepted because our data model stores:

- a 1h bar as [H, H+1h)
- close_time = H+1h-1 ms

Therefore the exact entry bar is:

open_time = signal_1h.close_time + 1 ms

This is the next UTC hour boundary.

### B1 — missing entry bar fallback
Grok proposed using the next available 5m open if the exact entry bar is missing.

This was **rejected**.

Reason: moving the fill to a later bar makes the strategy outcome depend on a data-quality gap and changes the locked execution rule.

Locked behavior:

- exact required 5m entry bar missing => official dataset/run invalid
- no interpolation
- no delayed replacement fill

## IMPLEMENTED

Spec changes:

- exact entry timestamp semantics
- exact missing-entry invalidation rule
- ATR signal-bar inclusion
- complete BTC-overlap definition
- >10% missing BTC overlap => INSUFFICIENT_SAMPLE
- exact ZEC/BTC warmup lengths
- one batch replay rule
- exact frozen cost snapshot identity/rates
- explicit research-only spot-short interpretation
- 50 USDT defined as gross entry notional before costs
- signals while already in a position remain ignored
- non-blocking caveats recorded

Engine changes:

- exact required entry bar validation
- BTC overlap numerator/denominator/drop accounting
- BTC missing-data sample rule
- source cost snapshot validation
- reviewed spec commit reference
- immutable batch replay metadata
- 24h ZEC / 25h BTC pre-fold warmup
- extra synthetic smoke checks for:
  - ATR including signal bar
  - missing exact entry bar rejection
  - BTC missing-overlap accounting

## ADDITIONAL CONSISTENCY FIX FOUND DURING IMPLEMENTATION

Profit factor with zero gross loss was previously ambiguous.

Locked before official results:

- gross_loss = 0 and gross_profit > 0 => PF = +infinity and PF>=1.10 passes
- gross_loss = 0 and gross_profit = 0 => PF undefined and the PF gate fails

This is metric bookkeeping only, not a strategy parameter.

## NOT CHANGED

The Grok review did not change:

- N = 24
- ATR period = 24
- ATR multiple = 2.0
- ZEC/USDT market
- 5m execution / 1h signal architecture
- long + short design
- forward evaluation fold
- 20-trade sample threshold
- expectancy gate
- PF 1.10 threshold
- both-active-sides profitability rule
- BTC overlap 60% threshold
- absence of volume / ATR-expansion / BTC entry filters
- no timeout
- no Strategy A exit
- no parameter grid
