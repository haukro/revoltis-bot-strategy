# GROK INDEPENDENT LOCK-GATE REVIEW — IMPLEMENTATION-SPEC-006 V4

Do not ask follow-up questions.
Do not change alpha.
Do not reopen TEST-SPEC-002/003.
Do not propose parameter tuning.
Do not enable runtime B.

PROJECT STATE

- TEST-SPEC-002 ZEC TSMOM remains frozen and blind through 2026-12-23 11:59:59.999 UTC.
- SPEC-004/005 paper infrastructure remains implemented and validated.
- TEST-SPEC-002 runtime binding remains DISABLED.
- SPEC-006 is still a draft and must not be implemented or locked unless this review returns CLEAN ENOUGH TO LOCK.

THE DRAFT NOW INCLUDES ALL PRIOR FIXES PLUS NEW LOCK-GATE HARDENING

1. OPEN/PAUSED intents only fence future ENTRY; SATISFIED/CRITICAL do not become permanent pair fences.
2. Global lock hierarchy: execution fence -> shadow when needed -> paper position -> lower economic rows.
3. SPEC-006 B economic apply must not delegate to a legacy RPC if that RPC locks outbox/order before paper position; all SPEC-004/005 lease/stale-snapshot/idempotency rules still apply.
4. Explicit durable ENTRY lifecycle including NEVER_CREATED_FENCED.
5. SATISFIED requires qty=0 plus no fill-capable ENTRY lifecycle/order/attempt/reservation/dispatch.
6. New reference ENTRY during an older live flatten is fenced, not CRITICAL merely because old paper qty still exists.
7. Durable paper-position ENTRY ownership proves which reference ENTRY actually created each paper position.
8. REFERENCE_EXIT may reduce only the paper position owned by its linked_entry_action_id.
9. A newer fenced reference cycle that later emits EXIT cannot claim an older cycle's paper position.
10. Every flatten intent may claim at most one paper position; a partial unique claimed_position_id invariant prevents two OPEN/PAUSED/CRITICAL intents from owning the same position.
11. One intent -> one EXIT order -> many attempts; every attempt uses remaining locked current base qty and a new immutable book snapshot.
12. REFERENCE_EXIT reduce side is derived from reference side; INVALID_RECOVERY reduce side is locked from actual current paper side.
13. INVALID stops reference actions only; an already-live paper flatten continues if global kill-switch permits.
14. INVALID replay is internal frozen incremental B only, persists reference state/cursor only, emits no delayed historical paper dispatch and never copies official B output.
15. Recovery LONG/SHORT may resume same epoch only if recovered cycle identity is provably the same pre-gap cycle and paper position ownership matches it.
16. Recovery FLAT + paper open first defers to any existing live/CRITICAL owner claim; otherwise one gap-scoped INVALID_RECOVERY intent may claim and reduce the position.
17. INVALID_RECOVERY recovery_key identifies the gap only and deliberately excludes recovery cutoff, so different workers cannot create multiple intents for the same gap.
18. INVALID_RECOVERY cannot SATISFY while any old same-pair ENTRY path can still create exposure.
19. Initial runtime bootstrap is defined: internal frozen-B replay only, no historical paper actions. If bootstrap ends open, binding stays disabled until a verified future FLAT boundary. Dispatch begins only on subsequent future 5m bars.
20. Exact bootstrap cutoff / activation timestamp is blind-hidden because it can reveal a reference FLAT boundary.
21. Blind surfaces remain category-only and redact B-linked ids, codes, payloads, constraint errors, pair/side, exact cursor/action timing and all performance.
22. Official TEST-SPEC-002 runner, scoring, fold, parameters, costs, tsmom_b_v1.py and partial official metrics remain untouched.

TRY TO BREAK THESE AREAS

- same-bar ENTRY+EXIT and outbox replay
- ENTRY fill vs EXIT-intent commit ordering
- multiple reference cycles while an older paper flatten is still live
- cross-cycle position ownership
- two intents or recovery workers attempting to flatten the same position
- CRITICAL position claims
- partial EXIT / stale snapshot / lease loss / retry / crash
- legacy SPEC-004 SQL lock order accidentally reintroduced inside SPEC-006
- SATISFIED too early for REFERENCE_EXIT or INVALID_RECOVERY
- INVALID replay crossing EXIT -> same-side ENTRY
- first activation/bootstrap in the middle of an already-running blind fold
- blind leakage from activation timing, ids, error text, queue/recon surfaces or logs
- any write/read path that contaminates official B evaluation or uses official B output as a repair feed

RETURN EXACTLY

1. BLOCKING FLAWS
2. NON-BLOCKING CAVEATS
3. SAME-BAR / ENTRY-FENCE RISKS
4. DURABLE EXIT-INTENT / POSITION-OWNERSHIP RISKS
5. LOCK-ORDER / ATOMICITY RISKS
6. INVALID RECOVERY / BOOTSTRAP RISKS
7. BLIND / OFFICIAL-B ISOLATION RISKS
8. REQUIRED CHANGES BEFORE LOCK
9. THINGS ALREADY CORRECT — DO NOT CHANGE
10. LOCK VERDICT:
   - CLEAN ENOUGH TO LOCK
   - NOT CLEAN ENOUGH TO LOCK
11. HANDOFF FOR CHATGPT

If any ambiguity still permits duplicate exposure, delayed exposure, two flatten owners for one position, lock-order inversion, reference drift, unsafe bootstrap, official-B contamination, or blind leakage, verdict must be NOT CLEAN ENOUGH TO LOCK.
