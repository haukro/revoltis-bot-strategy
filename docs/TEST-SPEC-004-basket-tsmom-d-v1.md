# TEST-SPEC-004 — Strategy D v1: daily long-only volatility-scaled trend on a predeclared basket

Status: **DRAFT FOR REVIEW — NOT LOCKED, NOT IMPLEMENTED, BLOCKED BEFORE LOCK**

Grok reviewed the initial draft at `dcd4552f5290592c28564970af16f95f3565a1af`.
His verdict is **not clean enough to lock as written**. See
`GROK-REVIEW-DISPOSITION-TEST-SPEC-004.md` for individual dispositions.
Gemini roadmap review is processed in
`GEMINI-REVIEW-DISPOSITION-TEST-SPEC-004.md`: appropriate draft candidate,
with Grok methodology blockers still open. This documentation update does not approve
a lock, implementation, data download, performance run, merge or deploy.

## Research boundary and hypothesis

Candidate hypothesis: a slow, long-only, multi-horizon spot trend basket with
volatility sizing may have positive net returns after its specified costs.
This is a hypothesis, not evidence of profitability. Slower bars, lower
turnover and volatility scaling do not establish an edge by themselves.

The proposed design differs from the failed 1h N=24 / Wilder ATR(24) / 2xATR
coin-by-coin replication, but remains trend/momentum research. Gemini's
family verdict is accepted as roadmap suitability for a draft, not proof of
a distinct profitable economic edge. `RESEARCH-ROADMAP.md` now explicitly
permits spot or futures basket directions; this proposal remains **spot-only**.
No shorts, leverage or perpetuals are added. The remaining methodology and
final lock decisions are not resolved by the roadmap alignment.

No rescue of closed A/C branches, parameter grid or result-driven retuning.
TEST-SPEC-002 ZEC TSMOM B remains unchanged and blind through
2026-12-23 11:59:59.999 UTC. HYPE paper tests remain unchanged.

The initial draft's assertions about stronger academic evidence and daily
moves being an order of magnitude larger are withdrawn as established facts.
The cost/turnover motivation is provisional and must be tested under a
declared model; no return magnitude or historical cost advantage is assumed.

## Candidate universe — G1 unresolved choice

OKX spot, quote USDT. Candidate core: BTC/USDT, ETH/USDT, SOL/USDT; up to
3 additional pairs, excluding stablecoins and wrapped/leveraged tokens.
The original proposal required 3 years of daily history and lock-date
median 30d quote-volume / median-spread ranks.

**Lock-date selection is not point-in-time historical membership.**
Before lock, choose and record exactly one design:

1. **Conditional surviving basket:** retain a basket selected at lock date,
   publish its full selection manifest, and label all historical performance
   as conditional on that surviving basket. Do not claim a bias-free historical
   universe or general historical edge.
2. **As-of historical design:** fully specify eligibility and membership using
   only information available at each declared selection date, including
   listing/delisting history, as-of volume/spread inputs, exclusions,
   deterministic ranks/ties and exact asset-count rule.

No option is selected by this review update. The following are blocking:
selection date(s); exact additional-pair count rule; metadata window and
provenance; missing spread/history behavior; delisting treatment; membership
schedule; weights/cash before eligibility; full manifest and content hash.
Eligibility/liquidity metadata may be inventoried in a later authorized
data-only step; inspecting strategy evaluation outcomes is a separate action.
No pair or membership rule may be selected using its backtest profitability.
Freeze the chosen design before evaluating it; a predeclared as-of schedule
is distinct from discretionary additions/removals after lock.

## Candidate signal and regime filter

At fully closed UTC daily bars, `r_k` is the log return over k days,
k in {20, 60, 120}. `s = mean(sign(r_20), sign(r_60), sign(r_120))`.
Use `positive_signal = max(s, 0)` for long-only sizing.
`sign(0)=0`; zero returns can produce values beyond the initial draft's
four-value list. This is a correction to the stated formula, not a new grid.

Candidate filter: basket exposure is zero when BTC daily close is below its
200-day simple moving average. Equality behavior and the causal timing of
exit/re-entry must be finalized before lock. Missing/nonfinite features
must never be coerced into a favorable signal.

## Causal chronology — G3 required before lock

Candidate cadence remains weekly, described in the first draft as the first
daily close after Monday 00:00 UTC, with a next-daily-open fill.

The final spec must give an exact UTC timeline for bar availability, signal,
volatility/covariance estimates, target calculation, order eligibility and fill.
An adjacent daily close/open boundary is not proof of a realizable fill after
observing the close. Specify executable latency/feed assumptions and whether
that boundary price is only a research convention. No later bar may be read
to decide an earlier trade.

Required decisions, none silently chosen here:

- exact weekly close timestamp and earliest causal fill;
- filter evaluation cadence: daily next-bar flattening or weekly-only action;
- whether filter exits and sell-to-zero override the no-trade band;
- equality at the BTC SMA, missing/stale/duplicate bars and unavailable fills;
- zero/missing/nonfinite input behavior and deterministic cash handling.

Missing required execution data invalidates the run; no delayed, interpolated
or favorable-price fallback may be introduced without prior specification.

## Candidate sizing — G4 deterministic prescription required

- Asset volatility: proposed 60-day EWMA of daily returns, annualized.
- Raw weight: `positive_signal_i / vol_i`.
- Proposed basket target: 25% annualized ex-ante volatility using a
  60-day covariance estimate.
- Caps: single asset <=40%; gross exposure <=100%; no leverage.
- Remainder held in USDT.
- Proposed no-trade band: absolute target/current difference <5% of equity.

Before lock specify EWMA decay/span/half-life and initialization; covariance
method/window/weights; annualization basis; all estimation timestamps;
zero/nonfinite volatility, floor and singular/non-PSD covariance behavior;
normalization/scaling/cap order; post-band and post-rounding actual weights;
fee reserve, cash feasibility, lot size and minimum notional.
25% is a target, not a guaranteed achieved value when caps/band bind.
These are blocking reproducibility requirements even though they need not
change the economic hypothesis. No unreviewed numeric convention is inserted.

## Costs and accounting — G5 required before lock

Candidate per-side model: taker fee + half-spread + impact. Before lock define
asset/notional-specific provenance, venue/tier, timestamps, quantity/currency
units, conversion, fill/slippage assumptions, and immutable cost identities.
Missing/nonfinite cost inputs cannot become zero-cost assumptions.

If current-book data are applied to historical prices, explicitly label
`current_book_snapshot_not_historical_l2`; do not imply historical L2
coverage. Any cost sensitivity must be declared before results and cannot
be used to choose the model which passes.

Specify portfolio equity and mark timestamps, turnover, final holdings and
liquidation convention, costs at both ends, USDT cash yield/depeg assumptions,
and equal-weight B&H entry/exit/rebalance and membership rules.
The strategy and benchmark must use the same declared window and membership
interpretation. No forced terminal trade or new cost snapshot is authorized
by this document update.

## Data, membership, warmup and contamination — G2

The original earliest-common-date >=2019-01-01 rule conflicted with staggered
entry after 120+60 days. Neither start convention is selected here.
The lock must fix exact dates, membership/cash treatment, partial-month rules
and the minimum usable observations under the chosen universe design.

Warmup must inventory every feature. In particular BTC's 200-day SMA needs
200 fully closed daily closes; a generic 180-day warmup is insufficient for it.
Return horizons also require their reference close; volatility and covariance
must have their own explicit initialization. Warmup cannot generate scored
pre-fold trades.

Produce, in a separately authorized step, a versioned data-only certificate:
pair/instrument IDs, snapshot hashes, timestamp conventions, expected/actual
bars, gaps/duplicates, listing/delisting and required feature/fill coverage.
Separately inventory prior performance/selection inspection for every proposed
asset and period, including scanner cycles, abandoned runs and pre-screens.
Previously inspected BTC periods noted in the C review packet must be checked;
the proposed terminal period is **not certified unused**.

Original candidate split: development ends 12 months before the frozen
snapshot date; terminal holdout is the final 12 months. This is a draft split,
not permission to reuse an inspected period. If unused status cannot be
certified, stop and report that fact; choose a genuinely unused evaluation
design only by a new recorded pre-run decision, never by inspecting alternatives
and selecting their results. B's official forward result remains blind.

## Evaluation and gates — G6/G7 unresolved statistical design

Original candidate unit: monthly portfolio returns plus per-rebalance tape.
The earlier approximately 80 development months are **not verified**; report
actual eligible complete months from the chosen manifest before lock.

Original proposed gates, retained for discussion rather than activated:

1. Net mean monthly return >0 after costs;
2. monthly-return profit factor >=1.2;
3. max drawdown <=30%;
4. return/maxDD above equal-weight B&H of the same basket;
5. Deflated Sharpe >0.95 with declared trials;
6. development and holdout both satisfy the chosen gates.

A 12-month terminal holdout has at most 12 full monthly observations.
Its statistical adequacy is unresolved. No power calculation was performed,
so this document does not assert a universal numeric power result.
Before lock, record a reviewed decision to keep the design with justified
small-sample properties, lengthen the holdout, change the inference unit, or
replace the statistical gate. No option, frequency, new threshold or holdout
length is adopted here. Those changes affect the evaluation design and require
a new fixed version before any result inspection.

Define arithmetic/geometric and net return conventions; PF with zero losses
or no profits; daily/monthly DD sampling; total-return/maxDD and zero denominator;
terminal partial month; finite-value guards; minimum usable sample.
States must distinguish NOT_EVALUATED, INVALID, INSUFFICIENT_SAMPLE and an
economically valid PASS/FAIL. Missing data never silently pass or become an
economic failure result.

### Deflated Sharpe and trial accounting

Primary methodological reference:
David H. Bailey and Marcos Lopez de Prado (2014), "The Deflated Sharpe Ratio:
Correcting for Selection Bias, Backtest Overfitting, and Non-Normality",
The Journal of Portfolio Management 40(5), 94-107.
https://doi.org/10.3905/jpm.2014.40.5.094
Author manuscript: https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf

This citation does not make the candidate gate operational.
Before lock provide the exact equation/implementation reference and version,
return frequency, sample size, moment estimators, serial-dependence treatment,
benchmark Sharpe construction, and raw/effective trial-count procedure with
its assumptions and uncertainty. Distinguish DSR's probability convention from
an ordinary Sharpe ratio; do not apply an annualized SR to incompatible moments.
If required inputs/history are unavailable, do not certify the DSR gate.

Trial history must include prior inspected/tested baskets, settings, timeframes,
filters, cost alternatives, scanner cycles and abandoned results that informed
selection. Three fixed horizons combined in one signal are not automatically
three independently selected strategies. One grid-free run does not reset
research history. Define family boundaries before evaluating any candidate.

## Diagnostics and closure

Report, without selecting on them: exposure, turnover, achieved volatility,
cost share, per-asset contribution and declared regime-period performance.
Final PASS/FAIL rules and sufficient-sample handling are pending the above
decisions; no D verdict exists. A valid FAIL of the finalized D v1 closes that
prescription without a rescue variant on its used folds.

## Pre-lock blockers

G1 universe design/manifest; G2 dates, warmup and contamination certificate;
G3 causal timeline/filter/band semantics; G4 deterministic sizing;
G5 costs/accounting; G6 inference/sample design; G7 computable trial/DSR rules;
final methodological clearance and Roman's explicit lock decision.
Gemini's suitable-draft verdict does not close Grok's blockers.
"Recorded requirement" is not "resolved".
Only a separately authorized final spec lock can close this checklist.

## Out of scope

Live trading, shorts, leverage, perpetuals/funding, intraday entries,
running HYPE changes, ZEC B changes, optimizer jobs, runtime implementation,
migrations, DDL, deployment and ledger cutover. Existing freeze controls.
