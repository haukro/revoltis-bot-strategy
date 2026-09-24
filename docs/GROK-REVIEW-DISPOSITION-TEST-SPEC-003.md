# Grok Review Disposition — TEST-SPEC-003 BTC TSMOM C v1

Status: **PRE-RUN REVIEW APPLIED**

No TEST-SPEC-003 performance run or PASS/FAIL result existed when this review was processed.

## ACCEPTED

### C1 — end_of_test accounting
Accepted.

If a position remains open at the official fold end:

- exit at the final valid 5m close
- exit_reason = end_of_test
- apply the same frozen BTC cost snapshot
- include the trade in n, net PnL, expectancy and PF
- do not perform an additional final-tick chandelier update

Invalid or missing final 5m close => official run invalid.

### C2 — fold boundaries and warmup
Accepted.

- pre-fold data seed range and ATR only
- no trade with entry_time < fold_start
- eligible signal must close inside the official fold
- exact required entry must also lie inside the official fold
- BTC warmup = exactly 24 fully closed 1h bars before the first eligible signal hour

### C3 — continuity certificate before official run
Accepted.

The specification may be locked first.

Before the official run, a data-only / execution-availability certificate must record:

- expected vs actual 5m bars
- expected vs actual aggregated 1h bars
- timestamp gaps
- exact required entry-bar availability for every eligible signal
- final 5m close validity
- no interpolation or delayed fill

The certificate must not compute Strategy C PnL, expectancy, PF, side PnL, drawdown, or PASS/FAIL.

### C4 — zero-loss PF
Accepted.

- gross_loss = 0 and gross_profit > 0 => PF = +infinity; PF>=1.10 passes
- gross_loss = 0 and gross_profit = 0 => PF undefined; PF gate fails

### C5 — max-one-position signal handling
Accepted.

- while a position is open, all additional long/short signals are ignored for entry
- no reversal and no pyramiding
- if both long and short conditions were ever true on the same 1h candle, discard that signal

### C6 — Strategy C must not mutate Strategy B
Accepted.

TEST-SPEC-003 will use a separate Strategy C engine / runner path.

No implementation change for C may alter:

- TEST-SPEC-002 engine defaults
- TEST-SPEC-002 forward-fold semantics
- TEST-SPEC-002 official runner
- TEST-SPEC-002 frozen parameters or accounting

Shared helpers are allowed only if their use cannot change B behavior.

## IMPLEMENTED IN THE SPEC

The reviewed TEST-SPEC-003 now explicitly contains:

- end_of_test bookkeeping
- exact 24h BTC warmup
- fold eligibility boundaries
- continuity-cert requirements
- missing required entry bar => invalid run
- zero-loss PF semantics
- max-one-position signal handling
- explicit C-vs-B implementation isolation

## DISAGREED / MODIFIED

No substantive disagreement with Grok's C1-C6 review.

One note: C1 and parts of C4/C5 were already present in the draft spec, but they were retained and made explicit rather than treated as new strategy changes.

## NOT CHANGED

The review did not change:

- market: BTC/USDT spot on OKX
- execution feed: 5m
- signal timeframe: 1h UTC
- N = 24
- Wilder ATR period = 24
- ATR multiple = 2.0
- long + short design
- exact next-hour-boundary entry
- no timeout
- no opposite-breakout exit
- no volume/volatility/ETH/ZEC entry filter
- 50 USDT gross entry notional
- BTC-specific frozen cost snapshot
- official fold 2025-09-02 through 2025-11-30
- minimum 20 closed trades
- expectancy > 0 gate
- PF >= 1.10 gate
- both-active-sides-profitable gate
- ETH overlap remains diagnostic only
- no grid / no retuning on this fold
