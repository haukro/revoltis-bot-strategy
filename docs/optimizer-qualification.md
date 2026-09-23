# Optimizer qualification (policy 3)

No exchange orders or API keys are involved.

1. Snapshot the active server-side locked universe before fetching data. Reject requests outside it or without a lock.
2. Search the existing parameter grid on three chronological validation windows, using only 15m and 5m data. Training trades are not validation trades. Indicator warmup candles cannot open positions in validation or holdout.
3. Validation requires 20 trades, positive total net PnL, DD at most 15%, and at least two of three profitable WF windows. Score is not an additional hidden acceptance gate. Freeze one eligible finalist per coin across both timeframes, ranked by validation expectancy, then payoff, then lower validation DD.
4. Evaluate only that eligible finalist on the final 20% holdout once. A failed validation never calls the holdout simulation. Eligible nonfinalists have no holdout metrics either. Failure never tries the next parameter set or timeframe. Buy-and-hold uses the same coin, holdout boundaries and cost assumptions.
5. Admit a candidate only with at least 20 validation trades (sum of three windows), 10 holdout trades, positive holdout profit after costs, holdout return at least buy-and-hold, holdout drawdown at most 15%, and membership in the locked universe.
6. Rank admitted coins by their frozen validation expectancy/payoff/DD; holdout does not rank parameter variants or choose a replacement. If none is admitted, return `winner: null`, `qualified: false`, `strategy_code: null`, and `job_verdict: "ŽIADNY PLATNÝ VARIANT"`. Remaining top-level metrics are diagnostic, not a winner.
7. Persist `variant_results` for every pair/timeframe/variant, including all three validation windows, aggregate net PnL/DD, profitable window count, explicit rejection codes, finalist status and nullable holdout fields. Combine every coin's rows in the UI without a top-five cutoff. All rows below 20 validation trades means this set's research ends; no automatic rerun occurs.

## Trade metrics and diagnostics

`avg_win` and `avg_loss` use closed trade net PnL after the existing fee/spread/slippage model on both sides. Payoff is their ratio, or null if there are no losses. Expectancy is total net PnL / all closed trades. This equals win-probability × average win minus loss-probability × average loss, correctly retaining zero-PnL trades. Aggregate WF statistics are trade-weighted using counts and PnL sums, never an average of window ratios.

Below 20 validation trades payoff cannot rank a variant; below 10 holdout trades it is displayed as n/a. WF payoff is descriptive, with sample sizes displayed. Payoff below 0.8 is a `slaby_pomer` warning only, not a new hard gate: the user's note described a soft label but also proposed a conflicting hard failure. This release retains the soft interpretation. There is no grid, stop-loss, trailing or rebound change. Rebound max is an entry filter, not an exit/take-profit cap.

Rejection codes: `malo_obchodov`, `zaporny_pnl` (includes zero net PnL), `drawdown`, `nestabilita` (fewer than two profitable windows), `horsie_ako_hold`, `holdout_malo_obchodov`. A row may have multiple reasons; its rejection phase identifies validation versus holdout. Diagnostic rows cannot enable export.

Stored older-policy results are downgraded on read without modifying history. Existing top-results evidence can be displayed, but missing variants/windows/ratios remain explicitly unrecorded. No candles are downloaded and no holdout is recalculated to fill legacy gaps. The UI independently checks the gates and hides rejected or legacy strategy code. Validation win rate is `n/a` below 20 validation trades; holdout win rate also remains `n/a` below 20 holdout trades even when a candidate meets its separate 10-trade minimum.

## Checks

```sh
cd backend
python -m pytest -q
```

```sh
cd frontend
node --test tests/*.test.mjs
npm run build
```

Tests mock candle/metric inputs and never launch a production optimization. Passing these checks confirms selection logic, not a profitable strategy. Repeated manual searches on an already viewed holdout do not make it independent again.
