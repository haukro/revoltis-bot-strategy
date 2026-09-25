# GROK SECOND ADVERSARIAL REVIEW — IMPLEMENTATION-SPEC-006 V2

Do not ask follow-up questions.
Do not change alpha.
Do not reopen TEST-SPEC-002/003.
Do not propose parameter tuning.

PROJECT STATE

TEST-SPEC-002 ZEC TSMOM remains frozen and blind until 2026-12-23 11:59:59.999 UTC.

SPEC-004/005 paper infrastructure is implemented and validated.

TEST-SPEC-002 runtime binding remains DISABLED.

FIRST REVIEW BLOCKERS ACCEPTED

Your first SPEC-006 review identified these required fixes:

- same-bar / late-fill orphan
- durable flatten intent
- in-flight ENTRY vs EXIT fencing
- paper already OPEN on reference ENTRY
- mid-position INVALID
- undefined INVALID recovery
- atomic shadow/action/cursor persistence
- ambiguous partial-exit continuation
- exact action idempotency
- blind category-only surfaces

All were incorporated into SPEC-006 v2 before any production code.

SPEC-006 V2

1. TWO PLANES

REFERENCE PLANE:
- frozen TEST-SPEC-002 only
- theoretical FLAT/LONG/SHORT
- exact reference entry/exit/stop semantics
- paper execution never writes it

PAPER PLANE:
- actual paper order/fill/position state only

2. REFERENCE STATE

paper_strategy_shadow_state now separates:

data_state:
- CONTIGUOUS
- INVALID

reference_position_state:
- FLAT
- LONG
- SHORT

INVALID does NOT erase theoretical position state.

Other persisted reference fields:
- entry action id
- signal time
- entry time/price
- ATR
- stop
- peak/trough
- exact 5m cursor
- gap key / invalid reason

No PnL.

3. EXECUTION FENCE

paper_strategy_execution_fence:
one row per strategy_version + pair.

Purpose:
serialize reference EXIT intent creation against any paper ENTRY fill that could increase exposure.

Paper fill may lock it but may not write reference state.

4. EXACT ACTION IDEMPOTENCY

Immutable paper_strategy_actions.

Canonical key:

SHA256(
"STRATEGY_ACTION|" +
strategy_version_id + "|" +
pair + "|" +
action_type + "|" +
reference_decision_time_utc_ms + "|" +
required_execution_time_utc_ms + "|" +
position_side + "|" +
reason_code
)

UNIQUE(idempotency_key).

EXIT_TO_FLAT links to the ENTRY action that opened the reference cycle.

5. DURABLE DISPATCH

Every reference action transaction inserts:

execution_outbox:
- event_type = STRATEGY_ACTION_DISPATCH
- entity_type = strategy_action
- entity_id = action id

Normal lease_generation/reclaim semantics.

A crash after reference action persistence therefore cannot lose paper dispatch.

6. DURABLE FLATTEN INTENT

paper_exit_intents:

- exit_action_id UNIQUE
- linked_entry_action_id
- strategy_version / pair
- reference side
- status OPEN | PAUSED | SATISFIED | CRITICAL
- paper_exit_order_id nullable
- last error
- state version

Target condition:

paper exposure must be zero AND linked ENTRY lifecycle must no longer be capable of creating exposure.

SATISFIED is forbidden merely because current paper qty happens to be zero.

7. ATOMIC REFERENCE TRANSITION

For each exact expected next 5m, one DB transaction:

- lock execution fence
- lock shadow state
- verify CONTIGUOUS
- verify exact next 5m key
- compute frozen B transition
- insert 0/1/2 immutable actions
- if EXIT, insert one exit intent
- insert durable action-dispatch outbox events
- update shadow + cursor
- commit atomically

Same-bar initial-stop case:

- ENTRY action
- EXIT_TO_FLAT action linked to ENTRY
- one exit intent
- final shadow reference position = FLAT
- all committed together

8. FROZEN-B PARITY

Incremental adapter must match simulate_b_v1 on synthetic contiguous fixtures:

- entry timestamp/price
- exit timestamp/price
- exit reason
- ignored signal behavior
- same-bar initial stop
- gap-through
- chandelier touch
- no-retroactive-stop

tsmom_b_v1.py is not modified.

No official forward performance data used.

9. ENTRY DISPATCH FENCE

When ENTRY action dispatches:

- lock execution fence
- if linked OPEN/PAUSED exit intent already exists:
  - no paper ENTRY order
  - internal ENTRY_FENCED_BY_EXIT_INTENT
- if an active paper position already exists:
  - CRITICAL
  - no second entry
  - no stack/flip
- otherwise use existing ENTRY path

10. ENTRY FILL FENCE

Before ENTRY fill can increase exposure:

- fill transaction locks same execution fence
- checks for linked OPEN/PAUSED exit intent

Ordering:

EXIT intent commits first:
- later ENTRY fill economic effect rejected

ENTRY fill commits first:
- later durable exit intent sees paper qty and flattens it

Thus same-bar / near-race cannot leave an unfenced late position.

11. FLATTEN INTENT PROCESSING

Intent processing:

HALTED / RECOVERY_PENDING:
- PAUSED
- no fill
- intent survives
- retry later
- no second EXIT_TO_FLAT

RUNNING / HALT_NEW_ENTRIES:
- lock paper position
- locate linked ENTRY lifecycle
- fence/terminalize remaining nonterminal ENTRY order
- release only unused entry reservation
- re-read position

No paper qty + ENTRY terminal/fenced:
- SATISFIED
- audit EXIT_NO_PAPER_POSITION
- no EXIT order

No paper qty + ENTRY still fill-capable:
- remain OPEN

Matching paper side:
- create at most one reduce-only EXIT order
- intended qty = locked current base qty
- no reservation
- LONG->SELL
- SHORT->BUY

Side mismatch:
- CRITICAL
- no order
- no flip
- may HALT operationally
- reference unchanged

12. REDUCE-ONLY EXIT

One exit intent -> at most one EXIT order.

EXIT:
- quantity based
- cannot exceed current base qty
- cannot increase/reverse
- current paper position is fill truth

Partial:
- same EXIT order
- same exit intent
- new attempt_seq
- new immutable snapshot each attempt
- no second reference exit
- no second EXIT order

Residual qty:
- intent remains unsatisfied

13. GAP / INVALID

True gap:
required expected 5m key unavailable.

On true gap:
- data_state INVALID
- preserve last valid reference FLAT/LONG/SHORT and reference fields
- store missing expected 5m key
- CRITICAL
- stop all reference actions
- no interpolation
- no next-bar substitute
- no invented reference exit

Paper exposure remains as-is. Operational HALT may occur.

14. INVALID RECOVERY

INVALID is sticky in current adapter epoch.

Recovery may only:

- start from last good shadow checkpoint
- fetch verified contiguous raw 5m including missing key through cutoff
- replay frozen incremental logic internally
- suppress ALL paper action dispatch for past replay interval
- issue NO delayed paper orders
- never copy official B result
- find a verified reference FLAT boundary

New adapter epoch starts only if:

- verified reference FLAT boundary
- current paper position flat
- no nonterminal paper entry/exit order
- no OPEN/PAUSED exit intent
- no critical adapter issue

Otherwise remain INVALID/HALTED.

15. BLIND HARDENING

Before B blind end do NOT expose:

- action/intent rows or counts
- entry/exit type
- timestamps
- side
- pair tied to B
- reference prices/stop
- shadow LONG/SHORT
- cursor exact time
- fills/orders/positions
- divergence counts
- B-specific rejection reason
- numeric B queue/backlog
- worker processed/claimed counts
- any performance

With B runtime enabled, blind operational surfaces become category-only:

- adapter HEALTHY/INVALID
- worker FRESH/LATE/STALE
- queue HEALTHY/DEGRADED
- recon/audit PASS/WARNING/CRITICAL
- kill-switch state
- blind active/until

Logs redact B action payload and linked identifiers.

16. OFFICIAL B ISOLATION

SPEC-006 never:

- modifies tsmom_b_v1.py
- calls official B evaluation early
- computes PASS/FAIL
- computes/display partial official PnL/PF/expectancy
- changes fold/parameters/costs
- repairs shadow using official B outputs

17. REQUIRED TESTS

Must include:

- frozen B module unchanged
- exact incremental parity to B fixtures
- same-bar ENTRY+EXIT atomic
- two adapter workers => one action/intent
- action/shadow/cursor rollback together
- exact action key uniqueness
- ENTRY dispatch fenced by existing exit intent
- ENTRY fill before exit intent => flattened
- ENTRY fill after exit intent => rejected
- paper already open on reference ENTRY => CRITICAL/no stack
- exit intent not satisfied while linked entry can still fill
- one exit intent -> one exit order
- quantity-based exit
- no over-close/no flip
- partial exit same order + new attempt/snapshot
- lease reclaim idempotent
- HALT_NEW_ENTRIES allows flatten
- HALTED pauses it
- gap preserves reference position state
- INVALID emits no actions
- recovery emits no historical paper action
- new epoch only at verified reference/paper flat
- category-only blind surfaces
- no B action logs
- B 11/11 smoke unchanged
- SPEC-004/005 smoke unchanged
- no official B metrics touched

YOUR TASK

Second review gate.

Focus especially on:

1. whether the execution fence actually closes same-bar/late-entry races
2. whether exit intent SATISFIED can still happen too early
3. whether one intent / one EXIT order / many attempts is coherent
4. whether atomic reference transition can deadlock or drift
5. whether paper-open-on-new-reference-entry behavior is safe
6. whether INVALID recovery can still create hidden reference drift
7. whether blind category-only surfaces are sufficient
8. whether this still contaminates or alters official B in any way

RETURN EXACTLY

1. BLOCKING FLAWS
2. NON-BLOCKING CAVEATS
3. SAME-BAR / ENTRY-FENCE RISKS
4. DURABLE EXIT-INTENT RISKS
5. SHADOW / ATOMICITY RISKS
6. INVALID RECOVERY RISKS
7. BLIND / OFFICIAL-B ISOLATION RISKS
8. REQUIRED CHANGES BEFORE LOCK
9. THINGS ALREADY CORRECT — DO NOT CHANGE
10. LOCK VERDICT:
   - CLEAN ENOUGH TO LOCK
   - NOT CLEAN ENOUGH TO LOCK
11. HANDOFF FOR CHATGPT

Do not ask questions.
Do not propose alpha changes.
Finish the review.
