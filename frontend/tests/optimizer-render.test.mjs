import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithOxc } from 'vite';
import { normalizeOptimizerResult } from '../src/optimizer-result.ts';

// Exercise the actual result component without mounting App, fetching markets,
// writing user data, or starting an optimizer job.
const source = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8');
const component = source.slice(source.indexOf('function FreqtradeVisual('), source.indexOf('function Metric('));
assert.ok(component.length > 0);
const compiled = (await transformWithOxc(component + '\nexports.View = AlgorithmResult; exports.ReplayNotice = ReplayAvailabilityNotice; exports.Freqtrade = FreqtradeVisual;', 'optimizer-view.tsx', {
  jsx: { runtime: 'classic' },
})).code;
const exports = {};
new Function('React', 'normalizeOptimizerResult', 'fields', 'exports', compiled)(React, normalizeOptimizerResult, {}, exports);

test('Freqtrade readiness is absent without a candidate', () => {
  const html = renderToStaticMarkup(React.createElement(exports.Freqtrade, { candidate: false }));
  assert.match(html, /ZABLOKOVANÉ/);
  assert.doesNotMatch(html, /PRIPRAVENÉ|freqtrade trade/);
  assert.match(html, /backtesting --export trades/);
});

test('actual old NEAR markup has no winner, n/a and a disabled copy button', () => {
  const html = renderToStaticMarkup(React.createElement(exports.View, { onCopy() {}, result: {
    pair: 'NEAR/USDT', timeframe: '5m', qualified: false,
    holdout_metrics: { closed_trades: 4, realized_profit: 2.3419, win_rate: 100, max_drawdown_percent: 1.68 },
    settings: { initial_capital: 100 }, buy_hold_percent: 70.8068,
    validation_windows: [{ closed_trades: 8 }, { closed_trades: 7 }, { closed_trades: 5 }],
    strategy_code: 'LEGACY_CODE_MUST_NOT_RENDER',
  } }));
  assert.match(html, /ŽIADNY PLATNÝ VARIANT/);
  assert.match(html, /NEDOSTATOK OBCHODOV/);
  assert.match(html, /Víťaz: žiadny/);
  assert.match(html, /<b>n\/a<\/b>/);
  assert.match(html, /<button disabled="">Kopírovať algoritmus<\/button>/);
  assert.doesNotMatch(html, /LEGACY_CODE|100 %|NAJLEPŠÍ/);
});

test('candidate markup enables copy, but ten trades still show n/a win rate', () => {
  const html = renderToStaticMarkup(React.createElement(exports.View, { onCopy() {}, result: {
    pair: 'SUI/USDT', timeframe: '15m', qualified: true, validation_passed: true,
    selection_policy_version: 3, holdout_evaluated: true, profitable_validation_windows: 3, winner: { pair: 'SUI/USDT' }, locked_pairs: ['SUI/USDT'],
    holdout_metrics: { closed_trades: 10, realized_profit: 8, win_rate: 100, max_drawdown_percent: 15 },
    walk_forward_metrics: { closed_trades: 20 }, settings: { initial_capital: 100 }, buy_hold_percent: 8,
    strategy_code: 'CANDIDATE_CODE',
  } }));
  assert.match(html, /KANDIDÁT/);
  assert.match(html, /<button>Kopírovať algoritmus<\/button>/);
  assert.match(html, /CANDIDATE_CODE/);
  assert.match(html, /<b>n\/a<\/b>/);
});

test('all twenty table rows render and failed validation hides every holdout field', () => {
  const variant_results = Array.from({ length: 20 }, (_, index) => ({
    pair: `COIN${index}/USDT`, timeframe: '15m', variant: 1, variant_id: `v-${index}`,
    validation_passed: false, is_finalist: true, holdout_evaluated: true,
    validation_windows: [{ closed_trades: 8, realized_profit: 1 }, { closed_trades: 8, realized_profit: -0.1 }, { closed_trades: 8, realized_profit: -0.1 }],
    walk_forward_metrics: { closed_trades: 24, realized_profit: .8, max_drawdown_percent: 1 },
    profitable_validation_windows: 1, rejection_reasons: ['nestabilita'], rejection_phase: 'validation',
    holdout_metrics: { realized_profit: 999999, avg_win: 999999, payoff: 999999 },
  }));
  const html = renderToStaticMarkup(React.createElement(exports.View, { onCopy() {}, result: {
    pair: 'COIN0/USDT', timeframe: '15m', qualified: false, validation_passed: false,
    tested_combinations: 20, selection_policy_version: 3, variant_results,
    walk_forward_metrics: { closed_trades: 24 }, validation_rejection_reasons: ['nestabilita'],
    strategy_code: 'DO_NOT_COPY',
  } }));
  assert.equal((html.match(/<tr>/g) || []).length, 21);
  assert.match(html, /20\/20/);
  assert.match(html, /nestabilita/);
  assert.match(html, /Expectancy validácie/);
  assert.doesNotMatch(html, /999999|DO_NOT_COPY|skoro prešlo/);
  assert.match(html, /<button disabled="">Kopírovať algoritmus<\/button>/);
});

test('requested legacy trade tapes stay visibly unavailable without launching replay', () => {
  const html = renderToStaticMarkup(React.createElement(exports.View, { onCopy() {}, result: {
    pair: 'UNI/USDT', timeframe: '5m', variant_results: ['UNI/USDT', 'ZEC/USDT'].map((pair, i) => ({
      pair, timeframe: '5m', variant: 2, variant_id: pair, validation_passed: false,
      walk_forward_metrics: { closed_trades: i ? 23 : 21 },
    })),
  } }));
  assert.match(html, /Páska obchodov/);
  assert.match(html, /replay_unavailable/);
  assert.equal((html.match(/Páska sa v pôvodnom behu neuložila/g) || []).length, 2);
  assert.doesNotMatch(html, /<table class="trade-tape">/);
  assert.match(html, /<button disabled="">Kopírovať algoritmus/);
});

test('missing original report displays replay_unavailable even without optimizer results', () => {
  const html = renderToStaticMarkup(React.createElement(exports.ReplayNotice, { availability: { status: 'replay_unavailable' } }));
  assert.match(html, /replay_unavailable/);
  assert.match(html, /Replay sa nespustil/);
  assert.doesNotMatch(html, /<button|<table/);
});

test('recorded tape renders all 44 validation trades and no train or holdout trades', () => {
  const html = renderToStaticMarkup(React.createElement(exports.View, { onCopy() {}, result: {
    pair: 'UNI/USDT', timeframe: '5m', variant_results: ['UNI/USDT', 'ZEC/USDT'].map((pair, index) => ({
      pair, timeframe: '5m', variant: 2, variant_id: pair, validation_passed: false, trade_tape_version: 1,
      walk_forward_metrics: { closed_trades: index ? 23 : 21 },
      trades: Array.from({ length: index ? 23 : 21 }, (_, i) => ({ window: `wf${i % 3 + 1}`,
        entry_ts: `ENTRY_${index}_${i}`, exit_ts: 'EXIT', duration_min: 5, entry_px: 100, exit_px: 99,
        pnl_net: -1, mae: -2, mfe: .1, exit_reason: 'stop_loss', sl_before_trail: false,
      })).concat([{ window: 'holdout', entry_ts: 'HOLDOUT_MUST_NOT_RENDER' }, { window: 'train', entry_ts: 'TRAIN_MUST_NOT_RENDER' }]),
    })),
  } }));
  assert.equal((html.match(/<td>ENTRY_/g) || []).length, 44);
  assert.match(html, /Čisté PnL USDT/);
  assert.match(html, /MAE %/);
  assert.match(html, /SL pred trailingom/);
  assert.doesNotMatch(html, /HOLDOUT_MUST_NOT_RENDER|TRAIN_MUST_NOT_RENDER/);
  assert.match(html, /<button disabled="">Kopírovať algoritmus/);
});
