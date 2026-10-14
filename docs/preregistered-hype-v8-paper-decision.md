# Pre-terminal decision rule — HYPE v8 paper forward

Status: **NOT EVALUATED — R2 CUTOFF PROPOSAL, EVIDENCE CHECKS PENDING**

Filename retained for continuity: `preregistered-hype-v8-paper-decision.md`.
The numerical rule was documented after an interim result was observed and
before the terminal gate. It is a **pre-terminal decision rule**, not a blind
preregistration before the experiment. Grok's review and dispositions are in
`GROK-REVIEW-DISPOSITION-TEST-SPEC-004.md`.
The revised cutoff/accounting proposal is tracked in
`DECISIONS-TEST-SPEC-004-R2.md` and requires R2 consistency review.

This document changes no test settings, cost model, scheduler, strategy,
runtime or ledger. No early stop, restart or final verdict is authorized.

## Scope and timestamp provenance

| Test | Pair / TF | Capital / stake | Trading-start source (UTC/ms) | Reported earliest end (UI gate) |
|---|---|---|---|---|
| Official paper forward | HYPE/USDT 5m v8 | 100 / 50 USDT | Existing run.progress.trading_start_time; actual value not read in this revision | 2026-10-26 (previously reported UI date) |
| Parallel scale test | HYPE/USDT 5m v8 | 300 / 150 USDT | Existing run.progress.trading_start_time; actual value not read in this revision | 2026-10-27 (previously reported UI date) |

The initial September25/26 timestamps were not certified trading starts and
are removed from the trading-start table rather than replaced with guessed dates.
Source verification at main b200950: frontend saved.started_at -> startedAt ->
request.trading_start_time (main.tsx406,448; Scale502,538); backend stores that
value in simulation_runs.progress.trading_start_time (main.py2264).
The frontend gate is startedAt +30*24hours, not a warmup start or the run's
started_at/finished_at. Values must be recorded for each test from its existing
source by separately authorized read-only inspection before evaluation.
No DB, browser localStorage or application endpoint was read for this revision.
The previously reported UI dates are context only, not verified exact deadlines.

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

## Eligibility, exact cutoff and evidence availability

For EACH test independently, let S be its verified existing trading_start_time.
Let c30 be the30th closed trade's timestamp in the single certified test tape,
sorted by closed_at then immutable trade ID; do not mix differently costed runs.
T*=max(S+30*24hours,c30). If fewer than30closed trades, T* is not yet eligible.
Before the verified time AND trade conditions hold, status is NOT_EVALUATED.

Proposed terminal sample: all trades closed in [S,T*], including more than30
if those closed before the time gate. An open position at T* is excluded from
closed-trade expectancy/PF and recorded separately, but remains relevant to
marked equity/DD. No forced close, parameter change or scheduler change.

Exact T* is a precision requirement subject to evidence availability. Record:
S,c30,T*, source run/tape IDs and hashes, run.test_end / evaluation as-of,
cost profile IDs/book times, actual recalculation and finished times.
Do not assert that a later current-book recalculation occurred "at T*".
The existing scheduler usually evaluates after the mathematical gate; code alone
does not guarantee a persisted exact-cutoff result, DD path or historical cost book.
If no existing evidence supports the exact sample/DD/cost convention, record
INVALID with reason exact_cutoff_not_reproducible and seek a separate decision.
No fallback to a later favorable run and no new simulation/OKX call is authorized.

Use only a single existing certified calculation at/as-of T* if available,
retaining its ACTUAL cost observation times and full provenance; an archived
same-cutoff calculation may finish later, which must be distinguished explicitly.
No freshly fetched cost profile may retroactively represent the T* book.
No new recalculation is requested by this document. A prospective terminal
capture/replay or a change to first-observed-snapshot methodology would require
its own authorization before it is performed. Availability is not certified here.

Official has the economic verdict; Scale has its own terminal diagnostic.
A valid completed verdict is not repeated with additional trades/favorable costs.
An unavailable/invalid assessment is not economic FAIL and cannot be used to
close a family as if it were valid. Ledger eligibility remains BOTH tests30/30
plus a separate Roman instruction, independent of Official verdict.

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

## Accounting provenance and remaining validity checks

Authoritative DD proposal is the backend stored run.summary.max_drawdown_percent
for the certified exact-cutoff run, not a frontend reconstructed fallback.
Source b200950 simulation.py143-148,198-204,335: cash plus marked open positions
using cost-aware position_return, updated at simulation events; summary DD is
rounded to2decimals. Record that precision, value and source as stored.
Open positions are outside closed-trade PF/expectancy, not outside this DD.
A sparse equity chart is not proof of the exact intervening DD.

main.tsx62 also contains a closed-trade-only fallback DD. It is a different
calculation; do not substitute it for backend marked-equity DD in this verdict.
No source code is changed and no claim of a live UI discrepancy is made here.

Source b200950 main.py2232-2254 loads pair costs anew when
use_current_cost_model=true; main.tsx448-450 and538-540 request this for paper.
Trade raw.cost_components are recorded by simulation.py131-141.
Thus an existing single recalculation can have stored cost components while
later recalculations use a different current model. This is not an immutable
trade ledger. Require the actual cost profile/source timestamps for the chosen
run and sufficient same-cutoff evidence, never infer them from a model label.

DD/cost/tape identity and actual S values still need evidence verification.
No new cost observation, market-data request, DB query or calculation occurred
in this revision. Missing/nonfinite/unverifiable inputs block the assessment.

## Consequences

Official FAIL rejects HYPE v8 for paper-to-live promotion. The project's
5m BB/RSI/rebound/trailing mean-reversion research family is then closed
alongside closed Strategy A. This is a research-budget policy, not proof
that all possible 5m mean-reversion strategies are impossible.
No rescue coin/timeframe/exit grid or post-hoc filter on the used evidence.

PASS permits only drafting a new separately preregistered experiment on a
fresh period. Starting it requires Roman's separate authorization.
It does not start that experiment, approve live trading, release the freeze
or authorize ledger cutover.

Ledger cutover remains independent: **30 days AND30 trades at BOTH Official
and Scale**, plus Roman's new explicit instruction. No migration/rebase/merge
or production action follows automatically from a paper verdict.

## Non-goals

No early economic verdict from the interim snapshot, no stop/restart,
no optimizer run, no change to HYPE, ZEC TSMOM B, paper tests, costs, policy v4,
ledger, real trading or production. Current freeze remains in force.
