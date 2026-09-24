# FOR GROK ADVERSARIAL REVIEW — IMPLEMENTATION-SPEC-004

PROJECT STATE

We are building a strategy-neutral crypto Paper Execution / Risk / Audit layer.

Research status:

- Strategy A ZEC mean reversion: CLOSED FAIL
- Filter B: CLOSED FAIL
- TEST-SPEC-002 ZEC TSMOM B v1: FROZEN BLIND FORWARD TEST through 2026-12-23 11:59:59.999 UTC
- TEST-SPEC-003 BTC TSMOM C v1: CLOSED FAIL
- same-prescription TSMOM coin cycling: CLOSED

Current work is infrastructure only.
No alpha changes are allowed.

GEMINI REVIEW

Gemini proposed a complete Paper Execution & Risk Engine architecture including:

- immutable signals
- order state machine
- fill simulator
- position engine
- risk engine
- kill switch
- reconciliation
- idempotency/concurrency
- Supabase schema
- audit hash chain
- blind-test protection
- paper-to-live gate

ChatGPT reviewed Gemini's proposal and changed several design points before implementation.

ACCEPTED FROM GEMINI

- event-driven strategy-neutral architecture
- immutable signals
- explicit order state machine
- executable-price simulation rather than last-price fills
- position state derived from fills
- risk layer
- kill switch
- reconciliation
- idempotency and DB uniqueness
- immutable audit trail
- market-data health monitoring
- server-side blind-test protection
- paper-to-live operational gate
- no live trading/router/alpha tuning yet

MODIFIED / REJECTED FROM GEMINI

1. No in-memory durable queue fallback.
   Critical events must be durably persisted in Supabase/Postgres.
   DB unavailable => fail closed.

2. Locked-strategy risk engine cannot resize, delay, reverse or alter a signal.
   APPROVE or REJECT only.

3. No stochastic or volatility-penalty fill model in v1.
   Primary paper fill is deterministic book walking against an immutable persisted order-book snapshot.

4. No arbitrary 25% of 5m candle-volume partial-fill rule.
   Partial fills depend on persisted book depth and explicit execution policy only.

5. No arbitrary hard-coded risk thresholds such as 30%, 10%, 6s or 50bps.
   Architecture defines configurable policy fields.
   Numeric thresholds are locked separately before they are used.

6. Kill switch does not automatically invent liquidation fills during stale/missing-data conditions.
   It blocks new execution and deterministically cancels cancellable paper orders.
   Emergency liquidation is a separate explicit policy requiring valid executable market data.

7. Reconciliation is detection-only.
   Repairs are performed by a separate audited Recovery Worker.

8. No single implicit global audit hash-chain head.
   Use append-only audit events with per-stream sequence/hash chaining to avoid global concurrency serialization.

9. Blind protection is not UI masking or generic JSON stripping only.
   Performance endpoints must deny blind-strategy performance access before blind_until.
   Operational endpoints use blind-safe projections/views.

10. Position Engine owns accounting state only.
    Strategy-specific stops/exits remain in strategy logic.
    Risk emergency exits require a separate explicit policy.

11. Gemini's proposed live-readiness numbers such as 60 days, 99.9% uptime and 0.05% fill error are NOT locked here.
    Operational categories are accepted.
    Numeric acceptance thresholds require a later preregistered policy.

12. Gemini example timestamp had a 1ms rendering error.
    Repository rule remains:
    intended_entry_time = signal_close_time + 1ms
    Example:
    21:59:59.999Z -> 22:00:00.000Z

DRAFT IMPLEMENTATION-SPEC-004

CORE INVARIANTS

1. Signals are immutable.
2. Infrastructure may APPROVE or REJECT a locked strategy signal but may not silently rewrite it.
3. Last price is never assumed to be executable price.
4. Critical trading state must be durably persisted before downstream action.
5. No critical event may exist only in local process memory.
6. Retries and restarts must be idempotent.
7. Missing/stale/unverifiable execution data fail closed.
8. Position accounting is derived from fills.
9. Strategy-specific alpha exits remain outside infrastructure accounting.
10. Blind forward-test performance remains inaccessible before blind_until.
11. No live real-money routing exists in this implementation phase.

COMPONENTS

- Signal Ingestor
- Durable Event Outbox / Claim Queue
- Pre-Trade Risk Engine
- Paper Order State Manager
- Deterministic Fill Simulator
- Position Accounting Engine
- Portfolio Risk Monitor
- Kill-Switch State Manager
- Reconciliation Engine
- Recovery/Repair Worker
- Market-Data Health Monitor
- Immutable Audit Writer
- Blind-Test Policy Layer
- Read-only Operational API/UI

SIGNAL MODEL

Immutable fields include:

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

Unique:
UNIQUE(idempotency_key)

Duplicate submission returns existing signal and creates no duplicate downstream action.

DURABLE OUTBOX

Use Supabase/Postgres as durable queue.

execution_outbox fields:

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
UNIQUE(idempotency_key)

Workers claim atomically using DB transaction/RPC with FOR UPDATE SKIP LOCKED.

If source event + outbox row cannot be durably written, execution fails closed.

RISK DECISION

One immutable final pre-trade decision per signal:

APPROVED
REJECTED

Unique:
UNIQUE(signal_id, decision_stage)

For locked strategies risk may not alter:
- side
- pair
- entry time
- notional
- alpha rule

PAPER ORDER STATE MACHINE

States:

CREATED
ACCEPTED
PENDING_FILL
PARTIALLY_FILLED
FILLED
CANCEL_REQUESTED
CANCELLED
REJECTED
EXPIRED
FAILED

Risk approval occurs before order creation, so RISK_CHECKED is not an order state.

Allowed transitions:

CREATED -> ACCEPTED
CREATED -> REJECTED
ACCEPTED -> PENDING_FILL
ACCEPTED -> CANCEL_REQUESTED
PENDING_FILL -> PARTIALLY_FILLED
PENDING_FILL -> FILLED
PENDING_FILL -> CANCEL_REQUESTED
PENDING_FILL -> EXPIRED
PENDING_FILL -> FAILED
PARTIALLY_FILLED -> FILLED
PARTIALLY_FILLED -> CANCEL_REQUESTED
PARTIALLY_FILLED -> FAILED
CANCEL_REQUESTED -> CANCELLED
CANCEL_REQUESTED -> FILLED only if deterministic fill occurred before cancellation became effective

Terminal:
FILLED
CANCELLED
REJECTED
EXPIRED
FAILED

Every transition creates immutable paper_order_events row.

Illegal transition => no mutation; emit critical issue.

FILL SIMULATOR V1

Deterministic.

Every fill references immutable persisted execution_market_snapshot.

For market BUY:
- walk asks from best outward
- consume actual base qty until quote notional filled

For market SELL:
- walk bids from best outward

Persist:
- quantity
- VWAP
- spread
- impact
- fee
- book snapshot reference

No stochastic slippage.
No volatility penalty.
No last-price fill.

If book depth insufficient:
- partial only if active execution policy explicitly allows it
- otherwise reject/fail with INSUFFICIENT_BOOK_DEPTH

No candle-volume percentage fallback.

MARKET-DATA EXECUTION POLICY

Configurable versioned fields:

- max_book_age_ms
- max_signal_age_ms
- max_order_age_ms
- max_spread_bps nullable
- min_depth_multiple nullable
- partial_fill_allowed
- abnormal_slippage_bps nullable

Before a strategy starts paper execution, an execution-policy version is frozen.

Policy violation => reject/expire, no optimistic fallback.

POSITION ACCOUNTING

Position Engine owns accounting state only.

It does NOT create alpha entry/exit signals.

Fields include:

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
- closed_at
- state_version

Position is reconstructable from immutable fills.

For max_open=1 strategy:
only one active position per strategy_version_id + pair.

RISK POLICY

Versioned immutable active policy.

Configurable fields:

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

Architecture itself does not lock arbitrary percentages.

Hard invariants:

- duplicate order/fill blocked
- no execution on unverifiable data
- no execution while kill switch active
- no execution with unresolved critical reconciliation state
- no hidden leverage
- no execution without durable persistence

KILL SWITCH

Global states:

RUNNING
HALT_NEW_ENTRIES
HALTED
RECOVERY_PENDING

Activation:

- reject new signals/orders
- cancel deterministic unfilled paper orders
- prevent invented new fills
- mark open positions risk-frozen operationally

No unconditional liquidation during stale/missing data.

Emergency liquidation:
- separate explicit policy
- requires valid executable data
- creates normal audited exit order/fill

Automatic trigger classes:

- persistence unavailable for critical write
- duplicate fill
- ghost fill
- impossible position state
- critical reconciliation failure
- audit integrity failure
- configured portfolio-risk breach
- configured market-data outage rule

Reset is never automatic.

RECONCILIATION

Read-only detection.

May:
- emit issue
- classify severity
- activate strategy/global halt

May NOT repair state directly.

Separate Recovery Worker performs explicit repair actions with:
- unique idempotency key
- explicit repair type
- before/after state
- audit event

IDEMPOTENCY / CONCURRENCY

Unique constraints:

paper_signals(idempotency_key)
risk_decisions(signal_id, decision_stage)
paper_orders(idempotency_key)
paper_fills(idempotency_key)
execution_outbox(idempotency_key)
recovery_actions(idempotency_key)

Order workers:
FOR UPDATE SKIP LOCKED claim.

Position update:
transaction/RPC + state_version + row lock.

Risk capacity:
serialize reservation via portfolio/account risk-state lock or advisory/RPC lock.

AUDIT

Append-only execution_audit.

Per-stream sequence/hash chain.

Fields include:

- event_id
- event_type
- entity_type
- entity_id
- correlation_id
- strategy_version_id
- market_time
- received_at
- decision_at
- persisted_at
- state_before
- state_after
- input_snapshot_hash
- reason_code
- worker_id
- software_commit
- stream_key
- stream_sequence
- prev_hash
- event_hash

Unique:
UNIQUE(stream_key, stream_sequence)
UNIQUE(event_hash)

DB permissions/trigger reject UPDATE/DELETE.

BLIND TEST PROTECTION

TEST-SPEC-002 blind until:
2026-12-23 11:59:59.999 UTC

Raw operational records may be persisted internally.

Before blind_until normal application APIs must not expose reconstructable performance.

Allowed:
- service health
- market-data freshness
- gap counts
- worker heartbeat
- queue backlog
- audit completeness
- reconciliation health
- kill-switch state

Hidden:
- realized/unrealized PnL
- entry/fill/current price combinations that reconstruct returns
- trade result list
- PF
- expectancy
- win rate
- long/short PnL
- equity curve
- return distribution
- PASS/FAIL proximity

Enforcement:
- performance endpoint denies blind strategy version before blind_until
- operational endpoint reads blind-safe projection/view
- not client-side masking
- not generic response stripping only

SUPABASE TABLES PROPOSED

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

Reuse existing strategy version identity where possible.

API V1

POST /api/paper/signals
POST /api/paper/kill-switch/activate
POST /api/paper/kill-switch/reset
POST /api/paper/reconciliation/run

GET /api/paper/system-health
GET /api/paper/market-health
GET /api/paper/orders
GET /api/paper/positions
GET /api/paper/reconciliation
GET /api/paper/execution-quality

No live-order API.

SERVERLESS CONSTRAINT

Current app is Vercel/serverless-oriented.

Correctness must not rely on:
- one long-lived process
- one in-memory queue
- one worker remaining alive
- process-local locks

Correctness must come from:
- durable DB state
- atomic DB operations
- unique constraints
- idempotency
- transactional claims

PAPER-TO-LIVE

SPEC-004 does not set arbitrary numerical live-readiness thresholds.

Later create separate preregistered operational acceptance policy.

Absolute invariants allowed now:

- zero duplicate orders
- zero duplicate fills
- zero unresolved critical reconciliation errors
- kill-switch tests pass
- restart recovery tests pass
- idempotency tests pass
- DB-outage fail-closed behavior passes
- blind-test protection tests pass

DO NOT BUILD YET

- live real-money routing
- strategy router
- adaptive strategy selection
- alpha-confidence sizing
- ML alpha optimization
- parameter tuning
- multi-exchange routing
- self-modifying risk rules
- unconditional automated emergency liquidation
- new alpha logic in this infrastructure work

YOUR ROLE

Act as an adversarial systems/methodology reviewer.

Do NOT redesign the product from scratch unless a blocking flaw requires it.

Do NOT propose alpha changes.

Try to break this architecture.

FOCUS ON

- race conditions
- DB transaction boundaries
- idempotency weaknesses
- Vercel/serverless incompatibilities
- queue semantics
- risk-reservation races
- partial-fill accounting
- cancellation races
- fill/cancel ordering
- position reconstruction
- accounting correctness
- kill-switch semantics
- restart recovery
- audit-chain concurrency
- blind-test leakage
- API authorization/data exposure
- Supabase/RLS issues
- failure during transaction commit
- two workers processing same entity
- event ordering
- stale market snapshot handling

RETURN EXACTLY

1. BLOCKING FLAWS
   Only flaws that must be fixed before implementation.

2. NON-BLOCKING CAVEATS

3. RACE CONDITIONS / TRANSACTION ISSUES

4. ACCOUNTING OR STATE-MACHINE AMBIGUITIES

5. BLIND-TEST LEAKAGE RISKS

6. SERVERLESS / SUPABASE RISKS

7. REQUIRED CHANGES BEFORE LOCK

8. THINGS THAT ARE ALREADY CORRECT AND SHOULD NOT BE CHANGED

9. LOCK VERDICT
   Exactly one:
   - CLEAN ENOUGH TO LOCK
   - NOT CLEAN ENOUGH TO LOCK

10. HANDOFF FOR CHATGPT

In HANDOFF FOR CHATGPT give:
- accepted blocking fixes
- exact schema/state-machine changes required
- tests that must be added
- anything you explicitly disagree with
- final implementation recommendation

Do not ask follow-up questions.
Make reasonable assumptions and finish the review.
