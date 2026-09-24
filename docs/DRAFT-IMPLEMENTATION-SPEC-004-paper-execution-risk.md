# IMPLEMENTATION-SPEC-004 — Strategy-Neutral Paper Execution & Risk Engine

Status: **DRAFT FOR GROK ADVERSARIAL REVIEW — NOT LOCKED, NOT IMPLEMENTED**

Purpose: define the strategy-neutral paper execution, risk, reconciliation and audit infrastructure that can later support multiple strategies without altering their alpha logic.

This spec does not change TEST-SPEC-002, Strategy A, or closed TEST-SPEC-003.

## 0. Core invariants

1. Signals are immutable.
2. Infrastructure may approve/reject a locked strategy signal, but may not silently resize, delay, reverse, or rewrite it.
3. Last traded price is never treated as executable price.
4. Critical trading state must be durably persisted before downstream action.
5. No critical event may exist only in local process memory.
6. Retries/restarts must be idempotent.
7. Missing/stale/unverifiable execution data fail closed.
8. Position accounting is derived from fills.
9. Strategy-specific exits remain in strategy logic; infrastructure does not invent alpha exits.
10. Blind forward-test performance stays inaccessible before blind_until.
11. No live real-money routing is included in this implementation phase.

## 1. Architecture

Components:

1. Signal Ingestor
2. Durable Event Outbox / Claim Queue
3. Pre-Trade Risk Engine
4. Paper Order State Manager
5. Deterministic Fill Simulator
6. Position Accounting Engine
7. Portfolio Risk Monitor
8. Kill-Switch State Manager
9. Reconciliation Engine
10. Recovery/Repair Worker
11. Market-Data Health Monitor
12. Immutable Audit Writer
13. Blind-Test Policy Layer
14. Read-only Operational API/UI

All components are strategy-neutral.

## 2. Signal model

Table/entity: `paper_signals`

Required immutable fields:

- id
- signal_id
- strategy_id
- strategy_version_id
- test_spec_id nullable
- pair
- side
- signal_close_time
- intended_entry_time
- intended_notional
- source_timeframe
- execution_timeframe
- reason_code
- rule_id
- correlation_id
- blind_test_id nullable
- generated_at
- persisted_at
- idempotency_key
- software_commit

For the repository timestamp model:

`intended_entry_time = signal_close_time + 1 ms`

Example:

`21:59:59.999Z -> 22:00:00.000Z`

Signals are append-only.

Unique:

`UNIQUE(idempotency_key)`

Duplicate submission returns the existing signal and performs no downstream duplicate action.

## 3. Durable queue / transactional outbox

Use Postgres/Supabase as the durable queue.

Table: `execution_outbox`

Fields:

- id
- event_type
- entity_type
- entity_id
- correlation_id
- payload
- available_at
- claimed_at nullable
- claimed_by nullable
- attempt_count
- processed_at nullable
- last_error nullable
- idempotency_key
- created_at

Unique:

`UNIQUE(idempotency_key)`

Workers claim rows atomically with database-side transaction/RPC using:

`FOR UPDATE SKIP LOCKED`

No critical event is queued only in process memory.

If Supabase cannot persist the source event + outbox row atomically, processing fails closed.

## 4. Risk decision model

Table: `risk_decisions`

One immutable final pre-trade decision per signal:

- APPROVED
- REJECTED

Unique:

`UNIQUE(signal_id, decision_stage)`

For locked strategies, risk may not change:

- side
- pair
- entry time
- notional
- alpha rule

It may only approve or reject.

Risk reasons are explicit reason codes.

## 5. Paper order state machine

Order states:

- CREATED
- ACCEPTED
- PENDING_FILL
- PARTIALLY_FILLED
- FILLED
- CANCEL_REQUESTED
- CANCELLED
- REJECTED
- EXPIRED
- FAILED

Risk approval exists before order creation; therefore `RISK_CHECKED` is not an order state.

Allowed transitions:

- CREATED -> ACCEPTED
- CREATED -> REJECTED
- ACCEPTED -> PENDING_FILL
- ACCEPTED -> CANCEL_REQUESTED
- PENDING_FILL -> PARTIALLY_FILLED
- PENDING_FILL -> FILLED
- PENDING_FILL -> CANCEL_REQUESTED
- PENDING_FILL -> EXPIRED
- PENDING_FILL -> FAILED
- PARTIALLY_FILLED -> FILLED
- PARTIALLY_FILLED -> CANCEL_REQUESTED
- PARTIALLY_FILLED -> FAILED
- CANCEL_REQUESTED -> CANCELLED
- CANCEL_REQUESTED -> FILLED only if the remaining quantity was already deterministically filled before cancellation became effective

Terminal:

- FILLED
- CANCELLED
- REJECTED
- EXPIRED
- FAILED

Every transition creates an immutable `paper_order_events` row.

Illegal transition => no mutation; emit critical audit/reconciliation issue.

## 6. Fill simulator v1

Paper market fills are deterministic.

Every candidate fill must reference an immutable persisted market snapshot.

Table: `execution_market_snapshots`

Fields:

- id
- pair
- provider
- provider_ts
- received_at
- bid_levels JSONB
- ask_levels JSONB
- best_bid
- best_ask
- snapshot_hash
- software_commit
- created_at

Unique snapshot identity is based on provider/pair/provider_ts/snapshot_hash.

Market order fill:

BUY:
- walk asks from best price outward
- consume actual base quantity until quote notional is filled

SELL:
- walk bids from best price outward

Compute:

- fill quantity
- VWAP
- spread
- depth consumed
- impact vs best price
- fee separately

No stochastic slippage.
No volatility penalty.
No last-price fill.

If depth cannot support required notional under the persisted snapshot:
- partial fill only if execution policy explicitly permits partial fill
- otherwise REJECT/FAILED with reason `INSUFFICIENT_BOOK_DEPTH`

No 5m candle-volume percentage rule.

Each fill references:

- order_id
- market_snapshot_id
- fill sequence
- fill quantity
- fill price
- fee
- spread_bps
- impact_bps
- filled_at
- idempotency_key

## 7. Market-data validity policy

Numerical thresholds such as max book age are configuration, not hard-coded architecture constants.

Required policy fields:

- max_book_age_ms
- max_signal_age_ms
- max_order_age_ms
- max_spread_bps nullable
- min_depth_multiple nullable

Before paper execution begins for a strategy, the active execution-policy version must be frozen.

If the current snapshot violates the active policy:
- no optimistic fallback
- reject/expire order
- audit exact reason

Missing exact required entry-time execution data:
- no delayed substitute fill unless the strategy/execution spec explicitly allows it
- default = reject/invalid execution attempt

## 8. Position accounting engine

Table: `paper_positions`

The position engine owns accounting state only.

It does NOT calculate strategy-specific entry/exit signals.

Required fields:

- id
- strategy_version_id
- pair
- side
- quantity
- average_entry_price
- gross_entry_notional
- cumulative_fees
- cumulative_execution_cost
- realized_pnl
- unrealized_pnl nullable/cache
- status
- opened_at
- updated_at
- closed_at nullable
- state_version

Allowed statuses:

- OPENING
- OPEN
- EXIT_PENDING
- CLOSING
- CLOSED
- RECONCILIATION_ERROR

Position state is reconstructed from immutable fills.

Use optimistic state_version plus DB locking/RPC for atomic updates.

Partial unique constraint:

only one active position per `strategy_version_id, pair` for strategies declared max_open=1.

Strategy-provided stop metadata may be stored, but stop generation remains outside this engine.

## 9. Risk policy

Table: `risk_policy_versions`

Risk configuration is versioned and immutable once activated.

Configurable policy fields:

- max_position_notional
- max_total_gross_exposure
- max_net_exposure
- max_asset_exposure
- max_concurrent_positions
- max_daily_realized_loss
- max_intraday_drawdown
- max_book_age_ms
- max_signal_age_ms
- max_order_age_ms
- max_spread_bps nullable
- min_book_depth_multiple nullable
- abnormal_slippage_bps nullable

Architecture does not lock arbitrary percentages or bps.

Hard invariants independent of policy values:

- duplicate order/fill blocked
- execution blocked on unverifiable market data
- execution blocked while kill switch active
- execution blocked on unresolved critical reconciliation state
- no hidden leverage
- no execution without durable persistence

Every decision stores:
- policy_version_id
- observed values
- rule result
- reason code

## 10. Kill switch

Global states:

- RUNNING
- HALT_NEW_ENTRIES
- HALTED
- RECOVERY_PENDING

Activation actions:

1. reject all new signals/orders
2. cancel unfilled paper orders where cancellation is deterministic
3. prevent new fills from being invented
4. mark open positions as risk-frozen for operational control

Do NOT automatically fabricate market liquidation during stale/missing-data conditions.

Emergency liquidation is a separate explicit policy:
- must be enabled/configured separately
- requires valid executable market data
- creates normal audited exit orders/fills

Automatic kill-switch trigger classes:

- persistence unavailable for critical write
- duplicate fill detected
- ghost fill
- impossible position quantity/side
- critical reconciliation failure
- audit integrity failure
- explicitly configured portfolio-risk breach
- explicitly configured market-data outage rule

Manual activation requires authenticated operator action and reason.

Reset:
- no automatic reset
- health checks pass
- reconciliation passes
- audit chain/checkpoint passes
- operator explicitly resets to RECOVERY_PENDING
- separate explicit enable returns to RUNNING

## 11. Reconciliation vs recovery

Reconciliation is read-only detection.

It may:
- emit issues
- escalate severity
- activate strategy/global halt

It may NOT directly repair orders/positions.

Recovery/Repair Worker performs an explicit, idempotent, audited action only after:
- issue identified
- repair rule exists
- repair action has unique idempotency key

Critical examples:

- fill without order -> HALTED
- position qty != fill-derived qty -> HALTED
- duplicate active position -> HALTED
- invalid audit hash/checkpoint -> HALTED

Warning examples:

- signal with no risk decision while still within processing SLA
- order awaiting worker claim but within age policy

## 12. Idempotency and concurrency

Required unique constraints:

- paper_signals(idempotency_key)
- risk_decisions(signal_id, decision_stage)
- paper_orders(idempotency_key)
- paper_fills(idempotency_key)
- execution_outbox(idempotency_key)
- recovery_actions(idempotency_key)

Order processing:
- atomic claim with `FOR UPDATE SKIP LOCKED`
- only claimed worker may transition order

Position update:
- DB transaction/RPC locks active position row
- insert fill + apply position update atomically where possible
- retry uses same fill idempotency key

Risk capacity:
- serialize capacity reservation using account/portfolio risk-state row lock or DB advisory lock/RPC
- approved risk reservation is released/consumed deterministically by order terminal state

## 13. Immutable audit model

Table: `execution_audit`

Append-only.

Fields:

- event_id
- event_type
- entity_type
- entity_id
- correlation_id
- strategy_version_id
- market_time nullable
- received_at
- decision_at nullable
- persisted_at
- state_before nullable
- state_after nullable
- input_snapshot_hash nullable
- reason_code
- worker_id
- software_commit
- stream_key
- stream_sequence
- prev_hash nullable
- event_hash

Use per-stream hash chaining, e.g.:

`stream_key = correlation_id` or stable aggregate id.

Unique:
- `UNIQUE(stream_key, stream_sequence)`
- `UNIQUE(event_hash)`

DB role/trigger must reject UPDATE/DELETE on audit rows.

A periodic checkpoint/root may later summarize stream heads.

## 14. Blind-test protection

TEST-SPEC-002 remains blind until:

`2026-12-23 11:59:59.999 UTC`

Create `blind_test_policies`:

- blind_test_id
- strategy_version_id
- blind_until
- allowed_categories
- created_at
- locked_at

Before blind_until, normal APIs must not expose reconstructable performance.

Allowed operational projections:

- service health
- market-data freshness
- missing bar/gap counts
- queue backlog
- worker heartbeat
- audit completeness
- reconciliation health
- kill-switch state
- number of pipeline events only if it does not expose prices/sides/results

Hidden:

- realized/unrealized PnL
- fill prices where they enable performance reconstruction
- position entry price/current mark combination
- trade result list
- PF
- expectancy
- win rate
- long/short PnL
- return distribution
- PASS/FAIL proximity
- equity curve

Enforcement:

- performance endpoints reject blind strategy-version access before blind_until
- operational endpoints query blind-safe projections/views
- do not rely on client-side masking
- do not rely only on generic response-body stripping middleware

Official TEST-SPEC-002 evaluation remains separately hard-blocked by its existing runner.

## 15. Supabase schema additions

New tables:

- execution_outbox
- execution_market_snapshots
- paper_signals
- risk_decisions
- paper_orders
- paper_order_events
- paper_fills
- paper_positions
- paper_position_events
- risk_policy_versions
- portfolio_risk_state
- kill_switch_state
- kill_switch_events
- market_data_health
- execution_audit
- reconciliation_runs
- reconciliation_issues
- recovery_actions
- blind_test_policies

Reuse existing strategy identity/version records where possible rather than creating a competing strategy-version system.

All immutable event tables require restrictive UPDATE/DELETE policy.

## 16. API surface v1

Operational / write:

- `POST /api/paper/signals`
- `POST /api/paper/kill-switch/activate`
- `POST /api/paper/kill-switch/reset`
- `POST /api/paper/reconciliation/run`

Read:

- `GET /api/paper/system-health`
- `GET /api/paper/market-health`
- `GET /api/paper/orders`
- `GET /api/paper/positions`
- `GET /api/paper/reconciliation`
- `GET /api/paper/execution-quality`

Blind-test strategy versions receive blind-safe projections automatically.

No live-order endpoint in SPEC-004.

## 17. Worker model

Because the current app is serverless-oriented, no correctness assumption may depend on one long-lived process.

Worker roles:

- signal intake / outbox writer
- execution claimant
- market-health updater
- reconciliation worker
- recovery worker

Correctness comes from durable DB state, unique constraints, idempotency keys and atomic claims.

A worker may die at any point and a replacement must safely continue.

## 18. Paper execution quality

Track:

- signal_to_order_latency_ms
- order_to_fill_latency_ms
- book_age_ms_at_decision
- spread_bps
- impact_bps
- intended_vs_fill_bps
- fill_ratio
- partial_fill_ratio
- rejection_rate
- stale_data_rejection_rate
- missed_execution_rate
- reconciliation_issue_rate
- data_gap_rate

Formulas must be side-aware.

These metrics are operational execution metrics, not alpha metrics.

For blind TEST-SPEC-002, ensure none of these views expose reconstructable strategy profitability.

## 19. Paper-to-live gate framework

Do not lock arbitrary numerical thresholds in SPEC-004.

Before any future live-readiness review, create a separate preregistered operational acceptance policy.

Required categories:

- data quality
- execution quality
- reliability
- risk
- auditability
- recovery

Absolute invariants that can be required now:

- zero duplicate orders
- zero duplicate fills
- zero unresolved critical reconciliation issues
- kill-switch tests pass
- restart recovery tests pass
- idempotency tests pass
- persistence-failure behavior tested fail-closed
- blind-test protection tests pass

Thresholds such as minimum paper days, uptime %, fill-model error and stale-data rate must be locked later before the live-readiness measurement period.

## 20. What is explicitly not included

Do not build yet:

- live real-money order submission
- strategy router
- adaptive strategy selection
- dynamic alpha-confidence sizing
- ML alpha optimization
- parameter tuning
- multi-exchange routing
- self-modifying risk rules
- automated emergency liquidation without explicit policy
- new alpha research inside this infrastructure PR

## 21. Required pre-implementation tests

Before merging implementation code, tests must cover:

1. duplicate signal -> one signal / one downstream order path
2. HTTP retry -> no duplicate order
3. worker crash after claim -> safe reclaim
4. duplicate fill insertion blocked
5. fill without order detected
6. missing/stale book -> no optimistic fill
7. insufficient depth -> reject/partial according to explicit policy
8. position reconstruction from fills
9. concurrent workers cannot open duplicate max-one positions
10. risk capacity locking under concurrency
11. DB unavailable -> fail closed, no local-memory execution
12. kill switch blocks new execution
13. kill switch does not invent liquidation fill without valid market data
14. reconciliation does not directly mutate state
15. recovery action is explicit/idempotent/audited
16. audit event immutability
17. audit stream sequence/hash verification
18. TEST-SPEC-002 blind APIs expose no performance/reconstructable price data
19. existing TEST-SPEC-002 smoke remains unchanged and passing
20. existing closed research results remain unchanged

## 22. Implementation order after lock

1. DB migrations + immutable/RLS/trigger rules
2. domain models
3. transactional outbox and atomic claim RPCs
4. market snapshot persistence
5. signal ingest + risk decision
6. order state machine
7. deterministic fill simulator
8. position accounting
9. kill-switch state manager
10. reconciliation + recovery separation
11. audit writer
12. blind-test policy/read projections
13. operational APIs
14. tests/failure injection
15. UI only after backend invariants pass
