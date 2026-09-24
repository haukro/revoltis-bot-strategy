# Gemini Architecture Review Disposition — Paper Execution & Risk Engine

Status: **PRE-IMPLEMENTATION REVIEW PROCESSED**

Source: user-provided Gemini architecture review.

No paper/live execution code has been implemented from this review yet.

## ACCEPTED

The following Gemini design directions are accepted:

- strategy-neutral event-driven architecture
- immutable signal events
- deterministic order state machine
- explicit fill simulation instead of last-price fills
- authoritative position state derived from fills
- strategy-neutral pre-trade and portfolio risk layers
- kill-switch state machine
- reconciliation across Signal -> Risk -> Order -> Fill -> Position -> PnL
- idempotency keys and database-enforced uniqueness
- durable persistence in Supabase
- append-only audit events
- explicit market-data health monitoring
- server-side blind-test protection for TEST-SPEC-002
- paper-to-live operational gate independent of alpha optimization
- no live trading, no strategy router, no adaptive alpha tuning yet

## ACCEPTED WITH MODIFICATION

### 1. Durable queue, not in-memory fallback
Gemini allowed an in-memory / DB-backed queue and suggested keeping events in local memory during DB loss.

Modified rule:

- Supabase/Postgres is the durable system of record.
- Critical trading events must never exist only in process memory.
- If durable persistence is unavailable, execution fails closed.
- No paper order/fill is created until the corresponding event can be persisted.
- Use a transactional outbox/claim pattern rather than best-effort in-memory buffering.

Reason: Vercel/serverless restarts make local memory non-durable and non-reproducible.

### 2. Risk engine must not resize a locked strategy signal
Gemini allowed the risk layer to reduce intended size.

Modified rule:

- For preregistered / locked strategies, the risk engine may APPROVE or REJECT only.
- It must not silently resize, delay, reverse, or transform the signal.
- A future strategy may explicitly opt into risk resizing in its own spec, but this is not the default.

Reason: silent resizing changes strategy economics.

### 3. Fill simulator
Gemini proposed a synthetic slippage formula using spread, L2 depth, an impact factor and volatility penalty.

Modified rule:

- Primary paper fill must be deterministic and replayable from a persisted market snapshot.
- For market orders, walk persisted bid/ask depth for the intended quote notional.
- Apply venue fee separately.
- Do not invent a volatility penalty or stochastic slippage term in v1.
- If the persisted book cannot support the requested notional or is too stale under the configured execution policy, mark the order unfillable/rejected.
- Every fill references the exact persisted market snapshot used.

Reason: deterministic replay is more important than a speculative slippage model.

### 4. Partial fills
Gemini proposed a 25% of 5m candle-volume cap.

Modified rule:

- Do not use an arbitrary candle-volume percentage.
- Partial fills are determined only by available persisted book depth or explicit execution-policy limits.
- No hidden fallback to candle volume.

### 5. Risk thresholds
Gemini proposed hard defaults such as 30% gross exposure, 10% asset exposure, 3% daily loss, 10% drawdown, 6-second staleness and 50 bps slippage.

Modified rule:

- Architecture defines configurable risk policies and parameter types.
- No arbitrary portfolio/risk thresholds are locked by this architecture review.
- Hard invariants may be fixed now: no duplicate orders/fills, no execution on unverifiable data, no execution during kill-switch state, no unresolved critical reconciliation issue.
- Numeric risk thresholds require a separate risk-policy lock before paper-to-live assessment.

### 6. Kill switch
Gemini proposed automatic market liquidation of open paper positions.

Modified rule:

- Kill switch immediately blocks new signals/orders and cancels unfilled paper orders.
- Existing positions enter a frozen risk state.
- Automatic liquidation is a separate explicit emergency-liquidation policy and may execute only when required market data is healthy enough to price the liquidation.
- If market data is unreliable, do not invent a liquidation fill.

Reason: a kill switch must not create fictional fills during the exact conditions that made execution unsafe.

### 7. Reconciliation
Gemini described Reconciliation Engine as read-only but also proposed it marking orphan orders FAILED.

Modified rule:

- Reconciliation is detection/reporting only.
- It may trigger strategy/global halt states.
- Any repair/state mutation is performed by a separate recovery action/worker with an explicit audit event and idempotency key.

### 8. Audit hash chain
Gemini proposed one global SHA-256 chain.

Modified rule:

- Use append-only audit events with deterministic content hashes.
- Chain events per correlation/aggregate stream or another explicitly serialized partition, not one implicit global mutable head across concurrent workers.
- DB permissions/triggers must reject UPDATE/DELETE on immutable audit/event tables.
- A periodic root/checkpoint can summarize partition hashes later.

Reason: one global chain creates avoidable concurrency serialization.

### 9. Blind-test protection
Gemini proposed response-body masking middleware.

Modified rule:

- Blind-test protection must be enforced at service/data-access policy level, not only by stripping JSON after generation.
- Performance endpoints must reject access for blind strategy versions before blind_until.
- Blind-safe APIs expose only operational projections.
- Raw execution events may be persisted internally but normal application APIs must not return reconstructable performance data.

### 10. Position engine and strategy exits
Gemini made the Position Engine responsible for monitoring stop triggers.

Modified rule:

- Position Engine owns accounting state, not alpha/strategy exit logic.
- Strategy-specific stop/exit logic stays in the strategy or strategy-state engine.
- Position Engine may store strategy-provided stop metadata and process resulting exit order intents.
- Risk engine may generate explicit emergency-risk exit intents under a separate policy.

Reason: infrastructure must remain strategy-neutral.

### 11. Paper-to-live thresholds
Gemini proposed 60 days, 99.9% uptime and <0.05% fill-model error.

Modified rule:

- Keep the categories and measurement framework.
- Do not lock these numerical thresholds yet.
- Before any live-readiness evaluation, preregister a separate operational acceptance policy with fixed thresholds.
- Some invariants can be absolute: zero duplicate fills/orders, zero unresolved critical reconciliation errors, kill-switch/restart/idempotency tests passing.

## DISAGREED / REJECTED

- Reject using local process memory as a durable event queue during persistence failure.
- Reject hard-coded arbitrary risk percentages and latency/slippage thresholds from the architecture review.
- Reject 25% of 5m volume as a generic partial-fill rule.
- Reject stochastic/volatility-penalty slippage in v1.
- Reject unconditional kill-switch liquidation when execution data is unavailable.
- Reject reconciliation directly mutating trading state.
- Reject relying on UI or response-masking middleware alone for blind-test secrecy.
- Reject allowing the infrastructure risk layer to silently change locked strategy notional.

## IMPORTANT GEMINI ERRATUM

Gemini's example SignalEvent used:

- market_time = 21:59:59.999Z
- intended_entry_time = 22:00:00.001Z

For the repository's established timestamp model, the exact next boundary is:

- intended_entry_time = signal_close_time + 1 ms
- therefore 21:59:59.999Z -> **22:00:00.000Z**

The +1ms rule is correct; the rendered example timestamp was off by 1 ms.

## IMPLEMENTATION STATUS

Nothing from this review is live yet.

Next step:

1. write IMPLEMENTATION-SPEC-004 from the accepted/modified architecture,
2. send that exact spec to Grok for adversarial review,
3. resolve Grok blocking issues,
4. lock spec,
5. only then write migrations/code.
