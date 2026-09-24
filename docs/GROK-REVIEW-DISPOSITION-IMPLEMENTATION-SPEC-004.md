# Grok Adversarial Review Disposition — IMPLEMENTATION-SPEC-004

Status: **PRE-IMPLEMENTATION REVIEW APPLIED**

No Paper Execution / Risk Engine production code was written before this review was processed.

## ACCEPTED

Accepted as blocking and incorporated before lock:

- I1 risk reservation atomically created with APPROVED risk decision
- I2 fill + order state + position + risk exposure + audit applied in one DB transaction
- I3 outbox lease with claim expiry and reclaim
- I4 canonical server-generated idempotency keys
- I5 cancel/fill total ordering via order row lock
- I6 explicit partial-fill continuation semantics
- I7 blind row-API restrictions
- I8 RLS restrictions and no Supabase Realtime on sensitive execution tables
- I9 audit stream-head row lock for sequence/hash allocation
- I10 UTC realized-loss clock and internal blind drawdown mark
- explicit order-state meanings
- per-order fill_seq
- long/short realized-PnL formulas
- exact kill-switch state semantics

## IMPLEMENTED IN THE SPEC

The lock candidate now includes:

- separate risk_reservations lifecycle table
- portfolio_risk_state row-lock serialization
- atomic APPROVE + reservation RPC semantics
- atomic reserved -> used conversion during fill
- reservation release on unfilled terminal outcomes
- execution_outbox.claim_expires_at
- lease/reclaim rules
- canonical idempotency formulas
- paper_execution_attempts to bind a retry to the same immutable market snapshot
- explicit next-snapshot path for partial fills
- atomic fill-apply RPC requirements
- order-row total ordering for fill vs cancel
- fill_seq and deterministic position replay ordering
- realized-PnL formulas with no double-counting of execution cost
- HALT_NEW_ENTRIES vs HALTED vs RECOVERY_PENDING behavior
- audit_stream_heads
- direct RLS/no-Realtime restrictions
- blind API/log redaction rules
- UTC daily-risk semantics
- expanded concurrency/failure/blind-protection tests

## DISAGREED / MODIFIED

No substantive disagreement with I1-I10.

Two implementation refinements:

1. Risk capacity is represented by a separate risk_reservations table rather than mutable reservation columns on risk_decisions. This preserves immutable risk decisions while making reservation lifecycle explicit.

2. SPEC-004 removes an active CANCEL_REQUESTED -> FILLED transition. Since fill insertion and order-state mutation are one transaction, a fill committed before cancellation is already reflected before the later cancel transaction gets the row lock. Once CANCEL_REQUESTED commits, no new fill is allowed. This implements Grok's requested total ordering without an ambiguous transition.

## NOT CHANGED

- no alpha changes
- TEST-SPEC-002 remains frozen
- TEST-SPEC-003 remains CLOSED FAIL
- no numeric risk thresholds were locked
- no live routing
- no strategy router
- no adaptive sizing
- no parameter tuning
