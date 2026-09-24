# Result: preregistered rebound confirmation vs 24h low

Status: **CLOSED — FAIL**

This file records the outcome of the preregistered test defined in:

- `docs/preregistered-rebound-confirmation-24h-low.md`
- preregistration commit: `4bd956d077bbaa24bac60adc12380aa4e2609817`

The preregistration document and stored run artifacts remain unchanged.

## Evaluation run

- run id: `8ec7642c-fbdc-4dc3-a28b-ee53fc6bb819`
- pair: `ZEC/USDT`
- trading timeframe: `5m`
- candidate confirmation timeframe: `4h`
- existing max-no-trail timeout: `4h`
- evaluation window: `2025-12-27 17:15:00.000 UTC` to `2026-03-27 17:14:59.999 UTC`
- grid: none
- smoke checks: passed
  - UTC 4h boundaries
  - latest equal-low candle
  - confirmation after low
  - no look-ahead

## Locked primary result

| Metric | Baseline | Candidate | Delta |
|---|---:|---:|---:|
| Total signals | 40 | 40 | 0 |
| Taken trades | 40 | 27 | -13 / -32.5% |
| Skip rate | 0% | 32.5% | +32.5 p.p. |
| Overall WR | 62.5% | 63.0% | +0.5 p.p. |
| Trail reached share | 50.0% | 51.9% | +1.9 p.p. |
| WR if trail reached | 100.0% | 100.0% | 0 p.p. |
| WR if trail not reached | 25.0% | 23.1% | -1.9 p.p. |
| -1% before trail | 52.5% | 40.7% | -11.8 p.p. |
| Median time -> trail | 77.5 min | 77.5 min | 0 |
| Median time -> -1% | 35 min | 25 min | -10 min |
| Avg MAE | -1.67% | -1.40% | +0.27 p.p. |
| Avg MAE losses | -2.77% | -2.55% | +0.21 p.p. |

## Locked PASS / FAIL conditions

| Condition | Result |
|---|---|
| WR in trail-not-reached improves by at least +15 p.p. | **FAIL** |
| Trail-reached share falls by no more than 10 p.p. | PASS |
| WR among trail-reached remains >= 90% | PASS |
| Trade count falls by no more than 35% | PASS |

Final verdict: **FAIL**

Filter B skipped 13 baseline entries; 53.8% of skipped entries were baseline trail-not-reached trades.

Trail-reached share changed by +1.9 percentage points.

## Interpretation boundary

This evaluation fold is now closed and must not be mined for a new threshold or a revised version of Filter B.

Do not:

- tune Filter B on this fold
- derive a new range-position threshold from this fold
- derive a new percent-above-low threshold from this fold
- alter the preregistered PASS / FAIL rules
- rerun this fold with modified Filter B semantics

If research continues, it requires a separately preregistered hypothesis and an unused evaluation fold.
