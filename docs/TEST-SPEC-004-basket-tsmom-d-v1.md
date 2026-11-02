# TEST-SPEC-004 — Strategy D v1: fixed BTC/ETH daily long-only trend

Status: **DRAFT R2 FOR METHODOLOGY REVIEW — NOT LOCKED, NOT IMPLEMENTED**

Roman's 2026-10-14 "ok urob co treba" authorizes recording the recommended
research direction and completing its documentary prescription in PR70.
It does not authorize merge, final lock, code, data acquisition, evaluation
or production. See `DECISIONS-TEST-SPEC-004-R2.md`.
R2 changes the candidate basket and evaluation design; the initial Grok/Gemini
reviews do not certify this revised design. A new methodology review is required.

## 1. Hypothesis, boundaries and fixed basket

Hypothesis: fixed daily multi-horizon positive trend signals on BTC/ETH,
long-only with volatility sizing and a BTC regime filter, can generate positive
net forward portfolio returns under the declared cost model.
This is a candidate hypothesis, not evidence of alpha.
A/C remain closed; frozen trading Strategy B and HYPE are not changed.

Exactly BTC/USDT and ETH/USDT, OKX spot; zero additional coins, no SOL,
no scanner winners, no rotation to another market after observing results.
Before launch declare canonical exchange instrument IDs and availability,
listing status, lot sizes, minimum notional and timestamped source manifest.
Historical availability uses information as of each historical date; no
pre-listing data, manufactured backfill, interpolation or synthetic instrument.
BTC/ETH were selected today: historical development is conditional on this
selected surviving pair set, not proof of point-in-time historical coin selection.
The untouched claim is restricted to the separately controlled forward fold.

All pre-lock history is development/discovery, including previously inspected
periods. Inventory prior inspections; do not relabel any historical final12months
as untouched. Historical results may not be used to select a rescue variant.

## 2. Direction and approved candidate choices

- Closed UTC daily bars; log-return horizons 20, 60 and 120 days.
- Long-only; USDT cash otherwise; no borrow, perpetuals, funding or leverage.
- Target annual volatility 25%; per-asset target cap40%.
- Weekly decision/execution schedule; no-trade band5 percentage points of equity.
- Fixed6-calendar-month forward; daily statistical observations; monthly reporting.
- Broad related-trend research trial inventory, including informative B/C attempts.

Because there are two capped assets, capped TARGET exposure is at most80%;
actual weights drift with prices between rebalances. This is an explicit
limitation of this v1, not an instruction to increase caps or add a third coin.

R2 technical proposals below are fixed candidates for the next review, not
claimed to have been chosen from performance or fully approved for lock:
vol floor10% annualized; fee cash reserve0.5%; EWMA conventions; fill latency;
covariance fallback; benchmark accounting; sample minimum120; dependence test.
No numerical market calculation has been performed to select these values.

## 3. Data clock, startup and fold

All time is UTC. A daily bar covers [day00:00, nextday00:00), with the exchange
last-millisecond close timestamp; only confirmed completed bars are features.
Prices must be finite and strictly positive, volumes finite and nonnegative;
duplicate/gapped required timestamps invalidate the affected certified dataset.
Do not omit a losing date, fill a missing return with zero or extend a fold.

Lock time L is the future explicitly authorized final spec merge/recorded SHA.
Operational readiness R is the certified isolated runner/dataset/cost manifest
ready time. Launch authorization A is a separate future Roman instruction.
T0 is the first00:00UTC calendar-month start strictly after max(L,R,A).
Record exact T0/T1 and all identities in the launch manifest before any official
performance observation. No6month clock starts merely because this draft exists.
T1 is00:00UTC exactly six calendar months after T0; fold=[T0,T1).
First and last monthly reporting bins are therefore full calendar months.
No result-dependent extension, early successful verdict or replacement fold.

Development ends at a frozen snapshot cutoff <=L and strictly before T0.
Any interval between that cutoff and T0 is feature warmup only, not an alternative
scored development/holdout. Forward observations are prices/equity after T0.
Historical bars seed features, never generate scored pre-T0 orders.

Before T0 require consecutive200 closed BTC daily closes for SMA200, at least
121 closed closes per asset for120day returns, and61 closed closes per asset
for60 daily returns used by covariance. These are feature-specific requirements;
the full BTC200bar warmup dominates its shorter horizons. First missing requirement
blocks launch; later required gaps invalidate the run rather than creating a fallback.

Certificates: (a) instrument/data availability and continuity only;
(b) prior performance-inspection/selection inventory with time ranges and sources.
Neither certificate authorizes downloading data now or examining frozen B metrics.
Forward settings remain fixed. Operational health inspection cannot be used to
adapt parameters, timing, costs, gates or market membership to interim returns.

## 4. Exact signal and weekly causal chronology

For eligible completed day d:
r_k(d)=ln(C_d/C_(d-k)), k in{20,60,120}.
sign(x)=-1 if x<0, 0 if x=0, +1 if x>0.
s_i=mean(sign(r20),sign(r60),sign(r120)); a_i=max(s_i,0).
Zero-return cases are included; do not assume only four possible s values.
BTC C_d<=mean(last200completed BTC closes) sets every a_i=0.
Equality means flat. Invalid features mean INVALID, not a profitable flat result.

R2 timing proposal: decide each Monday00:00UTC after Sunday's bar has completed;
use Sunday's close and features through Sunday only. Store immutable target
weights and feature/source identities. Execute at Tuesday00:00UTC's daily-open
research proxy, after one full daily bar of delay.
No Monday bar information can revise that stored target; Tuesday prices are
execution/valuation inputs only, not signal-selection inputs.
This conservative full-bar delay differs from the ambiguous original
adjacent-close/open wording and must be checked in the second Grok review.
An OHLC open+cost is a research fill proxy, not a claim of executable live fills.

BTC filter is evaluated weekly only, on the same signal clock. No intraday
or unscheduled daily flatten. A flat regime or asset a_i=0 requires sell-to-zero
at the scheduled fill and overrides the5pp band; no new SL/trailing exit.
First scheduled execution inside the fold can enter; start equity is cash.
An order generated inside the fold but scheduled at/afterT1 is not executed.
Missing required scheduled price/bar invalidates the run; no next-available fill.
Order keys include spec/version, decision time, execution time and instrument;
repeating the same input cannot create another order/event.

## 5. Volatility, covariance and target-weight algorithm

Use exactly last60 daily log returns through the signal day for both assets.
Decay lambda=exp(-ln(2)/60), a60day half-life, truncated to60 observations.
For oldest-to-newest j=0..59, p_j=lambda^(59-j)/sum(lambda^(59-k),k=0..59).
Weighted means mu_i=sum(p_j*r_ij).
Population covariance V_ik=sum(p_j*(r_ij-mu_i)*(r_kj-mu_k)).
Annual covariance Sigma=365*V; crypto clock uses365, not252.
Volatility_i=sqrt(Sigma_ii); inversion uses max(volatility_i,0.10).

Require finite symmetric covariance. Numerical tolerance
eps=1e-12*max(1,max(diagonal(Sigma))). Eigenvalue below-eps is INVALID.
A finite PSD matrix that is singular/numerically singular (minimum eigenvalue<=eps)
uses diagonal Sigma for that decision, logged COV_DIAGONAL_FALLBACK.
Nonfinite or materially indefinite matrices do not get a convenient diagonal fix.
Raise each used diagonal by max(0,0.10^2-current_diagonal); retain valid off-diagonals.
This produces the covariance used for target risk, consistent with the vol floor.
Do not use a pseudo-inverse or choose a fallback by its PnL.

u_i=a_i/max(volatility_i,0.10). If sum(u)=0, targets are zero.
Otherwise b_i=u_i/sum(u). v=sqrt(b' Sigma_used b), which must be finite/positive.
Scale h=min(0.25/v,0.995), and set w_i=min(0.40,h*b_i).
No renormalization or redistribution after capping. Cash=1-sum(w).
25% is a TARGET; caps, rounding, band and changing prices affect achieved risk.
Log target risk, achieved weights, cap bindings, floor/fallback and held cash.

## 6. Band, execution sizing and cash

Band compares current and proposed weights at the scheduled rebalance using
that execution proxy's pre-trade equity/marks. |target-current|<0.05 skips an
ordinary nonzero adjustment; equality0.05 trades.
Regime-flat, signal-zero exits, violated cap or fee-reserve constraints override
the band. Do not leave an over-cap position unchanged merely because its
difference is small. No risk action occurs outside the weekly clock in this v1.

Compute shares/notionals from the fixed prior target weights and causal execution
prices; use the pre-trade equity value as denominator for sizing/caps/band.
Sell reductions first, instrument lexical order BTC thenETH; then buy increases.
Quantities round down to frozen lot increments; below minimum notional is skipped
and logged, not accumulated into an unreviewed later entry. If a mandatory
sell-to-zero/cap correction cannot meet frozen execution constraints, the run
is INVALID; do not report an unexecuted flatten as successfully flat.
Keep at least0.005*pre-trade-equity cash for fee reserve after planned transaction
costs. If buys cannot fit, scale all buys down by the same affordability factor,
round down again, retain remaining cash and log CASH_SCALE_DOWN.
No borrowing, negative cash, hidden leverage or fee-free fills.

The40% cap applies to requested target/gross execution notionals relative to
pre-trade equity; costs and later marks can change actual fractions. Report these
rather than claiming an uninterrupted intraday40% guarantee.
Proposed paper accounting basis E0=100USDT, no external deposits/withdrawals.
This normalization and notional basis is an R2 review proposal, not a new
production account, stake setting or instruction to fund trading.

## 7. Costs, equity and benchmark

Before launch store one immutable per-asset cost profile with hash, provenance,
venue/tier, observed time, fee and half-spread and buy/sell impact rates,
notional validity limits, instrument metadata and rounding conventions.
Each order must fall within the profile's declared notional validity range.
Missing/nonfinite profiles or unsupported order notionals invalidate the run.
This draft obtains no book snapshot and specifies no invented observed rates.

Research proxy buy cash cost=q*P*(1+entry_rate);
sell proceeds=q*P*(1-exit_rate), entry/exit rates each include their respective
fee+half-spread+impact. No double-charging the same fee as a separate debit.
Using launch/current book for history is explicitly current_book_not_historical_L2.
The model remains frozen across the fold; no fresh favorable snapshot rescues it.
No USD/USDT FX assumption is hidden: accounting is USDT, cash yield assumed0,
no independent stablecoin-depeg hedge; disclose that limitation.

Equity at every daily close and execution event is cash plus each holding's
marked liquidation proceeds under its frozen exit rate. DD uses that complete
event sequence, initial E0 included, not a thinned chart or closed-trade-only curve.
No terminal forced order is added; final holdings are marked at the last completed
fold close with the same liquidation-cost convention. Record them separately.

Same-basket benchmark: initial equal BTC/ETH allocations at T0 daily-open proxy,
same E0/instrument/lot/cost conventions and0.5% fee reserve, no further rebalance
or BTC filter. Each asset receives half of the0.995*E0 cash budget inclusive
of buy costs: q_i=floor_to_lot((0.995*E0/2)/(P_i*(1+entry_rate_i))).
Retain rounding leftovers as cash. Apply ongoing liquidation marking.
B&H does not inherit D's40% caps; disclose its higher initial market exposure.
No benchmark variant is selected after results. Report exposure differences.

## 8. Returns, sample and economic gates

Daily net simple return R_d=E_d/E_(d-1)-1, E_start=E0.
Include genuine no-trade/cash days, and all execution costs on their actual
event day. Missing observations are not cash days.
Daily statistics use the complete chronological sequence; monthly report returns
compound the daily returns in each of the six full UTC calendar months.

Economic gates retained from the candidate prescription:
- positive net mean return (daily mean proposed for R2; monthly mean also reported);
- monthly compounded-return PF>=1.20;
- complete-event maxDD<=30%;
- total-return/maxDD strictly above the equal-weight B&H comparator;
- proposed DSR significance>0.95, subject to the statistical review below.

No daily-PF1.20 substitution. Monthly PF is descriptive economic accounting,
not a claim of six independent observations or reliable inference by itself.
PF sums positive monthly returns / abs(sum negative monthly returns).
Positive profit and zero losses=>+infinity; allzero=>undefined/no positive-mean PASS.
Calmar-style return/DD uses total net fold return, not an annualized numerator;
positive return with zeroDD=>+infinity; nonpositive return with zeroDD has no
defined favorable ratio; equality to benchmark does not pass.

Both development and forward are reported under the fixed accounting. Forward
is the untouched evidence; positive development cannot rescue failed forward.
The original both-splits gate is retained as a review candidate, with development
explicitly labelled selected/inspected rather than independently confirming it.
No development grid or additional performance run is authorized by this draft.

Fixed6months endingT1; at least120 valid daily observations is a proposed data
minimum, not proof of statistical adequacy. No adaptive extension to reach PASS.
NOT_EVALUATED until the fixed fold ends. Missing/nonfinite source metrics=>INVALID.
Too few otherwise valid observations=>INSUFFICIENT_SAMPLE.
Unknown trial/DSR inputs or unapproved statistical method block any overall PASS.
Economic gate failure on a valid sufficient-sample run=>FAIL; otherwise all approved
economic/statistical gates must pass. An economic screen result must not be
presented as statistically established alpha.

## 9. Trial inventory and statistical proposal

Define a broad related-trend discovery inventory, including B/C attempts or
pre-screens that informed D's choices; do not rename intraday B/C as daily
long-only. Each actually inspected alternative has its own attempt identity.
Retain abandoned results; exact same retry with no new inspected result is not
automatically another independent strategy. Count unfinished B as an attempt
without reading its blind official forward outcome.
Three horizons in one fixed composite are one prescription, not three grid winners.

Freeze the inventory and its missing-history statement before T0. N_raw is the
number of recorded tested/inspected alternative prescriptions; at least one
for D. Unfinished B is counted conservatively as its declared attempt without
revealing its blind outcome; this counting assumption requires R2 review.
Proposed conservative multiplicity input N=N_raw: no unverified independence
discount or invented N_effective. Count/hypothesis grouping must be reviewed.
The SR variance input needs archived, comparable daily-return development records
under declared return/accounting conventions; heterogeneous scalar SRs cannot
be pooled blindly. Absent valid inputs=>DSR_UNAVAILABLE, no overall PASS.
No new B outcome inspection, backtest or resampling of missing archived prices
is authorized just to obtain this input.

Primary reference: Bailey & Lopez de Prado (2014), Deflated Sharpe Ratio,
Journal of Portfolio Management40(5),94-107.
https://doi.org/10.3905/jpm.2014.40.5.094
https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf

Canonical proposed daily DSR:
S=mean(R)/sample_std(R), nonannualized; T=number of daily observations.
g3=mean(((R-mean(R))/population_std(R))^3);
g4=mean(((R-mean(R))/population_std(R))^4), ordinary kurtosis.
With v_SR=sample variance of comparable per-trial daily SR estimates and
gamma=Euler's constant,
S0=sqrt(v_SR)*[(1-gamma)*Phi_inv(1-1/N)+gamma*Phi_inv(1-1/(N*e))].
For a genuinely certified N=1 use S0=0; unknown history is not N=1.
DSR=Phi((S-S0)*sqrt(T-1)/sqrt(1-g3*S+(g4-1)*S^2/4)).
Undefined/nonpositive denominator or missing inputs does not pass.
Sample variance with only one comparable historical trial is unavailable,
except the certified sole-trial case; never silently impute it as zero.

This classical formula's treatment of serial dependence is NOT resolved by
using daily observations. R2 additionally proposes, for Grok review:
circular moving-block bootstrap of the fixed daily-return tape, block length7,
10,000 replications, predeclared seed4001; no strategy reruns.
Under H0 recenter returns to mean=S0*sample_std(R), preserve block ordering,
sample uniformly selected starts, concatenate/truncate each replication to T,
compute its daily SR with the same conventions.
p=(1+number of bootstrap SR>=observed S)/(10001).
Require p<0.05 in addition to canonical DSR>0.95; neither rescues the other.
Invalid bootstrap replications are not dropped/reseeded into a favorable sample;
a reproducibility defect means INVALID.
This dependence safeguard is a NEW PRE-LOCK STATISTICAL PROPOSAL, not a formula
claimed to be from the DSR paper and not approved inference yet.
The block length, finite-sample calibration, trial/SR-variance assumptions and
combined gate require a second Grok review. No bootstrap or market calculation
has been run here. This open review condition prevents final lock.

## 10. Trial outcome and isolation

PASS would permit only a separately authorized next research stage, not live
trading or deployment. FAIL closes this v1 on its used evidence without a rescue
coin, horizon, band, cost, filter or holdout rerun. INVALID/INSUFFICIENT_SAMPLE
are explicit limitations, not favorable results or permission to keep sampling.

Runner, spec, launch, dataset, cost, order and result identities must be separate
from frozen B, HYPE and ledger. No shared-helper change can alter their behavior.
Structural metadata/archive repair is a different workstream from trading B.
No new optimizer job before that repair; a standalone D runner's exact storage
dependency must be declared before separately authorized implementation.

## 11. Pre-lock / prelaunch checklist

Second Grok clearance for R2 timing, sizing, economic-unit changes and statistical
proposal; reconciliation with Gemini's draft roadmap verdict; final explicit
Roman lock approval. Data/cost manifests and continuity/contamination certificates
must be complete before launch. Implementation, data acquisition and official
evaluation each need their own authorization; none is supplied by PR70.

No code, performance calculation, data download, DB write, migration, DDL,
HYPE/ZEC B change, PR66/67/69 mutation, merge or production deploy in this update.
