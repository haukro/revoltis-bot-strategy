# IMPLEMENTATION-SPEC-006 — Frozen Strategy-B Paper Adapter + Reduce-Only Exit

Status: **DRAFT FOR GROK ADVERSARIAL REVIEW — NOT LOCKED, NOT IMPLEMENTED**

Purpose: connect frozen TEST-SPEC-002 to the existing SPEC-004/005 paper execution stack without changing alpha logic, official verdict logic, or blind-test secrecy.

This is **not** the official TEST-SPEC-002 evaluation.
It is an operational paper-execution mirror only.

## 0. Non-negotiable boundaries

1. Do not modify TEST-SPEC-002 parameters, PASS/FAIL rules, fold, cost model, or official runner.
2. Do not modify `backend/app/tsmom_b_v1.py`.
3. No partial official B PnL/PF/expectancy/win rate/equity curve may be exposed.
4. Strategy reference state and paper execution state are separate.
5. Paper execution failures must not change the frozen reference strategy path.
6. No delayed substitute entry for a missing exact reference entry bar.
7. Exit orders are **reduce-only to flat**; they may never open, reverse, or increase exposure.
8. No live real-money routing.
9. No synthetic liquidation.
10. Any runtime binding for TEST-SPEC-002 remains disabled until SPEC-006 is reviewed, locked, implemented, and validated.

## 1. Why a separate reference strategy state is required

TEST-SPEC-002 ignores new signals while its theoretical position is open.

Paper execution can diverge from the theoretical reference path because:

- paper fill may be delayed
- paper fill may be partial
- paper order may be rejected
- infrastructure may be halted
- market data may be stale/missing

Therefore paper position state cannot decide whether the frozen strategy is logically flat or open.

SPEC-006 maintains an internal **reference strategy state** that follows frozen TEST-SPEC-002 semantics independent of actual paper fills.

This reference state is used only to decide intended ENTRY / EXIT_TO_FLAT actions.

It does not compute or expose cumulative strategy performance.

## 2. Frozen TEST-SPEC-002 semantics reused exactly

Reference market:

- ZEC/USDT spot prints
- 5m execution bars
- fully closed 1h UTC signal bars
- N=24
- Wilder ATR(24)
- ATR multiple=2.0
- long + short
- max one theoretical position
- exact next-hour-boundary reference entry
- no timeout
- no opposite-breakout exit

Signal:

`close_t > prior 24h range high` => LONG
`close_t < prior 24h range low` => SHORT

Signal candle excluded from range.

ATR includes the fully closed signal candle.

Entry reference:

`reference_entry_time = signal_close_time + 1ms`

Reference entry price:

- exact open of the required 5m candle

Missing exact required 5m bar:

- adapter enters INVALID/PAUSED operational state
- no delayed substitute entry
- no strategy action is fabricated

Initial stop:

LONG:
`reference_entry_price - 2*ATR`

SHORT:
`reference_entry_price + 2*ATR`

Existing-position stop before each 5m candle uses:

- latest fully closed 1h ATR
- extrema only through the previous completed 5m bar

Gap-through stop:

- exit reference price = current 5m open

Otherwise intrabar touch:

- exit reference price = active stop

Only a surviving 5m candle may tighten the stop for the next candle.

## 3. Reference vs paper semantics

### 3.1 Reference strategy state

Reference state answers:

- is the frozen strategy theoretically FLAT/LONG/SHORT?
- what was the exact intended entry?
- what stop was active before the current 5m bar?
- when did the frozen strategy intend to exit?
- what reference price did the frozen backtest semantics assign?

Reference state is independent from actual paper order success.

### 3.2 Paper execution state

Paper state answers:

- did the paper order actually fill?
- at what book-derived VWAP?
- how much quantity filled?
- what fees/impact were paid?
- was the action rejected/missed/partial?

Paper divergence never rewrites reference state.

## 4. New reference-state tables

### 4.1 paper_strategy_shadow_state

Internal, RLS protected, no Realtime.

Fields:

- strategy_version_id text
- pair text
- state FLAT | LONG | SHORT | INVALID
- reference_signal_close_time nullable
- reference_entry_time nullable
- reference_entry_price nullable numeric
- reference_entry_atr nullable numeric
- active_stop nullable numeric
- peak_high nullable numeric
- trough_low nullable numeric
- last_processed_5m_open_time nullable
- last_processed_1h_close_time nullable
- invalid_reason nullable
- state_version bigint
- created_at
- updated_at

Primary key:

`(strategy_version_id,pair)`

No PnL fields.

### 4.2 paper_strategy_actions

Immutable append-only.

Fields:

- id uuid PK
- strategy_version_id
- pair
- action_type ENTRY | EXIT_TO_FLAT
- position_side LONG | SHORT
- reference_decision_time
- required_execution_time
- reference_price
- reason_code
- rule_id
- reference_atr nullable
- active_stop nullable
- shadow_state_before jsonb
- shadow_state_after jsonb
- idempotency_key unique
- software_commit
- created_at

No realized/unrealized PnL.

Canonical action key:

`SHA256("STRATEGY_ACTION|" + strategy_version_id + "|" + pair + "|" + action_type + "|" + required_execution_time + "|" + rule_id)`

## 5. Strategy action lifecycle

### ENTRY

When frozen reference state is FLAT and a valid breakout signal becomes actionable:

1. required exact 5m bar exists
2. derive exact 5m open as reference entry price
3. initialize reference stop/state
4. persist immutable ENTRY action
5. persist shadow state transition to LONG/SHORT
6. create the existing paper entry signal/order path idempotently
7. paper execution may succeed, reject, partial fill, or miss
8. reference state remains open regardless of paper result

If paper execution misses the entry:
- this is execution divergence
- do not pretend the reference strategy remained flat
- all future reference signals while shadow state is open are ignored exactly as frozen B requires

### EXIT_TO_FLAT

When the frozen reference position reaches its locked stop rule:

1. persist immutable EXIT_TO_FLAT action with exact reference time/price/reason
2. transition reference shadow state to FLAT
3. create at most one paper reduce-only exit intent
4. paper exit is allowed to reduce existing paper quantity only
5. if no paper position exists, record `EXIT_NO_PAPER_POSITION`; no order
6. if paper position side conflicts with reference position side, treat as CRITICAL operational inconsistency; do not flip
7. after reference exit, future frozen signals may again create ENTRY actions even if paper execution still has residual quantity; paper risk controls may reject such entry due active paper exposure

This divergence is operational evidence, not a reason to alter alpha.

## 6. Reduce-only exit order semantics

Existing `paper_orders.intent_type='EXIT'` is used.

A new service-role-only RPC:

`paper_create_reduce_only_exit_order(action_id,...)`

must:

1. lock kill-switch state
2. allow creation only in RUNNING or HALT_NEW_ENTRIES
3. reject in HALTED / RECOVERY_PENDING
4. lock active `paper_positions` row for strategy_version_id + pair
5. verify side matches the reference action
6. if no active paper position: no-op with audited `EXIT_NO_PAPER_POSITION`
7. set order side opposite to current paper position:
   - LONG -> SELL
   - SHORT -> BUY
8. set `intent_type='EXIT'`
9. set `intended_quantity = current paper position quantity`
10. guarantee reduce-only semantics
11. create BIND_FILL_ATTEMPT outbox event
12. never create a risk reservation for the exit

The order must not be able to exceed the quantity observed under the same locked position row.

## 7. Exit fill completion semantics

ENTRY order completion remains unchanged.

For EXIT orders:

- completion is quantity-based, not quote-notional-based
- order is FILLED when cumulative filled_quantity >= intended_quantity within locked numeric tolerance
- a fill quantity greater than current open paper position is rejected
- fill side must reduce the existing position
- no flip is possible
- partial exit leaves remaining paper position open
- the next execution attempt uses a new immutable market snapshot
- terminal incomplete exit leaves residual paper position and records execution divergence

No ENTRY fill semantics are changed by this extension.

## 8. Outbox worker behavior for EXIT

For ENTRY BIND_FILL_ATTEMPT:
- existing quote-notional book walk remains unchanged

For EXIT BIND_FILL_ATTEMPT:

1. load order
2. load active runtime risk/execution policy from strategy binding
3. fetch and persist immutable OKX order-book snapshot
4. bind attempt with active lease_generation
5. compute remaining **base quantity**
6. deterministic book walk by base quantity:
   - SELL consumes bids
   - BUY consumes asks
7. apply fill through reduce-only fill path
8. ACK only with current lease_generation

No last-price fallback.

## 9. Reference adapter market-data processing

The adapter processes ZEC 5m bars sequentially and idempotently.

It must be able to recover after restart by fetching from:

`last_processed_5m_open_time + 5m`

through the newest available 5m data.

Warmup before first reference decision:

- enough fully closed hourly history to compute prior-24 breakout and Wilder ATR(24)
- no pre-runtime action is emitted

Every processed 5m bar is consumed exactly once according to shadow-state `last_processed_5m_open_time`.

If a gap exists:

- do not interpolate
- shadow state -> INVALID
- emit CRITICAL operational issue
- no further reference actions until explicit recovery/reinitialization from a verified contiguous replay

## 10. Action discovery timing

Paper execution is an execution audit, not the official B replay.

Because the adapter is serverless/polled:

- reference ENTRY/EXIT time and price are derived from locked 5m bar semantics
- `action_discovered_at` may be later than the reference execution time
- paper fill uses the current immutable book at execution time
- latency from reference time to actual paper action is an execution-quality metric

This may be materially worse than a future streaming implementation.

Therefore SPEC-006 paper results are **not** a live-readiness proof by themselves.

A later live architecture may require streaming market data / always-on execution.

## 11. Blind protection

Before TEST-SPEC-002 blind end, normal API/UI must not expose:

- ENTRY/EXIT action rows
- action counts
- action timestamps
- side
- reference price
- active stop
- shadow state LONG/SHORT
- paper position rows
- paper fill rows
- execution divergence counts tied to B
- realized/unrealized PnL
- performance metrics

Allowed:

- adapter service health
- CONTIGUOUS / INVALID data-state category
- last-run age category
- generic critical/warning integrity counts
- runtime binding enabled/disabled flag only if it does not reveal strategy activity

No logs may dump B action payloads.

## 12. TEST-SPEC-002 official result isolation

The official B runner remains unchanged.

SPEC-006 must never:

- write official B evaluation tables
- call official B evaluate endpoint
- calculate PASS/FAIL
- calculate/display partial official PnL/PF/expectancy
- change official fold
- change TEST-SPEC-002 strategy module

At fold end, official verdict remains one batch replay from the official snapshots.

## 13. Runtime binding activation

Do not enable TEST-SPEC-002 runtime binding until all are true:

- SPEC-006 locked after adversarial review
- reduce-only exit path implemented
- shadow adapter implemented
- ENTRY and EXIT synthetic tests pass
- restart replay test passes
- gap invalidation test passes
- reduce-only no-flip tests pass
- blind-leak tests pass
- existing SPEC-004/005 regressions pass

The paper runtime may start later than the official fold start.
That does not alter official evaluation.
Its execution dataset must record its actual activation time.

## 14. Required tests

1. frozen B module unchanged
2. exact breakout signal produces one ENTRY action
3. duplicate adapter run produces no duplicate action
4. signal while shadow state open produces no second ENTRY action
5. exact missing required entry bar => INVALID, no delayed entry
6. initial stop same-bar touch produces EXIT_TO_FLAT reference action
7. chandelier uses prior completed 5m extrema only
8. gap through stop uses current 5m open reference price
9. surviving candle tightens stop only for next bar
10. exit action sets shadow state FLAT independent of paper fill success
11. no paper position at exit => audited no-op, no order
12. side mismatch => CRITICAL, no flip
13. reduce-only exit order quantity equals locked current paper qty
14. EXIT fill completion uses quantity, ENTRY still uses quote-notional
15. partial EXIT cannot exceed remaining paper quantity
16. repeated EXIT worker retry is idempotent
17. HALT_NEW_ENTRIES allows reduce-only exit
18. HALTED / RECOVERY_PENDING block new exit fill
19. stale snapshot rejected at apply
20. market-data gap invalidates adapter, no interpolation
21. restart replay from last_processed produces same shadow state/actions
22. blind API/UI reveals no action count/time/side/reference price/stop/state
23. logs redact B action payload
24. TEST-SPEC-002 11/11 smoke unchanged
25. SPEC-004/005 smokes unchanged
26. no official B performance endpoint/data touched

## 15. Implementation order after lock

1. shadow/action schema + RLS/no Realtime
2. pure frozen-B incremental reference adapter
3. action persistence/idempotency RPC
4. reduce-only exit order creation RPC
5. base-quantity deterministic book walk
6. EXIT fill application extension with ENTRY regression lock
7. outbox EXIT handling
8. adapter worker route
9. recovery/restart/gap handling
10. blind-safe health only
11. failure-injection tests
12. runtime binding remains disabled until final validation
