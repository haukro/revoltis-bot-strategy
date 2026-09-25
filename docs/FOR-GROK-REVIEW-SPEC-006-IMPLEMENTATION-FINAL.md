# GROK ADVERSARIAL IMPLEMENTATION REVIEW — LOCKED IMPLEMENTATION-SPEC-006 — FINAL PR #43

Do not ask follow-up questions.
Do not change alpha.
Do not reopen or reinterpret locked IMPLEMENTATION-SPEC-006.
Do not modify TEST-SPEC-002/003 or `backend/app/tsmom_b_v1.py`.
Do not propose parameter tuning.
Do not enable runtime B.

## LOCKED STATE

Canonical spec:
`docs/IMPLEMENTATION-SPEC-006-b-paper-adapter.md`

Lock verdict:
Grok V6 = **CLEAN ENOUGH TO LOCK**

Implementation under review:
- branch: `implement-spec006-paper-adapter`
- draft PR: **#43**

Superseded PR #42 is closed.

TEST-SPEC-002 runtime binding:
- live Supabase row count = **0**
- enabled binding count = **0**

Frozen `backend/app/tsmom_b_v1.py` Git blob SHA is identical on main and PR:
`80c0cc7c3551ea5745be4cff8af5e32df04cd942`

## FINAL DELTAS BEYOND THE EARLIER IMPLEMENTATION DRAFT

PR #43 carries the validated Python/test implementation plus the exact live Supabase migration history through migration 027.

Additional hardening in this final branch:

1. EXIT attempt quantity is captured under execution-fence + paper-position lock and stored in outbox payload as `remaining_base_qty`.
2. Worker sizes SPEC-006 EXIT from that locked payload. It does not perform an unlocked position-qty re-read for sizing.
3. Partial EXIT apply queues the next attempt on the same order/intent with the newly remaining base qty.
4. Disabled/missing runtime binding terminally fences queued SPEC-006 ENTRY work and ACKs it; it cannot open after a future re-enable.
5. Durable SPEC-006 EXIT/recovery flatten is not cancelled merely because the runtime binding is not enabled; if a binding policy row exists, paper flatten execution may continue.
6. Missing execution fee policy on SPEC-006 EXIT keeps the outbox retryable instead of terminally destroying the durable flatten.
7. Bootstrap and recovery rebuild frozen B indicators from the exact official ZEC warmup origin: **24h before fold start**, not a rolling 30h window. This avoids Wilder ATR path drift.
8. CI contains a migration-source machine check that SPEC-006 apply never delegates to legacy `paper_apply_fill`, and migration 027 binds `remaining_base_qty`.

## VERIFIED IMPLEMENTATION FACTS

### CI

Latest authoritative PR CI:
`36125858781`

- backend: **124 passed**
- frontend production build: **passed**
- only one unrelated Starlette/anyio deprecation warning

Vercel exact-head preview is currently prevented by platform build-rate limiting; this is not an application build failure.

### Frozen reference parity

Synthetic only.

Coverage includes:
- LONG same-bar initial stop
- SHORT same-bar initial stop
- gap-through timestamp/price
- chandelier touch
- no retroactive same-bar stop tighten
- ignored signals while reference open
- deterministic action replay

No live blind ZEC forward data is used as a parity fixture.

### DB / lock order

Live Supabase verifies:
- `paper_exit_intents_live_position_claim_unique` present
- `paper_positions_reject_reopen_trg` present
- `paper_spec006_apply_fill` does not call legacy `paper_apply_fill`
- `paper_spec006_queue_exit_attempt` contains locked `remaining_base_qty`
- all `paper_spec006_*` functions are non-executable by anon/authenticated

Economic path remains:
`execution fence -> shadow when required -> paper position -> lower economic rows`

### ENTRY / same-bar / claim

- same-bar ENTRY+EXIT persists `NEVER_CREATED_FENCED`
- lifecycle terminal check remains before order creation
- ENTRY fill apply re-takes SPEC-006 fence
- cross-transaction open paper creates/reuses one INTEGRITY_CRITICAL claim
- CRITICAL ownership fences residual ENTRY growth before claim commit
- disabled binding terminally fences queued SPEC-006 ENTRY action/risk work rather than leaving delayed exposure retryable

### EXIT / partial

- one intent -> one EXIT order -> many attempts
- each attempt has new immutable book snapshot/current lease
- EXIT is base-qty
- initial attempt qty is current locked position qty
- continuation attempt qty is newly remaining locked qty
- no original intended_quantity sizing on later attempts
- no over-close / no flip
- durable flatten survives retry/configuration faults

### Frontier / recovery

- bootstrap/recovery/new-epoch frontier is persisted
- recovery RPC requires cutoff == latest verified reference-bar frontier at enable commit
- historical cutoff is not a live dispatch cursor
- late historical bars are non-emitting
- same-cycle LONG/SHORT recovery requires exact ownership
- wrong-cycle waits to FLAT then recovery-flattens if needed
- recovery replay starts from official frozen 24h ZEC warmup origin so Wilder ATR remains path-consistent
- official B output/scoring is never used as repair state

### Blind / official isolation

- category-only blind projections when B runtime is active
- exact action/cursor/frontier/activation timing hidden
- ids, pair/side, B reason codes and performance hidden
- official B runner/scoring/fold/costs/parameters untouched
- `tsmom_b_v1.py` unchanged

## SUPABASE ADVISOR RESULT

No new SPEC-006 security WARN/ERROR.

Only existing external security WARN:
`public.rls_auto_enable()` — outside SPEC-006.

RLS-no-policy INFO is intentional for server-only tables with public/auth access revoked.

## YOUR TASK

Review PR #43 implementation against the **locked SPEC only**.

Try to produce a concrete execution path for any of these:

1. historical/delayed ENTRY after binding disable/re-enable
2. late pre-enable candle creating action/lifecycle/intent/outbox
3. frozen-B ATR/reference drift caused by bootstrap/recovery history
4. ENTRY economic effect after OPEN/PAUSED/CRITICAL ownership should stop it
5. two flatten owners or two EXIT orders against one paper position
6. cross-cycle position ownership theft
7. CLOSED position reuse
8. EXIT sizing from stale/original quantity rather than locked remaining base qty
9. partial EXIT ACK with no continuation
10. stale lease/snapshot economic effect
11. lock inversion through legacy RPC
12. SATISFIED while ENTRY/EXIT effect can still arrive
13. recovery resume from historical cursor rather than frontier
14. blind leakage through normal API/log/error surfaces
15. official B scoring/output used by bootstrap/recovery
16. any code path that configures/enables TEST-SPEC-002 runtime B before post-merge activation gate

A BLOCKING flaw must show a concrete path to wrong exposure, duplicate economic effect, reference drift, deadlock, official-B contamination, or blind leakage.

Do not fail for style, comments, naming, optional optimization, Vercel rate-limit state, or absence of runtime activation.

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

Runtime B must remain DISABLED/UNBOUND regardless of merge verdict. Activation is a separate later gate.
