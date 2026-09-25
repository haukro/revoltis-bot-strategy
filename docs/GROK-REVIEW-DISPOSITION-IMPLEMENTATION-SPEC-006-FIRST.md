# Grok Adversarial Review Disposition — IMPLEMENTATION-SPEC-006

Status: **FIRST REVIEW APPLIED — SECOND REVIEW REQUIRED**

No SPEC-006 production code was implemented before this review.

## ACCEPTED

Accepted as blocking:

- same-bar ENTRY/EXIT orphan risk
- EXIT_TO_FLAT must create a durable paper flatten intent
- in-flight ENTRY must be fenced once flatten intent exists
- paper already OPEN on reference ENTRY => CRITICAL/no second entry
- mid-position INVALID cannot invent a B-linked exit
- INVALID recovery must be explicit and non-emitting for missed bars
- reference action + shadow + cursor must commit atomically
- exact server-generated action idempotency + unique constraint
- one EXIT intent may have many execution attempts but at most one EXIT order
- blind surfaces must remain category-only once B runtime is enabled

## IMPLEMENTED IN SPEC V2

The revised draft now adds:

- two-plane architecture: reference plane vs paper plane
- data_state CONTIGUOUS/INVALID separate from reference_position_state FLAT/LONG/SHORT
- strategy execution fence row
- exact canonical strategy-action key
- execution_outbox STRATEGY_ACTION_DISPATCH for durable paper dispatch
- paper_exit_intents with OPEN/PAUSED/SATISFIED/CRITICAL lifecycle
- atomic per-5m reference transition transaction
- same-bar ENTRY+EXIT atomic handling
- frozen-B differential parity tests against simulate_b_v1
- ENTRY dispatch fencing
- ENTRY fill fencing against committed exit intent
- paper-open-on-reference-entry CRITICAL/no-stack rule
- durable flatten-intent processing
- same EXIT order with multiple immutable attempts for partial exits
- sticky INVALID semantics
- verified replay recovery with suppressed historical paper actions
- new adapter epoch only at verified future FLAT boundary with paper account flat
- category-only blind operational surfaces while B runtime is enabled

## DISAGREED / MODIFIED

No substantive disagreement.

One structural refinement:

- INVALID is modeled separately from reference position state. A data gap sets data_state=INVALID while preserving last valid FLAT/LONG/SHORT state. This avoids erasing theoretical position state at the exact moment recovery needs it.

## NOT CHANGED

- no alpha change
- no tsmom_b_v1.py change
- no TEST-SPEC-002/003 change
- no official B metrics
- no runtime binding activation
- no live routing

## NEXT STEP

Run a second Grok adversarial review against SPEC-006 v2.

Do not lock or implement SPEC-006 until that second review returns CLEAN ENOUGH TO LOCK.
