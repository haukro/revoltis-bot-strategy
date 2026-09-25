import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeOptimizerResult, combineOptimizerResults, variantDiagnostics } from '../src/optimizer-result.ts';

const accepted = (pair = 'SUI/USDT') => ({
  pair, timeframe: '15m', settings: { initial_capital: 100 },
  selection_policy_version: 4, qualified: true, validation_passed: true,
  holdout_evaluated: true, profitable_validation_windows: 4,
  winner: { pair }, locked_pairs: ['NEAR/USDT', 'SUI/USDT'], strategy_code: 'test code',
  walk_forward_metrics: { closed_trades: 40, expectancy: .2, max_drawdown_percent: 4 },
  holdout_metrics: { closed_trades: 10, realized_profit: 2, expectancy: .15, max_drawdown_percent: 5, win_rate: 60 },
  buy_hold_percent: 117.9,
  holdout_exposure_matched_bh_percent: 1.5,
  holdout_excess_return_percent: .5,
  risk_adjusted_oos_score: 1.2,
  tested_combinations: 10,
  max_validation_trades: 50,
});

test('policy v4 qualifies against exposure-matched benchmark even when raw B&H is huge', () => {
  const view = normalizeOptimizerResult(accepted('NEAR/USDT'));
  assert.equal(view.qualified, true);
  assert.equal(view.job_verdict, 'QUALIFIED');
  assert.equal(view.winner.pair, 'NEAR/USDT');
  assert.equal(view.strategy_code, 'test code');
});

test('underpowered sample is UNPROVEN and cannot export', () => {
  const row = accepted();
  row.walk_forward_metrics = { ...row.walk_forward_metrics, closed_trades: 39 };
  const view = normalizeOptimizerResult(row);
  assert.equal(view.qualified, false);
  assert.equal(view.job_verdict, 'UNPROVEN');
  assert.equal(view.winner, null);
  assert.equal(view.strategy_code, null);
});

test('expectancy decay blocks qualification', () => {
  const row = accepted();
  row.holdout_metrics = { ...row.holdout_metrics, expectancy: .09 };
  const view = normalizeOptimizerResult(row);
  assert.equal(view.qualified, false);
});

test('exposure-matched underperformance blocks qualification, raw hold does not', () => {
  const raw = accepted();
  raw.buy_hold_percent = 999;
  assert.equal(normalizeOptimizerResult(raw).qualified, true);

  const matched = accepted();
  matched.holdout_exposure_matched_bh_percent = 2.1;
  assert.equal(normalizeOptimizerResult(matched).qualified, false);
});

test('legacy policy is never grandfathered', () => {
  const old = { ...accepted(), selection_policy_version: 3 };
  const view = normalizeOptimizerResult(old);
  assert.equal(view.qualified, false);
  assert.equal(view.result_status, 'legacy');
  assert.match(view.verdict, /STARÝ VÝSLEDOK/);
});

test('combine ranks eligible rows by risk-adjusted OOS score', () => {
  const a = { ...accepted('NEAR/USDT'), risk_adjusted_oos_score: 2.0 };
  const b = { ...accepted('SUI/USDT'), risk_adjusted_oos_score: 1.0 };
  const result = combineOptimizerResults([b, a]);
  assert.equal(result.winner.pair, 'NEAR/USDT');
});

test('five-window diagnostics retain WF1-WF5 and suppress holdout for non-finalist', () => {
  const row = {
    validation_passed: false, is_finalist: true, holdout_evaluated: true,
    validation_windows: Array.from({ length: 5 }, () => ({ closed_trades: 8 })),
    trades: [
      { window: 'train' }, { window: 'wf1' }, { window: 'wf2' }, { window: 'wf3' },
      { window: 'wf4' }, { window: 'wf5' }, { window: 'holdout' },
    ],
  };
  const raw = { selection_policy_version: 4, tested_combinations: 1, variant_results: [row] };
  const diag = variantDiagnostics(raw);
  assert.deepEqual(diag.variant_results[0].trades.map(t => t.window), ['wf1', 'wf2', 'wf3', 'wf4', 'wf5']);
  assert.equal(diag.variant_results[0].holdout_metrics, null);
  assert.equal(diag.diagnostics_complete, true);
});
