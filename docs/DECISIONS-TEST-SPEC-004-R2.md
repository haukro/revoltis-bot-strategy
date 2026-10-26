# Decisions and dispositions — TEST-SPEC-004 R2 / HYPE decision text

Status: **DIRECTION CONFIRMED; TECHNICAL R2 DRAFT AWAITING REVIEW; NO LOCK**
Date: 2026-10-14 Europe/Bratislava.
Baseline before this edit: 7b4ec031166e987d90becf43a9961e7b4e3234f8.
Roman replied "ok urob co treba" to Codex's six recommended decision packages.
This authorizes documentary preparation/recording in PR70. Final lock, merge,
implementation, data acquisition, evaluations and production remain separate.
Prior Grok/Gemini responses are preserved in their disposition files; they
reviewed earlier prescriptions, not these newly completed R2 choices.

## Recorded direction — six packages

| Package | Recorded choice | Scope/qualification |
|---|---|---|
| 1 basket | BTC/ETH only; zero additional assets | Historical as-of availability; selected-survivor development labelled conditional. No false historical selection claim. |
| 2 holdout | Forward after final lock and separately authorized readiness/start | No terminal historical12month holdout without a certified unused period. This version chooses forward. |
| 3 duration/unit | Fixed6calendar months; daily statistical data, monthly reporting | Proposed120daily minimum is not a guarantee of power. No extension until PASS. |
| 4 trials | Broad relevant trend discovery history, including informative B/C attempts | Preserve original timeframe/direction labels; no blind B outcome inspection, unknown history not N=1. |
| 5 core rules | Target cap40%, weekly rebalance, original band5pp | Two-asset capped targets max80%. Numerical technical additions below remain R2 review candidates. |
| 6 HYPE | Disclose18trade interim threshold timing; exactT* only with reproducible evidence; PASS permits drafting only | No new simulation/cost capture; missing exact-cutoff evidence blocks assessment. No ledger/trading authorization. |

## Disposition table — Claude suggestions checked against Grok/Gemini

ACCEPTED denotes accepted review/direction, not permission to execute.
DISAGREED-MODIFIED preserves the issue while documenting the concrete correction.

| Finding | Claude proposal | Disposition / R2 text | Changes hypothesis/test? | Remaining decision/input |
|---|---|---|---|---|
| G1 universe | As-of basket, BTC/ETH core | ACCEPTED direction; exactly2fixed markets, instrument history as-of, historical development labelled selected-survivor conditional | Yes versus original BTC/ETH/SOL+0..3 | Source/listing/lot manifest before launch; R2 review |
| G2 forward holdout | Forward afterlock,6or12months | ACCEPTED direction;6fullcalendar months after separately authorized readiness, immutable T0/T1; development<=lock | Yes fold/unit | Continuity/inspection certificates; no historical clean claim |
| G2 warmup | BTC200daily closes | ACCEPTED; feature-specific200/121/61closed closes for BTC MA/120day return/60daily covariance returns | Clarifies availability | Source timestamps and complete certified warmup |
| G3 chronology/equality | Weekly BTCfilter, equalityflat | ACCEPTED technical candidate; Monday decision/Sunday features, Tuesday proxy fill after1fullbar delay, BTC<=MA flat, no intraday override | Yes timing/regime | New exact timing requires R2 Grok clearance |
| G4 cap/band |40% and2pp band | DISAGREED-MODIFIED; user-confirmed Codex direction retains5pp, not2pp; explicit band overrides and cap interpretation | Yes different turnover | Verify deterministic risk/cash semantics before lock |
| G4 volfloor |10% annual floor | ACCEPTED as R2 technical PROPOSAL, not performance-selected; half-life60/truncated60 weighted population covariance,365annualization | Yes sizing | R2 reviewer signoff; no observed calibration |
| G4 covariance |Diagonal for singular/notPD | DISAGREED-MODIFIED; only finite PSD numerical singularity gets logged diagonal fallback; material negative eigenvalue/nonfinite=>INVALID | Yes risk behavior | Tolerance/floor order reviewed before lock |
| G4 reserve |0.5% equity cash | ACCEPTED as R2 technical PROPOSAL; pre-trade reserve/cash scaling/lot rounding declared; proposed E0=100USDT | Yes execution/cost basis | E0/notional conventions reviewed; no funding/stake change |
| G5 costs |Frozen provenance | ACCEPTED; per-asset frozen rates/notional validity manifest, current-book-history label, costed equity and fixed B&H | Defines net outcomes | No observed rates invented; profiles obtained only under future authorization |
| G6 sample |<120or nonfinite=>INSUFFICIENT_SAMPLE | DISAGREED-MODIFIED; low valid sample=>INSUFFICIENT_SAMPLE, malformed/nonfinite inputs=>INVALID;120not power proof | Yes stats/unit | R2 mean-unit/monthly-PF/DSR review; no market inference yet |
| G7 trialfamily |Daily long-only includingB/C | DISAGREED-MODIFIED; wider trend inventory with truthful old labels and unknown inputs; canonical DSR candidate plus proposed dependence safeguard | Yes inference | N/trialSRvariance may be unavailable; no statistical PASS by default |
| G8 HYPE accounting |ExactT*, one recalculation | DISAGREED-MODIFIED; require already-existing same-cutoff tape/DD/cost evidence; real book/calculation times recorded | Yes decision sampling | Scheduler does not promise exact historical capture; no new computation |
| G9 claims |Hypotheses unless sourced | ACCEPTED; no claim slower timeframe/vol sizing proves edge | No new alpha premise | Supply bounds/source for any future empirical assertion |
| Scanner |Risk flag/trial ledger | ACCEPTED diagnostic-only; no policy-v4/QUALIFIED gate change | No runtime gate | Future statistical/policy work separately authorized |
| HYPE1starts |Use trading_start_time | ACCEPTED; frontend saved.started_at->request->run.progress field verified fromsource; erroneous old labels removed | No strategy change | Actual values not read; no guessed timestamps |
| HYPE2T*sample |max(S+30days,c30); closed<=T* | DISAGREED-MODIFIED precision proposal; single certified tape, exact-cutoff evidence orINVALID | Yes sample vs first-observed R1 | Evidence unavailable status must remain explicit |
| HYPE3open/cost |Openoutside; costsatT* | DISAGREED-MODIFIED; openoutsidePF/expectancy but inside backend marked DD; distinguish book_ts,test_end,finished_at | Accounting definition | No later currentbook result masquerading as oldcost |
| HYPE4states/PASS |BeforeT*NOT_EVALUATED; draftingonly | ACCEPTED; own gates, validity states, Official-only verdict, Scale diagnostic; both-tests30/30ledger still separate | No trade change | No valid verdict until evidence/gates |
| HYPE5posthoc |Thresholdsafter18trades | ACCEPTED; explicitly pre-terminal decision, not blind preregistration; thresholdsunchanged | No numerical gate change | Disclosure retained |

## New technical proposals needing R2 review

These were completed as draft choices under the instruction to prepare the
technical prescription. They are not retroactively attributed to reviewer votes:
- one-full-day causal delay and precise Monday/Tuesday weekly timeline;
- EWMA half-life/truncation/covariance/floor/fallback conventions;
- fee reserve, proposed100USDT accounting basis, rounding/cash/cap/band semantics;
- six full calendar months from predeclared first-month-start T0 after readiness;
- daily mean proposed as positive-return gate; monthly PF1.20 remains monthly;
- canonical DSR with fully declared inputs, plus proposed7day-block/10,000rep
  seed4001 dependence check at p<0.05.
No statistical procedure has been certified by the old reviews. An unavailable
trial inventory/SRvariance or failed certification cannot become overall PASS.
Grok may reject/modify these before lock; do not run a grid to choose among them.

## Historical and runtime limits

Source-only HYPE inspection at b200950:
main.tsx46-60 time/trade gate;406/448 and502/538 trading-start propagation;
main.py2232-2254 fresh currentcost per simulation;2264stored start and summary;
simulation.py131-148costcomponents/marked equity;198-204eventDD;335summary;
main.tsx62closed-trade-only fallback is not interchangeable with backend DD.
No source changes, application endpoints, OKX downloads, DB reads/writes,
stored paper-result inspection or performance calculation occurred.
Numeric S values and exactT* reproducibility remain unverified, not "fixed live".

## Freeze and next stage

PR66/67 unchanged; PR69 parkedC; no optimizer before structural metadata/archive
repair, no merge/production deploy, ledger/migration/DDL or HYPE/ZECB/trading change.
The only next product of this update is documentary R2 review.
After review, final explicit lock and separate implementation/run authorizations
are still required. Neither PR70 draft text nor this decision record starts
the forward clock. Preserve all earlier reviews and original results.
