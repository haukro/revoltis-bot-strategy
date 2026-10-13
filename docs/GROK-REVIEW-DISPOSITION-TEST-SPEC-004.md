# Grok Review Disposition — PR70 / TEST-SPEC-004 Strategy D v1

Status: **GROK DOCUMENTATION DISPOSITION APPLIED — BLOCKERS OPEN**
Date: 2026-10-14 Europe/Bratislava.
Reviewed initial HEAD: `dcd4552f5290592c28564970af16f95f3565a1af`.
Review source: Grok response returned by Roman in chat; copied below.
Initial reviewer line references refer to that HEAD, not the expanded revision.
Gemini response has not been received. No agent vote or this document approves
a lock, research run, implementation, merge or deploy.

## ACCEPTED

| Point | Disposition | Documentation action | Remaining condition |
|---|---|---|---|
| G1 universe bias | ACCEPTED | D explicitly separates lock-date conditional-survivor and as-of designs; requires count/tie/membership manifest and hash. | Design and complete inputs not chosen; lock blocked. |
| G2 folds/warmup/contamination | ACCEPTED | Removes claim of a resolved common/staggered start; requires all-feature warmup, BTC 200 closes, exact folds and separate continuity/inspection certificates. | No certificate or clean holdout claim; data-only access not executed. |
| G3 chronology | ACCEPTED | Requires exact causal timeline, regime/band precedence and missing-bar semantics; corrects sign(0) implication of the existing formula. | No latency/filter cadence is chosen; lock blocked. |
| G7 DSR/trials | ACCEPTED | Adds primary methodological reference and required versioned equation, moments, frequency, dependence and trial-estimation inputs. | Formula conventions and research inventory still incomplete; citation alone does not resolve gate. |
| G9 empirical assertions | ACCEPTED | Removes unsupported stronger-evidence/order-of-magnitude statements as facts; retains only candidate cost/turnover motivation. | No new empirical advantage claimed. |
| Scanner gate tension | ACCEPTED | Withdraws trial-count-vs-backtest-length rule and cannot-be-QUALIFIED effect; any future OVERFIT_RISK is diagnostic absent separate policy approval. | No flag, DSR or policy-v4 runtime implementation. |
| H1 start dates | ACCEPTED | Relabels supplied timestamps as role-unverified; requires existing trading-start/time-gate provenance. | No runtime/DB read to certify dates in this session; terminal assessment blocked until reconciled. |
| H2 terminal observation | ACCEPTED | First valid observed existing snapshot after own30days AND>=30closedtrades, using all trades to that snapshot; no selecting favorable later results or forced terminal trade. | DD/open-position/cost conventions still require documented consistency with existing calculation. |
| H3 Official/Scale/ledger separation | ACCEPTED | Official-only verdict; Scale diagnostic; both-tests30/30 plus new Roman instruction remain necessary for ledger cutover. | No cutover or new experiment authorized by PASS. |
| H4 incomplete states | ACCEPTED | NOT_EVALUATED/INVALID separated from valid economic FAIL; no missing-data PASS. Specifies closed-trade expectancy and net PF edge cases. | Undefined DD/cost provenance blocks evaluation. |
| H5 registration timing | ACCEPTED | Retains filename but labels rule pre-terminal, after interim inspection, not blind pre-experiment registration. | No final HYPE verdict. |

## DISAGREED-MODIFIED

| Point | What is accepted | Modification and reason | Document effect |
|---|---|---|---|
| G6 small-sample inference | Twelve full monthly observations at most, unresolved DSR/PF adequacy, explicit INVALID/INSUFFICIENT_SAMPLE and a pre-run statistical-design decision. | Do not state quantified or universally negligible power without assumptions/calculation. No new power test was performed. Do not silently choose longer holdout, daily returns or replacement threshold. | Original numeric gates retained only as candidate proposals; statistical design blocks lock pending an explicit reviewed choice. |
| G4 labelled non-blocking | Sizing conventions can be specified without changing the economic hypothesis. | They are nevertheless mandatory before lock: absent cap order, covariance/EWMA/cash rules there is no unique reproducible prescription. Economic-hypothesis neutrality does not mean implementation ambiguity can remain. | Lists unresolved deterministic sizing choices as pre-lock blockers; no new numeric convention chosen. |
| G5 labelled non-blocking | Frozen asset/notional cost provenance, units, current-book label and missing-cost guard are required. | Costs/accounting also must be complete before lock because they define net outcomes and benchmark comparison. | Pre-lock costs/accounting requirements; no current-book request, cost update or runtime change. |

## IMPLEMENTED — DOCUMENTS ONLY

Modified:
- `docs/TEST-SPEC-004-basket-tsmom-d-v1.md`: retains candidate 20/60/120,
  25%, 40%/100%, weekly cadence and 5% band; adds blockers and removes
  unsupported performance/clean-holdout claims.
- `docs/preregistered-hype-v8-paper-decision.md`: numeric expectancy>0,
  PF>=1.10, DD<=15% unchanged; adds explicit own30day AND30trade eligibility,
  first-observation/all-closed-trades convention and validity states; keeps
  accounting/date verification explicitly pending.
- `docs/proposal-scanner-liquidity-filter.md`: trial-ledger requirements
  and diagnostic-only risk proposal; no qualification change.
- This disposition preserves the complete returned Grok review.

Recording a requirement is an implemented documentation change, **not**
resolution of its empirical inputs or a finalized lock decision.
No code, settings, runtime, ledger, migrations, frozen B/HYPE specification,
main branch or PR66/67/69 changes. No application endpoints or evaluation
jobs called. No optimizer run. No merge or production deployment initiated.
The GitHub/Vercel integration may automatically build a branch Preview on a
documentation commit; no application Preview is exercised by this workflow.

## Next state

**NOT CLEAN ENOUGH TO LOCK.** Gemini roadmap review remains pending.
Before any lock: choose universe/statistical design, finalize causal/sizing/cost
rules and data/fold provenance in a separately authorized documentation review.
No future data/performance action is authorized merely by accepting this review.
HYPE remains NOT_EVALUATED until its verified gates and accounting conditions.
The existing freeze and PR69 parked-C / no-optimizer-before-structural-B rule
remain in force. Structural repair B is not trading Strategy B.

## Original Grok response — as supplied

**1. BLOCKING findings**

- **G1 / D:28–38** — Universe selection uses lock-date (today’s) median 30d volume and spread rank to choose up to 3 additional pairs for a backtest that begins in 2019. This is not point-in-time. Concrete failure mode: survivorship and selection bias; historical performance is conditional on coins that survived and remained liquid until the lock date. Required pre-lock clarification: either (a) declare the entire evaluation as conditional on the today’s surviving basket and prohibit any claim of pure historical edge, or (b) specify a fully as-of historical eligibility design (listing dates, delistings, volume/spread measured only with data available on each decision date, deterministic tie-breaking, exact count rule). Hypothesis changes if the basket is treated as known in advance rather than selected with hindsight.
- **G2 / D:69–72 + BTC 200-day filter** — “Earliest common date ≥ 2019-01-01” conflicts with staggered entry (120+60 days). BTC 200-day SMA requires 200 closed bars; 180 days is insufficient. Concrete failure mode: undefined warmup, partial-month membership, and possible contamination of the terminal 12-month holdout by previously inspected BTC/ETH/SOL periods (including the momentum pre-screen noted in the C packet). Required clarification: explicit as-of membership rules, cash treatment before listing, minimum usable observation count, full warmup inventory for every feature, and a contamination certificate that separates availability/continuity from performance inspection. Hypothesis changes if the holdout is not certified unused.
- **G3 / D:40–47, 61–65** — Rebalance timing (“first daily close after Monday 00:00 UTC”), next-open fill, and regime-filter application are underspecified at bar boundaries. Concrete failure mode: possible look-ahead or non-causal execution (signal and fill meeting at the same timestamp; unclear whether BTC < MA flattens immediately or only at weekly rebalance; sign(0), missing bars, equality cases undefined). Required clarification: exact causal chronology, stale-data handling, and whether the filter overrides the no-trade band. Hypothesis changes if execution is not strictly causal.
- **G6 / D:82–88** — 12-month holdout yields at most 12 monthly observations. Deflated Sharpe > 0.95 and monthly PF ≥ 1.2 on this sample size have negligible power and are not a reliable inference. Concrete failure mode: gates that cannot support the claimed statistical claim. Required clarification: either lengthen the holdout, change the unit of evaluation, or replace DSR with a pre-specified rule whose small-sample properties are documented; define INVALID / INSUFFICIENT_SAMPLE for missing or non-finite data. Hypothesis changes if the statistical claim is retained as written.
- **G7 / D:88 + scanner:23–27** — No exact DSR definition, return frequency, serial-dependence treatment, or trial-count accounting. “One grid-free run” does not erase prior inspected baskets, timeframes, or scanner cycles that informed selection. Concrete failure mode: trial count is not operational; DSR cannot be computed reproducibly. Required clarification: primary reference, moment conventions, effective trial count with uncertainty, and explicit family boundaries. Hypothesis changes if the 0.95 threshold is kept without a computable procedure.

**2. NON-BLOCKING caveats**

- **G4 / D:49–61** — EWMA decay convention, covariance estimation timing, annualization, singular-covariance handling, vol floor, cap-order, and cash/fee-reserve rules are unspecified. These can be fixed without changing the economic hypothesis, but must be deterministic before lock.
- **G5 / D:63–64** — Cost model is referenced as “stored” but lacks asset-specific frozen provenance, date/venue, units, and fail-closed behavior for missing costs. Label any current-book application to history explicitly; do not treat it as historical L2.
- **G9 / D:21–26** — Empirical claims (“academic evidence stronger…”, “daily moves an order of magnitude larger”) have no primary references or quantitative bounds. Mark as hypotheses or supply citations with scope limitations.
- Scanner proposal lines 23–28 introduce an OVERFIT_RISK flag that changes Policy v4 qualification semantics without a formula linking trial count to minimum backtest length. Treat as a documentation tension, not a silent new gate.

**3. Costs, basket selection and trial-count/DSR assessment**

Basket selection on lock-date liquidity is the dominant bias source (G1). Costs are currently a rationale plus an unspecified stored model, not a frozen historical series; any sensitivity must be pre-specified. Trial accounting is incomplete: three fixed horizons inside one signal are not three independent strategies, yet prior research (C packet inspections, scanner cycles, abandoned runs) must be inventoried. DSR > 0.95 on ~12 monthly points is not supportable without a documented small-sample procedure. No new calculations were performed; these are specification gaps.

**4. HYPE decision-document ambiguities (no economic verdict)**

- Lines 11–14 vs 20: UTC start timestamps vs UI earliest-end dates are not reconciled; unclear whether starts are warmup or trading starts.
- Lines 41–51: stop criterion is “minimum 30 days **and** 30 closed trades”, but the document does not define the exact evaluation instant when both conditions are first satisfied, how additional trades after day 30 are handled, or terminal open-position and cost treatment.
- Official-only economic verdict is correctly separated from Scale and from the ledger’s both-tests 30/30 cutover, but the document must state that PASS authorizes only a new preregistered experiment, not cutover.
- “Anything else is FAIL” must explicitly exclude incomplete/not-yet-evaluated states. The 18/17-trade snapshot is recorded but is not an evaluation.
- Thresholds were set after an interim observation; this must remain labeled as a pre-terminal decision rule, not a fully blind registration.

**5. LOCK VERDICT**

Not clean enough to lock as written. Necessary conditions before any lock: (1) point-in-time or explicitly conditional universe rule with manifest/hash, (2) complete causal chronology and warmup definitions, (3) contamination certificate for the terminal holdout, (4) computable DSR/trial-count procedure or replacement of the statistical gate, (5) deterministic sizing, cost, and missing-data rules. These are specification clarifications, not parameter searches. No runtime, evaluation, or merge authorization is implied.
