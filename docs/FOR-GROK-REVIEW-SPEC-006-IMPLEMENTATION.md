# GROK ADVERSARIAL IMPLEMENTATION REVIEW — LOCKED IMPLEMENTATION-SPEC-006

Do not ask follow-up questions.
Do not change alpha.
Do not reopen or reinterpret locked IMPLEMENTATION-SPEC-006.
Do not modify TEST-SPEC-002/003 or backend/app/tsmom_b_v1.py.
Do not propose parameter tuning.
Do not enable runtime B.

## LOCKED STATE

Canonical spec:
docs/IMPLEMENTATION-SPEC-006-b-paper-adapter.md

Lock verdict:
Grok V6 = CLEAN ENOUGH TO LOCK.

Implementation under review:
branch implement-spec-006
draft PR #42

TEST-SPEC-002 runtime binding is still DISABLED in Supabase.

Frozen backend/app/tsmom_b_v1.py blob SHA is identical on main and implementation branch:
80c0cc7c3551ea5745be4cff8af5e32df04cd942

## IMPLEMENTED SURFACES

Python:
- backend/app/spec006_adapter.py
- backend/app/main.py SPEC-006 bootstrap/recovery/reference tick/outbox integration
- backend/app/paper_ops.py base-quantity immutable-book walk
- blind category-only projections when runtime B is enabled

Tests:
- backend/tests/test_spec006_adapter.py
- backend/tests/test_spec006_worker.py
- backend/tests/test_spec006_blind.py
- existing suite remains active

DB migrations:
- 011_spec006_schema.sql
- 012_spec006_reference_transition.sql
- 013_spec006_dispatch_flatten.sql
- 014_spec006_fence_apply.sql
- 015_spec006_entry_order_fence.sql
- 016_spec006_bootstrap.sql
- 017_spec006_recovery.sql
- 018_spec006_recovery_hardening.sql
- 019_spec006_activation_gate.sql
- 020_spec006_indexes_acl.sql
- 021_spec006_db_convergence.sql
- 022_spec006_immutable_rows.sql
- 023_spec006_order_race_hardening.sql

## VERIFIED IMPLEMENTATION FACTS

### Frozen reference parity
Incremental adapter imports frozen helpers/constants but tsmom_b_v1.py is unchanged.

Synthetic parity tests cover:
- LONG same-bar initial stop
- SHORT same-bar initial stop
- gap-through timestamp/price
- chandelier touch
- no same-bar retroactive tighten
- ignored signal while reference position is open
- action idempotency replay

### Lock order / apply
SPEC-006 economic apply uses:
execution fence -> shadow when needed -> paper position -> lower economic rows.

Database source audit confirms paper_spec006_apply_fill does NOT call legacy order-first paper_apply_fill.

Worker tests assert BIND_FILL_ATTEMPT for SPEC-006 uses:
- paper_spec006_bind_execution_attempt
- paper_spec006_apply_fill

and never:
- paper_bind_execution_attempt
- paper_apply_fill

### ENTRY lifecycle / races
Durable lifecycle:
PENDING_DISPATCH | ORDER_ACTIVE | TERMINAL_FILLED | TERMINAL_NO_FILL | NEVER_CREATED_FENCED | TERMINAL_REJECTED.

Same-bar ENTRY+EXIT lifecycle is NEVER_CREATED_FENCED.

paper_spec006_create_entry_order_from_approved_signal re-takes the execution fence before order creation.

If paper exposure appears between action dispatch and order creation:
- reuse existing OPEN/PAUSED/CRITICAL owner if present
- otherwise fence remaining owner ENTRY effects
- create/reuse one INTEGRITY_CRITICAL claimed-position owner
- fence the new ENTRY lifecycle/reservation
- WAIT/retry if an in-flight/unacked effect is still possible

Worker WAIT reasons release/retry the outbox instead of ACKing success.

### Paper position ownership
Each first ENTRY fill creates:
paper_position_entry_ownership(position_id, entry_action_id, paper_entry_order_id).

CLOSED position IDs cannot be reopened:
- locked invariant
- DB trigger rejects CLOSED -> OPENING/OPEN/EXIT_PENDING/CLOSING from any RPC.

### Durable flatten
paper_exit_intents origins:
- REFERENCE_EXIT
- INVALID_RECOVERY
- INTEGRITY_CRITICAL

Unique:
- exit_action_id
- recovery_key
- integrity_key
- live claimed_position_id for OPEN/PAUSED/CRITICAL

One intent -> max one EXIT order.

Partial continuation:
- same intent
- same order
- new attempt_seq
- new immutable book snapshot
- current remaining locked base qty each attempt

### Reduce-only EXIT
EXIT uses base quantity.
LONG paper -> SELL.
SHORT paper -> BUY.
Cannot exceed current locked qty.
Cannot flip.
Current paper position + ownership are fill truth.

### Frontier / no catch-up
Shadow persists:
- dispatch_frontier_5m_open_time
- dispatch_enable_commit_time

Every enable/resume path uses the frontier:
- bootstrap
- INVALID same-epoch resume
- recovery-flatten completion
- new epoch

Bars at/before frontier or with close_time <= enable commit time are non-dispatchable.

Late historical frontier-extension bars update reference/shadow only:
- no action
- no lifecycle
- no intent
- no dispatch outbox

A DB shadow-cycle guard demotes CONTIGUOUS open reference state with no immutable ENTRY identity to INVALID.

### Recovery
INVALID stops normal reference processing only.
Existing paper flatten may continue if global kill-switch permits.

Recovery:
- verified contiguous raw 5m
- internal frozen incremental replay only
- never copies official B output
- no delayed historical paper dispatch
- same-cycle LONG/SHORT requires exact ENTRY ownership
- wrong-cycle stays INVALID until later reference FLAT
- INVALID_RECOVERY fences every same-pair fill-capable ENTRY path before claiming paper
- existing OPEN/PAUSED/CRITICAL owner blocks a second recovery owner

### Activation
paper_spec006_configure_disabled_binding creates/locks only disabled binding.
paper_spec006_enable_runtime_binding exists but has NOT been called.

Activation RPC requires:
- fence/shadow lock
- CONTIGUOUS FLAT reference
- paper flat
- no nonterminal orders
- no unsafe ENTRY lifecycle
- no OPEN/PAUSED/CRITICAL intent
- cursor exactly at current verified frontier
- stores frontier/enable time before enabled=true

Current Supabase check:
TEST-SPEC-002 enabled binding = false.

### Blind
When B runtime is enabled, exposed ops surfaces collapse to coarse categories only:
- adapter HEALTHY/INVALID
- queue HEALTHY/DEGRADED
- worker FRESH/LATE/STALE
- recon/audit PASS/WARNING/CRITICAL
- kill-switch state
- blind status

No numeric B trade-correlated counts or B-specific action details.

### DB audit
Verified:
- runtime_b_enabled = false
- RLS enabled on SPEC-006 tables
- trigger helpers not executable by anon/authenticated
- CLOSED reopen trigger present
- live claimed_position unique index present
- recovery function contains active ENTRY fencing
- entry-order path contains INTEGRITY_CRITICAL claim
- paper_spec006_apply_fill does not call legacy paper_apply_fill

Supabase security advisor shows no new SPEC-006 WARN/ERROR. The only security WARN is pre-existing public rls_auto_enable(), outside SPEC-006.
RLS-without-policy INFO is intentional for server-only tables with anon/auth privileges revoked.

### CI
GitHub CI runs:
- full backend pytest
- frontend production build

A prior current-head run completed:
118 passed, 1 unrelated Starlette deprecation warning.
Frontend build passed.

Additional endpoint-level smoke tests were then added for:
- /api/paper/spec-006/smoke with no preview Supabase credentials
- /api/paper/spec-004/smoke
- /api/paper/spec-005/smoke
- /api/research/tsmom-b-v1/smoke, including exact 11/11 frozen smoke count

Latest CI must be checked before merge.

Vercel preview deploys are temporarily rate-limited by the platform, so GitHub CI FastAPI TestClient is the authoritative pre-merge HTTP smoke path until a newer preview is available.

## YOUR TASK

Review the IMPLEMENTATION against the LOCKED SPEC, not the strategy idea.

Try to find concrete implementation paths that allow:

1. delayed historical paper action after bootstrap/recovery/restart
2. a late candle to create action/lifecycle/intent/outbox
3. ENTRY economic effect after OPEN/PAUSED/CRITICAL ownership should stop it
4. two live flatten owners or two EXIT orders against one paper position
5. cross-cycle position ownership theft
6. CLOSED position reuse
7. EXIT over-close or flip
8. partial EXIT retry using original quantity instead of remaining locked base qty
9. stale lease/snapshot economic effect
10. lock inversion through any legacy RPC
11. SATISFIED while ENTRY or EXIT economic effect can still arrive
12. recovery resume from a historical cursor instead of dispatch frontier
13. bootstrap/recovery use of official B output/scoring
14. blind leakage through normal API/log/error surfaces
15. any code path that enables TEST-SPEC-002 binding before validation

Do not fail the implementation for style, naming, missing comments, or optional optimization.
A BLOCKING flaw must show a concrete path to wrong exposure, duplicate economic effect, reference drift, deadlock, official-B contamination, or blind leakage.

RETURN EXACTLY

1. BLOCKING IMPLEMENTATION FLAWS
2. NON-BLOCKING IMPLEMENTATION CAVEATS
3. ENTRY / SAME-BAR / CLAIM RACE REVIEW
4. EXIT / PARTIAL / OWNERSHIP REVIEW
5. LOCK-ORDER / LEASE / ATOMICITY REVIEW
6. RECOVERY / FRONTIER / BOOTSTRAP REVIEW
7. BLIND / OFFICIAL-B ISOLATION REVIEW
8. REQUIRED FIXES BEFORE MERGE
9. VERIFIED CORRECT — DO NOT CHANGE
10. IMPLEMENTATION VERDICT:
   - CLEAN ENOUGH TO MERGE WITH RUNTIME B DISABLED
   - NOT CLEAN ENOUGH TO MERGE
11. HANDOFF FOR CHATGPT

Runtime B must remain DISABLED regardless of merge verdict. Activation is a separate later gate.
