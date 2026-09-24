# Gemini Phase 2 Architecture Disposition

Status: **PRE-IMPLEMENTATION REVIEW PROCESSED**

Source: Gemini Phase 2 architecture handoff supplied by the user.

No Phase 2 production code has been implemented from this review.

## ACCEPTED

The following design directions are accepted:

- explicit kill-switch operational layer
- reconciliation remains detection-only
- separate Recovery Worker
- OKX order-book snapshot collection for paper execution
- short idempotent serverless jobs
- blind-safe operational API
- operational-only UI
- alert severity classes
- no live routing
- no synthetic liquidation on stale/missing data

## ACCEPTED WITH MODIFICATION

### 1. Reuse existing SPEC-004 tables and RPCs

Gemini proposed new tables such as:

- kill_switch_state
- reconciliation_incidents
- market_snapshots
- recovery_plans

Modified rule:

- reuse existing `kill_switch_state`
- reuse `reconciliation_runs` + `reconciliation_issues`
- reuse `execution_market_snapshots`
- reuse `recovery_actions`
- add new tables only when an existing SPEC-004 table cannot represent the required state

Reason: avoid competing sources of truth.

### 2. No hard-coded operational thresholds in Phase 2 architecture

Gemini proposed fixed values such as:

- stale snapshot > 3000ms
- queue backlog > 100
- warning latency > 1500ms
- reservation > 30s
- signal without risk > 60s
- order without attempt > 15s
- PnL tolerance $0.0001
- fixed cron cadences

Modified rule:

- Phase 2 defines threshold categories and policy fields
- numeric thresholds are versioned operational policy, not architecture constants
- defaults are not silently invented here

### 3. Kill-switch recovery is not automatic

Gemini proposed:

`RECOVERY_PENDING -> RUNNING` automatically when checks pass.

Modified rule:

- RECOVERY_PENDING runs health/reconciliation checks
- passing checks makes the system **eligible** for operator re-enable
- final transition back to RUNNING requires an explicit authenticated operator action
- no automatic reset

This preserves locked SPEC-004 semantics.

### 4. Order terminal status

Gemini proposed `CANCELLED_KILL_SWITCH`.

Modified rule:

- do not add a new order state
- use existing terminal `CANCELLED`
- record reason code such as `KILL_SWITCH_CANCELLED`

### 5. HALTED execution behavior

Gemini proposed aborting/marking active attempts `BLOCKED_KILL_SWITCH`.

Modified rule:

- HALTED prevents new fill application
- cancellable unfilled orders are terminalized through the existing audited order path
- immutable execution attempts/fills are not rewritten
- an unapplied BOUND attempt may remain as historical evidence and be reconciled against the terminal order
- no invented fill or history rewrite

### 6. Reconciliation names and states must match current core

Gemini used names/states such as:

- capacity_reservations
- execution_attempts COMPLETED
- orders SUBMITTED
- portfolio_risk_state.total_exposure

Modified rule:

use current core names and states:

- risk_reservations
- paper_execution_attempts: BOUND | APPLIED | REJECTED
- paper_orders existing state machine
- paper_positions
- portfolio_risk_state used/reserved gross/net fields

### 7. Audit reconciliation is per stream

Gemini described row N versus row N-1 globally.

Modified rule:

- validate `execution_audit` per `stream_key`
- contiguous `stream_sequence`
- each row `prev_hash` equals prior row's `event_hash` in the same stream
- last row agrees with `audit_stream_heads`

### 8. Recovery cannot fabricate missing immutable economic events

Gemini proposed auto-repairing a missing order event.

Modified rule:

- if an economic/order record exists but its required immutable companion event is missing despite an atomic RPC, treat it as integrity corruption
- reconciliation raises CRITICAL
- recovery does not invent the missing historical event automatically

### 9. Expired outbox lease already has recovery semantics

Current core already supports lease expiry + reclaim with `lease_generation`.

Modified rule:

- no separate mutation job is needed merely to reset expired leases
- workers reclaim expired leases through the normal claim RPC
- monitoring may report expired/reclaimed lease counts

### 10. Stale reservation recovery reuses current hardened core

Current core already includes:

- `risk_reservations.expires_at`
- `paper_find_stale_reservations`
- `paper_release_stale_reservation`

Phase 2 orchestration should invoke these primitives rather than create a second reservation recovery model.

### 11. No advisory locks

Gemini proposed `pg_try_advisory_xact_lock`.

Modified rule:

- do not rely on session/advisory locks through pooled Supabase connections
- use existing row locks, `FOR UPDATE SKIP LOCKED`, lease generation, unique constraints and DB RPC transactions

### 12. Market snapshot collector

Gemini proposed a separate `market_snapshots` table and hard-coded ZEC endpoint/depth.

Modified rule:

- store snapshots in existing `execution_market_snapshots`
- collector is pair-agnostic
- book depth request size is execution-policy/configuration, not hard-coded to one strategy
- bind the immutable persisted snapshot to `paper_execution_attempts`
- no valid snapshot => deterministic no-fill/terminal path according to execution policy

### 13. Blind-safe API/UI

Gemini's safe endpoint direction is accepted, but:

- no blinded per-order/per-fill/per-position rows
- no strategy-specific execution success/fill rejection ratios if they allow reconstruction of trading activity
- no risk exposure/margin percentage for the blinded strategy
- UI shows only safe system-level operational health for TEST-SPEC-002

## DISAGREED / REJECTED

- reject hard-coded Phase 2 timing/risk thresholds from the architecture document
- reject advisory-lock scheduling
- reject duplicate replacement tables for existing SPEC-004 state
- reject automatic RECOVERY_PENDING -> RUNNING
- reject new order state CANCELLED_KILL_SWITCH
- reject automatic fabrication of missing immutable order/audit history
- reject blind UI widgets that reveal strategy-specific exposure, attempt success rate or fill rejection breakdown

## NEXT STEP

Write SPEC-005 as the concrete Phase 2 operations specification, then send it to Grok for adversarial review before implementation.
