# FOR GROK ADVERSARIAL REVIEW — IMPLEMENTATION-SPEC-006

Do not ask follow-up questions.
Do not change alpha.
Do not reopen TEST-SPEC-002/003.
Do not propose parameter tuning.

PROJECT STATE

- Strategy A: CLOSED FAIL
- Filter B: CLOSED FAIL
- TEST-SPEC-002 ZEC TSMOM B v1: FROZEN BLIND FORWARD until 2026-12-23 11:59:59.999 UTC
- TEST-SPEC-003 BTC TSMOM: CLOSED FAIL
- SPEC-004 core implemented
- P1-P5 hardening implemented
- SPEC-005 Phase 2 operations implemented:
  - kill-switch
  - reconciliation
  - recovery
  - outbox worker
  - immutable OKX snapshots
  - blind-safe ops API/UI
- live_trading=false
- TEST-SPEC-002 runtime binding is still disabled

CURRENT PROBLEM

The current paper runtime can safely process ENTRY orders but does not yet have a locked strategy-neutral reduce-only exit path.

Enabling TEST-SPEC-002 now would be wrong:
- a paper position could open
- but the frozen B chandelier exit could not yet close it through a formally locked reduce-only mechanism

SPEC-006 proposes:
1. a separate frozen Strategy-B reference/shadow state
2. immutable strategy action events
3. reduce-only EXIT_TO_FLAT semantics
4. quantity-based exit execution
5. no change to official B runner or alpha logic

KEY DESIGN

REFERENCE STATE IS SEPARATE FROM PAPER POSITION STATE

Reason:
paper execution can reject, delay or partially fill.
Those execution failures must not change the frozen theoretical strategy path.

The shadow/reference state follows only frozen TEST-SPEC-002 semantics.

PAPER STATE records actual execution only.

REFERENCE B RULES

- ZEC/USDT
- 5m execution bars
- fully closed 1h UTC signal bars
- N=24
- Wilder ATR(24)
- ATR multiplier 2.0
- long + short
- max one theoretical position
- exact next-hour boundary reference entry
- no timeout
- no opposite-breakout exit
- signals while theoretical position open ignored

Reference entry:
signal_close_time + 1ms
price = exact required 5m open

Missing exact entry bar:
- adapter INVALID
- no delayed fill/action

Stop semantics unchanged from TEST-SPEC-002:
- initial stop active immediately
- current 5m cannot retroactively tighten its own stop
- gap-through exits at current 5m open
- otherwise touch exits at active stop
- only surviving candle tightens stop for next 5m

NEW INTERNAL TABLES

paper_strategy_shadow_state:
- strategy_version_id
- pair
- FLAT/LONG/SHORT/INVALID
- reference entry metadata
- ATR
- active stop
- peak/trough
- last processed 5m / 1h time
- invalid reason
- state version
- NO PnL

paper_strategy_actions immutable:
- action_type ENTRY | EXIT_TO_FLAT
- position_side
- reference decision time
- required execution time
- reference price
- reason/rule
- ATR/stop context
- shadow state before/after
- canonical idempotency key
- NO PnL

ENTRY

When reference state is FLAT and frozen B signal occurs:
- create one immutable ENTRY action
- shadow state becomes LONG/SHORT
- existing paper ENTRY signal/order path is invoked
- actual paper success/failure does NOT rewrite shadow state

If paper entry is missed:
- shadow remains theoretically open
- later B signals are ignored until frozen reference exit
- execution divergence is recorded internally

EXIT_TO_FLAT

When frozen reference stop triggers:
- create immutable EXIT_TO_FLAT action
- shadow state becomes FLAT
- create at most one reduce-only paper exit
- if no paper position exists => audited no-op
- paper side mismatch => CRITICAL, no flip
- future reference entries can occur even if residual paper qty remains; normal risk controls may reject them

REDUCE-ONLY EXIT

New RPC paper_create_reduce_only_exit_order(action_id,...):

- lock kill switch
- allowed RUNNING or HALT_NEW_ENTRIES
- reject HALTED / RECOVERY_PENDING
- lock current paper position
- side must match reference action
- no position => no-op
- LONG closes with SELL
- SHORT closes with BUY
- intended_quantity = current paper qty under lock
- intent_type=EXIT
- no new risk reservation
- create BIND_FILL_ATTEMPT
- cannot increase/flip exposure

EXIT FILL

ENTRY completion stays unchanged.

EXIT completion is quantity-based:
- FILLED when cumulative filled_quantity reaches intended_quantity
- fill > current paper qty rejected
- side must reduce current position
- partial exit allowed only under policy
- residual paper qty may remain if execution fails
- no flip

OUTBOX

ENTRY:
existing quote-notional book walk unchanged

EXIT:
- load active runtime policy
- persist immutable book
- bind attempt with lease_generation
- compute remaining base qty
- deterministic base-quantity book walk
- SELL consumes bids / BUY consumes asks
- apply reduce-only fill
- ACK current generation only

REFERENCE ADAPTER DATA

Processes ZEC 5m bars sequentially/idempotently.

Restart:
fetch from last_processed_5m + 5m through current available data.

Gap:
- no interpolation
- shadow state INVALID
- CRITICAL operational issue
- no more actions until explicit verified recovery

DISCOVERY TIMING

This is not live-readiness proof.

Reference action time/price comes from locked 5m semantics.
Paper execution may happen later because serverless/polling discovers the action later.

Record:
- reference execution time
- action_discovered_at
- actual paper fill time

But keep them hidden before blind end.

BLIND

Before blind end do not expose:
- action rows/counts
- action timestamps
- side
- reference prices
- active stop
- shadow LONG/SHORT
- fills/positions
- divergence counts tied to B
- any PnL/performance

Allowed:
- adapter service health
- CONTIGUOUS / INVALID data category
- last-run age category
- generic integrity counts

OFFICIAL B ISOLATION

SPEC-006 must not:
- touch official B evaluate path
- compute PASS/FAIL
- compute/display partial official performance
- change B fold/params/costs
- modify tsmom_b_v1.py

REQUIRED TESTS

- B module unchanged
- one ENTRY action only
- duplicate adapter run idempotent
- signals ignored while shadow open
- missing exact entry => INVALID
- initial same-bar stop exit
- chandelier no retroactive tighten
- gap exit uses 5m open
- exit shadow FLAT independent of paper fill
- exit no-position => no-op
- side mismatch => CRITICAL no flip
- reduce-only qty locked to current position
- EXIT quantity completion; ENTRY quote-notional regression
- partial exit cannot over-close
- retry idempotent
- HALT_NEW_ENTRIES allows reduce-only exit
- HALTED blocks it
- stale snapshot reject
- gap invalidation
- restart replay deterministic
- blind leak tests
- TEST-SPEC-002 11/11 smoke unchanged
- SPEC-004/005 smokes unchanged
- no official B performance data touched

YOUR TASK

Try to break SPEC-006 before implementation.

Focus on:
1. whether shadow state can drift from frozen B semantics
2. whether paper execution can accidentally influence alpha/reference state
3. reduce-only race conditions
4. partial exit / residual position accounting
5. quantity-vs-notional completion bugs
6. repeated exit intents / duplicate orders
7. restart/gap replay
8. HALT_NEW_ENTRIES vs HALTED races
9. outbox lease_generation
10. whether any blind field leaks strategy activity/performance
11. whether using a polled 5m adapter invalidates the intended execution-audit interpretation
12. whether official B remains truly untouched

RETURN EXACTLY

1. BLOCKING FLAWS
2. NON-BLOCKING CAVEATS
3. SHADOW-STATE RISKS
4. REDUCE-ONLY / EXIT RACE RISKS
5. ACCOUNTING RISKS
6. RESTART / DATA-GAP RISKS
7. BLIND-TEST LEAKAGE RISKS
8. REQUIRED CHANGES BEFORE LOCK
9. THINGS ALREADY CORRECT — DO NOT CHANGE
10. LOCK VERDICT
   - CLEAN ENOUGH TO LOCK
   - NOT CLEAN ENOUGH TO LOCK
11. HANDOFF FOR CHATGPT

Do not ask questions.
Do not propose alpha changes.
Finish the review.
