# GROK REVIEW DISPOSITION — IMPLEMENTATION-SPEC-006 V4

Status: **BLOCKERS ACCEPTED AND PATCHED — SPEC STILL UNLOCKED**

Source verdict: **NOT CLEAN ENOUGH TO LOCK**

No alpha change.
No TEST-SPEC-002/003 change.
No `tsmom_b_v1.py` change.
No official scoring/performance change.
TEST-SPEC-002 runtime binding remains DISABLED.

## Accepted V4 blockers

### 1. Bootstrap / activation catch-up ambiguity — ACCEPTED

Patched §17.1:

- bootstrap cutoff is the latest fully closed exact 5m at bootstrap commit wall time
- an older convenient FLAT may never be used as the activation cursor
- every bar at/before bootstrap cutoff is permanently non-dispatchable
- if cutoff is FLAT, binding enables only after the cutoff commit and the first dispatch-eligible bar is the first newly closed expected 5m strictly after it
- if cutoff is LONG/SHORT, binding remains disabled and only subsequent newly closed exact 5m bars are internally consumed
- those unbound-forward bars are also non-dispatchable
- first epoch is committed only when a newly observed reference FLAT arrives
- dispatch begins on the next newly closed 5m
- no catch-up dispatch exists between any historical FLAT and enable time

### 2. Position identity / ownership reuse — ACCEPTED

Patched §5.3:

- a CLOSED `paper_positions.id` is permanently retired
- no SPEC-006 path may reopen/recycle a CLOSED row
- every new paper ENTRY exposure cycle inserts a new `paper_positions.id`
- every new position id gets its own immutable `paper_position_entry_ownership`
- ownership remains one position-id / one reference ENTRY cycle

This matches existing SPEC-004 behavior and makes it a locked SPEC-006 invariant.

### 3. CRITICAL without claim — ACCEPTED

Patched §6.1 / §10 / §14.3:

- any transition to CRITICAL while paper qty > 0 must already own or atomically acquire `claimed_position_id`
- a new CRITICAL-with-live-qty row with null claim is forbidden
- if another intent already owns the position, the faulting intent cannot steal it or create a second order/owner
- legacy/malformed CRITICAL null-claim state blocks INVALID_RECOVERY until explicit ownership repair
- INVALID_RECOVERY also refuses CRITICAL-owned exposure

### 4. Wrong-cycle INVALID recovery sequence — ACCEPTED

Patched §14.2 → §14.3 → §14.4:

For recovered LONG/SHORT that is flat/opposite/wrong-cycle on paper:

`stay INVALID -> internal replay only -> later verified FLAT -> §14.3 recovery flatten if paper still open -> paper flat + no late-entry capability -> optional §14.4 new epoch`

No delayed historical ENTRY and no flip.

## Additional cleanup

- removed duplicate Fence Scope section heading
- retained machine-checkable test that SPEC-006 economic apply cannot call the legacy SPEC-004 order-first fill RPC
- added explicit tests for new position ids, CRITICAL claims, wrong-cycle recovery chain, and no-catch-up bootstrap

## Current verdict

SPEC-006 remains **NOT LOCKED / NOT IMPLEMENTED** pending the next independent lock-gate review.

Runtime B remains **DISABLED**.
