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
const compiled = (await transformWithOxc(component + '\nexports.View = AlgorithmResult; exports.ReplayNotice = ReplayAvailabilityNotice; exports.Freqtrade = FreqtradeVisual; exports.CurrentAudit = CurrentTradeAuditPanel;', 'optimizer-view.tsx', {
  jsx: { runtime: 'classic' },
})).code;
const exports = {};
new Function('React', 'normalizeOptimizerResult', 'fields', 'exports', compiled)(React, normalizeOptimizerResult, {}, exports);

test('Freqtrade stays unevaluated until the current lock has a matching result', () => {
  const html = renderToStaticMarkup(React.createElement(exports.Freqtrade, { result: null, activeVersionId: 'lock-1', activePairs: [], timeframe: '5m' }));
  assert.match(html, /ZABLOKOVANÉ — NEVYHODNOTENÉ/);
  assert.match(html, /NEVYHODNOTENÉ/);
  assert.doesNotMatch(html, /PRIPRAVENÉ|NEPREŠIEL/);
  assert.match(html, /backtesting --export trades/);
});

test('Freqtrade ignores a result from a different lock version', () => {
  const html = renderToStaticMarkup(React.createElement(exports.Freqtrade, {
    result: { version_id: 'old-lock', qualified: false },
    activeVersionId: 'current-lock', activePairs: [], timeframe: '5m',
  }));
  assert.match(html, /ZABLOKOVANÉ — NEVYHODNOTENÉ/);
  assert.doesNotMatch(html, /NEPREŠIEL/);
});

test('actual old NEAR markup has no winner, n/a and a disabled copy button', () => {
  const html = renderToStaticMarkup(React.createElement(exports.View, { onCopy() {}, result: {
    pair: 'NEAR/USDT', timeframe: '5m', qualified: false,
    holdout_metrics: { closed_trades: 4, realized_profit: 2.3419, win_rate: 100, max_drawdown_percent: 1.68 },
    settings: { initial_capital: 100 }, buy_hold_percent: 70.8068,
    validation_windows: [{ closed_trades: 8 }, { closed_trades: 7 }, { closed_trades: 5 }],
    strategy_code: 'LEGACY_CODE_MUST_NOT_RENDER',
  } }));
  assert.match(html, /REJECTED/);
  assert.match(html, /STARÝ VÝSLEDOK — vyžaduje nové overenie/);
  assert.match(html, /Víťaz: žiadny/);
  assert.match(html, /<b>n\/a<\/b>/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>Kopírovať algoritmus<\/button>/);
  assert.doesNotMatch(html, /LEGACY_CODE|100 %|NAJLEPŠÍ/);
});

test('QUALIFIED v4 markup enables copy, but ten trades still show n/a win rate', () => {
  const html = renderToStaticMarkup(React.createElement(exports.View, { onCopy() {}, result: {
    pair: 'SUI/USDT', timeframe: '15m', qualified: true, validation_passed: true,
    selection_policy_version: 4, holdout_evaluated: true, profitable_validation_windows: 3, winner: { pair: 'SUI/USDT' }, locked_pairs: ['SUI/USDT'],
    holdout_metrics: { closed_trades: 10, realized_profit: 8, expectancy: .8, win_rate: 100, max_drawdown_percent: 15 },
    walk_forward_metrics: { closed_trades: 40, expectancy: .2 }, settings: { initial_capital: 100 }, buy_hold_percent: 8,
    holdout_exposure_matched_bh_percent: 4,
    strategy_code: 'CANDIDATE_CODE',
  } }));
  assert.match(html, /QUALIFIED/);
  assert.match(html, /<button class="ghost">Kopírovať algoritmus<\/button>/);
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
  assert.match(html, /<button[^>]*disabled=""[^>]*>Kopírovať algoritmus<\/button>/);
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
  assert.match(html, /<button[^>]*disabled=""[^>]*>Kopírovať algoritmus/);
});

test('missing original report is labeled as historical diagnostics, not current verdict', () => {
  const html = renderToStaticMarkup(React.createElement(exports.ReplayNotice, { availability: { status: 'replay_unavailable' } }));
  assert.match(html, /Legacy replay diagnostika nedostupná/);
  assert.match(html, /nie je verdiktom aktuálneho locku/);
  assert.doesNotMatch(html, /Verdikt ostáva NEPREŠIEL|<button|<table/);
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
  assert.match(html, /<button[^>]*disabled=""[^>]*>Kopírovať algoritmus/);
});


test('read-only current trade audit summarizes stored tape without starting work', () => {
  const html = renderToStaticMarkup(React.createElement(exports.CurrentAudit, { view: normalizeOptimizerResult({
    pair: 'ZEC/USDT', timeframe: '5m', variant: 2, qualified: false,
    selection_policy_version: 3, validation_passed: true, holdout_evaluated: true,
    walk_forward_metrics: { closed_trades: 25 }, holdout_metrics: { closed_trades: 12 },
    settings: { initial_capital: 100 }, buy_hold_percent: 17.8,
    trades: [
      { window: 'wf1', entry_ts: 'A', exit_ts: 'B', pnl_net: 1, mae: -1, mfe: 2, exit_reason: 'trailing', mfe_reached_trailing_start: true },
      { window: 'wf2', entry_ts: 'C', exit_ts: 'D', pnl_net: -3, mae: -4, mfe: 2, exit_reason: 'stop_loss', mfe_reached_trailing_start: true },
      { window: 'holdout', entry_ts: 'E', exit_ts: 'F', pnl_net: 1, mae: -.5, mfe: 3, exit_reason: 'window_end', mfe_reached_trailing_start: true },
    ],
  }) }));
  assert.match(html, /Read-only audit aktuálneho výsledku/);
  assert.match(html, /Straty s MFE ≥ trailing start a následným stop-lossom:/);
  assert.match(html, /1\/1/);
  assert.match(html, /Medián MFE výhier/);
  assert.match(html, /medián \|MAE\| strát/);
  assert.doesNotMatch(html, /Spustiť|optimizer\/run/);
});


for (const [policy, windowCount] of [[3, 3], [4, 5]]) {
  test(`policy ${policy} UNI candidate is labeled as diagnostics and cannot promote or export`, () => {
    const rows = [['XRP/USDT', 5, 1.0137], ['UNI/USDT', 17, 4.146]].map(([pair, trades, pnl]) => ({
      pair, timeframe: '15m', variant_id: pair, validation_passed: false,
      walk_forward_metrics: { closed_trades: trades, realized_profit: pnl, max_drawdown_percent: 4 },
      validation_windows: Array.from({ length: windowCount }, () => ({ realized_profit: 1 })),
      rejection_reasons: ['malo_obchodov'],
    }));
    const raw = { selection_policy_version: policy, pair: 'XRP/USDT', qualified: false, variant_results: rows };
    const view = normalizeOptimizerResult(raw);
    assert.equal(view.diagnostic_only, true);
    assert.equal(view.pair, 'UNI/USDT');
    assert.equal(view.winner, null);
    assert.equal(view.qualified, false);
    const html = renderToStaticMarkup(React.createElement(exports.View, { result: raw, onCopy() {} }));
    assert.match(html, /Víťaz: žiadny\. Diagnostika variantu: UNI\/USDT/);
    assert.match(html, /Diagnostické nastavenia — nepoužiť/);
    assert.match(html, /disabled="">▶ Spustiť paper test/);
    assert.match(html, /disabled="">Kopírovať algoritmus/);
    assert.doesNotMatch(html, /QUALIFIED|KANDIDÁT/);
  });
}


test('underpowered v4 result renders UNPROVEN without allowing export', () => {
  const html = renderToStaticMarkup(React.createElement(exports.View, { onCopy() {}, result: {
    selection_policy_version: 4, pair: 'UNI/USDT', validation_passed: false,
    walk_forward_metrics: { closed_trades: 39 }, qualified: false,
  } }));
  assert.match(html, /UNPROVEN/);
  assert.match(html, /disabled="">Kopírovať algoritmus/);
});


test('substantive v4 failure renders REJECTED without allowing export', () => {
  const html = renderToStaticMarkup(React.createElement(exports.View, { onCopy() {}, result: {
    selection_policy_version: 4, pair: 'UNI/USDT', validation_passed: false,
    walk_forward_metrics: { closed_trades: 40 }, qualified: false,
    result_status: 'rejected', verdict: 'REJECTED — drawdown',
  } }));
  assert.match(html, /REJECTED — drawdown/);
  assert.match(html, /disabled="">Kopírovať algoritmus/);
});
