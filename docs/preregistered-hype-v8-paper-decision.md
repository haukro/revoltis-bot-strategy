# Pre-terminal decision rule — HYPE v8 paper forward

Status: **NOT EVALUATED — R3 VARIANT B DOCUMENTARY PROPOSAL; EVIDENCE CHECKS PENDING**

Filename retained for continuity: `preregistered-hype-v8-paper-decision.md`.
The numerical rule was documented after an interim result was observed and
before the terminal gate. It is a **pre-terminal decision rule**, not a blind
preregistration before the experiment. Grok's review and dispositions are in
`GROK-REVIEW-DISPOSITION-TEST-SPEC-004.md`.
R3 replaces R2's exact-cutoff-only requirement with Roman's selected variant B:
the first provably persisted automatic evaluation with as_of >= T*.
This is a documentary draft for explicit commit approval, not an evaluation.
The R2 decision record remains historical; the present R3 cutoff text supersedes
its exact-cutoff-only/INVALID rule. The economic thresholds are unchanged.

Draft date: 2026-10-26 Europe/Bratislava. The handoff reports Roman selected B
after the 2026-10-25 interim snapshot below. The original decision-message UTC
timestamp is not supplied and is NOT invented here. At preparation, GitHub
PR70 still points to 0a06e299750a252fda5082c8906421146413874a.
The draft date/local file time is not evidence of a prior GitHub publication.
Before claiming decision-before-T*, record the actual approval/message or
publication evidence and compare its UTC time to the independently verified T*
for EACH test. If ordering cannot be established, record
decision_timing_unverified; if the decision was at/after T*, disclose
post_cutoff_decision. Neither label may be presented as pre-terminal or blind
preregistration. Numerical metrics, if later certified, remain descriptive
until the decision-timing qualification is explicitly resolved; no clean
pre-terminal PASS/FAIL is certified by this draft.

This document changes no test settings, cost model, scheduler, strategy,
runtime or ledger. No early stop, restart or final verdict is authorized.

## Scope and timestamp provenance

| Test | Pair / TF | Capital / stake | Trading-start source (UTC/ms) | Reported earliest end (UI gate) |
|---|---|---|---|---|
| Official paper forward | HYPE/USDT 5m v8 | 100 / 50 USDT | Existing run.progress.trading_start_time; actual value not read | 2026-10-26 08:18:59, reported local UI |
| Parallel scale test | HYPE/USDT 5m v8 | 300 / 150 USDT | Existing run.progress.trading_start_time; actual value not read | 2026-10-27 08:40:51, reported local UI |

The initial September25/26 timestamps were not certified trading starts and
are removed from the trading-start table rather than replaced with guessed dates.
Source verification at main b200950: frontend saved.started_at -> startedAt ->
request.trading_start_time (main.tsx406,448; Scale502,538); backend stores that
value in simulation_runs.progress.trading_start_time (main.py2264).
The frontend gate is startedAt +30*24hours, not a warmup start or the run's
started_at/finished_at. Values must be recorded for each test from its existing
source by separately authorized read-only inspection before evaluation.
No DB, browser localStorage or application endpoint was read for this revision.
The reported UI dates/times are context only, not verified exact deadlines.
Conditional conversion ONLY: if displayed in Europe/Bratislava after the DST
change, those times correspond to 2026-10-26T07:18:59Z and
2026-10-27T07:40:51Z. These are not certified T* values.
Record S as the actual integer UTC milliseconds AND an ISO-8601 Z rendering.
Compute 30 days as exactly 2,592,000,000 milliseconds; do not add 30 local dates.
S is neither warmup test_start nor backend started_at/finished_at.

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

## Interim snapshot 2 — cutoff choice made after this observation

Source: Roman's handoff of the screenshot from 2026-10-25, with last evaluation
approximately 16:41 as displayed. No UTC timezone, run IDs, full tapes or PF
are certified by that screenshot/handoff. These are reported figures, not a
fresh API/database verification and not a final economic verdict.

| Metric | Official 100/50 | Scale 300/150 |
|---|---:|---:|
| Closed trades | 34 | 33 |
| Realized PnL (USDT) | -3.8224 | -12.1751 |
| Max DD as displayed | 7.85% | 7.86% |
| Profit factor | Not supplied | Not supplied |
| Time gate | Reported pending at screenshot | Reported pending at screenshot |

Disclosure: the numerical gates already followed interim snapshot 1; variant B
and its cutoff/accounting convention were chosen AFTER interim snapshot 2.
This additional look is disclosed even if the decision is later proven to
precede T*. Do not present the cutoff choice as independent of interim results.

## Eligibility, variant B selection and evidence availability

For EACH test independently, S is its verified existing trading_start_time.
Candidate evaluations must belong to the same test identity: pair/timeframe,
frozen strategy/version/settings, start S, Official 100/50 or Scale 300/150,
and automatic paper-forward origin. Never combine the two tests.

For a candidate's OWN complete archived tape, sort trades with closed_at >= S
by (closed_at, archived trade ID). Let c30 be the 30th close timestamp.
If fewer than 30 valid closes exist, the candidate is not eligible.
Define T*=max(S+2,592,000,000 ms,c30) using that SAME candidate tape.
This is a snapshot-defined cutoff; current-book reruns may differ. Do not take
c30 from one calculation and trades or DD from another.

as_of is the persisted requested evaluation boundary run.test_end (UTC ms),
whose source is request.end_time. It is NOT run.finished_at, the current clock,
or a guessed last UI refresh. Separately record the actual last complete market
bar and data coverage; a later requested boundary is not proof of complete data.

Variant B selects the FIRST persisted automatic candidate with as_of >= T*
and the 30-trade condition above. Determine order from provable persistence
chronology; tie equal timestamps by exact run ID lexical order. Timestamp/ID
ties and automatic origin must be evidenced. No ad-hoc/manual replay is eligible.
Persisted backend started_at/finished_at are recorded at save time in the source;
they are not proof of an independent scheduler event or the actual request start.
If chronology cannot be certified, record INVALID/first_run_unverifiable.

Selection precedes economic inspection and all subsequent validity checks.
An earlier eligible candidate with missing tape/cost/DD evidence BLOCKS the
assessment; do not skip it in favor of a later complete or favorable candidate.
Evidence must establish no earlier eligible saved automatic candidate was omitted.
A "latest run" panel, missing earlier archives, or the mere existence of a
completed run cannot establish firstness. Any later duplicate, retry or more
profitable result is excluded. The source/UI can also stop on an already-known
trade count before issuing a fresh run: existence of an eligible saved candidate
is NOT guaranteed and this document does not request that one be generated.

From the selected calculation, include ONLY closed trades with S <= closed_at
<= T*. Include every such trade, potentially more than 30 at the time gate,
including all simultaneous closes at T*. Later closes are excluded even if
they appear in the selected run's summary. Compute terminal closed-trade count,
net realized PnL, expectancy and PF from this filtered SAME tape, not the full
run's summary. Such future arithmetic is an evaluation requiring separate
authorization; no calculation is performed by preparing this document.

Keep selected-run net PnL and its actual stored cost components unchanged.
Label the cost convention current_book_at_as_of. This denotes the selected
as-of evaluation, NOT a claim that the book was observed at the precise as_of
millisecond: retain actual profile/book observation times and calculation/save
times separately, including any observation after the request boundary.
Never call it historical cost at T*. No new book, repricing, replay, interpolation
or mix of costs/tapes from different evaluations is authorized.

Use selected-run backend summary.max_drawdown_percent unchanged over its full
covered interval [S,as_of], including events/open positions after T*.
Do not truncate/reconstruct DD from a sparse chart. This longer-interval DD is
weakly no smaller than truncating THAT SAME recalculated equity path to T*.
It is NOT necessarily conservative versus a different historical calculation,
cost book or tape that existed at T*. Realized metrics and DD deliberately
have different end boundaries; disclose this in every eventual scorecard.

An open position at T* is excluded from closed-trade PF/expectancy and disclosed
separately, even if it closes after T* in the selected run. Identify it only from
certified archived position/trade evidence; do not invent its T* valuation.
Open exposure still affects the selected run's marked-equity DD. No forced close.

Required evidence record (unknown fields stay unknown):
- test/version/settings identity; S UTC ms and ISO-Z; selected run/tape IDs;
- c30, T*, as_of, actual last complete bar and coverage;
- delay_ms=as_of-T*, plus actual request/book/calculation/persistence times
  where recorded, with unknown fields identified;
- full selected tape and artifact hashes; filtered terminal trade IDs;
- complete candidate-order evidence proving firstness and automatic origin;
- actual cost profile/component IDs/hashes and observation times;
- backend full-interval DD source/value/2-decimal stored precision;
- open exposure evidence; both interim observations; actual decision/approval/
  publication evidence and its ordering relative to EACH test's T*.

An export hash certifies the exported bytes, not original historical immutability.
Source at main b200950 writes simulated_trades by trade ID and stores a run
summary; this alone does not prove a run-specific immutable trade archive.
Require certified association of the chosen run and tape, not today's mixed
or overwritten trade rows. This requirement may remain unsatisfied.

Before time AND trade gates, status is NOT_EVALUATED. Without a provable
eligible automatic run, firstness, source time, tape association, actual cost
provenance, coverage or backend DD, status is INVALID with the precise reason.
Unknown eligibility cannot be assumed true. Invalid/unavailable is not economic
FAIL. No fallback A, later favorable run, new simulation or OKX call is allowed.

Official alone has the economic verdict; Scale has its own terminal diagnostic.
A certified completed assessment is not repeated with extra trades or costs.
Ledger eligibility still requires BOTH tests' 30 days AND 30 trades and a new
Roman instruction, independently of this decision procedure.

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
for the variant-B selected full-interval run, not a frontend reconstructed fallback.
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
run and certified run/tape association, never infer them from a model label.

DD/cost/tape identity, firstness, decision timing and actual S values still need evidence verification.
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
