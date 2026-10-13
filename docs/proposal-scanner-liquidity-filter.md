# Proposal — Auto Market Scanner as a liquidity filter only

Status: **PROPOSAL FOR REVIEW — NO CODE CHANGE**

## Problem

The current flow scans the whole OKX USDT universe, shortlists by volume,
spread and movement, then the optimizer tests parameter variants on each
locked market and promotes the best finalist per coin. Policy v4 gates
each finalist on validation and holdout, but it does not account for how
many markets × variants were tried across scanner cycles. With a weak
signal-to-noise ratio, picking the best of many trials produces finalists
whose edge is partly selection luck (Bailey & López de Prado, Probability
of Backtest Overfitting; Deflated Sharpe Ratio).

## Proposal

1. The scanner selects markets **only** by tradability: median quote
   volume, median spread, data completeness, listing age. Not by backtest
   result, ATR fit or BB crosses.
2. Research strategies run on a universe frozen in their spec (e.g.
   TEST-SPEC-004), not on scanner output.
3. Every optimizer job records `trial_count = markets × variants ×
   timeframes` and cumulative trials per strategy family; reports show a
   Deflated Sharpe next to the raw score.
4. A family whose cumulative trial count exceeds the minimum backtest
   length supported by its data is flagged `OVERFIT_RISK` and cannot be
   QUALIFIED without a fresh forward fold.

## Not changed

Policy v4 gates, running paper tests, paper ledger freeze, UI.
