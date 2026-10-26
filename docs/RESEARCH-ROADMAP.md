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

### HYPE v8 — Official and Scale paper forward
Status: **RUNNING — NO TERMINAL ECONOMIC VERDICT**

- Interim figures are observations, not final PASS/FAIL.
- Each test needs its own minimum 30 trading days AND 30 closed trades.
- Official economic verdict and Scale diagnosis follow
  `preregistered-hype-v8-paper-decision.md`, including its still-open
  timestamp/accounting validity requirements.
- No setting, cost, stop/restart or scheduler change follows from this roadmap.
- Ledger cutover remains both-tests30/30 plus Roman's new explicit instruction.

### TEST-SPEC-004 — Strategy D v1
Status: **DRAFT R2 FOR REVIEW — NOT LOCKED, NOT IMPLEMENTED**

- Hypothesis: daily long-only volatility-scaled20/60/120day trend on a fixed
  BTC/ETH spot basket. Roman confirmed this documentary direction on2026-10-14.
- Core choices: fixed6calendar-month forward, daily statistics/monthly reports,
  cap40% per asset, weekly rebalance, original5pp band. Capped targets max80%.
- Roadmap alignment: accepted as an appropriate draft candidate for slower
  multi-market trend research, distinct from cycling the failed C prescription
  through another coin. Spot limits are explicitly permitted as a research
  direction; this is not empirical evidence of a profitable new economic edge.
- Reviews: Gemini's roadmap disposition is recorded in
  `GEMINI-REVIEW-DISPOSITION-TEST-SPEC-004.md`; Grok's methodology verdict
  remains not clean enough to lock.
- R2 candidate prescriptions, decision dispositions and new review packet are in
  `TEST-SPEC-004-basket-tsmom-d-v1.md`, `DECISIONS-TEST-SPEC-004-R2.md` and
  `FOR-GROK-REVIEW-TEST-SPEC-004-R2.md`.
- Dependencies: second Grok clearance for newly completed timing/sizing/cost
  and statistical proposals, actual continuity/inspection/cost manifests and
  final Roman lock instruction. No certificate or observed cost is fabricated.
- Historical development is conditional on today's fixed BTC/ETH choice and
  includes inspected history. The prior historical-final12month holdout is
  replaced by a forward fold after a future authorized lock/readiness/launch.
  Exact T0/T1 are frozen before outcomes; no result-driven extension.
- Daily observations are not automatically independent or sufficient. Proposed
  DSR/trial inputs and dependence safeguard still require R2 methodology review.
- No performance test, market-data job, optimizer run or runtime implementation
  is approved by this draft inclusion or by external review.

#### Permission and dependency sequence

| Stage | Required condition | Separate authorization / boundary |
|---|---|---|
| Current documentation review | Recorded Grok/Gemini dispositions, direction decisions and revised R2 prescription | Docs edits in PR70 only. PR70 merge is not authorized here and, if later approved, is not a final D lock while blockers remain. |
| Future metadata/archive repair | PR69 remains PARKED(C); structural metadata/archive repair is its future replacement, not merge of current PR69 | Separate scope/authorization for implementation, data-only access/backfill, schema changes and production actions after freeze release. No change to frozen trading B or HYPE. |
| D final spec lock | Complete deterministic spec, universe/folds/provenance and statistical decisions; methodology cleared | Roman explicitly authorizes a final lock commit/version. A general docs merge does not authorize implementation or data acquisition. |
| D implementation/synthetic verification | Final locked D spec; independent runner/data/cost IDs and behavior isolation | Separate authorization to write code and run isolated synthetic tests. No official performance data inspection. |
| Availability/contamination certification | Declared data-only scope; prior inspection inventory; exact dataset identities | Separate authorization for necessary data reads/downloads; availability is not permission to compute strategy PnL. |
| Official D evaluation | Clean, fixed development/holdout design, approved locked spec and required certificates | Separately authorized one-shot evaluation. No grid, result-selected alternative, extra optimizer job or production deployment. |

Structural metadata/archive repair and **trading Strategy B (ZEC TSMOM)**
are different workstreams. Use explicit names, separate versions/run identities
and isolated code paths. Current no-new-optimizer-before-structural-repair rule
remains; PR69 is not reopened or merged by this roadmap update.
A standalone D runner's storage dependency must be specified before its
implementation; an export repair is not by itself alpha clearance.
No shared helper may alter the existing frozen B or HYPE behavior.
Keep B's official forward results blind; do not use them to choose D settings.

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
- slower multi-market trend on a predeclared spot or futures basket;
  Strategy D is the spot-only draft, with no shorts, leverage or perpetuals

These are research directions only, not approved strategies. The priority
lists are planning documents; the current freeze still controls all execution.
A future OVERFIT_RISK proposal remains diagnostic-only without a defined
procedure; it adds no global qualification gate or policy-v4 rule.

## External review roles

- Gemini: product architecture, system design, research roadmap, new edge-family ideation
- Grok: adversarial methodology review, execution/accounting ambiguity, look-ahead, data-integrity and reproducibility audit
- ChatGPT: implementation, GitHub/Supabase/Vercel integration, consistency check, and explicit ACCEPTED / IMPLEMENTED / DISAGREED-MODIFIED disposition

No agent vote overrides preregistered methodology.
