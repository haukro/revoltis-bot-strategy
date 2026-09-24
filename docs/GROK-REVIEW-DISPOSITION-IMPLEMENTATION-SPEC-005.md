# Grok Adversarial Review Disposition — IMPLEMENTATION-SPEC-005 Phase 2

Status: **PRE-IMPLEMENTATION REVIEW APPLIED**

No Phase 2 production code was written before this review was processed.

## ACCEPTED

Accepted as blocking before lock:

- Q1 undefined EXIT intent
- Q2 reconciliation false CRITICAL on in-flight leased work
- Q3 reconciliation isolation / single-run serialization
- Q4 APPLIED only inside atomic fill transaction
- Q5 blind reconciliation/heartbeat leakage
- Q6 stale PASSED reconciliation cannot re-enable RUNNING
- Q7 canonical snapshot hash normalization
- one OPEN reconciliation issue per deterministic fingerprint
- kill-switch state has one authoritative writer

## IMPLEMENTED IN THE SPEC

The lock candidate now explicitly states:

- Phase 2 introduces no EXIT or REDUCE_ONLY intent
- HALT_NEW_ENTRIES permits only already-existing pending fills to complete
- reconciliation runs in one REPEATABLE READ transaction
- only one reconciliation run may be RUNNING for the paper account
- missing economic effects are IN_FLIGHT, not CRITICAL, while a matching outbox lease is unexpired
- attempt APPLIED is legal only in the same atomic paper_apply_fill transaction that inserts the fill
- blind reconciliation APIs expose only severity/check-code counts
- blind worker APIs expose status + last-success age category, not lifetime processed counters
- re-enable RUNNING requires a fresh reconciliation after the latest kill-switch event and recovery action
- re-enable requires zero open CRITICAL issues and zero unexpired economic execution leases
- snapshot normalization uses exact decimal canonicalization, deterministic sorting and canonical JSON bytes
- repeated unresolved reconciliation conditions use deterministic fingerprints to prevent issue floods
- paper_set_kill_switch_state is the sole writer of kill_switch_state.state

## DISAGREED / MODIFIED

No substantive disagreement with Q1-Q7.

Implementation choice for Q1:

- chose Grok's preferred simpler option: no new EXIT/REDUCE_ONLY behavior in Phase 2.

Implementation choice for Q3:

- use DB-backed run serialization / active-run guard plus REPEATABLE READ
- do not introduce advisory/session locks.

## NOT CHANGED

- no alpha logic
- no TEST-SPEC-002 changes
- no TEST-SPEC-003 changes
- no SPEC-004 fill/PnL math changes
- no P1-P5 fencing changes
- no live routing
- no hard-coded operational policy thresholds
