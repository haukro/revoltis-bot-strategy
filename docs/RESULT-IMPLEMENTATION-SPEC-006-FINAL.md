# RESULT — IMPLEMENTATION-SPEC-006 FINAL PRE-MERGE VALIDATION

Status: **IMPLEMENTED ON DRAFT PR #43 — PRE-MERGE VALIDATION GREEN — RUNTIME B UNBOUND**

Canonical locked spec:
`docs/IMPLEMENTATION-SPEC-006-b-paper-adapter.md`

Implementation branch:
`implement-spec006-paper-adapter`

Draft PR:
#43 — Implement locked SPEC-006 paper adapter — final V6 DB sync

## Boundaries preserved

- no alpha change
- TEST-SPEC-002 unchanged
- TEST-SPEC-003 unchanged
- `backend/app/tsmom_b_v1.py` unchanged byte-for-byte:
  `80c0cc7c3551ea5745be4cff8af5e32df04cd942`
- official B scoring/fold/costs/parameters unchanged
- no live real-money routing
- TEST-SPEC-002 runtime binding row does not exist in live Supabase
- enabled TEST-SPEC-002 binding count = **0**

## CI

GitHub Actions PR CI run:
`36125858781`

Backend:
- **124 passed**
- 1 Starlette/anyio deprecation warning only
- no failed tests

Frontend:
- production build passed

The CI suite includes:
- frozen TEST-SPEC-002 synthetic smoke
- SPEC-004 smoke
- SPEC-005 smoke
- SPEC-006 synthetic parity tests
- SPEC-006 worker routing tests
- SPEC-006 blind-surface tests
- SPEC-006 migration source guards

## Reference parity

Incremental SPEC-006 adapter reuses frozen B helpers/constants but never modifies `tsmom_b_v1.py`.

Synthetic differential parity covers:
- LONG same-bar initial stop
- SHORT same-bar initial stop
- gap-through timestamp/price
- chandelier touch
- no same-bar retroactive tighten
- ignored signal while already open
- action idempotency replay

Bootstrap and recovery use the same frozen ZEC pre-fold warmup origin as official TEST-SPEC-002:
- **24 hours**
- no rolling 30h ATR restart
- Wilder ATR path remains aligned to the official fold input history

No live blind ZEC fold is used as a parity fixture.

## Database convergence

GitHub now contains the exact live Supabase SPEC-006 migration history through:
`027_spec006_exit_attempt_locked_base_qty.sql`

Live DB migrations were synchronized into repo rather than reconstructed manually.

Verified live invariants:

- one live OPEN/PAUSED/CRITICAL claim per paper position
- CLOSED paper position reopen guard present
- immutable reference/action/ownership rows guarded
- all 24 `paper_spec006_*` functions deny EXECUTE to `anon` and `authenticated`
- SPEC-006 apply does **not** call legacy order-first `paper_apply_fill`
- EXIT attempts carry `remaining_base_qty` captured under execution-fence + paper-position lock
- partial EXIT continuation reuses the same intent/order and queues a new attempt with the new remaining base qty

## Worker / economic execution

SPEC-006 BIND_FILL_ATTEMPT uses:
- `paper_spec006_bind_execution_attempt`
- `paper_spec006_apply_fill`

and never uses legacy:
- `paper_bind_execution_attempt`
- `paper_apply_fill`

SPEC-006 EXIT:
- uses base quantity, not quote notional
- worker uses locked `remaining_base_qty` from the attempt outbox payload
- does not re-read position quantity unlocked for sizing
- partial depth may fill partially and continue with same intent/order
- policy/configuration failure keeps durable EXIT retryable rather than destroying the flatten intent
- paper flatten may continue with its configured policy even when runtime binding is not enabled

Queued historical ENTRY safety:
- disabled/missing B binding terminally fences queued SPEC-006 ENTRY lifecycle
- queued ENTRY cannot remain retryable and later open after re-enable
- approved-risk SPEC-006 ENTRY order creation re-takes execution fence
- INTEGRITY_CRITICAL owner is reused/created when paper exposure appears cross-transaction

## Frontier / recovery / no catch-up

Persisted shadow fields:
- `dispatch_frontier_5m_open_time`
- `dispatch_enable_commit_time`

Applied to:
- bootstrap
- INVALID same-cycle recovery
- recovery-flatten completion
- new epoch activation

Rules verified:
- historical recovery cutoffs are not live dispatch cursors
- activation/recovery requires current verified DB frontier
- late historical bars cannot create action/lifecycle/intent/outbox
- safe-enable conditions are re-evaluated at frontier
- restart reloads frontier state

Recovery uses frozen incremental replay from the same 24h ZEC warmup origin, avoiding a rolling-Wilder-ATR drift.

## Blind protection

When B runtime is eventually enabled, normal ops surfaces collapse to category-only projections.

No B-specific:
- action/intent/order/fill rows
- pair/side
- exact cursor/frontier/activation time
- linked ids/idempotency keys
- B-specific rejection codes
- numeric trade-correlated counts
- partial performance

are exposed by blind-safe surfaces.

## Supabase advisors

No new SPEC-006 security WARN/ERROR.

Existing security WARN remains outside SPEC-006:
- `public.rls_auto_enable()` executable by anon/authenticated

RLS-without-policy INFO remains intentional for server-only tables with public/auth privileges revoked.

No unresolved new SPEC-006 foreign-key index issue was reported.

## Vercel

The exact current preview head is blocked by Vercel account build-rate limiting, not by an application build error.

Authoritative pre-merge validation is therefore:
- GitHub Actions backend pytest
- GitHub Actions frontend production build
- FastAPI TestClient endpoint smokes
- live Supabase invariant/advisor audit

## Merge gate

Do not merge PR #43 until:
- independent implementation review returns **CLEAN ENOUGH TO MERGE WITH RUNTIME B DISABLED**
- latest CI remains green

Do not configure or enable TEST-SPEC-002 runtime binding as part of merge.

Activation is a separate later gate.
