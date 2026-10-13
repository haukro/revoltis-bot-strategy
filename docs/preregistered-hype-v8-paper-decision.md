# Preregistered decision rule — HYPE v8 paper forward

Status: **PREREGISTERED — NOT EVALUATED**

This document fixes the PASS/FAIL rule for the two running HYPE v8 paper
forward tests **before** they reach their stop criteria. It does not change
the tests, their settings, the cost model or the runtime.

## Scope

| Test | Pair / TF | Capital / stake | Started (UTC) | Earliest end |
|---|---|---|---|---|
| Official paper forward | HYPE/USDT 5m v8 | 100 / 50 USDT | 2026-09-25 22:58 | 2026-10-26 (per UI gate) |
| Parallel scale test | HYPE/USDT 5m v8 | 300 / 150 USDT | 2026-09-26 23:20 | 2026-10-27 (per UI gate) |

Strategy family: 5m mean reversion (Bollinger lower band + RSI oversold +
rebound confirmation, exit by stop-loss / trailing profit / max-no-trail
timeout). Cost model: `current_okx_book`.

Stop criterion (unchanged): minimum 30 days **and** 30 closed trades.

## Interim snapshot (recorded only, not an evaluation)

Snapshot from run `4866ecc0-a70a-40cc-b65d-6f3dc07c2484`
(2026-10-13 22:57 UTC). Recorded so the decision cannot be reframed later.
No parameter, filter or exit may be changed because of it.

| Metric | Official 100/50 | Scale 300/150 |
|---|---:|---:|
| Closed trades | 18 | 17 |
| Win rate | 50.0% | 47.1% |
| Avg win | +0.378 USDT | +1.171 USDT |
| Avg loss | −0.786 USDT | −2.363 USDT |
| Payoff (avg win / avg loss) | 0.481 | 0.496 |
| Break-even win rate implied by payoff | 67.5% | 66.9% |
| Realized PnL | −3.673 USDT | −11.897 USDT |
| Max DD | 7.45% | 7.49% |

## Decision rule (applied once, at the stop criterion)

Evaluated on the **official** 100/50 test only. The scale test is a
diagnostic of size sensitivity and cannot rescue a FAIL.

HYPE v8 is **PASS** only if all hold:

1. closed trades ≥ 30;
2. net expectancy per trade > 0 after the stored cost model;
3. net profit factor ≥ 1.10;
4. max drawdown ≤ 15%.

Anything else is **FAIL**.

## Consequence of FAIL

- HYPE v8 is REJECTED for paper-to-live promotion.
- The 5m mean-reversion family (BB / RSI / rebound / trailing) is closed,
  together with Strategy A (ZEC), which already failed on the same
  mechanism: trail-hit profits did not pay for trail-never losses.
- No rescue variant, coin swap, timeframe swap, trail/timeout grid or
  post-hoc filter is allowed on this evidence.

## Consequence of PASS

PASS only allows a new, separately preregistered forward test on a fresh
period. It does not enable live trading.

## Non-goals

- No early stop on the interim numbers above.
- No change to the running tests, the optimizer, or the paper ledger freeze.
