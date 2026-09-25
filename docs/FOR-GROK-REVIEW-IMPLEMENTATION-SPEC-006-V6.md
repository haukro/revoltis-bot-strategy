# GROK INDEPENDENT LOCK-GATE REVIEW — IMPLEMENTATION-SPEC-006 V6

Do not ask follow-up questions.
Do not change alpha.
Do not reopen TEST-SPEC-002/003.
Do not propose parameter tuning.
Do not enable runtime B.

PROJECT STATE

- TEST-SPEC-002 ZEC TSMOM remains frozen and blind through 2026-12-23 11:59:59.999 UTC.
- SPEC-004/005 infrastructure remains implemented and validated.
- TEST-SPEC-002 runtime binding remains DISABLED.
- SPEC-006 remains DRAFT / NOT IMPLEMENTED / NOT LOCKED.
- Review the full current `docs/DRAFT-IMPLEMENTATION-SPEC-006-b-paper-adapter.md`, not this brief alone.

V5 VERDICT WAS NOT CLEAN ENOUGH TO LOCK.

BOTH V5 BLOCKERS WERE ACCEPTED AND PATCHED.

1. GLOBAL DISPATCH-ENABLE FRONTIER

Every transition that can make B paper dispatch eligible now uses one rule:

- initial bootstrap enable
- INVALID same-epoch resume
- recovery-flatten completion followed by resume
- new adapter epoch

At each transition:

- capture `enable_commit_wall_time`
- derive `enable_frontier_5m` from the verified immutable adapter source series
- persist `dispatch_frontier_5m_open_time` and `dispatch_enable_commit_time`
- internally/non-emitting advance reference state through all verified bars to the frontier
- re-evaluate full reference-cycle / paper ownership / side / flatness / live-claim / late-ENTRY / integrity conditions at the frontier
- recovered historical cutoffs never become dispatch cursors
- bars at/before frontier are permanently non-dispatchable
- any late-arriving candle with `close_time <= dispatch_enable_commit_time` is processed shadow-only with zero action/lifecycle/intent/outbox creation
- first dispatch-eligible bar must be the first expected bar after frontier with `close_time > enable_commit_wall_time`
- frontier fields survive restart
- if frontier state is not safe, remain INVALID/DISABLED and keep consuming newly closed bars internally only until safe

2. §9.1 CRITICAL IS NOW A REAL CLAIMING INTENT

Added `INTEGRITY_CRITICAL` origin:

- canonical unique key = SHA256(INTEGRITY_CRITICAL | strategy_version | pair | claimed_position_id)
- paper qty > 0 with no OPEN/PAUSED owner cannot produce a log-only CRITICAL
- under fence -> position lock, create/reuse one CRITICAL intent and atomically claim the live paper position
- if another OPEN/PAUSED/CRITICAL owner already claims it, preserve/reuse that owner and create no second intent/order
- INTEGRITY_CRITICAL creates no automatic EXIT order
- new reference ENTRY lifecycle is fenced/terminalized
- every existing ENTRY lifecycle/order/attempt/reservation that can still increase the claimed position is fenced/terminalized before CRITICAL commit
- CRITICAL cannot commit while an in-flight/unacknowledged ENTRY economic effect can still increase the position
- INVALID_RECOVERY cannot steal a CRITICAL claim

ADDITIONAL LOCKED-IN HARDENING

- CLOSED paper position id is permanently retired; later cycle = new position id + new immutable ownership row
- DB guard rejects CLOSED -> active from 006 and legacy paths
- one OPEN/PAUSED/CRITICAL flatten claim per paper position
- REFERENCE_EXIT may claim only the position owned by its linked ENTRY action
- one intent -> one EXIT order -> many attempts
- remaining locked base qty per attempt
- new immutable book snapshot and current lease per attempt
- same-bar ENTRY+EXIT persists NEVER_CREATED_FENCED before outbox can create an order
- historical SATISFIED/CRITICAL do not permanently fence future cycles
- INVALID recovery persists reference/cursor only; no historical paper dispatch
- wrong-cycle recovery = INVALID -> internal replay -> later FLAT -> recovery flatten if needed -> paper safe-flat -> optional new epoch
- exact cycle ownership, not same side, is required for same-epoch LONG/SHORT recovery
- SPEC-006 economic apply cannot delegate to legacy order-first paper_apply_fill
- global lock order = fence -> shadow when needed -> position -> lower economic rows
- blind surfaces hide action/cursor/bootstrap/activation timing, ids, pair/side, B-specific errors and performance
- official B runner/scoring/fold/parameters/costs/tsmom_b_v1.py remain untouched
- synthetic parity only
- runtime B remains DISABLED

TRY TO BREAK SPECIFICALLY

1. Can any bootstrap/recovery/new-epoch path dispatch a candle whose close_time was already <= its enable commit wall time?
2. Can restart forget the no-catch-up frontier?
3. Can a late-arriving historical candle create action/lifecycle/intent/outbox after enable?
4. Can a pre-frontier safe-state check authorize dispatch even if reference/ownership changes while internally advancing to the frontier?
5. Can §9.1 live-paper CRITICAL exist without a durable owner claim?
6. Can an old partially fillable ENTRY increase a paper position after INTEGRITY_CRITICAL is created?
7. Can two concurrent §9.1 workers create two CRITICAL intents/owners?
8. Can INVALID_RECOVERY steal exposure owned by OPEN/PAUSED/CRITICAL intent?
9. Can CLOSED paper position identity be reused?
10. Can two reference cycles create EXIT orders against one paper position?
11. Can SATISFIED occur before all delayed ENTRY or EXIT economic effects are impossible?
12. Can any SPEC-006 SQL path invert lock order by calling the legacy SPEC-004 apply RPC?
13. Can bootstrap/recovery/frontier timing leak a reference FLAT boundary?
14. Can any recovery/bootstrap path call official evaluation/scoring or use official output as repair state?

RETURN EXACTLY

1. BLOCKING FLAWS
2. NON-BLOCKING CAVEATS
3. SAME-BAR / ENTRY-FENCE RISKS
4. DURABLE EXIT-INTENT / POSITION-OWNERSHIP RISKS
5. LOCK-ORDER / ATOMICITY RISKS
6. INVALID RECOVERY / BOOTSTRAP / FRONTIER RISKS
7. BLIND / OFFICIAL-B ISOLATION RISKS
8. REQUIRED CHANGES BEFORE LOCK
9. THINGS ALREADY CORRECT — DO NOT CHANGE
10. LOCK VERDICT:
   - CLEAN ENOUGH TO LOCK
   - NOT CLEAN ENOUGH TO LOCK
11. HANDOFF FOR CHATGPT

If any ambiguity still permits delayed exposure, forgotten frontier after restart, late historical dispatch, unclaimed CRITICAL exposure, residual ENTRY growth under CRITICAL, duplicate flatten ownership, lock-order inversion, reference drift, official-B contamination, or blind leakage, verdict must be NOT CLEAN ENOUGH TO LOCK.
