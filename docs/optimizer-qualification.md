# Optimizer qualification (policy 2)

No exchange orders or API keys are involved.

1. Snapshot the active server-side locked universe before fetching data. Reject requests outside it or without a lock.
2. Search the existing parameter grid on three chronological validation windows, using only 15m and 5m data. Training trades are not validation trades. Indicator warmup candles cannot open positions in validation or holdout.
3. Freeze one finalist per coin across both timeframes using validation eligibility and validation score. Retain the existing per-window positive-profit and validation-score checks.
4. Evaluate that finalist on the final 20% holdout once. Failure never tries the next parameter set or timeframe. Buy-and-hold uses the same coin, holdout boundaries and cost assumptions.
5. Admit a candidate only with at least 20 validation trades (sum of three windows), 10 holdout trades, positive holdout profit after costs, holdout return at least buy-and-hold, holdout drawdown at most 15%, and membership in the locked universe.
6. Rank admitted coins by validation score. If none is admitted, return `winner: null`, `qualified: false`, `strategy_code: null`, and `job_verdict: "ŽIADNY PLATNÝ VARIANT"`. Remaining top-level metrics are diagnostic, not a winner. Report `max_validation_trades` across every tested variant and `per_coin_results`.

Stored policy-1 results are downgraded on read without modifying history. The UI independently checks the gates and hides the strategy code of rejected or legacy results. Win rate is shown as `n/a` below 20 holdout trades even when a candidate meets the separate 10-trade holdout minimum.

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
