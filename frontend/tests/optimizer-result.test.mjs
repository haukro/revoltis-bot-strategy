import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeOptimizerResult, combineOptimizerResults, variantDiagnostics } from '../src/optimizer-result.ts';

const accepted = (pair = 'SUI/USDT') => ({
  pair, timeframe: '15m', settings: { initial_capital: 100 }, score: 2,
  selection_policy_version: 3, qualified: true, validation_passed: true, holdout_evaluated: true, profitable_validation_windows: 3,
  winner: { pair }, locked_pairs: ['NEAR/USDT', 'SUI/USDT'], strategy_code: 'test code',
  walk_forward_metrics: { closed_trades: 20 },
  holdout_metrics: { closed_trades: 10, realized_profit: 8, max_drawdown_percent: 15, win_rate: 100 },
  buy_hold_percent: 8, tested_combinations: 10, max_validation_trades: 25,
});

test('old NEAR refresh: insufficient trades, n/a, no winner, no export', () => {
  const old = { ...accepted('NEAR/USDT'), selection_policy_version: undefined,
    walk_forward_metrics: undefined, validation_windows: [{ closed_trades: 8 }, { closed_trades: 7 }, { closed_trades: 5 }],
    holdout_metrics: { closed_trades: 4, realized_profit: 2.3419, max_drawdown_percent: 1.68, win_rate: 100 },
    buy_hold_percent: 70.8068, verdict: 'NAJLEPŠÍ NÁJDENÝ VARIANT – NEPOTVRDENÝ' };
  const view = normalizeOptimizerResult(old);
  assert.equal(view.verdict, 'NEDOSTATOK OBCHODOV');
  assert.equal(view.displayed_win_rate, 'n/a');
  assert.equal(view.winner, null);
  assert.equal(view.strategy_code, null);
  assert.equal(view.qualified, false);
  assert.equal(view.validation_trade_count, 20);
});

test('win rate hidden at 19 holdout trades, shown at 20', () => {
  for (const count of [4, 10, 19, 20]) {
    const row = accepted(); row.holdout_metrics.closed_trades = count;
    assert.equal(normalizeOptimizerResult(row).displayed_win_rate, count < 20 ? 'n/a' : '100 %');
  }
});

test('each mandatory gate disables export even if stale flag says qualified', () => {
  const changes = [
    { walk_forward_metrics: { closed_trades: 19 } },
    { holdout_metrics: { ...accepted().holdout_metrics, closed_trades: 9 } },
    { holdout_metrics: { ...accepted().holdout_metrics, realized_profit: 0 }, buy_hold_percent: -5 },
    { buy_hold_percent: 8.001 },
    { holdout_metrics: { ...accepted().holdout_metrics, max_drawdown_percent: 15.001 } },
    { locked_pairs: [] }, { selection_policy_version: undefined }, { winner: null },
    { buy_hold_percent: NaN }, { validation_passed: false },
  ];
  for (const change of changes) {
    const view = normalizeOptimizerResult({ ...accepted(), ...change });
    assert.equal(view.qualified, false);
    assert.equal(view.winner, null);
    assert.equal(view.strategy_code, null);
  }
  assert.equal(normalizeOptimizerResult(accepted()).qualified, true);
});

test('accepted coin beats high-scoring rejected NEAR; aggregation keeps global maximum', () => {
  const near = { ...accepted('NEAR/USDT'), score: 100, max_validation_trades: 90,
    holdout_metrics: { ...accepted().holdout_metrics, closed_trades: 4 } };
  const result = combineOptimizerResults([near, accepted()]);
  assert.equal(result.winner.pair, 'SUI/USDT');
  assert.equal(result.max_validation_trades, 90);
  assert.equal(result.tested_combinations, 20);
  assert.equal(near.tested_combinations, 10);
});

test('all rejected means empty winner, not an unconfirmed winner', () => {
  const result = combineOptimizerResults([{ ...accepted(), walk_forward_metrics: { closed_trades: 19 } }]);
  assert.equal(result.job_verdict, 'ŽIADNY PLATNÝ VARIANT');
  assert.equal(result.winner, null);
  assert.equal(result.strategy_code, null);
});

test('combine retains all 20 variants and marks research finished when all samples are short', () => {
  const jobs = ['UNI', 'ZEC', 'SUI', 'PEPE', 'NEAR'].map(coin => ({ ...accepted(`${coin}/USDT`),
    qualified: false, validation_passed: false, tested_combinations: 4,
    variant_results: ['15m', '5m'].flatMap(timeframe => [1, 2].map(variant => ({
      pair: `${coin}/USDT`, timeframe, variant, variant_id: `${coin}:${timeframe}:${variant}`,
      validation_windows: [{ closed_trades: 6 }, { closed_trades: 6 }, { closed_trades: 5 }],
      walk_forward_metrics: { closed_trades: 17 }, validation_passed: false, rejection_reasons: ['malo_obchodov'],
      // Even a stale accidental holdout value must be suppressed for this row.
      holdout_evaluated: true, is_finalist: true, holdout_metrics: { realized_profit: 999999 },
    }))),
  }));
  const result = combineOptimizerResults(jobs);
  assert.equal(result.variant_results.length, 20);
  assert.equal(result.diagnostics_complete, true);
  assert.equal(result.all_variants_insufficient_trades, true);
  assert.ok(result.variant_results.every(row => row.holdout_metrics === null));
  assert.equal(result.winner, null);
  assert.equal(result.strategy_code, null);
});

test('legacy top-five evidence does not invent missing windows or trigger holdout', () => {
  const result = variantDiagnostics({ tested_combinations: 20, top_results: [{
    pair: 'UNI/USDT', timeframe: '5m', variant: 2, validation_metrics: { closed_trades: 24 },
  }] });
  assert.equal(result.variant_results.length, 1);
  assert.equal(result.diagnostics_complete, false);
  assert.deepEqual(result.variant_results[0].validation_windows, []);
  assert.equal(result.variant_results[0].holdout_metrics, null);
  assert.equal(result.all_variants_insufficient_trades, false);
});

test('accepted coins rank on validation expectancy, not holdout profit or payoff alone', () => {
  const a = { ...accepted('NEAR/USDT'), walk_forward_metrics: {
    closed_trades: 20, realized_profit: 10, expectancy: .5, payoff: .7, max_drawdown_percent: 3,
  }, holdout_metrics: { ...accepted().holdout_metrics, realized_profit: 8 } };
  const b = { ...accepted(), walk_forward_metrics: {
    closed_trades: 20, realized_profit: 5, expectancy: .25, payoff: 2, max_drawdown_percent: 1,
  }, holdout_metrics: { ...accepted().holdout_metrics, realized_profit: 1000 } };
  assert.equal(combineOptimizerResults([b, a]).winner.pair, 'NEAR/USDT');
});
