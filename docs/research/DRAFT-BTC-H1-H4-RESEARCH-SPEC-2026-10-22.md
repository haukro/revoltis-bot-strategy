# DRAFT ONLY — BTC H1 vs H4 preregistration candidate
Date 2026-10-22. Status: **DISCUSSION DRAFT, NOT LOCKED, NOT IMPLEMENTED**.
No code or market data reads, no backtest, no permission to merge/deploy/trade.
Separate from frozen Strategy B and running HYPE.

## Economic questions
- H4: Can a BTC/USDT spot exposure managed using only past realized volatility reduce downside versus risk-matched passive BTC after fees?
- H1: Does a slow BTC direction signal add predictive value **beyond H4**, after realistic OKX spot execution costs?

## Draft market and controls
- BTC/USDT OKX spot; USDT cash; long-only, no leverage, margin, synthetic shorts, perpetuals or funding.
- One BTC portfolio, one set of market/cost snapshots and common accounting for H1/H4 and benchmarks. Same signal clocks and realized-volatility estimator for comparability.
- Weekly signal decisions from completed UTC daily bars; earliest eligible execution after signal is causally observable, with explicitly locked delay and fill convention.
- Fixed trade-cost scenarios: 10 bp and 20 bp **per side** plus venue-consistent spread/impact; 40 bp per side stress, clear total vs fee units; no assumption of maker fills or free stops.
- Track target and actual weights, turnover, min size/lot, realized and marked equity, max drawdown, time in market and risk-adjusted excess return.
- No intrabar stop optimization, signal/exit rescue grids, stock-picking scanner or adaptation from paper HYPE losses.

## Proposed simple, testable prescriptions — not yet approved
H4 baseline: weekly BTC exposure scaled downward when 60-day trailing realized volatility rises, capped at 80% total wealth. Volatility target candidate 25% annualized (365-day calendar); min estimate floor 10%; target weight min(0.80,0.25/max(vol60,0.10)). USDT remainder. Volatility estimated using strictly past completed daily returns. Weekly rebalance only if absolute target-current weight difference >=5 percentage points, except documented mandatory cash/venue risk constraints.

H1: identical H4 cost, sizing and rebalancing engine, multiplied by slow directional eligibility indicator derived from 60-day BTC log return using only fully completed daily closes: indicator=1 when log(C[t]/C[t-60])>0, else 0. All cash when ineligible on scheduled weekly execution. This is one hypothesis, no optimization of 20/60/120 or BTC/ETH basket. 60 days is a pre-review **candidate**, not a result-selected optimum.

Benchmarks:
1. USDT cash (zero assumed yield unless separately observed).
2. Static BTC buy-and-hold adjusted to comparable average BTC exposure, with consistent trading-cost convention.
3. H4 (primary benchmark for H1). Compare excess-return and risk profile fairly; avoid claiming lower drawdown due solely to less exposure proves alpha.

## Critical design and inference issues to resolve *before any lock*
1. **Predeclared fresh test window:** all known/pre-reviewed BTC historical windows are exploratory/development; neither 2018–2021 nor 2022–2025 may be advertised as a virgin confirmatory fold. The only genuinely fresh forward data begin after immutable lock and instrument/data readiness. Retrospective analyses are descriptive, not a final alpha PASS.
2. **Sampling/power:** 6 or 12 months of slow weekly decisions cannot guarantee independent inferential power. Register a justified forward horizon and an explicit INSUFFICIENT_EVIDENCE verdict rather than manufacturing a low-variance statistical win. Do not repeatedly peek and stop at favorable profitability.
3. **One economic primary comparison:** H1 vs H4 net excess return and uncertainty under comparable exposure; H4 vs exposure-matched passive BTC as separate stated research question. Report DD, fee sensitivity and turnover as material guardrails. Decide primary test statistic, confidence rule, block dependence approach and multiplicity adjustment before evaluation.
4. **Costs:** freeze provenance of rate, fee tier, book-depth data, timestamped liquidity, order latency, minimum notional, rejection and order-fill model; weekly/monthly turnover estimates are HYPOTHESES, not inputs guaranteed by labels.
5. **Accounting:** define order chronology, same-bar protection, fraction rounding, fee reserve, partial fills, end-of-fold mark-to-liquidation, and volatility estimator warmup/missing-data invalidity.
6. **Safety:** distinguish an infrastructure kill switch due to malformed/stale data (protects execution) from an **extra price-based trade rule**. Proposed daily -25% emergency signal is **not adopted** without an independently justified locked rule, because it would contaminate weekly comparisons.
7. **Trial provenance:** inventory all previous NoFomo attempts, predeclared benchmark alternatives and relevant literature selection; DSR/PBO cannot be computed with a fictional trial count N=1 or from an absent variance estimate.
8. **Compatibility:** new work in isolated `backend/research/` (later only); no imports into `main.py`, no Supabase writes, no Vercel deploys, no HYPE/B switches and no modifications to Strategy D R2 without separate reviewed scope.
9. **Historical data availability:** BTC OKX spot listing/history and second-source price verification must be established *before* historical analyses; no invented OHLCV or look-ahead from retrospective universe selection.
10. **Decide what to do with existing D draft:** H1 BTC-only **is an alternative, not an already approved modification** of BTC/ETH D R2. A future single final specification must explicitly supersede or park D with documented reasons. No parallel cherry-picked winners.

## Proposed sequence of artifacts (no evaluation until approvals)
A. Adversarial review of this draft by independent reviewers, especially effective sample size, primary metric and datum contamination.
B. Freeze a reviewed `TEST-SPEC` with fixed algorithm, window, costs, benchmarks, decision rules and commit SHA, after explicit owner authorization.
C. Separately authorized implementation of isolated BTC/H4 research runner with **synthetic** data and checks for look-ahead, recursive indicators, invariants and cost arithmetic.
D. Separately authorized market-data acquisition + provenance certification, with NO strategy performance computation.
E. One authorized descriptive historical development run; don't call previously studied 2022–2025 clean OOS.
F. One fixed, future, forward-fold final run and PASS/FAIL/INSUFFICIENT_EVIDENCE only when the registered inference and validity conditions permit.
G. Realistic order-level paper execution only after separate gate; no live trading or ledger cutover implicit.

## Evidence source
- Claude independent audit on `claude/beautiful-bardeen-n4xe2x`: `docs/research/RESEARCH-AUDIT-2026-10-strategy-candidates.md`.
- Independent Grok review provided in chat, 2026-10-22; no direct GitHub attachment assumed.
- Existing BTC/ETH daily Strategy D R2 remains in `docs/strategy-d-and-hype-decision`.
- `main` baseline `b200950cffa6c349a6044c4ff70d28f9d7b9e8dd`.
