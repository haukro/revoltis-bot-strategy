# Optimizer qualification (policy 4)

No exchange orders or API keys are involved. This policy changes qualification and reporting only; it does not change strategy entry/exit logic, Strategy B, or live-trading state.

## Frozen research design

1. Snapshot the active server-side locked universe before fetching data. Reject requests outside it or without a lock.
2. Freeze markets, 5m/15m timeframes, parameter variants, cost model and split dates before evaluating variants.
3. Keep the final 20% as one terminal holdout. No ranking metric may see it before the per-coin finalist is frozen.
4. Failure of the frozen finalist is terminal for that research cycle. No fallback variant or timeframe is tried after holdout.

## Validation

The development sample is split into five chronological expanding walk-forward OOS windows. Indicator warmup candles may be present before each validation boundary but cannot open positions inside the warmup region.

A variant is validation-eligible only when all are true:

- at least 40 validation trades across the five OOS windows;
- total after-cost validation PnL is positive;
- aggregate OOS maximum drawdown is at most 15%;
- no individual WF window exceeds the 15% drawdown budget;
- at least 3 of 5 WF windows have positive after-cost PnL.

Forty trades is an eligibility floor, not a statistical proof of durable edge. Results remain research evidence.

## Exposure-matched benchmark and ranking

Raw buy-and-hold remains visible as a diagnostic but is not a qualification veto.

For each variant, estimate average capital exposure from recorded trade holding time. The passive comparator is `exposure_matched_bh = raw_bh_return × average_exposure_fraction`.

Validation excess return is `strategy_validation_return - exposure_matched_bh`.

The single predeclared ranking key is `risk_adjusted_oos_score = exposure_matched_excess_return / max(validation_max_dd, 1%)`.

Eligible variants for one coin are ranked by this score. Trade count and lower DD are deterministic tie-breakers. Expectancy and payoff remain descriptive; they are no longer lexicographic selectors.

Only one finalist per coin receives holdout.

## Holdout qualification

A frozen finalist is `QUALIFIED` only if all are true:

- at least 10 holdout trades;
- after-cost holdout PnL > 0;
- holdout maximum drawdown ≤ 15%;
- holdout expectancy > 0;
- holdout expectancy is at least 50% of validation expectancy;
- holdout return is at least the exposure-matched buy-and-hold return;
- pair is still a member of the frozen locked universe.

Raw 100%-invested buy-and-hold is reported but never used as the veto.

## Status taxonomy

`QUALIFIED`: validation and terminal holdout meet policy v4. Export may be enabled for further paper/dry-run work only.

`UNPROVEN`: the only failure is insufficient validation/holdout sample size. This is not a positive result and does not enable export.

`REJECTED`: a substantive gate failed: negative edge, drawdown breach, expectancy decay, instability, invalid metrics, or underperformance versus the exposure-matched benchmark.

Legacy policy results are never grandfathered into policy v4. They require a fresh run.

## Diagnostics persisted per variant

- five WF window metrics;
- aggregate validation PnL, DD, win rate, payoff and expectancy;
- validation trade count and profitable-window count;
- validation exposure;
- exposure-matched passive return;
- excess return;
- risk-adjusted OOS score;
- finalist flag;
- terminal holdout metrics for the finalist only;
- raw B&H, holdout exposure, exposure-matched B&H and holdout excess return;
- explicit rejection codes and final status;
- trade tape when recorded by the simulator.

## Anti-overfitting limits

The policy keeps the existing no-retry holdout rule and removes raw B&H as a regime-dependent veto. It does not claim that five WF windows or 40 validation trades prove an edge. The UI must continue to show sample size and the full tested family. Repeated manual searches after seeing a holdout do not make that holdout independent again.

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

Tests use mocked inputs and do not launch production optimization or exchange orders.
