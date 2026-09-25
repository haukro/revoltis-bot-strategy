# GROK INDEPENDENT LOCK-GATE REVIEW — IMPLEMENTATION-SPEC-006 V5

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

V4 VERDICT WAS NOT CLEAN ENOUGH TO LOCK.

All four V4 blockers were accepted and patched:

1. BOOTSTRAP HAS NO HISTORICAL CATCH-UP DISPATCH
   - cutoff = latest fully closed exact 5m at bootstrap commit wall time
   - older FLAT can never become activation cursor
   - all bars <= cutoff are permanently non-dispatchable
   - if cutoff FLAT: enable after commit, first dispatch-eligible bar = first newly closed expected 5m strictly after cutoff
   - if cutoff LONG/SHORT: remain unbound and consume only subsequent newly closed bars internally until newly observed FLAT
   - those internally consumed unbound bars remain non-dispatchable
   - dispatch begins only on the next newly closed expected 5m after that FLAT

2. EACH PAPER EXPOSURE CYCLE HAS A NEW POSITION ID
   - CLOSED paper_positions.id is permanently retired
   - never reopen a CLOSED row
   - later ENTRY cycle inserts new paper_positions.id
   - new id gets new immutable ownership row
   - REFERENCE_EXIT ownership remains per exact ENTRY cycle

3. CRITICAL WITH LIVE QTY MUST OWN THE POSITION
   - CRITICAL transition with qty>0 must already own or atomically acquire claimed_position_id
   - new CRITICAL-with-live-qty + null claim is forbidden
   - if another intent owns the position, no claim steal / no second owner / no second EXIT order
   - malformed historical CRITICAL null-claim state blocks INVALID_RECOVERY until explicit ownership repair

4. WRONG-CYCLE GAP RECOVERY IS AN EXPLICIT SEQUENCE
   - wrong-cycle LONG/SHORT stays INVALID
   - continue frozen incremental replay internally only
   - no delayed ENTRY/flip
   - wait until later verified reference FLAT
   - then §14.3: if paper still open, defer to existing owner or use one INVALID_RECOVERY flatten
   - only after paper is safely flat and no late-entry effect remains may §14.4 start a new epoch if needed

ALREADY-HARDENED INVARIANTS TO RETEST

- same-bar ENTRY + EXIT_TO_FLAT + NEVER_CREATED_FENCED + durable outbox
- outbox retry checks lifecycle before order creation
- live fence = OPEN/PAUSED only
- SATISFIED/CRITICAL do not permanently fence future cycles
- ENTRY fill apply re-takes execution fence before economic effect
- explicit ENTRY lifecycle; missing lifecycle is never terminal
- SATISFIED requires qty=0 + no fill-capable ENTRY path
- one intent -> one EXIT order -> many attempts
- every attempt uses remaining locked current base qty
- new immutable book + current lease on every attempt
- durable paper-position ENTRY ownership
- one OPEN/PAUSED/CRITICAL flatten claim per paper position
- REFERENCE_EXIT may act only on the position owned by linked_entry_action_id
- INVALID_RECOVERY has gap-identity recovery_key excluding replay cutoff
- global lock hierarchy = fence -> shadow when needed -> position -> lower economic rows
- SPEC-006 economic apply must not delegate to legacy SPEC-004 order-first apply RPC
- INVALID stops reference actions only; live paper flatten can continue
- recovery persists reference/cursor only; no historical paper dispatch
- same-cycle LONG/SHORT recovery requires exact cycle ownership, not same side
- blind surfaces are category-only and hide exact bootstrap/activation/cursor/action timing
- official B runner/scoring/fold/parameters/costs/tsmom_b_v1.py remain untouched
- synthetic parity only
- runtime B remains DISABLED

TRY TO BREAK SPECIFICALLY

1. Can any bootstrap path dispatch a bar that closed before binding was enabled?
2. Can a CLOSED paper_positions.id ever be reused, directly or indirectly?
3. Can any live qty coexist with CRITICAL intent and no enforceable owner claim?
4. Can INVALID_RECOVERY create an order while a CRITICAL/OPEN/PAUSED owner already controls that exposure?
5. Can hidden EXIT -> same-side ENTRY during INVALID ever cause old paper to be treated as the new cycle?
6. Can two intents across different reference cycles create EXIT orders for one position?
7. Can SATISFIED occur before all possible delayed ENTRY/EXIT economic effects are impossible?
8. Can any SPEC-006 SQL path invert fence -> position -> lower-row lock order by calling a legacy RPC?
9. Can bootstrap/recovery leak a reference FLAT boundary through exact timestamps, status transitions, logs or queue/recon details?
10. Can any recovery/bootstrap code call official evaluation/scoring or use official B output as repair state?

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

If any ambiguity still permits delayed exposure, reused position identity, unclaimed CRITICAL exposure, duplicate flatten ownership, lock-order inversion, reference drift, unsafe bootstrap, official-B contamination, or blind leakage, verdict must be NOT CLEAN ENOUGH TO LOCK.
