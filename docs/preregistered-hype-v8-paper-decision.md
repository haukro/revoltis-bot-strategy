# Pre-terminal decision rule — HYPE v8 paper forward

Status: **NOT EVALUATED — DOCUMENT CLARIFICATIONS PENDING**

Filename retained for continuity: `preregistered-hype-v8-paper-decision.md`.
The numerical rule was documented after an interim result was observed and
before the terminal gate. It is a **pre-terminal decision rule**, not a blind
preregistration before the experiment. Grok's review and dispositions are in
`GROK-REVIEW-DISPOSITION-TEST-SPEC-004.md`.

This document changes no test settings, cost model, scheduler, strategy,
runtime or ledger. No early stop, restart or final verdict is authorized.

## Scope and timestamp provenance

| Test | Pair / TF | Capital / stake | Initially reported start (UTC; role unverified) | Reported earliest end (UI gate) |
|---|---|---|---|---|
| Official paper forward | HYPE/USDT 5m v8 | 100 / 50 USDT | 2026-09-25 22:58 | 2026-10-26 |
| Parallel scale test | HYPE/USDT 5m v8 | 300 / 150 USDT | 2026-09-26 23:20 | 2026-10-27 |

The reported UTC timestamps have not been reconciled with warmup/run/trading
start semantics or the UI date gate. Do not infer elapsed trading days from
this table. Before terminal evaluation, record the existing stored trading
start and exact time gate with their field/source identity by authorized
read-only access. Do not alter runtime timestamps to fit this document.
The UI dates are the earliest possible time condition, not an end guarantee.

Strategy family: 5m mean reversion (Bollinger lower band + RSI oversold +
rebound confirmation; stop-loss / trailing profit / max-no-trail exit).
Reported cost basis: `current_okx_book`. No ledger immutability is implied.

## Interim snapshot — recorded, not evaluated

Reported source run: `4866ecc0-a70a-40cc-b65d-6f3dc07c2484`,
2026-10-13 22:57 UTC. These are the initial document's recorded figures,
not a new verification by this review. No setting may change because of them.

| Metric | Official 100/50 | Scale 300/150 |
|---|---:|---:|
| Closed trades | 18 | 17 |
| Win rate | 50.0% | 47.1% |
| Avg win | +0.378 USDT | +1.171 USDT |
| Avg loss | -0.786 USDT | -2.363 USDT |
| Payoff | 0.481 | 0.496 |
| Break-even win rate implied by payoff | 67.5% | 66.9% |
| Realized PnL | -3.673 USDT | -11.897 USDT |
| Max DD | 7.45% | 7.49% |

## Eligibility and single terminal observation

Each test's eligibility requires **at least 30 trading days AND at least
30 closed trades**, using its own verified existing trading start/time gate.
Until both conditions hold, its state is NOT_EVALUATED, not FAIL.

For Official, use the first valid existing result snapshot observed after
both conditions hold. Identify its run ID, as-of time, verified time gate and
complete input/output identities. Include all closed trades from the test's
trading start through that snapshot; do not truncate to the first 30 trades or
choose a later, more favorable snapshot. If more than 30 trades have closed
before the first eligible observation, include them. This is an assessment
convention; it does not stop or alter the running scheduler.

A first eligible but incomplete/corrupt snapshot is INVALID. Preserve its
identity and reason; do not search later results until a PASS appears. Any
recovery/evaluation procedure needs a documented non-strategy validity reason
and separate authorization. Once a valid economic verdict is recorded, do not
reevaluate it with newly favorable costs or additional trades.

Scale is assessed diagnostically under its own eligibility. It cannot rescue
Official FAIL. Official eligibility or PASS does not satisfy Scale's gate.

## Numerical economic rule — unchanged

Evaluate only the Official 100/50 result. PASS requires all:

1. closed trades >=30, after the separately checked 30-day time gate;
2. net expectancy per closed trade >0;
3. net profit factor >=1.10;
4. max drawdown <=15%.

A valid, sufficiently complete eligible result failing any economic gate is
FAIL. NOT_EVALUATED, INVALID and unavailable/undefined metrics are not silently
converted into an economic FAIL or PASS.

Net expectancy is total net realized PnL divided by closed-trade count.
PF uses the sum of positive net closed-trade PnL divided by the absolute sum
of negative net closed-trade PnL; zero-PnL trades remain in the trade count
and expectancy denominator. Positive profit with zero losses gives infinite
PF; all-zero PnL has undefined PF and already fails positive expectancy.

## Accounting requirements still open before evaluation

The DD gate's equity definition and sampling (including fees and unrealized
positions) must be reconciled with the existing tested calculation and recorded.
Do not substitute realized-only drawdown for total-equity drawdown, or vice
versa, without an explicit documented decision before terminal outcomes.

Closed-trade expectancy/PF do not invent terminal closes of open positions.
Record open-position exposure separately. Terminal valuation, DD and the
existing cost-calculation provenance must be specified consistently before
evaluation; no new forced exit or simulation is authorized.

Identify whether each reported cost component is stored/frozen or recomputed
from a current book. Record the model, timestamps, source, rates and result
identity used at the single terminal observation. Do not describe today's
results as historically immutable, replace existing running costs, obtain a
new cost model to rescue the result, or implement a ledger through this rule.
Missing/nonfinite metrics or unverifiable cost/accounting provenance block
evaluation as INVALID until a properly authorized validity resolution.

## Consequences

Official FAIL rejects HYPE v8 for paper-to-live promotion. The project's
5m BB/RSI/rebound/trailing mean-reversion research family is then closed
alongside closed Strategy A. This is a research-budget policy, not proof
that all possible 5m mean-reversion strategies are impossible.
No rescue coin/timeframe/exit grid or post-hoc filter on the used evidence.

PASS permits only consideration of a new separately preregistered forward
experiment on a fresh period, subject to Roman's separate authorization.
It does not start that experiment, approve live trading, release the freeze
or authorize ledger cutover.

Ledger cutover remains independent: **30 days AND30 trades at BOTH Official
and Scale**, plus Roman's new explicit instruction. No migration/rebase/merge
or production action follows automatically from a paper verdict.

## Non-goals

No early economic verdict from the interim snapshot, no stop/restart,
no optimizer run, no change to HYPE, ZEC TSMOM B, paper tests, costs, policy v4,
ledger, real trading or production. Current freeze remains in force.
