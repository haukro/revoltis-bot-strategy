# GROK LOCK-GATE REVIEW — IMPLEMENTATION-SPEC-006 V3

Do not ask follow-up questions.
Do not change alpha.
Do not reopen TEST-SPEC-002/003.
Do not propose parameter tuning.
Do not enable runtime B.

PROJECT STATE

TEST-SPEC-002 ZEC TSMOM remains frozen and blind until 2026-12-23 11:59:59.999 UTC.
SPEC-004/005 paper infrastructure remains implemented and validated.
TEST-SPEC-002 runtime binding remains DISABLED.

THIS REVIEW IS ONLY FOR THE PATCHED SPEC-006 DRAFT.

The previous review required these fixes, all now incorporated in the draft:

1. fence only OPEN/PAUSED exit intents
2. one global lock order: fence -> shadow -> paper position -> order/fill/reservation
3. same-bar ENTRY lifecycle = NEVER_CREATED_FENCED; no paper ENTRY order
4. SATISFIED requires qty=0 + terminal/fenced ENTRY lifecycle + no in-flight ENTRY economic effect
5. new reference ENTRY during an existing live flatten is fenced, not CRITICAL
6. INVALID preserves reference position and stops reference actions only; existing paper flatten may continue
7. INVALID replay is internal/frozen-B-only with no delayed paper dispatch
8. recovery-only reduce-only flatten exists when recovered reference is FLAT and paper remains open
9. one EXIT intent -> one EXIT order -> many attempts; every attempt uses remaining locked base qty and a new immutable book snapshot
10. blind surfaces are category-only and redact B-linked identifiers/codes/payloads
11. recovered LONG/SHORT may resume same epoch only if the recovered open cycle is provably the same pre-gap cycle; same-side alone is insufficient
12. INVALID_RECOVERY is schema-valid without fabricating a reference EXIT action, has a canonical unique recovery_key, and cannot SATISFY while any old ENTRY lifecycle/order/attempt can still create exposure

LOCK-GATE FOCUS

Try to break the draft specifically on:

- same-bar and cross-transaction ENTRY/EXIT races
- terminality of ENTRY lifecycles and delayed outbox retries
- SATISFIED predicates for both REFERENCE_EXIT and INVALID_RECOVERY
- historical SATISFIED/CRITICAL intents vs future valid cycles
- lock-order deadlocks across reference, ENTRY fill, flatten, and recovery paths
- one intent / one order / many attempts under partial fills, stale books, lease loss, crashes, retries
- side mismatch and CRITICAL handling
- exact 5m cursor atomicity and concurrent adapters
- INVALID replay drift, including hidden EXIT->same-side ENTRY during the gap
- recovery flatten duplication and late ENTRY reopening
- blind leakage through APIs, logs, outbox payloads, constraint errors, ids, exact timestamps
- any path that writes or contaminates official TEST-SPEC-002 state, metrics, scoring, or tsmom_b_v1.py

DO NOT LOWER THE BAR BECAUSE THIS IS A DRAFT.

RETURN EXACTLY

1. BLOCKING FLAWS
2. NON-BLOCKING CAVEATS
3. SAME-BAR / ENTRY-FENCE RISKS
4. DURABLE EXIT-INTENT RISKS
5. SHADOW / ATOMICITY RISKS
6. INVALID RECOVERY RISKS
7. BLIND / OFFICIAL-B ISOLATION RISKS
8. REQUIRED CHANGES BEFORE LOCK
9. THINGS ALREADY CORRECT — DO NOT CHANGE
10. LOCK VERDICT:
   - CLEAN ENOUGH TO LOCK
   - NOT CLEAN ENOUGH TO LOCK
11. HANDOFF FOR CHATGPT

If there is any unresolved ambiguity that could permit duplicate exposure, delayed exposure, reference drift, deadlock, duplicate flattening, official-B contamination, or blind leakage, verdict must be NOT CLEAN ENOUGH TO LOCK.
