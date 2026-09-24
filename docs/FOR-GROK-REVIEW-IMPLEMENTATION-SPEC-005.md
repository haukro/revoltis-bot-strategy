# FOR GROK ADVERSARIAL REVIEW — IMPLEMENTATION-SPEC-005 PHASE 2

Do not ask follow-up questions.
Do not change alpha.
Do not redesign the already-hardened SPEC-004 core unless a blocking Phase 2 dependency requires it.

PROJECT STATE

Research:
- Strategy A: CLOSED FAIL
- Filter B: CLOSED FAIL
- TEST-SPEC-002 ZEC TSMOM: FROZEN BLIND FORWARD until 2026-12-23 11:59:59.999 UTC
- TEST-SPEC-003 BTC TSMOM: CLOSED FAIL

Core:
- SPEC-004 locked commit: d94f624a685941c1eba9f3e1fe166371422437ce
- core merge: 625311c9033ab221b5cc24065785a7e4031052df
- Grok P1-P5 hardening merge: 7de918cf719d43802707af300fa2a1b57d01b3b3

P1-P5 hardening already implemented and directly validated:
- lease_generation fencing on outbox reclaim
- bind/apply/ack require active lease generation
- exact fill replay idempotent, different fill on FILLED rejected
- stale reservation expiry + explicit recovery
- partial reservation used/reserved math
- all public paper_* SECURITY DEFINER RPCs service-role-only
- RLS/no Realtime on sensitive execution tables
- live_trading=false

PHASE 2 PURPOSE

Operationalize:
- kill switch controls
- detection-only reconciliation
- explicit recovery orchestration
- OKX immutable market snapshot collector
- short idempotent serverless jobs
- blind-safe operational API
- operational UI

NO:
- live routing
- alpha changes
- strategy router
- new TSMOM tests
- hard-coded alpha/risk thresholds

GEMINI REVIEW DISPOSITION

Accepted:
- kill switch layer
- reconciliation
- recovery worker
- snapshot collector
- serverless jobs
- blind-safe API/UI
- alerts

Modified before this review:
- reuse existing SPEC-004 tables; no duplicate kill_switch/market snapshot/recovery tables
- no hard-coded 3000ms/60s/30s/queue thresholds
- no advisory locks
- RECOVERY_PENDING never auto-returns to RUNNING
- no new CANCELLED_KILL_SWITCH order state; use CANCELLED + reason code
- no rewriting/aborting immutable execution history
- reconciliation check names/states aligned to current core
- audit validation is per-stream
- expired outbox lease uses existing lease_generation reclaim
- stale reservation recovery reuses hardened RPCs
- blind UI does not expose exposure %, fill success or rejection breakdown for TEST-SPEC-002

DRAFT SPEC-005

1. CORE INVARIANTS

- reuse existing SPEC-004 state
- reconciliation detection-only
- recovery explicit/idempotent/audited
- no fake fills/history
- no auto reset to RUNNING
- no hard-coded ops thresholds; versioned ops policy
- no in-memory correctness dependency
- blinded B exposes system health only

2. NEW MINIMAL TABLES

ops_policy_versions:
- id
- name
- warning_book_age_ms nullable
- halt_book_age_ms nullable
- warning_queue_backlog nullable
- halt_queue_backlog nullable
- warning_worker_lag_ms nullable
- halt_worker_lag_ms nullable
- reconciliation_warning_age_ms nullable
- reconciliation_critical_age_ms nullable
- max_worker_batch nullable
- market_book_depth nullable
- created_at
- locked_at

worker_heartbeats:
- worker_name PK
- last_run_id
- last_started_at
- last_completed_at
- last_success_at
- status IDLE|RUNNING|DEGRADED|FAILED
- claimed_count
- processed_count
- last_error_code
- software_commit
- updated_at

No strategy price/side/PnL data in heartbeat table.

3. KILL SWITCH

Existing states:
RUNNING
HALT_NEW_ENTRIES
HALTED
RECOVERY_PENDING

Allowed:
RUNNING -> HALT_NEW_ENTRIES
RUNNING -> HALTED
HALT_NEW_ENTRIES -> HALTED
HALT_NEW_ENTRIES -> RECOVERY_PENDING
HALTED -> RECOVERY_PENDING
RECOVERY_PENDING -> HALTED
RECOVERY_PENDING -> RUNNING only explicit authenticated operator action after eligibility checks

RUNNING:
- normal

HALT_NEW_ENTRIES:
- no new ENTRY risk reservation/order
- pending deterministic fills may complete
- strategy risk-reducing EXIT intents may proceed

HALTED:
- no new entry
- no new fill
- remaining unfilled orders terminalized CANCELLED with reason KILL_SWITCH_CANCELLED
- committed fills immutable
- positions remain; no synthetic liquidation

RECOVERY_PENDING:
- same execution restrictions as HALTED
- reconciliation/recovery/health may run
- no automatic return to RUNNING

RPCs proposed:
- paper_set_kill_switch_state(...)
- paper_request_recovery(...)
- paper_enable_after_recovery(...)

All:
- row lock kill_switch_state
- validate transition
- append kill_switch_events
- append execution_audit
- idempotent

paper_enable_after_recovery requires:
- state RECOVERY_PENDING
- no unresolved CRITICAL reconciliation issue
- latest recon PASSED
- audit integrity PASSED
- persistence healthy

Manual mutation endpoint uses server-only OPS_ADMIN_TOKEN until full admin auth exists.

4. AUTO KILL TRIGGERS

Structural CRITICAL may HALT:
- fill without order
- APPLIED attempt without fill
- fill-position mismatch
- portfolio risk mismatch
- audit hash break
- duplicate economic effect
- uncertain state after critical persistence failure

Operational thresholds are from versioned ops_policy, not hard-coded.

5. RECONCILIATION

Detection-only.

CHK_SIG_RISK:
signal missing PRETRADE risk after ops age policy.
WARNING unless outbox processed without decision => CRITICAL.

CHK_RISK_RES:
APPROVED risk decision missing exactly one risk_reservation.
CRITICAL/HALTED because approval+reservation is atomic.

CHK_RES_ORDER:
expired active reservation with no active order.
WARNING; stale-reservation recovery candidate.
If active order exists, do not release blindly.

CHK_ORDER_ATTEMPT:
nonterminal accepted/pending/partial order missing required attempt after ops age policy.
WARNING if recoverable; CRITICAL if outbox processed without effect.

CHK_ATTEMPT_FILL:
APPLIED attempt without fill.
CRITICAL/HALTED.

CHK_FILL_SEQUENCE:
committed fill_seq starts at 1 and is contiguous/unique.
CRITICAL on inconsistency.

CHK_FILL_POSITION:
rebuild position exactly from ordered fills and compare:
side, qty, avg entry, gross entry notional, entry/exit/remaining fees, realized PnL, status.
CRITICAL/HALTED.

CHK_POSITION_EXPOSURE:
rebuild used + reserved gross/net and compare portfolio_risk_state.
CRITICAL/HALTED.

CHK_DAILY_REALIZED:
recalculate current UTC-day realized PnL from closing fills using locked core semantics.
CRITICAL mismatch.
Never expose result in blind API.

CHK_OUTBOX_EFFECT:
processed outbox must have target effect.
Missing economic effect => CRITICAL/HALTED.

CHK_AUDIT_STREAM:
per stream:
- contiguous sequence
- prev_hash chain
- recompute event hash
- final hash/sequence matches audit_stream_heads
CRITICAL/HALTED.

CHK_KILL_SWITCH_FILL:
fill committed while system was HALTED/RECOVERY_PENDING.
CRITICAL.

CHK_IMMUTABLE_COMPANION:
order/position mutation missing required immutable event despite atomic core RPC.
CRITICAL.
Do not fabricate missing event.

6. RECOVERY WORKER

Auto-safe only:

REC_STALE_RESERVATION:
use existing paper_release_stale_reservation if no active order.

REC_ORDER_TIMEOUT:
nonterminal expired order with no still-valid in-flight lease.
Use paper_terminalize_order EXPIRED/FAILED according to cause.

REC_KILL_SWITCH_CANCEL:
under HALTED/RECOVERY_PENDING terminalize remaining unfilled order CANCELLED / KILL_SWITCH_CANCELLED.
No fake fill.

REC_POST_COMMIT_ACK:
reclaimed event with canonical effect already present.
Use normal idempotent path then ACK with current lease_generation.
Do not force processed_at bypassing fencing.

REC_EXPIRED_LEASE:
normal paper_claim_outbox reclaim only; no separate reset mutation.

Operator-only:
- audit chain break
- fill-position mismatch
- exposure mismatch
- missing immutable companion
- unexplained duplicate economic effect
No Phase 2 ledger/history rewriting.

7. MARKET SNAPSHOT COLLECTOR

Reuse execution_market_snapshots.

Trigger:
BIND_FILL_ATTEMPT outbox item with valid active lease.

Source:
OKX public order book for order pair.
Depth from versioned policy/config, not ZEC-hardcoded.

Validate:
- valid provider response
- provider ts
- nonempty two-sided book
- positive prices/qty
- best_bid < best_ask
- server received_at

Canonical snapshot hash:
provider + pair + provider_ts + normalized ordered bids + asks.

No last-price fallback.

Staleness checked using frozen execution/risk policy max_book_age_ms at bind/apply.

No valid snapshot => no fill.

8. SERVERLESS WORKERS

No advisory locks.
No long-lived process assumption.

Roles:
- paper-outbox-worker
- paper-reconciliation-worker
- paper-recovery-worker
- paper-market-health-worker
- paper-audit-health-worker

Internal server-only routes:
POST /api/internal/paper/outbox-tick
POST /api/internal/paper/reconcile
POST /api/internal/paper/recover
POST /api/internal/paper/market-health
POST /api/internal/paper/audit-health

Scheduler authorization server-side only.

Cadence is deployment config, not architecture constant.
Correctness comes from lease_generation, DB row locks, canonical idempotency and durable state.

9. BLIND-SAFE API

Safe:
GET /api/paper/system-health
GET /api/paper/market-health
GET /api/paper/queue-health
GET /api/paper/worker-health
GET /api/paper/reconciliation
GET /api/paper/audit-health
GET /api/paper/kill-switch
GET /api/paper/blind-status

Before TEST-SPEC-002 blind end allow only:
- service/DB health
- feed freshness category
- current backlog
- worker health
- issue counts by severity
- audit integrity PASS/FAIL
- kill switch
- blind flag/until

Forbidden:
- signal/order/fill/position rows
- pair
- side
- per-trade times
- price
- quantity
- strategy-specific exposure
- strategy fill success/rejection stats
- performance metrics
- cumulative event counts that reveal B trading frequency

10. UI

Operational only:
- System Health
- Market Data Health
- Queue / Worker Health
- Kill Switch
- Reconciliation
- Recovery
- Audit Integrity
- Blind Forward Status

No B performance.

11. ALERTS

INFO / WARNING / CRITICAL.

Structural critical integrity breach may HALT.
Threshold behavior is ops-policy-driven.

12. REQUIRED TESTS

Kill:
- HALT_NEW_ENTRIES blocks new entry but allows already-pending fill
- HALTED blocks fill
- HALTED cancel produces no fake fill
- RECOVERY_PENDING blocks execution
- no auto RUNNING
- enable fails with unresolved critical
- enable succeeds only after clean eligibility checks

Recon:
- missing risk reservation critical
- APPLIED attempt without fill critical
- fill_seq gap critical
- valid fill reconstruction exactly equals stored position
- injected position/exposure/daily-realized mismatch critical
- processed economic outbox without effect critical
- audit corruption critical
- missing immutable companion critical/no auto repair

Recovery:
- stale reservation release exactly once
- active-order reservation not blindly released
- post-commit/pre-ACK recovery uses current lease and idempotent path
- kill cancel releases only remaining reservation
- repeated recovery no duplicate effect

Market:
- valid snapshot persists
- crossed/empty invalid rejected
- deterministic hash
- stale apply rejected
- missing snapshot => no fill

Security/blind:
- internal scheduler secret required
- ops mutation secret required
- service role never exposed client-side
- blind safe endpoints contain no reconstructable rows
- anon/auth no SELECT / no paper_* EXECUTE

Regression:
- SPEC-004 10/10 smoke
- P1-P5 smokes
- TEST-SPEC-002 unchanged
- TEST-SPEC-003 result unchanged

YOUR TASK

Try to break SPEC-005 before implementation.

Focus on:
- kill-switch transition races
- fill already in-flight when HALTED commits
- cancellation/reservation races
- reconciliation false positives / inconsistent snapshots
- recovery accidentally mutating economic history
- outbox worker and market snapshot ordering
- scheduler overlap
- lease_generation handling
- service-role / ops-token security
- blind leakage through health/issue payloads/logs
- audit verification correctness
- replay safety
- whether any proposed auto-recovery is unsafe

RETURN EXACTLY:

1. BLOCKING FLAWS
2. NON-BLOCKING CAVEATS
3. KILL-SWITCH RACE RISKS
4. RECONCILIATION DESIGN RISKS
5. RECOVERY SAFETY RISKS
6. MARKET SNAPSHOT / WORKER RISKS
7. BLIND / SECURITY RISKS
8. REQUIRED CHANGES BEFORE LOCK
9. THINGS ALREADY CORRECT — DO NOT CHANGE
10. LOCK VERDICT:
   - CLEAN ENOUGH TO LOCK
   - NOT CLEAN ENOUGH TO LOCK
11. HANDOFF FOR CHATGPT

Do not ask questions.
Do not propose alpha changes.
Finish the review.
