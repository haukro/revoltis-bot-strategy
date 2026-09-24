# IMPLEMENTATION-SPEC-005 — Paper Operations Phase 2

Status: **DRAFT FOR GROK ADVERSARIAL REVIEW — NOT LOCKED, NOT IMPLEMENTED**

Purpose: operationalize the already-hardened SPEC-004 paper execution core with kill-switch controls, reconciliation, recovery orchestration, market snapshot collection, serverless scheduling, blind-safe APIs, and operational UI.

This spec changes no alpha logic and does not modify TEST-SPEC-002 or TEST-SPEC-003.

Current core baseline:

- SPEC-004 locked commit: `d94f624a685941c1eba9f3e1fe166371422437ce`
- SPEC-004 core merge: `625311c9033ab221b5cc24065785a7e4031052df`
- P1-P5 hardening merge: `7de918cf719d43802707af300fa2a1b57d01b3b3`

## 0. Phase 2 invariants

1. Reuse the existing SPEC-004 tables/RPCs; do not create competing sources of truth.
2. No live real-money routing.
3. No alpha changes.
4. Reconciliation is detection-only.
5. Recovery is explicit, idempotent, audited, and never fabricates fills/history.
6. Kill-switch reset is never automatic.
7. No hard-coded operational timing/risk threshold is introduced by architecture; thresholds live in versioned operational policy.
8. No process-local lock or durable in-memory queue.
9. Blind TEST-SPEC-002 exposes system health only, not reconstructable trading activity/performance.
10. All internal workers are short, bounded, retry-safe, and correctness-independent from schedule cadence.

## 1. Existing core reused

Phase 2 reuses:

- `execution_outbox`
- `execution_market_snapshots`
- `paper_signals`
- `risk_decisions`
- `risk_reservations`
- `paper_orders`
- `paper_order_events`
- `paper_execution_attempts`
- `paper_fills`
- `paper_positions`
- `paper_position_events`
- `risk_policy_versions`
- `portfolio_risk_state`
- `kill_switch_state`
- `kill_switch_events`
- `market_data_health`
- `execution_audit`
- `audit_stream_heads`
- `reconciliation_runs`
- `reconciliation_issues`
- `recovery_actions`
- `blind_test_policies`

Existing hardened primitives reused:

- `paper_claim_outbox`
- `paper_ack_outbox`
- `paper_ingest_signal`
- `paper_reserve_risk`
- `paper_create_order_from_approved_signal`
- `paper_bind_execution_attempt`
- `paper_apply_fill`
- `paper_terminalize_order`
- `paper_append_audit`
- `paper_find_stale_reservations`
- `paper_release_stale_reservation`

## 2. New minimal Phase 2 state

### 2.1 ops_policy_versions

Immutable once locked.

Fields:

- id uuid PK
- name text
- warning_book_age_ms nullable
- halt_book_age_ms nullable
- warning_queue_backlog nullable
- halt_queue_backlog nullable
- warning_worker_lag_ms nullable
- halt_worker_lag_ms nullable
- reconciliation_warning_age_ms nullable
- reconciliation_critical_age_ms nullable
- max_worker_batch integer nullable
- market_book_depth integer nullable
- created_at
- locked_at

No values are hard-coded by SPEC-005.

### 2.2 worker_heartbeats

One row per logical worker role.

Fields:

- worker_name PK
- last_run_id uuid
- last_started_at
- last_completed_at
- last_success_at
- status: IDLE | RUNNING | DEGRADED | FAILED
- claimed_count
- processed_count
- last_error_code nullable
- software_commit
- updated_at

This table is operational and must not include strategy pair/side/price/PnL.

## 3. Kill-switch state machine

Reuse existing single paper account row:

`kill_switch_state(account_key='paper-default')`

States remain:

- RUNNING
- HALT_NEW_ENTRIES
- HALTED
- RECOVERY_PENDING

### 3.1 Allowed transitions

- RUNNING -> HALT_NEW_ENTRIES
- RUNNING -> HALTED
- HALT_NEW_ENTRIES -> HALTED
- HALT_NEW_ENTRIES -> RECOVERY_PENDING
- HALTED -> RECOVERY_PENDING
- RECOVERY_PENDING -> HALTED
- RECOVERY_PENDING -> RUNNING only by explicit authenticated operator enable after recovery eligibility checks pass

No automatic transition to RUNNING.

### 3.2 State semantics

RUNNING:
- new entry signals may proceed through risk
- pending orders may execute

HALT_NEW_ENTRIES:
- no new ENTRY risk reservation/order may be created
- already accepted/pending deterministic fills may complete
- risk-reducing strategy EXIT intents may proceed under their locked execution policy
- no alpha signal is rewritten

HALTED:
- no new ENTRY
- no new fill application
- unfilled or remaining-unfilled paper orders are terminalized to existing `CANCELLED` state with reason `KILL_SWITCH_CANCELLED`
- already committed fills remain immutable
- open positions remain recorded; no synthetic liquidation

RECOVERY_PENDING:
- same execution restrictions as HALTED
- reconciliation/recovery/health jobs may run
- no fill/new entry
- final return to RUNNING requires explicit operator enable

### 3.3 Kill-switch RPCs

Add service-role-only RPCs:

- `paper_set_kill_switch_state(target_state, reason_code, actor, software_commit)`
- `paper_request_recovery(reason_code, actor, software_commit)`
- `paper_enable_after_recovery(actor, software_commit)`

Each RPC:

- locks `kill_switch_state`
- validates allowed transition
- creates immutable `kill_switch_events`
- appends `execution_audit`
- is idempotent for duplicate transition requests

`paper_enable_after_recovery` may succeed only if:

- current state = RECOVERY_PENDING
- no unresolved CRITICAL `reconciliation_issues`
- latest reconciliation run = PASSED
- audit integrity check = PASSED
- persistence health = healthy

It does not inspect alpha performance.

### 3.4 Manual API authorization

Until a full user/admin auth subsystem exists, mutation endpoints are internal server routes protected by a server-only operational secret.

Assumption for Phase 2:

- environment secret: `OPS_ADMIN_TOKEN`
- browser never receives it
- mutation route validates a server-side authorization header
- service-role key remains server-side only

A future account-role system may replace this mechanism without changing core state semantics.

## 4. Automatic kill-switch triggers

Structural CRITICAL invariants may trigger HALTED immediately:

- fill without valid order
- APPLIED execution attempt without matching fill
- fill-derived position mismatch
- portfolio risk state mismatch
- audit stream/hash break
- duplicate economic effect that bypassed idempotency
- persistence failure during required critical write where state certainty is lost

Operational health thresholds such as feed age/queue backlog/worker lag are not hard-coded here.

They are read from active `ops_policy_versions`.

Depending on policy, an operational threshold may:

- emit WARNING only
- transition RUNNING -> HALT_NEW_ENTRIES
- escalate to HALTED

No automatic trigger may fabricate an exit fill.

## 5. Reconciliation engine

Reconciliation is detection-only.

Each run:

1. inserts `reconciliation_runs(status='RUNNING')`
2. evaluates checks from a consistent DB view/transaction where practical
3. inserts immutable issue evidence into `reconciliation_issues`
4. sets run result PASSED/WARNING/FAILED
5. may request kill-switch escalation according to check severity
6. never directly repairs order/fill/position/economic history

### 5.1 Reconciliation checks

#### CHK_SIG_RISK
Detect persisted signal with no PRETRADE risk decision after the active operational processing-age policy.

Severity:
- WARNING unless its outbox is already marked processed without a decision, then CRITICAL.

Proof:
- signal id
- source outbox state
- age
- absent risk decision

#### CHK_RISK_RES
APPROVED PRETRADE risk decision without exactly one matching `risk_reservations` row.

Because approval + reservation are one atomic RPC, this is structural corruption.

Severity: CRITICAL -> HALTED.

#### CHK_RES_ORDER
Expired RESERVED/PARTIALLY_CONSUMED reservation with no active order.

Severity: WARNING.
Recovery candidate: existing stale-reservation release RPC.

Expired reservation linked to an active nonterminal order is not auto-released; separate order-age check handles it.

#### CHK_ORDER_ATTEMPT
ACCEPTED/PENDING_FILL/PARTIALLY_FILLED order with no appropriate BOUND/APPLIED attempt after operational age policy.

Severity:
- WARNING while safely recoverable
- CRITICAL if outbox says processed but no attempt/effect exists

#### CHK_ATTEMPT_FILL
`paper_execution_attempts.status='APPLIED'` without a matching `paper_fills` row.

Severity: CRITICAL -> HALTED.

A BOUND attempt without fill is not automatically corruption; lease/order state determines whether it is still actionable.

#### CHK_FILL_SEQUENCE
For each order with committed fills:

- fill_seq starts at 1
- successful fill sequences are contiguous
- no duplicate sequence

Severity: CRITICAL on committed-history inconsistency.

#### CHK_FILL_POSITION
Deterministically reconstruct open/closed position state from ordered fills and compare against `paper_positions`:

- side
- quantity
- average_entry_price
- gross_entry_notional
- entry/exit/remaining fees
- realized_pnl
- status

Use NUMERIC arithmetic matching the core formulas.

Severity: CRITICAL -> HALTED.

#### CHK_POSITION_EXPOSURE
Reconstruct aggregate used exposure from positions and remaining reserved exposure from `risk_reservations`.

Compare to `portfolio_risk_state`:

- used_gross_notional
- reserved_gross_notional
- used_net_notional
- reserved_net_notional

Severity: CRITICAL -> HALTED.

#### CHK_DAILY_REALIZED
Recalculate current UTC-day realized PnL from closing fills using fill `filled_at` / market execution time semantics already locked in core.

Compare with `portfolio_risk_state.realized_pnl_utc_day`.

Severity: CRITICAL on deterministic mismatch.

No PnL is exposed through blind-safe API.

#### CHK_OUTBOX_EFFECT
For `execution_outbox.processed_at IS NOT NULL`, verify the expected target effect exists for event type.

Examples:

- RISK_EVALUATE -> risk decision
- BIND_FILL_ATTEMPT -> execution attempt or deterministic terminal outcome

Economic-effect missing after processed ACK:
Severity: CRITICAL -> HALTED.

Non-economic operational effect missing:
Severity determined by policy, normally WARNING.

#### CHK_AUDIT_STREAM
Per `stream_key`:

- stream_sequence contiguous
- row N prev_hash = row N-1 event_hash
- event_hash recomputes correctly
- final sequence/hash equals `audit_stream_heads`

Severity: CRITICAL -> HALTED.

#### CHK_KILL_SWITCH_FILL
Detect fill whose committed execution occurred while kill-switch state was HALTED or RECOVERY_PENDING according to immutable kill-switch event history.

Severity: CRITICAL -> HALTED.

#### CHK_IMMUTABLE_COMPANION
Detect economic rows whose required immutable companion event is absent despite core atomic RPC semantics.

Examples:

- order state mutation without paper_order_event
- position mutation without paper_position_event

Severity: CRITICAL.

Recovery must not fabricate missing historical events.

## 6. Recovery Worker

Recovery acts only on issues with an explicit safe repair rule.

Every recovery action:

- has canonical idempotency key
- inserts/updates `recovery_actions`
- uses existing service-role RPCs
- appends audit event
- is safe to retry
- never edits immutable fill/signal/audit history
- never invents fills

### 6.1 Automatic safe actions

#### REC_STALE_RESERVATION
Input:
- CHK_RES_ORDER issue with no active order

Action:
- call existing `paper_release_stale_reservation`

#### REC_ORDER_TIMEOUT
Input:
- nonterminal order beyond active order-age policy
- no in-flight valid lease that can still apply an effect

Action:
- terminalize with existing `paper_terminalize_order`
- terminal state chosen from existing state machine, normally EXPIRED/FAILED according to cause
- releases remaining reservation through existing core behavior

#### REC_KILL_SWITCH_CANCEL
Input:
- HALTED or RECOVERY_PENDING
- unfilled/remaining-unfilled order

Action:
- terminalize existing order as CANCELLED
- reason `KILL_SWITCH_CANCELLED`
- committed fills remain intact
- remaining reservation released

#### REC_POST_COMMIT_ACK
Input:
- reclaimed outbox event
- canonical economic effect already exists from prior worker
- current lease_generation belongs to recovery worker

Action:
- execute normal idempotent effect path if needed
- then ACK with current lease_generation
- do not directly force processed_at around fencing rules

#### REC_EXPIRED_LEASE
No special mutation is required.
Normal `paper_claim_outbox` reclaims expired leases using lease_generation.

Monitoring may count reclaim events.

### 6.2 Operator-only / non-auto recovery

Require operator approval / investigation:

- audit-chain break
- fill-position mismatch
- portfolio exposure mismatch
- missing immutable economic companion event
- unexplained duplicate economic effect
- any case where a repair would rewrite historical financial state

Phase 2 does not implement ledger/history rewriting.

## 7. Market Snapshot Collector

Reuse `execution_market_snapshots`.

Collector is strategy-neutral and pair-agnostic.

### 7.1 Trigger

A BIND_FILL_ATTEMPT outbox item with a valid active lease requests a fresh OKX order-book snapshot for the order pair.

The collector is short-lived and idempotent.

### 7.2 Source

OKX public order book endpoint.

Book depth request size comes from active operational/execution policy and is not hard-coded to ZEC or one strategy.

### 7.3 Validation

Before persistence:

- provider response code/data valid
- provider timestamp present
- bids and asks nonempty
- prices/quantities positive
- best_bid > 0
- best_ask > 0
- best_bid < best_ask
- snapshot received timestamp captured server-side

No last-price substitution.

### 7.4 Canonical hash

Hash canonical normalized:

- provider
- pair
- provider_ts_ms
- normalized ordered bids
- normalized ordered asks

Persist:

- provider timestamp
- received_at
- levels
- best bid/ask
- snapshot_hash
- software_commit

Snapshot rows are immutable.

### 7.5 Staleness

Snapshot validity at bind/apply is evaluated using the frozen execution/risk policy `max_book_age_ms`.

The collector itself does not invent a global hard-coded stale threshold.

### 7.6 Missing/invalid snapshot

No fill is created.

The worker records operational failure and leaves/terminalizes the order according to the active order-age/execution policy through existing audited state transitions.

No delayed substitute fill outside the locked strategy/execution semantics.

## 8. Serverless worker orchestration

Correctness must not depend on cadence.

All jobs are bounded, idempotent and use durable DB state.

### 8.1 Worker roles

- `paper-outbox-worker`
- `paper-reconciliation-worker`
- `paper-recovery-worker`
- `paper-market-health-worker`
- `paper-audit-health-worker`

### 8.2 Internal worker routes

Server-only:

- `POST /api/internal/paper/outbox-tick`
- `POST /api/internal/paper/reconcile`
- `POST /api/internal/paper/recover`
- `POST /api/internal/paper/market-health`
- `POST /api/internal/paper/audit-health`

Authorization:

- Vercel/server-side scheduler secret only
- no browser access
- no service-role key sent to client

### 8.3 Scheduling

Cadences are operational deployment configuration, not SPEC-005 constants.

Requirements:

- outbox cadence must be compatible with active max_signal_age/max_book_age policies
- reconciliation/recovery cadence must be short enough to meet active ops policy
- each invocation processes at most configured `max_worker_batch`
- overlapping invocations remain safe via lease_generation, row locks and idempotency
- no advisory/session lock is required

A failed scheduler invocation cannot corrupt state; later invocations resume from durable DB state.

## 9. Blind-safe operational API

TEST-SPEC-002 remains blind until:

`2026-12-23 11:59:59.999 UTC`

### 9.1 Safe endpoints

Reuse/add:

- `GET /api/paper/system-health`
- `GET /api/paper/market-health`
- `GET /api/paper/queue-health`
- `GET /api/paper/worker-health`
- `GET /api/paper/reconciliation`
- `GET /api/paper/audit-health`
- `GET /api/paper/kill-switch`
- `GET /api/paper/blind-status`

For a blinded strategy, responses are system-level operational projections only.

### 9.2 Allowed before blind end

- service status
- DB/persistence health
- market feed freshness category
- current queue backlog only
- worker heartbeat/status
- unresolved issue counts by severity
- audit integrity PASS/FAIL
- kill-switch state
- blind-test active flag
- blind_until timestamp/countdown

### 9.3 Forbidden before blind end

No:

- signal rows
- order rows
- fill rows
- position rows
- pair
- side
- per-trade timestamps
- price
- quantity
- strategy-specific exposure
- trade/fill success rate
- strategy-specific rejection breakdown
- realized/unrealized PnL
- PF
- expectancy
- win rate
- drawdown
- equity curve
- return distribution
- long/short result
- execution metrics tied to individual trades
- cumulative event counts that reveal strategy trading frequency if the system contains only that blinded strategy

Direct client SELECT remains forbidden by RLS/RPC privileges.

## 10. Operational UI

Operational only.

### Pages

1. System Health
   - backend
   - Supabase persistence
   - worker status
   - deployment/software commit

2. Market Data Health
   - provider health
   - freshness category
   - data gap/invalid snapshot alerts
   - no blinded pair-level trade reconstruction

3. Queue / Worker Health
   - current backlog
   - claimed/expired lease health
   - worker heartbeat
   - no cumulative strategy event history

4. Kill Switch
   - current state
   - reason
   - last transition
   - authorized control actions

5. Reconciliation
   - latest run status
   - unresolved WARNING/CRITICAL counts
   - integrity check names
   - no sensitive trade proof payload in normal UI

6. Recovery
   - safe auto-recovery status/count
   - operator-required incident count
   - no historical price/fill payload for blind strategy

7. Audit Integrity
   - PASS/FAIL
   - stream integrity count
   - software commit
   - no sensitive audit row payload

8. Blind Forward Status
   - ACTIVE
   - blind_until
   - operational pipeline health only
   - no performance

For TEST-SPEC-002 do not show utilization %, margin/exposure, attempt success %, fill rejection mix, or per-strategy event counts before blind end.

## 11. Alerting

Severity:

- INFO
- WARNING
- CRITICAL

INFO:
- successful lease reclaim
- successful recovery action
- routine health restoration

WARNING:
- operational threshold warning from active ops policy
- stale reservation recovery candidate
- delayed processing still within structural safety

CRITICAL:
- fill/position mismatch
- risk exposure mismatch
- APPLIED attempt without fill
- audit chain break
- processed economic outbox without target effect
- immutable companion event missing
- duplicate economic effect

CRITICAL structural integrity events may trigger HALTED.

Threshold-based WARNING/HALT behavior is controlled by versioned ops policy.

## 12. API mutation endpoints

Internal/operator only:

- `POST /api/paper/kill-switch/activate`
- `POST /api/paper/kill-switch/request-recovery`
- `POST /api/paper/kill-switch/enable`

No live-order endpoint.

Normal UI never calls service-role RPC directly.

## 13. Required tests before Phase 2 merge

### Kill switch

1. RUNNING -> HALT_NEW_ENTRIES blocks new entry reservation/order
2. HALT_NEW_ENTRIES permits already-pending deterministic fill
3. HALTED blocks new fill even with valid active lease
4. HALTED recovery terminalizes unfilled order to CANCELLED with reason, no fake fill
5. RECOVERY_PENDING blocks fills/new entries
6. RECOVERY_PENDING cannot auto-transition to RUNNING
7. explicit enable fails with unresolved CRITICAL issue
8. explicit enable succeeds only after clean reconciliation/audit health

### Reconciliation

9. approved risk decision without reservation -> CRITICAL
10. APPLIED attempt without fill -> CRITICAL
11. fill_seq gap -> CRITICAL
12. reconstruct position from fills equals stored position on valid fixture
13. injected position mismatch -> CRITICAL
14. portfolio exposure mismatch -> CRITICAL
15. UTC daily realized mismatch -> CRITICAL
16. processed economic outbox without effect -> CRITICAL
17. per-stream audit-chain corruption -> CRITICAL
18. missing immutable companion order/position event -> CRITICAL and no auto-fabrication

### Recovery

19. stale reservation without active order -> existing release RPC, exactly once
20. stale reservation with active order -> not blindly released
21. post-commit/pre-ACK reclaimed event -> idempotent effect + valid current-generation ACK
22. kill-switch cancel releases only remaining reservation
23. repeated recovery invocation -> no duplicate economic effect/audit repair

### Market snapshot

24. valid two-sided book persists immutable snapshot
25. crossed/empty/invalid book rejected
26. snapshot hash deterministic
27. stale-at-apply snapshot rejected by existing apply RPC
28. missing snapshot creates no fill

### Security / blind

29. internal worker routes reject missing/invalid scheduler secret
30. kill-switch mutation routes reject missing/invalid ops secret
31. service-role key absent from frontend bundle/config
32. blind endpoints contain no pair/side/price/qty/per-trade time/PnL
33. blind UI API contains no strategy-specific exposure/fill success/rejection breakdown
34. anon/auth still cannot SELECT sensitive execution tables or EXECUTE paper_* RPCs

### Regression

35. production SPEC-004 synthetic smoke still passes
36. P1-P5 hardening smokes still pass
37. TEST-SPEC-002 smoke remains unchanged
38. TEST-SPEC-003 closed result remains unchanged

## 14. Implementation order after Grok lock

1. ops_policy_versions + worker_heartbeats migration
2. kill-switch transition RPCs + authorization
3. reconciliation read-only RPC/service
4. recovery orchestration using existing hardened primitives
5. OKX snapshot collector
6. internal bounded worker routes
7. blind-safe operational projections/endpoints
8. alert projection
9. backend failure-injection tests
10. operational UI
11. production smoke / DB transaction validation

No Phase 2 implementation starts before Grok review disposition is applied.
