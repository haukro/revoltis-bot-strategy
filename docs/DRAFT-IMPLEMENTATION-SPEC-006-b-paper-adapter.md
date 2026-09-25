# IMPLEMENTATION-SPEC-006 — Frozen Strategy-B Paper Adapter + Durable Reduce-Only Flatten

Status: **DRAFT V2 PATCHED AFTER SECOND GROK REVIEW — LOCK-GATE RE-REVIEW REQUIRED — NOT LOCKED, NOT IMPLEMENTED**

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
10. TEST-SPEC-002 runtime binding remains disabled until SPEC-006 passes the current lock gate, is locked, implemented, and validated.
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

## 5. Durable paper dispatch and explicit ENTRY lifecycle

A crash after reference action persistence must not lose paper dispatch, and the absence of a paper ENTRY order must never be interpreted as a safe terminal state by itself.

Use existing durable `execution_outbox`.

Every reference action transaction inserts one internal dispatch event:

- event_type = `STRATEGY_ACTION_DISPATCH`
- entity_type = `strategy_action`
- entity_id = action id
- canonical outbox idempotency derived from action id
- normal `lease_generation` / reclaim semantics apply

Blind-safe queue APIs must not expose numeric backlog/event counts once TEST-SPEC-002 runtime is enabled; see section 15.

### 5.1 `paper_entry_lifecycles`

One durable lifecycle row exists for every reference ENTRY action, even when no paper ENTRY order is ever created.

Fields:

- entry_action_id uuid UNIQUE references `paper_strategy_actions(id)`
- adapter_epoch_id
- strategy_version_id
- pair
- status `PENDING_DISPATCH | ORDER_ACTIVE | TERMINAL_FILLED | TERMINAL_NO_FILL | NEVER_CREATED_FENCED | TERMINAL_REJECTED`
- paper_entry_order_id uuid nullable
- reservation_id uuid nullable
- state_version bigint
- last_error_code nullable
- created_at
- updated_at

`no lifecycle row` is never considered terminal.

`NEVER_CREATED_FENCED` means the paper ENTRY order was intentionally never created and can never be created later for that ENTRY action.

For a same-bar reference ENTRY + EXIT_TO_FLAT transaction:

- persist both immutable reference actions
- persist the one EXIT intent
- persist the ENTRY lifecycle directly as `NEVER_CREATED_FENCED`
- still persist the durable ENTRY outbox event
- when that outbox event is later claimed, it must observe `NEVER_CREATED_FENCED` and no-op permanently
- satisfying the EXIT intent later must not make that old ENTRY dispatch eligible again

For any other ENTRY action, create the lifecycle as `PENDING_DISPATCH` in the same reference transaction.

### 5.2 Safe-terminal ENTRY predicate

An ENTRY lifecycle is safe-terminal/fenced only when all are true:

1. lifecycle status is one of `TERMINAL_FILLED`, `TERMINAL_NO_FILL`, `NEVER_CREATED_FENCED`, `TERMINAL_REJECTED`
2. no active/nonterminal paper ENTRY order can still fill
3. no in-flight ENTRY `BIND_FILL_ATTEMPT` exists
4. no unacknowledged ENTRY fill/economic effect exists
5. any ENTRY reservation is unused/released or otherwise terminal under SPEC-004/005
6. any queued/retried dispatch for that ENTRY action is forced to terminal no-op and cannot later create an order or exposure

This predicate, not temporary paper quantity and not mere absence of an order row, is used by EXIT-intent satisfaction.

### 5.3 Paper-position ENTRY ownership

A paper position must be durably attributable to the reference ENTRY lifecycle that actually created it.

Add internal table `paper_position_entry_ownership`:

- position_id uuid UNIQUE references `paper_positions(id)`
- entry_action_id uuid UNIQUE references `paper_strategy_actions(id)`
- paper_entry_order_id uuid UNIQUE references `paper_orders(id)`
- strategy_version_id
- pair
- created_at

Rules:

- the first ENTRY fill that creates a paper position inserts this ownership row atomically with the economic position effect
- subsequent fills for the same ENTRY order reuse the same ownership row
- the ownership row is immutable and survives position close for audit
- a `NEVER_CREATED_FENCED` / no-fill ENTRY lifecycle can never own a paper position
- ownership is not inferred from side, pair, timing, or current quantity; it is proved by this durable relation

A `REFERENCE_EXIT` may reduce only the paper position whose ownership `entry_action_id` equals its own `linked_entry_action_id`.
## 6. Durable paper flatten intent

Table: `paper_exit_intents`

Mutable operational state; no PnL.

Fields:

- id uuid PK
- adapter_epoch_id
- intent_origin `REFERENCE_EXIT | INVALID_RECOVERY`
- exit_action_id uuid nullable references `paper_strategy_actions(id)`
- linked_entry_action_id uuid nullable references `paper_strategy_actions(id)`
- recovery_key text nullable
- claimed_position_id uuid nullable references `paper_positions(id)`
- strategy_version_id
- pair
- reference_position_side LONG | SHORT nullable
- locked_reduce_side BUY | SELL
- status OPEN | PAUSED | SATISFIED | CRITICAL
- paper_exit_order_id uuid nullable
- last_error_code nullable
- state_version bigint
- created_at
- updated_at
- satisfied_at nullable

Constraints:

- partial unique: `UNIQUE(exit_action_id) WHERE exit_action_id IS NOT NULL`
- partial unique: `UNIQUE(recovery_key) WHERE recovery_key IS NOT NULL`
- partial unique position claim: only one intent with status `OPEN | PAUSED | CRITICAL` may reference the same non-null `claimed_position_id`
- `intent_origin='REFERENCE_EXIT'` requires non-null `exit_action_id`, `linked_entry_action_id`, and `reference_position_side`; `recovery_key` must be null
- `intent_origin='INVALID_RECOVERY'` requires `exit_action_id IS NULL`, `linked_entry_action_id IS NULL`, and non-null canonical `recovery_key`
- for `REFERENCE_EXIT`, `locked_reduce_side` is immutable and derived from `reference_position_side`: LONG -> SELL, SHORT -> BUY
- for `INVALID_RECOVERY`, `locked_reduce_side` is immutable and derived from the actual paper side observed under execution-fence + position lock

One EXIT_TO_FLAT action creates exactly one `REFERENCE_EXIT` paper_exit_intent.

One INVALID recovery incident may create at most one `INVALID_RECOVERY` intent using:

`recovery_key = SHA256("INVALID_RECOVERY|" + adapter_epoch_id + "|" + strategy_version_id + "|" + pair + "|" + invalid_at_5m_open_time_utc_ms)`

The recovery cutoff is deliberately **not** part of the key. Different workers/retries may choose different verified replay cutoffs, but one gap in one adapter epoch may own only one recovery intent.

Duplicate recovery workers must therefore collide on the same gap key and reuse the same intent/order.

### 6.1 Position claim invariant

Before any flatten intent may create or continue an EXIT order against non-zero paper exposure, it must atomically claim the locked current `paper_positions.id` in `claimed_position_id`.

- `REFERENCE_EXIT` may claim only a position whose `paper_position_entry_ownership.entry_action_id == linked_entry_action_id`
- `INVALID_RECOVERY` may claim the current position only when no other OPEN/PAUSED/CRITICAL intent already claims it
- a claim conflict creates no second order and no economic effect
- OPEN/PAUSED claim conflict with the true owner => wait/retry the current intent
- CRITICAL claim conflict => do not steal the position; preserve the critical hold for explicit recovery
- `SATISFIED` intent no longer blocks the partial unique claim index
- claim identity is internal/blind-hidden

This DB invariant is the final duplicate-flatten gate across multiple reference cycles, recovery workers, crashes and retries.

### 6.2 Fence scope

### 6.1 Fence scope

Only `OPEN` or `PAUSED` flatten intents, regardless of `intent_origin`, fence new paper ENTRY creation or ENTRY fill application.

`SATISFIED` and `CRITICAL` intents are historical/non-live for fence lookup and must not permanently block a later reference cycle on the same `strategy_version_id + pair`.

`CRITICAL` still remains an operational fault. If paper exposure remains, the ordinary active-position / integrity guard prevents stacking or flipping; the historical CRITICAL intent itself is not used as a perpetual execution fence.

### 6.3 Satisfaction

For `REFERENCE_EXIT`, the intent target is:

**paper exposure for strategy_version_id + pair must become zero and the linked ENTRY lifecycle must no longer be capable of creating exposure.**

`REFERENCE_EXIT` may become `SATISFIED` only when all are true:

1. current paper position quantity is zero / no active paper position exists
2. the linked ENTRY lifecycle satisfies the section 5.2 safe-terminal/fenced predicate
3. there is no active ENTRY order
4. there is no in-flight ENTRY `BIND_FILL_ATTEMPT` or unacknowledged fill
5. the ENTRY reservation is unused/released/terminal
6. delayed/retried ENTRY dispatch cannot later create an order or exposure

A missing ENTRY lifecycle row is not terminal and therefore cannot satisfy a `REFERENCE_EXIT` intent.

For `INVALID_RECOVERY`, there is intentionally no single linked reference ENTRY lifecycle. Before it may become `SATISFIED`, recovery processing must terminalize/fence **every extant paper ENTRY lifecycle for the same strategy_version_id + pair that is still capable of creating exposure**, using the normal fence -> position -> order/fill/reservation order.

It may become `SATISFIED` only when:

1. current paper position quantity is zero
2. no extant ENTRY lifecycle for the same strategy_version_id + pair is dispatch-capable or fill-capable
3. no active ENTRY order exists
4. no in-flight ENTRY `BIND_FILL_ATTEMPT` or unacknowledged ENTRY fill exists
5. all relevant ENTRY reservations are unused/released/terminal
6. its single recovery EXIT order has no in-flight attempt or unacknowledged fill
7. no economic effect from either an old ENTRY path or the recovery EXIT order can still arrive
8. recovery remains internally marked non-official and reference history is unchanged

Reference state being FLAT is never sufficient to satisfy a paper intent.

A `CRITICAL` intent is never auto-converted to `SATISFIED`; side mismatch or other critical exposure remains held for explicit recovery/kill handling.

Kill-switch, stale snapshot, lease loss, retry, process crash, or serverless timeout do not delete the intent and do not create a second EXIT_TO_FLAT.
## 7. Atomic reference transition and global lock order

### 7.1 One lock hierarchy everywhere

Every SPEC-006 path that acquires cross-plane/economic rows uses this order and never inverts it:

1. `paper_strategy_execution_fence(strategy_version_id,pair)`
2. `paper_strategy_shadow_state(strategy_version_id,pair)` when that path needs shadow state
3. current paper position row when that path needs paper exposure
4. paper order / fill-attempt / fill / reservation rows

Skipping a level that a path does not need is allowed. Acquiring a lower level and later acquiring a higher level is forbidden.

Reference transitions normally acquire fence -> shadow.
ENTRY fill application acquires fence -> position -> order/fill/reservation rows.
Flatten processing acquires fence -> position -> order/fill/reservation rows.
Recovery that reconciles both planes acquires fence -> shadow -> position -> order/fill/reservation rows.

Kill-switch, stale-snapshot and `lease_generation` checks from SPEC-004/005 remain mandatory.

For SPEC-006 economic paths, existing SPEC-004/005 RPCs may be reused only if their actual SQL lock order conforms to this hierarchy. In particular, a B ENTRY/EXIT apply path must **not** delegate into a legacy RPC that first locks outbox/order and only later locks paper position.

Implementation must therefore use a SPEC-006 fence-aware transactional RPC/path that:

- acquires execution fence first
- acquires shadow only when required by that path
- acquires/establishes paper position next
- only then locks outbox/order/attempt/fill/reservation/risk-state rows needed for the economic effect
- performs the same current-lease, stale-snapshot, idempotency, reservation and ACK checks required by SPEC-004/005
- produces the same strategy-neutral accounting semantics except for the explicitly specified reduce-only EXIT quantity rules

A retained `kill_switch_state` row lock, if required, must be acquired only after the execution fence and must never be held by a path that later tries to acquire the execution fence. No SPEC-006 path may invert this order.

### 7.2 Reference 5m transaction

For each expected next 5m bar, one DB transaction/RPC must:

1. lock the execution fence
2. lock shadow state
3. re-read `last_processed_5m_open_time` after both locks
4. if another adapter already advanced the cursor, exit with no writes
5. verify `data_state=CONTIGUOUS`
6. verify current bar open_time is exactly the expected next 5m key
7. compute frozen B transition for that bar
8. insert zero, one, or two immutable actions
9. create the durable ENTRY lifecycle for every emitted ENTRY action
10. if EXIT_TO_FLAT is emitted, insert the unique `paper_exit_intent`
11. for same-bar ENTRY+EXIT, persist the ENTRY lifecycle as `NEVER_CREATED_FENCED`
12. insert durable `STRATEGY_ACTION_DISPATCH` outbox event(s)
13. update shadow state and `last_processed_5m_open_time`
14. commit all of the above atomically

If ENTRY and same-bar initial-stop EXIT both occur:

- insert ENTRY action
- insert EXIT_TO_FLAT action linked to that ENTRY
- insert one exit intent
- persist ENTRY lifecycle = `NEVER_CREATED_FENCED`
- final reference_position_state after the transaction is FLAT
- both actions, lifecycle, intent, outbox rows and cursor commit together

If frozen-B compute or any write fails, no action/lifecycle/intent/shadow/cursor change commits and the same expected 5m is retried.

Action uniqueness plus cursor re-read makes replay safe.
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
2. load the durable ENTRY lifecycle
3. if lifecycle is already terminal/fenced, no-op idempotently
4. check for any `OPEN`/`PAUSED` EXIT intent for the same `strategy_version_id + pair`
5. check current active paper position

If any live `OPEN`/`PAUSED` EXIT intent exists, including a prior reference cycle still flattening:

- do not create a new paper ENTRY order
- set this ENTRY lifecycle to `NEVER_CREATED_FENCED`
- make the outbox dispatch permanently terminal/no-op
- do not emit CRITICAL merely because paper qty is still > 0 under that live flatten
- no exposure is created

Historical `SATISFIED` or `CRITICAL` EXIT intents are ignored by this fence lookup.

If no live flatten intent exists but a paper position is already active:

- do not create a second paper ENTRY
- emit CRITICAL operational inconsistency
- do not flip or stack exposure
- reference plane remains unchanged

Otherwise:

- create the existing paper ENTRY signal/order path idempotently
- transition lifecycle to `ORDER_ACTIVE` as appropriate
- ENTRY paper execution remains quote-notional based

### 9.2 Fence inside ENTRY fill apply

Before any ENTRY fill can create/increase paper exposure:

1. lock the execution fence first
2. lock the paper position next
3. only then lock/read the relevant order/fill-attempt/fill/reservation rows
4. re-check for any `OPEN`/`PAUSED` EXIT intent on the same `strategy_version_id + pair`
5. re-check the ENTRY lifecycle is still fill-capable
6. if this fill creates the paper position, insert/verify `paper_position_entry_ownership(position_id, entry_action_id, paper_entry_order_id)` in the same economic transaction

If a live EXIT intent committed first:

- reject/no-op the new ENTRY economic effect with internal `ENTRY_FENCED_BY_EXIT_INTENT`
- do not apply quantity

If an ENTRY fill transaction acquired the fence first, it may commit according to SPEC-004/005. The later EXIT-intent transaction waits for the fence, then the durable flatten sees the resulting position.

Any already-created ENTRY attempt that has not applied economic effect is therefore fenced after intent commit. Before an EXIT intent can become SATISFIED, flatten processing must additionally prove there is no in-flight ENTRY `BIND_FILL_ATTEMPT` or unacknowledged fill.

Thus:

- live EXIT intent first => no later ENTRY exposure
- ENTRY fill first => later durable flatten removes that quantity
- same-bar outbox ENTRY event => permanent no-op because lifecycle is already `NEVER_CREATED_FENCED`

After a live intent becomes `SATISFIED`, the same execution-fence row remains reusable by later reference cycles; historical intent state does not fence them.

No paper result changes the reference plane.
## 10. Flatten-intent processing

One live EXIT intent may use zero or one paper EXIT order and many execution attempts.

Processing sequence:

1. load `OPEN`/`PAUSED` exit intent
2. lock execution fence
3. apply existing SPEC-004/005 kill-switch/lease guards without reversing the section 7 lock hierarchy
4. if independently `HALTED` or `RECOVERY_PENDING`:
   - status remains/changes to `PAUSED`
   - do not delete/complete intent
   - do not create a second EXIT_TO_FLAT
   - on resume, continue the same intent and same EXIT order; create that one EXIT order on first resume only if it does not yet exist
5. if `RUNNING` or `HALT_NEW_ENTRIES`:
   - lock current paper position
   - for `REFERENCE_EXIT`: then lock/read only the linked ENTRY lifecycle/order/attempt/fill/reservation rows
   - for `INVALID_RECOVERY`: then enumerate and lock/read **all extant fill-capable ENTRY lifecycles/orders/attempts/reservations for the same strategy_version_id + pair**
   - fence/terminalize every relevant nonterminal ENTRY path so no more ENTRY quantity can apply
   - invalidate/terminalize every relevant fill-capable queued ENTRY dispatch through its durable lifecycle
   - release only unconsumed ENTRY reservation through the existing terminal path
   - prove no relevant in-flight ENTRY `BIND_FILL_ATTEMPT` / unacknowledged fill remains before any SATISFIED transition
6. re-read paper position under lock

### No paper position / qty zero

Only if the full section 6.3 satisfaction predicate is true:

- mark intent `SATISFIED`
- audit `EXIT_NO_PAPER_POSITION` internally if no paper quantity ever existed
- no EXIT order is required

For `REFERENCE_EXIT`, if its linked lifecycle is missing, dispatch is still fill-capable, an ENTRY order/attempt/fill is still live, or reservation is not terminal:

- keep intent OPEN/PAUSED
- do not infer terminality from temporary qty=0

For `INVALID_RECOVERY`, qty=0 is insufficient until **every** same-pair ENTRY lifecycle/order/attempt/reservation satisfies the section 6.3 recovery predicate.

### Paper position exists and a live flatten already owns it

Continue the same intent. A new reference ENTRY encountered during this lag is fenced as `NEVER_CREATED_FENCED` by section 9 and is not CRITICAL.

### Paper position exists: ownership + origin-specific reduce-only validation

First read the immutable `paper_position_entry_ownership` row under the normal fence -> position -> lower-row hierarchy.

For `REFERENCE_EXIT`:

- it may act only when active paper-position ownership `entry_action_id == linked_entry_action_id`
- before creating/continuing an EXIT order, atomically set/reuse `claimed_position_id` for that exact position; claim conflict creates no order
- if its linked ENTRY lifecycle is `NEVER_CREATED_FENCED` / terminal-no-fill, it can never claim a position owned by another cycle
- if the active paper position is owned by a different ENTRY and another `OPEN`/`PAUSED` flatten intent owns that ENTRY, this intent stays OPEN and creates **no** EXIT order; the owning flatten proceeds
- if the active paper position is owned by a different ENTRY and no live flatten owns that exposure, set CRITICAL: this is unowned/cross-cycle paper exposure
- only after ownership matches may current paper side be compared to `reference_position_side`
- immutable `locked_reduce_side` must equal LONG -> SELL or SHORT -> BUY
- side mismatch after ownership match => CRITICAL, no order, no flip, no auto-satisfaction merely because reference is FLAT

For `INVALID_RECOVERY`:

- there is no reference side to compare against
- it may take operational ownership only when the current paper position is **not already claimed by another OPEN/PAUSED/CRITICAL flatten intent**
- it must atomically set/reuse its own `claimed_position_id` before creating/continuing the recovery EXIT order
- if another OPEN/PAUSED intent already claims the position, INVALID recovery creates no competing EXIT order and waits while that existing durable flatten continues
- if a CRITICAL intent claims the position, INVALID recovery does not steal it; explicit critical recovery is required
- otherwise current paper side must be reducible by the intent's immutable `locked_reduce_side`
- if the current paper side has changed such that `locked_reduce_side` would increase/reverse exposure, set intent CRITICAL, create no order, and do not flip

If the ownership and origin-specific side invariants pass:

- create the single reduce-only EXIT order if it does not already exist
- otherwise reuse the same order
- no risk reservation
- every attempt uses the intent's immutable `locked_reduce_side`
- every attempt sizes from the **remaining locked current base quantity**, not the original order notional or original intended quantity
- create a new `BIND_FILL_ATTEMPT` with a new immutable book snapshot
- reference plane remains unchanged
## 11. Reduce-only EXIT order semantics

Existing `paper_orders.intent_type='EXIT'` is used.

New service-role-only RPC:

`paper_create_reduce_only_exit_order(exit_intent_id,...)`

Rules:

- one paper EXIT order maximum per exit intent
- schema must enforce a unique relationship, e.g. `UNIQUE(exit_intent_id)` for EXIT orders
- canonical order idempotency includes `exit_intent_id`
- no new risk reservation
- quantity is base quantity, not quote notional
- order side must reduce the locked position
- order cannot open or reverse
- order cannot exceed current locked paper qty at apply time
- existing paper ENTRY quote-notional semantics remain unchanged

The order's original `intended_quantity`, if retained by the existing schema, is an immutable audit seed from first creation only. It is **not** the sizing target for later attempts.

Each EXIT attempt must use:

`attempt_base_qty = current remaining locked paper base qty`

Intent/order completion is governed by current fill-derived position truth plus the section 6.3 no-late-entry predicate, not by blindly reaching the original `intended_quantity`.
## 12. EXIT fill and partial continuation

ENTRY completion remains quote-notional based.

EXIT completion is base-quantity / position-truth based.

For every EXIT attempt/apply:

1. lock execution fence
2. lock current paper position
3. then lock order / attempt / fill / reservation rows
4. bind current `lease_generation` exactly as required by SPEC-004/005
5. bind a **new immutable book snapshot** for this attempt
6. compute `remaining_base_qty` from the locked paper position at this attempt
7. walk only that remaining quantity using SELL bids / BUY asks
8. reject fill_quantity > current open base quantity
9. reject fill side that does not reduce the position
10. apply only reducing quantity
11. ACK economic effect only for the current lease and current attempt
12. no flip is possible

Partial continuation:

- same EXIT intent
- same single EXIT order
- new `attempt_seq`
- new `BIND_FILL_ATTEMPT`
- new immutable book snapshot
- new attempt size from the remaining locked base qty
- no second EXIT_TO_FLAT action
- no second EXIT order

Transient stale-snapshot, lease-loss or attempt failure does not mint another order. The durable intent/order remain available for the next valid attempt under existing recovery rules.

Residual paper quantity keeps the intent unsatisfied.

If paper qty reaches zero, do not mark the intent SATISFIED until the complete section 6.3 ENTRY-lifecycle/no-in-flight predicate is also true.

Dust/residual quantity is execution divergence, not a new alpha exit.
## 13. Gap definition and INVALID behavior

A true gap exists when the exact next required 5m key is unavailable from the immutable source series.

Downtime is not a true gap if every interval can later be fetched contiguously before adapter processing advances past it.

On true gap:

1. lock execution fence -> shadow
2. set `data_state=INVALID`
3. preserve the last valid `reference_position_state` and all reference fields
4. store the missing expected 5m key as `invalid_at_5m_open_time`
5. expose only a blind-safe category-level adapter integrity failure
6. stop **reference-plane** ENTRY/EXIT_TO_FLAT emission
7. do not interpolate
8. do not substitute the next bar
9. do not invent a reference exit

`INVALID` applies to reference processing only.

An already `OPEN`/`PAUSED` paper flatten intent must keep running on the paper plane under the normal kill-switch state. The data-gap condition by itself must not convert the system into a state that prevents that existing flatten from continuing.

An independent system-critical fault may still HALT according to SPEC-004/005, but reference INVALID alone is not a reason to orphan a paper flatten.

The frozen incremental state rule remains unchanged: on every valid bar, evaluate prior active stop versus current OHLC first, then tighten only for the next bar.
## 14. INVALID recovery

Recovery is internal frozen-B replay only. It is not delayed live B trading.

### 14.1 Replay rules

A recovery attempt:

1. locks according to fence -> shadow -> paper position -> order/fill/reservation hierarchy as needed
2. starts from the last known-good shadow checkpoint before the gap
3. fetches a fully verified contiguous raw 5m series containing the exact missing key through a recovery cutoff
4. replays the same frozen incremental B transition logic internally and in exact chronological order
5. preserves the stop ordering: prior stop vs current OHLC first, tighten only for the next bar
6. persists recovered **reference state/cursor only**; replayed historical bars do not create live `paper_strategy_actions` or paper dispatch
7. emits no delayed ENTRY/EXIT paper order for the recovered interval
8. never copies official B evaluation output into shadow
9. never calls official scoring/PASS/FAIL or exposes partial official metrics

Recovery parity against `simulate_b_v1` is validated on synthetic fixtures only. The live blind ZEC forward window must not be replayed through the official runner as a parity/scoring shortcut.

### 14.2 Same-epoch recovery when reference remains open

If verified replay at the cutoff leaves reference `LONG` or `SHORT`, the current adapter epoch may return to `CONTIGUOUS` **without waiting for an invented FLAT boundary** only when the recovered open reference cycle is provably the same cycle that existed before the gap.

All must be true:

- data is verified contiguous through the missing key and cutoff
- replay did **not** cross a reference `EXIT_TO_FLAT` followed by a later reference `ENTRY` for the position that is open at the cutoff
- recovered `reference_entry_action_id` / cycle identity is unchanged from the last known-good pre-gap open position
- the active paper position is linked to that same ENTRY lifecycle/cycle, not merely the same side
- paper side matches the recovered reference side
- there is no contradictory live flatten intent
- no critical execution-integrity issue remains

Same-side equality alone is insufficient.

Example forbidden resume:

- pre-gap reference LONG + paper LONG
- hidden recovered bars exit that reference LONG
- later hidden recovered bars enter a new LONG
- cutoff is LONG again

The paper LONG still belongs to the old cycle. The adapter must remain INVALID; it must not treat that position as matching the new recovered LONG.

Then, and only then, persist the recovered shadow/cursor and resume with the next future 5m bar.

If recovered reference is LONG/SHORT but paper is flat, opposite-side, same-side-but-wrong-cycle, or otherwise not coherent, do not issue a delayed ENTRY or flip. Remain INVALID and continue only through an allowed future reconciliation/new-epoch path.

### 14.3 Recovery-only flatten when replay reference is FLAT

If verified replay at the cutoff leaves reference `FLAT` but paper quantity is still > 0, historical dispatch suppression must not wedge paper.

Under execution fence + shadow + paper-position lock, first inspect `paper_position_entry_ownership` and all OPEN/PAUSED flatten intents:

- if an existing OPEN/PAUSED flatten intent already claims the active paper position, do **not** create a competing recovery EXIT order; remain INVALID and let that durable intent continue
- if a CRITICAL intent claims the active position, do not steal it or auto-recover it through INVALID_RECOVERY
- once that owner intent finishes, re-evaluate recovery from the now-current paper state
- only when paper exposure is not already owned by a live flatten may recovery create/reuse the one gap-scoped operational recovery intent

Then create/reuse one durable **operational recovery flatten**:

- `intent_origin = INVALID_RECOVERY`, not `REFERENCE_EXIT`
- canonical `recovery_key` from section 6 prevents duplicate recovery intents across workers/retries
- `exit_action_id = NULL` and `linked_entry_action_id = NULL`
- no new `EXIT_TO_FLAT` reference action
- no rewrite of reference history
- no official B action/metric/PnL attribution
- current paper side observed under fence + position lock determines immutable `locked_reduce_side`: paper LONG -> SELL, paper SHORT -> BUY
- current locked paper base qty determines each attempt size
- current immutable book snapshot per attempt
- same lease/stale-snapshot/`BIND_FILL_ATTEMPT` rules as normal EXIT
- while the recovery intent is OPEN/PAUSED, ENTRY dispatch/fill fencing applies to every ENTRY lifecycle on the same strategy_version_id + pair
- before recovery SATISFIED, any old fill-capable ENTRY lifecycle/order/attempt must be terminalized/fenced and its unused reservation released
- one recovery-flatten intent -> one EXIT order -> many attempts
- no over-close, no flip

This recovery intent uses the same reduce-only execution machinery but must remain distinguishable internally from a B reference EXIT and must remain hidden by blind-safe surfaces.

While this recovery flatten is unresolved, reference processing remains INVALID.

If replayed reference is FLAT, data is verified contiguous through the cutoff, paper is already flat, and no fill-capable ENTRY path/live flatten remains, the same epoch may return directly to CONTIGUOUS at the recovered cursor without creating a recovery intent.

After a recovery flatten is safely complete, the same rule applies: if replayed reference is FLAT and all paper exposure/late-entry capability is gone, the same epoch may return to CONTIGUOUS at that recovered cursor.

A new epoch at a later verified FLAT boundary is also allowed when an integrity reset is needed, but it is not the only legal way to clear paper exposure left by a suppressed historical exit.

### 14.4 Future FLAT boundary / new epoch

If recovered reference and paper cannot be made coherent without delayed historical ENTRY or a flip, recovery may continue internal replay until a future verified reference FLAT boundary.

A new adapter epoch may begin there only if:

- current paper position is flat
- no nonterminal paper ENTRY/EXIT/recovery-flatten order exists
- no `OPEN`/`PAUSED` flatten intent remains
- no critical adapter integrity issue remains

No recovered historical B action is dispatched against a current book.
## 15. Blind protection strengthened for B runtime

Before TEST-SPEC-002 blind end, normal API/UI/client errors/logs must not expose:

- action rows or action counts
- action timestamps or exact cursor time
- ENTRY vs EXIT type
- `ENTRY_FENCED_BY_EXIT_INTENT` or other B-specific lifecycle/error codes
- idempotency keys
- intent ids / linked action ids / order ids
- side
- pair when tied to B execution
- reference prices
- stop
- shadow LONG/SHORT
- fill/order/position rows
- paper exit-intent or recovery-flatten status/count
- divergence counts
- B-specific rejection/error reason codes
- B-specific queue counts/backlog
- worker processed/claimed counts
- raw outbox bodies
- database constraint/unique-violation text that reveals action type or keys
- realized/unrealized PnL
- PF/expectancy/win rate/equity curve

While B runtime is enabled, existing generic queue/worker/reconciliation UI must be reduced to coarse category-only projections where needed:

Allowed:

- adapter health: HEALTHY / INVALID
- worker age category: FRESH / LATE / STALE
- queue health category: HEALTHY / DEGRADED
- reconciliation/audit category: PASS / WARNING / CRITICAL
- kill-switch state
- blind status + blind_until

No numeric count that moves only when B trades may be shown.

`queue DEGRADED` and `reconciliation CRITICAL` are coarse operational categories only; they may not include B-type, pair, side, linked ids, exact cursor or trade-correlated counts.

Client-facing errors must use generic operational codes and must not reveal B action timing/type.

Application/Vercel logs must redact B action payloads, outbox bodies, pair, side, reference price, active stop, fill/order payload, idempotency keys, linked identifiers, and constraint-error detail that permits reconstruction.
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

## 17. Runtime activation boundary and first bootstrap

Do not enable TEST-SPEC-002 runtime binding until all are true:

- SPEC-006 passes the next independent adversarial lock gate
- SPEC-006 locked
- reference transition transaction implemented
- explicit ENTRY lifecycle / `NEVER_CREATED_FENCED` implemented
- paper-position ENTRY ownership implemented
- live-intent fence scoped only to OPEN/PAUSED
- global lock hierarchy implemented
- durable exit intents implemented
- ENTRY fill fencing implemented
- one-intent/one-EXIT-order uniqueness implemented
- reduce-only remaining-base-quantity attempts implemented
- INVALID recovery + operational recovery flatten implemented
- blind category-only surfaces/log redaction implemented
- all required tests pass
- existing SPEC-004/005 validations remain clean

### 17.1 Initial bootstrap is not an ENTRY

Because paper runtime may be enabled after the official TEST-SPEC-002 fold has already started, the adapter must not assume reference `FLAT` at activation time and must not emit delayed historical paper trades.

Before creating/enabling the first runtime binding:

1. keep runtime binding DISABLED
2. require paper state for this strategy_version + pair to be flat with no nonterminal ENTRY/EXIT order, no active lifecycle capable of exposure, and no OPEN/PAUSED flatten intent
3. fetch a verified contiguous exact 5m series from the frozen forward-fold start (or an earlier frozen-B warmup checkpoint sufficient to reproduce the same state) through an activation cutoff
4. replay the frozen incremental B logic internally only
5. emit no `paper_strategy_actions`, paper orders, fills, or dispatch for historical bootstrap bars
6. do not call official scoring/PASS/FAIL and do not copy official B result output
7. expose no bootstrap position/side/entry/stop/cursor details on blind surfaces

If bootstrap replay at the cutoff is `FLAT`:

- create the first `adapter_epoch_id`
- persist shadow = CONTIGUOUS + FLAT at that verified cursor
- set paper activation time separately
- enable runtime dispatch only for the **next future expected 5m** after the committed bootstrap cursor

If bootstrap replay at the cutoff is `LONG` or `SHORT`:

- do not synthesize or delay an ENTRY
- keep runtime binding DISABLED
- continue internal contiguous frozen-B bootstrap replay until the first verified future reference FLAT boundary
- initialize the first epoch at that FLAT boundary
- enable dispatch only for subsequent 5m bars

If bootstrap data has a true gap or parity/integrity check fails:

- do not create/enable the runtime binding
- do not guess state
- remain operationally unbound until contiguous state can be verified

Thus the paper adapter begins from a clean, verified FLAT boundary without rewriting or contaminating official B history.

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
8. parity fixtures are synthetic only; live blind ZEC window is never used as official-runner parity input

Reference atomicity/idempotency:
9. duplicate adapter processing same 5m creates no duplicate action
10. action+lifecycle+intent+shadow+cursor rollback together on injected failure
11. exact canonical action-key uniqueness
12. two adapter workers on same 5m: loser sees cursor moved and writes nothing
13. compute failure retries the exact same expected 5m
14. same-bar creates exactly one EXIT intent

Lock order / race:
15. all reference/ENTRY-fill/flatten/recovery paths obey fence -> shadow -> position -> order/fill/reservation ordering
16. SPEC-006 B ENTRY/EXIT economic apply path does not call a legacy SQL path that locks outbox/order before paper position
17. failure injection cannot produce lock-order inversion/deadlock
18. EXIT intent commit first fences later ENTRY fill
19. ENTRY fill commit first is observed and later flattened

ENTRY lifecycle / same-bar:
20. every reference ENTRY has a durable lifecycle row
21. first economic ENTRY fill creates immutable paper-position ownership linked to that ENTRY action/order
22. later fills reuse ownership; NEVER_CREATED_FENCED/no-fill ENTRY can never own a position
23. same-bar ENTRY+EXIT persists lifecycle=`NEVER_CREATED_FENCED`
24. same-bar ENTRY outbox retry permanently no-ops and never creates a delayed order
25. missing lifecycle row is never treated as terminal
26. no-position intent cannot SATISFY while ENTRY order is active
27. no-position intent cannot SATISFY with in-flight `BIND_FILL_ATTEMPT`
28. no-position intent cannot SATISFY with unacknowledged fill
29. no-position intent cannot SATISFY with unreleased/active reservation

Fence scope / next cycles:
30. prior `OPEN`/`PAUSED` flatten fences a new reference cycle paper ENTRY as `NEVER_CREATED_FENCED`
31. that lagging new reference ENTRY is not CRITICAL solely because paper qty remains >0 under the live flatten
32. paper qty >0 with **no** live flatten on new reference ENTRY => CRITICAL/no stack
33. historical `SATISFIED` intent does not fence the next cycle
34. historical `CRITICAL` intent does not act as perpetual fence; active-position/integrity guards still prevent stack/flip
35. if a fenced newer reference cycle later emits EXIT while an older paper flatten is still live, the newer EXIT intent must not claim the older position
36. two live intents from different reference cycles cannot create two EXIT orders against the same paper position

Reduce-only / durable intent:
37. one exit intent => at most one EXIT order enforced by schema unique constraint
38. one non-zero paper position => at most one OPEN/PAUSED/CRITICAL flatten intent claim enforced by partial unique claimed_position_id
39. PAUSED resumes the same intent and same order; if order absent it is created once on first valid resume
40. each attempt sizes from remaining locked base qty, not original `intended_quantity`
41. each attempt has new attempt_seq + immutable book snapshot + current lease binding
42. stale snapshot/lease failure creates no second order/economic effect
43. partial EXIT uses same intent/order and leaves residual unsatisfied
44. no over-close/no flip
45. REFERENCE_EXIT side mismatch or INVALID_RECOVERY locked-reduce-side mismatch => CRITICAL/no order/no auto-SATISFIED
46. reference FLAT alone never satisfies paper intent

Kill switch / INVALID separation:
47. HALT_NEW_ENTRIES permits flatten-intent processing
48. independent HALTED/RECOVERY_PENDING pauses the same intent
49. `data_state=INVALID` by itself stops reference actions only
50. an already OPEN/PAUSED flatten continues while reference is INVALID if global kill-switch permits
51. true gap preserves reference_position_state and prior stop/extrema fields
52. no interpolation/substitute/invented reference exit

Recovery:
53. replay uses exact contiguous data through missing key and persists reference state/cursor only
54. replay emits no historical paper dispatch
55. replay never copies official B output
56. recovered LONG/SHORT may return same epoch to CONTIGUOUS only when recovered reference cycle identity is unchanged from pre-gap and paper is linked to that same ENTRY lifecycle
57. replay that crosses EXIT then later same-side ENTRY must not accept the old same-side paper position as a match
58. recovered LONG/SHORT + paper flat/opposite/wrong-cycle emits no delayed ENTRY/flip and remains INVALID
59. recovered FLAT + paper open first defers to any existing live flatten that owns that position; otherwise it creates/reuses exactly one operational `INVALID_RECOVERY` intent via a gap-identity recovery_key that excludes recovery cutoff, with no reference action link
60. recovery flatten is reduce-only/current-book/non-official and uses one order/many attempts; it cannot SATISFY until all old ENTRY lifecycles/orders/attempts on the pair are exposure-incapable
61. recovery flatten completion can clear the wedge without fabricating a second reference EXIT
62. new epoch at a future verified FLAT boundary remains available when coherence cannot otherwise be restored

Blind:
63. no action/intent/order/fill/position rows or counts leak
64. no internal fence code/idempotency key/linked id/pair/side/exact cursor leaks
65. queue/worker/recon surfaces are category-only with B enabled
66. logs redact B payloads, outbox bodies and constraint errors
67. generic client errors reveal no B action timing/type

Activation bootstrap:
68. bootstrap replay emits no historical paper action/order/fill/dispatch
69. bootstrap ending FLAT + paper flat initializes first epoch and dispatch starts only on next future 5m
70. bootstrap ending LONG/SHORT keeps binding disabled until a verified future FLAT boundary; no delayed ENTRY
71. bootstrap gap/integrity failure leaves runtime binding disabled
72. bootstrap never calls official scoring or copies official B output

Regression:
73. TEST-SPEC-002 11/11 smoke unchanged
74. SPEC-004 smoke unchanged
75. SPEC-005 smoke unchanged
76. no official B performance data/scoring touched
77. TEST-SPEC-002 runtime binding remains DISABLED
## 19. Implementation order after lock-gate approval

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
