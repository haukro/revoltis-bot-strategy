# SPEC-005 Recovery / Outbox Slice — Validation Result

Status: **IMPLEMENTED AND VALIDATED**

No alpha logic or live routing was added.

## Implemented

- immutable runtime binding of strategy version -> risk/execution policy
- TAKER fee policy required for paper runtime binding
- immutable/idempotent market snapshot ingestion
- create-order serialized against stale-reservation recovery
- stale-reservation release serialized with portfolio risk state
- safe recovery-candidate detector
- kill-switch cancellation recovery
- order-timeout recovery
- exact Decimal book walking
- internal server-only outbox worker
- internal server-only recovery worker
- OKX two-sided book fetch with canonical hash
- current-generation ACK fencing

## Direct Supabase validation

### Runtime binding

- initial binding created
- enabled toggle for same policy allowed
- replacing the bound risk policy rejected with `runtime_binding_policy_locked`

### Snapshot ingest

- same provider/pair/timestamp/hash returns the same immutable snapshot row

### Kill-switch recovery

Synthetic transaction:

- approved risk
- reservation
- accepted order
- HALTED
- recovery candidates included the unfilled order
- recovery terminalized order as CANCELLED
- zero fills were created
- remaining reservation released
- reserved portfolio exposure returned to zero

Result: **PASS**

### Order timeout and live lease

Synthetic transaction:

- order aged beyond max_order_age_ms
- BIND_FILL_ATTEMPT held an unexpired lease
- recovery candidate correctly excluded the order
- direct timeout recovery returned UNEXPIRED_EXECUTION_LEASE
- after lease expiry, candidate appeared
- timeout recovery terminalized order as EXPIRED

Result: **PASS**

## Operational requirements before workers are scheduled

Workers remain fail-closed until configuration exists for:

- active ops policy
- max_worker_batch
- market_book_depth
- PAPER_OUTBOX_LEASE_SECONDS
- PAPER_SCHEDULER_TOKEN
- at least one enabled paper_strategy_runtime_binding

No TEST-SPEC-002 runtime binding is created by this slice.
