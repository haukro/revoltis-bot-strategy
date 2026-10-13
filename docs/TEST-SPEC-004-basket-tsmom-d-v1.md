# TEST-SPEC-004 — Strategy D v1: daily long-only volatility-scaled trend on a predeclared basket

Status: **DRAFT FOR REVIEW — NOT LOCKED, NOT IMPLEMENTED**

This draft opens the research family listed in `RESEARCH-ROADMAP.md` as
"slower multi-market trend on a predeclared basket". It is a proposal for
Grok / Gemini review. No performance test may run until the spec is locked
by a merge commit.

## Why a different family

| Item | Closed / failing work | Strategy D |
|---|---|---|
| Timeframe | 5m (A, HYPE v8), 1h (TSMOM C) | **1D** |
| Mechanism | mean reversion, 1h breakout | **time-series momentum, multi-horizon** |
| Direction | long + short (C) | **long-only, otherwise USDT** |
| Market | one coin | **predeclared basket** |
| Sizing | fixed stake | **inverse volatility, portfolio vol target** |
| Turnover | ~1 trade/day | **weekly rebalance** |

Rationale: on 5m/1h the round-trip cost (~0.3% at 0.15%/side) consumed a
large part of the average winner. On daily bars typical moves are an order
of magnitude larger, so the same cost is a small drag. Academic evidence
for crypto momentum is stronger for time-series than cross-sectional
signals, stronger for long-only than long-short, and volatility scaling is
a main source of risk-adjusted performance.

## Universe (fixed before any data is read)

OKX spot, quote USDT:

- BTC/USDT, ETH/USDT, SOL/USDT (core);
- up to 3 additional pairs chosen **only** by liquidity rules, frozen at
  spec lock: median 30d quote volume and median spread rank on the lock
  date, excluding stablecoins, wrapped/leveraged tokens and pairs with
  less than 3 years of daily history.

No pair may be added or removed after lock.

## Signal (per asset, daily close UTC)

- `r_k` = log return over k days, k ∈ {20, 60, 120}.
- `s = mean(sign(r_20), sign(r_60), sign(r_120))`, values in {−1, −1/3, 1/3, 1}.
- Target weight before scaling: `max(s, 0)` (long-only; negative → 0).

## Regime filter

Basket exposure = 0 when BTC daily close < BTC 200-day simple MA.

## Sizing

- Asset volatility: 60-day EWMA of daily returns, annualized.
- Raw weight_i = signal_i / vol_i.
- Scale the basket so that ex-ante portfolio volatility = **25% annualized**,
  using a 60-day covariance estimate.
- Caps: single asset ≤ 40%, gross exposure ≤ 100% (no leverage).
- Remainder held in USDT.

## Execution and costs

- Rebalance once per week at the first daily close after Monday 00:00 UTC.
- Skip a trade if |target − current| < 5% of equity (no-trade band).
- Fill at next daily open + stored cost model (taker fee + half-spread +
  impact) per side.
- Signals use only fully closed daily bars.

## Data and folds

- Daily OHLCV from the earliest common date ≥ 2019-01-01 (assets with
  shorter history enter when 120 + 60 days of history exist).
- Development: start → 12 months before the data snapshot date.
- Terminal holdout: final 12 months. Touched once, after lock.
- No parameter grid. The values above are the single frozen prescription.
  If reviewers request alternatives, every variant must be listed before
  the run and the total trial count reported (Deflated Sharpe).

## Evaluation unit

Monthly portfolio returns (≈ 80 development months), plus a per-rebalance
trade tape. Trade-count gates from 5m research do not apply.

## Preregistered PASS (holdout and development must both pass)

1. Net mean monthly return > 0 after costs;
2. Profit factor on monthly returns ≥ 1.2;
3. Max drawdown ≤ 30%;
4. Return / max DD better than equal-weight buy-and-hold of the same basket;
5. Deflated Sharpe ratio > 0.95 given the reported trial count.

FAIL on any item closes Strategy D v1 with no rescue variant.

## Diagnostics (reported, not gates)

Win rate of rebalances, exposure %, turnover, cost share of gross PnL,
contribution per asset, performance in BTC < 200D MA periods.

## Out of scope

Live trading, shorts, leverage, perpetuals/funding, intraday entries, any
change to TEST-SPEC-002 (ZEC TSMOM B) which remains frozen until 2026-12-23.
