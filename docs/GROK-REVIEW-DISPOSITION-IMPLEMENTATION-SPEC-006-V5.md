# GROK REVIEW DISPOSITION — IMPLEMENTATION-SPEC-006 V5

Status: **V5 BLOCKERS ACCEPTED AND PATCHED — SPEC STILL UNLOCKED**

Source verdict: **NOT CLEAN ENOUGH TO LOCK**

No alpha change.
No TEST-SPEC-002/003 change.
No `tsmom_b_v1.py` change.
No official scoring/performance change.
TEST-SPEC-002 runtime binding remains DISABLED.

## Accepted V5 blockers

### 1. Recovery / resume delayed-dispatch frontier — ACCEPTED

Patched with a global §14.2 dispatch-enable frontier invariant used by every enable/resume path:

- bootstrap
- INVALID same-epoch resume
- recovery-flatten completion
- new epoch

For every enable/resume:

- capture `enable_commit_wall_time`
- derive `enable_frontier_5m` from the verified immutable adapter series
- persist `dispatch_frontier_5m_open_time` and `dispatch_enable_commit_time`
- internally advance reference state through the frontier with zero paper action/lifecycle/intent/outbox creation
- re-evaluate all safe-enable conditions at the frontier
- no recovered historical cutoff can become a dispatch cursor
- no candle with `close_time <= enable_commit_wall_time` can dispatch, even if it arrives late
- first dispatch-eligible bar must close strictly after the enable commit wall time
- persisted frontier survives restart

This removes historical catch-up dispatch from recovery, recovery completion and new-epoch activation.

### 2. §9.1 CRITICAL was log-only — ACCEPTED

Added `intent_origin=INTEGRITY_CRITICAL`:

- one canonical integrity key per live paper position:
  `SHA256("INTEGRITY_CRITICAL|" + strategy_version_id + "|" + pair + "|" + claimed_position_id)`
- §9.1 locks fence -> position and atomically creates/reuses the CRITICAL intent with `claimed_position_id`
- unique integrity key + position-claim uniqueness prevents two CRITICAL owners
- INTEGRITY_CRITICAL creates no automatic EXIT order
- if another OPEN/PAUSED/CRITICAL owner already claims the position, preserve/reuse it; no second intent/order
- the new reference ENTRY is fenced/terminalized
- every old ENTRY path capable of increasing the claimed paper position is also fenced/terminalized before CRITICAL commit
- CRITICAL cannot commit while an in-flight/unacknowledged ENTRY effect can still increase that position

## Additional hardening

- frontier fields are durable in shadow state, preventing restart from forgetting the no-catch-up boundary
- late-arriving pre-enable-closed candles use an internal shadow-only frontier-extension path
- normal reference processing cannot advance while `data_state=INVALID`; only §14 recovery may do so
- bootstrap cutoff is pinned to the verified immutable adapter source series
- one atomic FLAT/enable commit captures and persists the enable wall time/frontier
- CLOSED -> active paper-position transition remains guarded at DB level

## Current verdict

SPEC-006 remains **NOT LOCKED / NOT IMPLEMENTED** pending the next independent lock-gate review.

Runtime B remains **DISABLED**.
