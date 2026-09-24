# Research Roadmap — Current State

Status: **ACTIVE**

## Closed research branches

### Strategy A — ZEC mean reversion
Status: **CLOSED FAIL / RESEARCH-ONLY**

- economic scorecard negative after costs
- no paper/live deployment
- no rescue filter, timeout grid, trail grid or post-hoc threshold tuning

### Filter B — rebound confirmation
Status: **CLOSED FAIL**

- no retuning on the spent fold

### TEST-SPEC-003 — BTC TSMOM C v1
Status: **CLOSED FAIL**

- exact replication of the ZEC TSMOM prescription
- 92 trades
- expectancy negative
- PF below 1
- long and short both negative

The same N=24 / Wilder ATR(24) / 2x ATR prescription will not be cycled through additional coins after the BTC result.

## Open research branch

### TEST-SPEC-002 — ZEC TSMOM B v1
Status: **FROZEN FORWARD TEST**

Official fold:

2026-09-24 12:00:00 UTC
to
2026-12-23 11:59:59.999 UTC

Rules:

- no parameter changes
- no partial official PnL/PF/expectancy review
- no rescue filters
- one official batch replay after fold end

## Current engineering priority

Build strategy-neutral infrastructure that does not contaminate TEST-SPEC-002:

- paper execution engine
- risk engine
- kill switch
- stale-data guards
- duplicate-order protection
- signal/order/position state machines
- slippage and spread tracking
- reconciliation
- immutable audit logs
- paper-to-live execution diagnostics

## Current research priority

Do not start another same-prescription TSMOM coin replication.

The next alpha research branch, when opened, must be a genuinely different economic mechanism and must begin with a new preregistration before any performance test.

Examples of acceptable new families for later review include:

- volatility expansion / compression
- cross-sectional or residual relative-value signals
- carry / basis / funding-type premia
- slower multi-market trend on a predeclared futures basket

These are research directions only, not approved strategies.

## External review roles

- Gemini: product architecture, system design, research roadmap, new edge-family ideation
- Grok: adversarial methodology review, execution/accounting ambiguity, look-ahead, data-integrity and reproducibility audit
- ChatGPT: implementation, GitHub/Supabase/Vercel integration, consistency check, and explicit ACCEPTED / IMPLEMENTED / DISAGREED-MODIFIED disposition

No agent vote overrides preregistered methodology.
