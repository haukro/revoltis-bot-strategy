# RESULT — IMPLEMENTATION-SPEC-006 PRE-MERGE VALIDATION

Status: **IMPLEMENTED ON DRAFT PR #42 — PRE-MERGE VALIDATION GREEN — RUNTIME B DISABLED**

Canonical locked spec:
`docs/IMPLEMENTATION-SPEC-006-b-paper-adapter.md`

Implementation branch:
`implement-spec-006`

Draft PR:
#42 — Implement locked SPEC-006 paper adapter

## Boundaries preserved

- no alpha change
- TEST-SPEC-002 unchanged
- TEST-SPEC-003 unchanged
- `backend/app/tsmom_b_v1.py` unchanged byte-for-byte at Git blob SHA:
  `80c0cc7c3551ea5745be4cff8af5e32df04cd942`
- official B scoring/fold/costs/parameters unchanged
- runtime B remains disabled
- no live real-money routing

## CI

GitHub Actions code-validation run:
`36120383341`

Backend:
- **120 passed**
- 1 Starlette/anyio deprecation warning only
- no failed tests

Frontend:
- production build passed

Endpoint-level CI smoke includes:
- `/api/paper/spec-006/smoke`
- `/api/paper/spec-004/smoke`
- `/api/paper/spec-005/smoke`
- `/api/research/tsmom-b-v1/smoke`
- frozen B smoke count = **11/11**

SPEC-006 smoke is explicitly tested with Vercel-like environment and no Supabase credentials:
- HTTP 200
- status=passed
- runtime_b_enabled=false
- blind_safe=true
- live_trading=false

## Database state

Supabase project:
`revoltis-bot-strategy`

SPEC-006 migrations are applied through the final converged implementation.

Verified DB invariants:

- TEST-SPEC-002 enabled binding = **false**
- SPEC-006 RLS enabled
- anon/authenticated cannot execute internal SPEC-006 helper functions
- CLOSED paper position reopen guard present and functionally rejects CLOSED -> OPEN
- immutable ownership/action/reference-bar guards present
- one live OPEN/PAUSED/CRITICAL claim per paper position
- recovery actively fences same-pair fill-capable ENTRY paths
- entry-order cross-transaction race creates/reuses INTEGRITY_CRITICAL claim
- SPEC-006 apply does **not** call legacy order-first `paper_apply_fill`

Functional guard check:
- CONTIGUOUS + LONG/SHORT shadow without immutable ENTRY identity is demoted to INVALID with `FRONTIER_CYCLE_IDENTITY_CHANGED`

## Frontier / no catch-up

Implementation persists:
- `dispatch_frontier_5m_open_time`
- `dispatch_enable_commit_time`

Bootstrap, INVALID resume, recovery completion and new epoch all use the dispatch frontier.

Late historical bars:
- cannot create action
- cannot create ENTRY lifecycle
- cannot create exit intent
- cannot create dispatch outbox

Safe-enable conditions are re-evaluated at the frontier.

## Position ownership / flatten

- first ENTRY fill creates immutable paper-position ownership
- next exposure cycle gets a new position id
- REFERENCE_EXIT can claim only its linked ENTRY's position
- INVALID_RECOVERY cannot steal OPEN/PAUSED/CRITICAL ownership
- INTEGRITY_CRITICAL is durable, unique and non-auto-exiting
- one intent -> one EXIT order -> many attempts
- each EXIT attempt uses remaining locked base quantity
- each attempt binds a new immutable book snapshot/current lease
- no over-close / no flip

## Lock order

SPEC-006 economic path:
`execution fence -> shadow when needed -> position -> lower economic rows`

A machine test and DB-source audit verify SPEC-006 does not delegate economic apply to the legacy order-first `paper_apply_fill`.

## Blind protection

When B runtime is enabled in the future, operational surfaces collapse to coarse categories only.

Current runtime remains disabled, so existing SPEC-005 operational behavior is unchanged.

## Supabase advisors

No new SPEC-006 WARN/ERROR security finding.

The remaining SECURITY WARN is pre-existing:
`public.rls_auto_enable()`

RLS-without-policy INFO is intentional for server-only tables whose anon/authenticated privileges are revoked.

Performance advisor reports no unresolved new SPEC-006 FK-index requirement.

## Vercel

A previous preview smoke returned HTTP 500 because Preview had no Supabase credentials and the runtime-binding helper fell into LocalStore.

Fixed:
`_paper_runtime_binding()` now returns unbound/None when durable Supabase is unavailable.

The exact Vercel-like no-Supabase smoke is now covered by CI and passes.

Vercel is currently rate-limiting additional preview builds; this is a platform build-rate limit, not an application failure.

## Merge gate

Do not merge PR #42 until:
- latest CI remains green
- independent implementation review returns CLEAN ENOUGH TO MERGE WITH RUNTIME B DISABLED

Do not enable runtime B as part of merge.

Activation remains a separate post-merge validation gate.
