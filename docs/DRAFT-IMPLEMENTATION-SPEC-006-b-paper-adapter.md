# IMPLEMENTATION-SPEC-006 — Frozen Strategy-B Paper Adapter + Durable Reduce-Only Flatten

Status: **DRAFT V2 AFTER GROK REVIEW — RE-REVIEW REQUIRED — NOT LOCKED, NOT IMPLEMENTED**

Purpose: connect frozen TEST-SPEC-002 to the existing SPEC-004/005 paper execution stack without changing alpha logic, official verdict logic, or blind-test secrecy.

This is **not** the official TEST-SPEC-002 evaluation.
It is an operational paper-execution mirror only.

## 0. Non-negotiable boundaries

1. Do not modify TEST-SPEC-002 parameters, PASS/FAIL rules, fold, cost model, or official runner.
2. Do not modify `backend/app/tsmom_b_v1.py`.
3. No partial official B PnL/PF/expectancy/win rate/equity curve may be exposed.
4. The reference plane is authoritative only for frozen B intent; the paper plane is authoritative only for simulated execution.
5. Paper success/failure must never rewrite reference state.
6. No delayed substitute entry for a missing exact reference entry bar.
7. Paper flattening is reduce-only to zero; it may never open, reverse, or increase exposure.
8. No live real-money routing.
9. No synthetic liquidation.
10. TEST-SPEC-002 runtime binding remains disabled until SPEC-006 passes a second Grok review, is locked, implemented, and validated.
11. A reference EXIT_TO_FLAT and a paper flatten intent are different durable objects.
12. Any reference/paper divergence is operational evidence only and never changes official B.

## 1. Two-plane model

### 1.1 Reference plane

The reference plane processes ZEC 5m bars sequentially and applies only frozen TEST-SPEC-002 mechanics.

It answers:

- is the theoretical frozen strategy FLAT/LONG/SHORT?
- what exact reference entry occurred?
- what stop is active before the current 5m?
- what exact frozen reference exit occurred?

It does **not** answer whether paper execution succeeded.

### 1.2 Paper plane

The paper plane answers:

- whether the intended action was actually paper-filled
- at what immutable-book-derived VWAP
- how much base quantity was filled
- whether execution was rejected, partial, missed, paused, or stale

Paper data never writes reference fields.

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

Reference entry:

`reference_entry_time = signal_close_time + 1ms`

Reference entry price:

- exact open of the required 5m candle

Missing exact required entry 5m bar:

- no delayed substitute
- no reference ENTRY action
- data_state becomes INVALID

Initial stop:

LONG:
`reference_entry_price - 2*ATR`

SHORT:
`reference_entry_price + 2*ATR`

Existing-position ordering for every 5m bar:

1. determine latest fully closed 1h ATR available before the 5m open
2. calculate active stop using extrema through the previous completed 5m bar only
3. evaluate gap/touch against the current 5m
4. if exited, emit EXIT_TO_FLAT and do not update extrema for that closed reference position
5. only if the reference position survives may current high/low tighten the next-bar stop

Gap-through:

- LONG: if 5m open < active stop, reference exit price = 5m open
- SHORT: if 5m open > active stop, reference exit price = 5m open

Otherwise touch:

- LONG low <= active stop => reference exit price = active stop
- SHORT high >= active stop => reference exit price = active stop

Initial stop is active immediately on the required entry 5m, so ENTRY and EXIT_TO_FLAT may occur from the same 5m bar.

Signals while reference position is LONG/SHORT are ignored even when the paper account is flat.

## 3. Reference state schema

### 3.1 paper_strategy_shadow_state

Internal only. RLS protected. Not in Realtime.

Fields:

- strategy_version_id text
- pair text
- adapter_epoch_id uuid
- data_state CONTIGUOUS | INVALID
- reference_position_state FLAT | LONG | SHORT
- reference_entry_action_id uuid nullable
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
- invalid_at_5m_open_time nullable
- state_version bigint
- created_at
- updated_at

Primary key:

`(strategy_version_id,pair)`

Important:

- INVALID is a data state, not FLAT.
- On a true data gap, preserve the last valid reference_position_state and reference fields.
- No PnL fields exist.

### 3.2 paper_strategy_execution_fence

One row per strategy_version_id + pair.

Fields:

- strategy_version_id
- pair
- fence_version
- updated_at

Primary key:

`(strategy_version_id,pair)`

Purpose:

- serialize reference EXIT-intent creation against any paper ENTRY fill that could increase exposure
- the reference plane may lock this row but never reads paper profitability
- paper ENTRY fill may lock this row but never writes reference state

This is a cross-plane execution fence only.

## 4. Immutable reference actions

Table: `paper_strategy_actions`

Append-only.

Fields:

- id uuid PK
- adapter_epoch_id uuid
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
- linked_entry_action_id nullable
- shadow_state_before jsonb
- shadow_state_after jsonb
- idempotency_key text unique
- software_commit
- created_at

No realized/unrealized PnL.

### 4.1 Exact canonical action key

Server-generated only:

`SHA256("STRATEGY_ACTION|" + strategy_version_id + "|" + pair + "|" + action_type + "|" + reference_decision_time_utc_ms + "|" + required_execution_time_utc_ms + "|" + position_side + "|" + reason_code)`

Unique constraint:

`UNIQUE(idempotency_key)`

For EXIT_TO_FLAT:

- `linked_entry_action_id` must reference the ENTRY action that opened the current reference cycle.

## 5. Durable paper dispatch

A crash after reference action persistence must not lose the paper intent.

Use existing durable `execution_outbox`.

Every reference action transaction inserts one internal dispatch event:

- event_type = `STRATEGY_ACTION_DISPATCH`
- entity_type = `strategy_action`
- entity_id = action id
- canonical outbox idempotency derived from action id
- normal lease_generation/reclaim semantics apply

Blind-safe queue APIs must not expose numeric backlog/event counts once TEST-SPEC-002 runtime is enabled; see section 15.

ENTRY and EXIT action dispatch are retryable from the immutable action row.

## 6. Durable paper flatten intent

Table: `paper_exit_intents`

Mutable operational state; no PnL.

Fields:

- id uuid PK
- adapter_epoch_id
- exit_action_id uuid unique references paper_strategy_actions(id)
- linked_entry_action_id uuid references paper_strategy_actions(id)
- strategy_version_id
- pair
- reference_position_side LONG | SHORT
- status OPEN | PAUSED | SATISFIED | CRITICAL
- paper_exit_order_id uuid nullable
- last_error_code nullable
- state_version bigint
- created_at
- updated_at
- satisfied_at nullable

One EXIT_TO_FLAT action creates exactly one paper_exit_intent.

The intent target is:

**paper exposure for strategy_version_id + pair must become zero and must no longer be capable of being increased by the linked ENTRY lifecycle.**

The intent is not satisfied merely because current paper quantity is temporarily zero.

SATISFIED requires both:

1. current paper position quantity is zero / no active paper position exists, and
2. the linked ENTRY paper order/dispatch is terminal or fenced so no later ENTRY fill can create quantity.

Kill-switch, stale snapshot, lease loss, retry, process crash, or serverless timeout do not delete the intent and do not create a second EXIT_TO_FLAT.

## 7. Atomic reference transition transaction

For each expected next 5m bar, one DB transaction/RPC must:

1. lock `paper_strategy_execution_fence(strategy_version_id,pair)`
2. lock `paper_strategy_shadow_state(strategy_version_id,pair)`
3. verify `data_state=CONTIGUOUS`
4. verify current bar open_time is exactly the expected next 5m key
5. compute frozen B transition for that bar
6. insert zero, one, or two immutable actions
7. if EXIT_TO_FLAT is emitted, insert the unique `paper_exit_intent`
8. insert durable `STRATEGY_ACTION_DISPATCH` outbox event(s)
9. update shadow state and `last_processed_5m_open_time`
10. commit all of the above atomically

If ENTRY and same-bar initial-stop EXIT both occur:

- insert ENTRY action
- insert EXIT_TO_FLAT action linked to that ENTRY
- insert one exit intent
- final reference_position_state after the transaction is FLAT
- both actions and cursor commit together

If any write fails, no action/shadow/cursor change commits.

## 8. Frozen-reference parity requirement

The incremental adapter must not invent a second interpretation of frozen B.

It may reuse frozen constants/helpers from `tsmom_b_v1.py`, but the file itself is not modified.

Mandatory differential tests:

- feed the same synthetic contiguous 5m sequence to `simulate_b_v1` and the incremental adapter
- compare reference ENTRY/EXIT timestamps
- compare reference ENTRY/EXIT prices
- compare exit reasons
- compare ignored-signal behavior
- include same-bar initial stop
- include gap-through
- include chandelier touch
- include new-high/no-retroactive-stop case

For every fixture, incremental actions must match frozen B exactly.

No official forward-fold performance data is used for this parity test.

## 9. ENTRY dispatch and fencing

### 9.1 ENTRY dispatch

When processing an immutable ENTRY action:

1. lock the strategy execution fence
2. check for an OPEN/PAUSED exit intent linked to the same reference cycle
3. check current active paper position for strategy_version_id + pair

If linked exit intent already exists:

- do not create a new paper ENTRY order
- mark the dispatch internally as `ENTRY_FENCED_BY_EXIT_INTENT`
- no exposure is created

This handles same-bar ENTRY+EXIT discovered in one adapter invocation.

If a paper position is already active before a new reference ENTRY:

- do not create a second paper ENTRY
- emit CRITICAL operational inconsistency
- do not flip or stack exposure
- reference plane remains unchanged

Otherwise:

- create existing paper ENTRY signal/order path idempotently
- ENTRY paper execution remains quote-notional based

### 9.2 Fence inside ENTRY fill apply

Before an ENTRY fill can create/increase paper exposure:

1. the fill transaction must lock the same `paper_strategy_execution_fence`
2. check whether an OPEN/PAUSED exit intent exists for the linked reference cycle
3. if the exit intent was committed first, reject the new ENTRY economic effect with `ENTRY_FENCED_BY_EXIT_INTENT`
4. if the ENTRY fill transaction acquired/committed the fence first, that fill may complete; the later exit intent will see the resulting position and flatten it

Thus ordering is deterministic:

- EXIT intent wins first => no later ENTRY fill
- ENTRY fill wins first => durable EXIT intent subsequently flattens that quantity

No paper result changes the reference plane.

## 10. Flatten-intent processing

One EXIT intent may use zero or one paper EXIT order and many execution attempts.

Processing sequence:

1. load OPEN/PAUSED exit intent
2. lock kill-switch state
3. if HALTED or RECOVERY_PENDING:
   - status remains/changes to PAUSED
   - do not delete/complete intent
   - no fill
   - retry later; no second reference exit/action
4. if RUNNING or HALT_NEW_ENTRIES:
   - lock current paper position row for strategy_version_id + pair
   - locate linked ENTRY dispatch/order
   - fence/terminalize any remaining nonterminal ENTRY order so no more ENTRY quantity can apply
   - release only unconsumed ENTRY reservation through existing terminal path
5. re-read paper position under lock

Then:

### No paper position / qty zero

If linked ENTRY lifecycle is terminal/fenced:

- mark intent SATISFIED
- audit `EXIT_NO_PAPER_POSITION` if no paper quantity ever existed
- no EXIT order

If linked ENTRY lifecycle could still create exposure:

- keep intent OPEN
- do not mark satisfied

### Paper position exists and side matches reference_position_side

- create at most one reduce-only EXIT order
- intended_quantity = current locked base quantity
- no risk reservation
- LONG -> SELL
- SHORT -> BUY
- persist paper_exit_order_id on intent
- create BIND_FILL_ATTEMPT

### Paper position side conflicts with reference side

- mark intent CRITICAL
- emit CRITICAL reconciliation/integrity issue
- no order
- no flip
- operational kill-switch may HALT
- reference plane unchanged

## 11. Reduce-only EXIT order semantics

Existing `paper_orders.intent_type='EXIT'` is used.

New service-role-only RPC:

`paper_create_reduce_only_exit_order(exit_intent_id,...)`

Rules:

- one paper EXIT order maximum per exit intent
- canonical order idempotency includes exit_intent_id
- no new risk reservation
- quantity is base quantity, not quote notional
- quantity is read from the locked active paper position
- order side must reduce the locked position
- order cannot open or reverse
- order cannot exceed current locked paper qty
- existing paper ENTRY quote-notional semantics remain unchanged

## 12. EXIT fill and partial continuation

ENTRY completion remains quote-notional based.

EXIT completion is base-quantity based.

For every EXIT apply:

1. normal outbox lease_generation fencing applies
2. bind a new immutable book snapshot per attempt
3. lock current paper position
4. compute current remaining base quantity
5. reject fill_quantity > current open base quantity
6. reject fill side that does not reduce the position
7. apply only reducing quantity
8. no flip is possible

Order completion:

- EXIT is FILLED when cumulative filled_quantity reaches intended_quantity and current linked paper position reaches zero
- partial EXIT leaves residual paper position
- partial continuation uses the same EXIT order / same exit intent
- each continuation gets a new BIND_FILL_ATTEMPT, new attempt_seq, and new immutable book snapshot
- do not create a second EXIT_TO_FLAT action
- do not create a second EXIT order

If EXIT terminally fails while paper qty remains:

- exit intent remains OPEN/PAUSED, not SATISFIED
- record execution divergence internally
- later retry may create new execution attempts on the same reduce-only order only according to the locked recovery/order-state rules
- no new reference action is emitted

Dust/residual quantity is execution divergence, not a new alpha exit.

## 13. Gap definition and sticky INVALID

A true gap exists when the next required 5m key cannot be obtained from the source series.

Downtime is not a true gap if every missing interval can later be fetched contiguously before adapter processing advances past it.

On true gap:

1. lock shadow state
2. set `data_state=INVALID`
3. preserve last valid reference_position_state and all reference fields
4. store missing expected 5m key as `invalid_at_5m_open_time`
5. emit CRITICAL operational issue
6. stop emitting ENTRY/EXIT_TO_FLAT actions
7. do not interpolate
8. do not substitute the next bar
9. do not invent a reference exit

If paper exposure exists, it remains as-is; operational state may HALT. No B-linked discretionary close is fabricated.

## 14. INVALID recovery

INVALID is sticky within the current adapter epoch.

Recovery may not simply reset state to FLAT or skip bars.

A recovery attempt may:

1. start from the last known-good shadow checkpoint before the gap
2. fetch a fully verified contiguous series covering the missing key and all bars through a recovery cutoff
3. replay the frozen incremental B transition logic internally
4. suppress **all paper action dispatch** for bars whose required execution time is already in the past
5. never issue delayed paper ENTRY or EXIT orders from the replayed interval
6. never copy state/actions from the official B evaluation result
7. verify parity/replay determinism
8. find a verified reference FLAT boundary at or after the recovery cutoff

A new adapter epoch may begin only if:

- verified replay reaches a reference FLAT boundary
- current paper position is flat
- no nonterminal paper ENTRY/EXIT order exists
- no OPEN/PAUSED exit intent remains
- no critical adapter integrity issue remains

Then:

- create a new adapter_epoch_id
- initialize CONTIGUOUS + FLAT at that future boundary
- resume actions only for subsequent 5m bars

If these conditions are not met, remain INVALID/HALTED.

Thus recovery never emits delayed B actions against current books.

## 15. Blind protection strengthened for B runtime

Before TEST-SPEC-002 blind end, normal API/UI/logs must not expose:

- action rows
- action counts
- action timestamps
- ENTRY vs EXIT type
- side
- pair when tied to B execution
- reference prices
- stop
- shadow LONG/SHORT
- adapter cursor exact timestamp
- fill/order/position rows
- paper exit-intent status/count
- divergence counts
- B-specific rejection/error reason codes
- B-specific queue counts/backlog
- worker processed/claimed counts
- realized/unrealized PnL
- PF/expectancy/win rate/equity curve

While B runtime is enabled, existing generic queue/worker/reconciliation UI must be further reduced to category-only projections where needed:

Allowed:

- adapter health: HEALTHY / INVALID
- worker age category: FRESH / LATE / STALE
- queue health category: HEALTHY / DEGRADED
- reconciliation/audit category: PASS / WARNING / CRITICAL
- kill-switch state
- blind status + blind_until

No numeric count that moves only when B trades may be shown.

Client-facing errors must use generic operational codes and must not reveal B action timing/type.

Application/Vercel logs must redact B action payloads, pair, side, reference price, active stop, fill/order payload, and linked IDs that permit reconstruction.

## 16. Official TEST-SPEC-002 isolation

The official B runner remains unchanged.

SPEC-006 must never:

- write official B evaluation data
- call official B evaluate endpoint
- calculate PASS/FAIL
- calculate/display partial official PnL/PF/expectancy/win rate
- alter fold, costs, parameters, or verdict gates
- modify `tsmom_b_v1.py`
- use official forward results to repair paper shadow state

Official verdict remains one batch replay at fold end.

## 17. Runtime activation boundary

Do not enable TEST-SPEC-002 runtime binding until all are true:

- SPEC-006 passes second Grok adversarial review
- SPEC-006 locked
- reference transition transaction implemented
- durable exit intents implemented
- ENTRY fill fencing implemented
- reduce-only quantity path implemented
- INVALID/new-epoch recovery implemented
- blind category-only surfaces implemented
- all required tests pass
- existing SPEC-004/005 validations remain clean

Paper activation time must be stored separately from the official fold start.

Starting paper runtime later does not change official B.

## 18. Required tests

Reference parity:
1. `tsmom_b_v1.py` unchanged
2. incremental synthetic ENTRY equals frozen B
3. initial same-bar stop produces ENTRY + EXIT_TO_FLAT in one reference transaction
4. chandelier no-retroactive-tighten parity
5. gap-through parity
6. touch parity
7. ignored signals while reference open parity

Reference atomicity/idempotency:
8. duplicate adapter processing same 5m creates no duplicate action
9. action+shadow+cursor rollback together on injected failure
10. exact canonical action-key uniqueness
11. two adapter workers process same stop => one EXIT action + one exit intent
12. restart from cursor produces same state/actions

Same-bar/late-entry:
13. same-bar ENTRY+EXIT creates durable exit intent
14. ENTRY dispatch sees linked exit intent => fenced/no entry order
15. ENTRY fill that commits before exit-intent transaction is later flattened
16. ENTRY fill after exit-intent commit is rejected by execution fence
17. linked ENTRY order remainder is terminalized/released before intent satisfaction

Paper already open:
18. paper active position on reference ENTRY => CRITICAL/no second entry
19. reference state still changes according to frozen B

Reduce-only:
20. no paper position + linked entry terminal/fenced => intent SATISFIED/no exit order
21. no position + linked entry still fill-capable => intent remains OPEN
22. side mismatch => CRITICAL/no flip
23. one exit intent => at most one EXIT order
24. exit qty equals locked current base qty
25. EXIT completion quantity-based; ENTRY remains quote-notional
26. partial EXIT uses same order + new attempt/snapshot
27. partial EXIT cannot over-close
28. residual qty keeps intent unsatisfied
29. retry/lease reclaim creates no duplicate economic effect

Kill switch:
30. HALT_NEW_ENTRIES permits flatten-intent processing
31. HALTED/RECOVERY_PENDING pauses intent and blocks new fill
32. re-enable resumes existing intent, not a second reference exit

Gap/recovery:
33. missing expected 5m => data_state INVALID, position state preserved
34. no interpolation/substitute
35. no actions while INVALID
36. recovery replay emits no delayed paper actions
37. no recovery by copying official B output
38. new epoch only at verified FLAT + paper flat + no open orders/intents
39. if conditions fail, remain INVALID/HALTED

Blind:
40. no action/intent/order/fill/position/count/timestamp leaks
41. queue/worker/recon surfaces category-only with B enabled
42. logs redact B action payload
43. generic client errors reveal no action timing/type

Regression:
44. TEST-SPEC-002 11/11 smoke unchanged
45. SPEC-004 smoke unchanged
46. SPEC-005 smoke unchanged
47. no official B performance data touched

## 19. Implementation order after second review lock

1. shadow state + execution fence + actions + exit-intent schema
2. atomic reference-transition RPC
3. incremental frozen-B adapter + differential parity tests
4. strategy-action durable dispatch handler
5. ENTRY fill fence extension
6. reduce-only EXIT order creation RPC
7. base-quantity EXIT book walk/apply
8. exit-intent retry/partial continuation
9. sticky INVALID/new-epoch recovery
10. blind category-only API/UI/log redaction
11. failure-injection/regression tests
12. runtime binding still disabled until final validation
