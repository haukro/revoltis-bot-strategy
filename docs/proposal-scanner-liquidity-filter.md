# Proposal — Auto Market Scanner as a liquidity filter only

Status: **PROPOSAL FOR REVIEW — NOT IMPLEMENTED, NO POLICY CHANGE**

Grok feedback on the initial draft is recorded in
`GROK-REVIEW-DISPOSITION-TEST-SPEC-004.md`. Gemini's later roadmap response
and consistency check are in `GEMINI-REVIEW-DISPOSITION-TEST-SPEC-004.md`.
No scanner, optimizer, qualification, ranking, UI or runtime change is approved.

## Problem and boundary

The initial proposal describes universe shortlisting and repeated parameter
selection without an operational cumulative research-trial ledger.
Selecting finalists across many attempts requires documented selection history;
this document does not certify any existing candidate's adjusted performance.

## Candidate tradability-only design

1. Use declared eligibility/liquidity metadata: quote volume, spread,
   listing age and data completeness, not backtest profitability, ATR fit
   or Bollinger crosses.
2. Research uses the independently frozen universe design in its spec,
   not a discretionary scanner output. Historical studies need an as-of
   eligibility design or explicit surviving-basket conditional interpretation.
3. Define deterministic ranks/ties, metadata times/windows, asset-count rule,
   exclusions, missing inputs and a versioned manifest/hash before use.

These are proposed requirements, not changes to policy v4 or running scans.

## Trial ledger proposal — not an executable statistical rule

Record attempt ID, family/spec revision, basket and dataset identity, timeframe,
parameter/filter/cost configurations, intent, execution outcome and whether
performance was inspected. Retain abandoned and invalid attempts with reasons.
Distinguish an identical retry with no new inspected result from a new adaptive
research choice; never erase earlier inspected results by retrying or renaming.

`markets * variants * timeframes` is only a possible nominal combination
inventory when those alternatives were actually tried, not a universal count
of independent trials. Three fixed horizons within one composite signal are
not automatically three selected strategies. Record sequential scanner cycles,
prior basket selection and other inspected alternatives that informed selection.

A future DSR report needs an explicit primary reference, return frequency,
moment/serial-dependence conventions, and raw/effective trial procedure with
uncertainty. Unknown trial history must remain unknown, not default to one.
See TEST-SPEC-004's statistical-design blockers for the research requirements.

## OVERFIT_RISK — diagnostic proposal only

Withdraw the initial dimensional comparison "trial count exceeds minimum
backtest length": no formula, units or assumptions were specified.
Until a reviewed procedure exists, no quantitative flag criterion is defined.

If a future descriptive OVERFIT_RISK warning is adopted, it must not change
QUALIFIED, policy v4 gates, ranking or paper eligibility under this proposal.
A new mandatory qualification gate or fresh-forward-fold requirement is a
separate policy decision and implementation scope requiring Roman's approval.
This resolves the original "gates unchanged" versus "cannot be QUALIFIED"
contradiction without silently introducing a new gate. Gemini's suggestion
to integrate that superseded undefined gate into central policy v4 is
DISAGREED-MODIFIED: a diagnostic proposal is not authority to change policy.
Central qualification documentation and runtime policy remain unchanged.

## Not changed

Policy v4 gates, ranking, presenter, optimizer, scanner runtime, UI,
running HYPE/Scale/ZEC B tests, ledger, live trading and freeze.
No new optimizer run before separately authorized structural repair B.
