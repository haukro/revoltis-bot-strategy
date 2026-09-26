import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithOxc } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const source = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8');
const pairs = ['UNI/USDT', 'ZEC/USDT', 'SUI/USDT', 'PEPE/USDT', 'NEAR/USDT'];
const defaults = ['PEPE/USDT', 'DOGE/USDT', 'WIF/USDT', 'BONK/USDT', 'SUI/USDT'];
const lock = { version_id: 'locked-version', pairs, expires_at: '2099-01-01T00:00:00Z' };
const response = data => ({ ok: true, json: async () => data });

async function handler(name, nextName, environment) {
  const code = source.slice(source.indexOf(`  const ${name} =`), source.indexOf(`  const ${nextName} =`));
  const compiled = (await transformWithOxc(code + `\nreturn ${name};`, 'handler.ts', {})).code;
  return new Function(...Object.keys(environment), compiled)(...Object.values(environment));
}

test('auto market scanner requests top-30 shortlist and 24h lock', async () => {
  const calls = []; const state = {};
  const propose = await handler('proposeUniverse', 'acceptUniverse', {
    config: { timeframe: '5m', stake_amount: 50 }, universeLoading: false,
    setUniverseLoading: value => { state.loading = value; },
    setUniverseProposal: value => { state.proposal = value; },
    setMessage: value => { state.message = value; },
    fetch: async (url, options) => {
      calls.push({ url, body: JSON.parse(options.body) });
      return response({ status: 'ok', scanner: { usdt_markets_scanned: 200, eligible_universe: 42, shortlist_size: 30, deep_scan_size: 20 }, picks: pairs.map(pair => ({ pair })) });
    },
  });
  await propose();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/market/universe/pick');
  assert.equal(calls[0].body.max_picks, 5);
  assert.equal(calls[0].body.shortlist_size, 30);
  assert.equal(calls[0].body.deep_scan_size, 20);
  assert.equal(calls[0].body.lock_hours, 24);
  assert.equal(state.loading, false);
});

test('apply proposal saves and activates in one request without reloading stale defaults', async () => {
  const calls = []; const state = {};
  const apply = await handler('acceptUniverse', 'compare', {
    config: { selected_pairs: defaults }, universeSaving: false, optimizerRunning: false, isRunning: false,
    universeProposal: { status: 'ok', picks: pairs.map(pair => ({ pair })), lock: { hours: 12 }, source: 'okx' },
    setUniverseSaving: value => { state.saving = value; },
    setConfig: value => { state.config = value; }, setActiveUniverse: value => { state.lock = value; },
    setMarketPair: value => { state.pair = value; }, setUniverseProposal: value => { state.proposal = value; },
    setMessage: value => { state.message = value; }, setVersions: update => { state.versions = update([]); },
    fetch: async (url, options) => {
      calls.push({ url, body: JSON.parse(options.body) });
      return response({ strategy: { selected_pairs: pairs }, active_universe: lock, version: { id: lock.version_id } });
    },
  });
  await apply();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/strategy-versions');
  assert.deepEqual(calls[0].body.universe.pairs, pairs);
  assert.deepEqual(state.config.selected_pairs, pairs);
  assert.equal(state.lock.version_id, lock.version_id);
  assert.equal(state.proposal, null);
  assert.equal(state.saving, false);
});

async function optimizerHarness(context, selected = defaults) {
  const calls = []; const state = {};
  const start = await handler('startOptimizer', 'copyAlgorithm', {
    config: { selected_pairs: selected, stake_amount: 40 }, dashboard: { persistence: { production_ready: true } }, universeSaving: false, optimizerRunning: false,
    optimizerDays: 30, optimizerTrials: 2,
    setOptimizerRunning: value => { state.running = value; }, setOptimizer: value => { state.optimizer = value; },
    setActiveUniverse: value => { state.lock = value; }, setConfig: value => { state.config = value; },
    setMarketPair: update => { state.pair = update('BONK/USDT'); }, setMessage: value => { state.message = value; },
    combineOptimizerResults: () => ({ job_verdict: 'ŽIADNY PLATNÝ VARIANT', winner: null }),
    fetch: async (url, options) => {
      calls.push({ url, body: options?.body ? JSON.parse(options.body) : null });
      return response(url === '/api/strategy-context' ? context : { status: 'completed', result: {} });
    },
  });
  await start();
  return { calls, state };
}

test('matching cards run only the five server-locked coins with one version id', async () => {
  const { calls, state } = await optimizerHarness({ active_universe: lock }, pairs);
  assert.equal(calls[0].url, '/api/strategy-context');
  assert.deepEqual(calls.slice(1).flatMap(call => call.body.pairs), pairs);
  for (const call of calls.slice(1)) {
    assert.equal(call.body.version_id, lock.version_id);
    assert.deepEqual(call.body.settings.selected_pairs, pairs);
    assert.deepEqual(call.body.timeframes, ['15m', '5m']);
    assert.equal(call.body.settings.stake_amount, 40);
  }
  assert.equal(state.pair, 'UNI/USDT');
  assert.equal(state.optimizer.result.winner, null);
});

test('stale cards stop with a specific universe mismatch before any job', async () => {
  const { calls, state } = await optimizerHarness({ active_universe: lock });
  assert.equal(calls.length, 1);
  assert.equal(state.optimizer.status, 'failed');
  assert.match(state.optimizer.message, /Karty nie sú locknutý universe/);
});

test('missing server lock stops before requesting any optimizer job', async () => {
  const { calls, state } = await optimizerHarness({ active_universe: null });
  assert.equal(calls.length, 1);
  assert.equal(state.optimizer.status, 'failed');
  assert.equal(state.running, false);
});

test('UNI and NEAR stay visible, checked and locked after proposal is dismissed', async () => {
  const declaration = source.split('\n').find(line => line.includes('const availableCoins ='));
  const gridStart = source.indexOf('<div className="coin-grid">');
  const grid = source.slice(gridStart, source.indexOf('{groups.map', gridStart));
  const compiled = (await transformWithOxc(`${declaration}\nreturn (${grid});`, 'coin-grid.tsx', { jsx: { runtime: 'classic' } })).code;
  const markup = new Function('React', 'useMemo', 'coins', 'selected', 'activeUniverse', 'universeProposal',
    'universeLocked', 'optimizerRunning', 'universeSaving', compiled)(React, fn => fn(), defaults, pairs, lock, null, true, false, false);
  const html = renderToStaticMarkup(markup);
  for (const coin of ['UNI', 'NEAR']) {
    assert.match(html, new RegExp(`<input[^>]*disabled=""[^>]*checked=""[^>]*\/><span>${coin}<\/span>`));
  }
});
