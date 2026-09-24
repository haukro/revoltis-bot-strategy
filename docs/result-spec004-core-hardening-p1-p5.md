# SPEC-004 Core Hardening — Grok Post-Implementation Audit P1-P5

Status: **IMPLEMENTED AND VALIDATED**

This hardening patch changes no alpha logic and does not touch TEST-SPEC-002 or TEST-SPEC-003.

## Accepted blocking fixes

### P1 — Outbox ABA / split-brain
Implemented:

- `execution_outbox.lease_generation bigint`
- every successful claim increments `lease_generation`
- `paper_ack_outbox`, `paper_bind_execution_attempt`, and `paper_apply_fill` require the active lease generation
- stale worker / generation mismatch returns a no-op result
- execution attempts record `source_outbox_id` and `source_lease_generation`
- a reclaimed worker may take over the same logical attempt while the stale worker loses authority

### P2 — Exact replay vs different fill
Implemented/validated:

- exact canonical fill key replay returns `idempotent_replay=true`
- a different fill key against a terminal `FILLED` order is rejected by the order-state check
- exact replay remains economic no-op

### P3 — Reservation leak
Implemented:

- `risk_reservations.expires_at`
- risk approval requires a structural reservation TTL from the active policy
- order creation extends the reservation TTL to the order-age policy when configured
- terminal order outcomes release unconsumed reservation
- `paper_find_stale_reservations` is detection-only
- `paper_release_stale_reservation` is an explicit idempotent recovery action and writes reconciliation/recovery/audit records

### P4 — Partial reservation accounting
Implemented/validated:

- entry fill converts only actual filled quote notional from reserved -> used
- partial fill leaves the remainder reserved
- partial reservation state is `PARTIALLY_CONSUMED`
- terminal incomplete order releases only the remaining reserved amount
- entry fill above remaining reservation is rejected with `fill_exceeds_reserved_notional`

### P5 — SECURITY DEFINER EXECUTE privileges
Implemented/validated:

- all public functions matching `paper_%` revoke EXECUTE from `PUBLIC`, `anon`, and `authenticated`
- EXECUTE granted to `service_role` only
- applies to internal audit RPCs as well

## Direct Supabase validation

The following real database transaction smokes were run with rollback after validation.

### P1 lease generation / split-brain

- worker A claimed an outbox event at generation N
- claim was expired
- worker B reclaimed at generation N+1
- worker A bind => `STALE_LEASE`
- worker B bind => success
- worker A apply => `STALE_LEASE`
- worker B apply => exactly one fill
- worker A ack => not acknowledged
- worker B ack => acknowledged

Result: **PASS**

### P2 fill replay

- first fill committed
- exact same canonical fill replay => `idempotent_replay=true`
- different fill sequence/key on terminal FILLED order => `order_state_disallows_fill:FILLED`

Result: **PASS**

### P3 stale reservation

- APPROVED risk reservation created without an order
- expiry forced past for the smoke
- detection function found the stale reservation
- explicit recovery released it
- portfolio reserved gross returned to zero

Result: **PASS**

### P4 partial reservation

For 50 USDT reservation:

- first fill = 20 USDT
- reservation consumed = 20
- reserved exposure = 30
- used exposure = 20
- terminal failure released remaining 30
- used exposure remained 20 for the actual open partial position

Result: **PASS**

### P5 privileges

For every `public.paper_%` function checked:

- anon EXECUTE = false
- authenticated EXECUTE = false
- service_role EXECUTE = true

Result: **PASS**

## Deliberate design clarification

Reconciliation remains detection-only.

Stale-reservation mutation is performed only by the explicit recovery RPC, not by the reconciliation detector.

## Not changed

- no alpha changes
- no Strategy B changes
- no Strategy C changes
- no live trading
- no strategy router
- no parameter tuning
