// Run against the production React bundle: pnpm test:paper
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync, readdirSync } = require('node:fs');
const { join } = require('node:path');
const { JSDOM } = require('jsdom');

const assets = join(__dirname, '../dist/assets');
const bundle = readFileSync(join(assets, readdirSync(assets).find(f => f.endsWith('.js'))), 'utf8');
const css = readdirSync(assets).filter(f => f.endsWith('.css')).map(f => readFileSync(join(assets, f), 'utf8')).join('\n');
const config = { selected_pairs: ['HYPE/USDT'], timeframe: '5m', initial_capital: 100, stake_amount: 50, max_open_trades: 1, daily_trade_limit: 10, bb_period: 20, bb_deviation: 2, rsi_period: 14, rsi_oversold: 35, atr_period: 14, atr_min_percent: 0.15, atr_max_percent: 4, min_volume_ratio: 0.8, min_quote_volume_usdt: 10000, rebound_min_percent: 0.3, rebound_max_percent: 0.6, stop_loss_percent: 4, trailing_start_percent: 1.2, trailing_distance_percent: 0.5, max_no_trail_hours: 0 };
const pause = () => new Promise(resolve => setTimeout(resolve, 10));
async function until(predicate) {
  for (let i = 0; i < 300; i++) {
    if (predicate()) return;
    await pause();
  }
  assert.fail('Panel did not reach the expected state');
}

async function panel(t, labels = ['official', 'scale_300_150']) {
  const dom = new JSDOM('<!doctype html><html><head></head><body><div id="root"></div></body></html>', { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window;
  const errors = [];
  w.addEventListener('error', e => errors.push(e.message));
  t.after(() => assert.deepEqual(errors, []));
  const style = w.document.createElement('style');
  style.textContent = css;
  w.document.head.append(style);
  const saved = { version_id: 'fixture', pair: 'HYPE/USDT', timeframe: '5m', variant: 8, started_at: Date.now() - 35 * 86400000, settings: config, paused_by_user: true };
  if (labels.includes('official')) w.localStorage.setItem('nofomo-paper-forward-v1', JSON.stringify(saved));
  if (labels.includes('scale_300_150')) w.localStorage.setItem('nofomo-paper-scale-v1', JSON.stringify(saved));
  const pending = {};
  w.fetch = async (url, options = {}) => {
    assert.equal(options.method || 'GET', 'GET', 'Paused tests must not start simulations');
    const path = String(url);
    if (path.startsWith('/api/paper/ledger?')) {
      const label = new URL(path, 'http://localhost').searchParams.get('label');
      return new Promise(resolve => { (pending[label] ||= []).push(resolve); });
    }
    let data = {};
    if (path === '/api/dashboard') {
      const metrics = { initial_capital: 100, portfolio_value: 199.9, realized_profit: 99.9, unrealized_profit: 0, closed_trades: 99, win_rate: 90, max_drawdown_percent: 0 };
      const run = { id: 'recalculated', summary: metrics, finished_at: '2026-10-01T23:00:00Z', progress: { paper_forward: true, per_pair_metrics: { 'HYPE/USDT': metrics } } };
      data = { strategy: config, metrics, trades: [], last_simulation: run, last_scale_simulation: run, persistence: { durable: true, production_ready: true } };
    } else if (path === '/api/strategy-versions' || path === '/api/backtests') data = [];
    else if (path === '/api/optimizer-latest') data = null;
    else if (path.startsWith('/api/market/scan')) data = { markets: [] };
    else if (path.startsWith('/api/market/candles')) data = { candles: [] };
    return new Response(JSON.stringify(data));
  };
  w.eval(bundle);
  await until(() => labels.every(label => pending[label]?.length));
  const card = label => w.document.querySelectorAll('.simulation-control')[label === 'official' ? 0 : 1];
  const alert = label => card(label).querySelector('[role="alert"]');
  const loading = label => card(label).querySelector('[role="status"]');
  function respond(label, status = 200, count) {
    let profits = label === 'official' ? [0.257, -0.365, 0.744] : [1.908];
    if (count) profits = profits.concat(Array(count - profits.length).fill(0));
    const trades = profits.map((profit, i) => ({ id: `${label}|HYPE/USDT|${i}`, paper_label: label, pair: 'HYPE/USDT', opened_at: `2026-09-${String(i + 1).padStart(2, '0')}T10:00:00Z`, closed_at: `2026-09-${String(i + 1).padStart(2, '0')}T11:00:00Z`, entry_rate: 90, exit_rate: 91, stake_amount: 50, profit_usdt: profit, exit_reason: 'trailing_profit' }));
    assert.ok(pending[label]?.length, `No pending ${label} request`);
    pending[label].shift()(new Response(JSON.stringify(status === 200 ? { trades } : { detail: 'unavailable' }), { status }));
  }
  return { w, card, alert, loading, respond, pending };
}

test('both panels show loading while their first responses are pending', async t => {
  const p = await panel(t);
  for (const label of ['official', 'scale_300_150']) {
    assert.match(p.loading(label)?.textContent || '', /Načítavam zápisník obchodov/);
    assert.equal(p.alert(label), null);
  }
});

test('rendered metrics and gates use frozen rows instead of dashboard recalculations', async t => {
  const p = await panel(t);
  p.respond('official'); p.respond('scale_300_150');
  await until(() => !p.loading('official') && !p.loading('scale_300_150'));
  assert.match(p.w.document.querySelector('.metric-grid').textContent, /\+0\.636 USDT/);
  assert.match(p.card('official').textContent, /3\/30 obchodov/);
  assert.match(p.card('scale_300_150').textContent, /\+1\.9080 USDT/);
  assert.match(p.card('scale_300_150').textContent, /1\/30 obchodov/);
});

test('both panels complete after 30 days and 30 ledger trades', async t => {
  const p = await panel(t);
  p.respond('official', 200, 30); p.respond('scale_300_150', 200, 30);
  await until(() => p.card('official').textContent.includes('Paper test dokončený'));
  assert.match(p.card('scale_300_150').textContent, /Scale test 300\/150 dokončený/);
});

test('both failed loads show red warnings instead of lingering loading messages', async t => {
  const p = await panel(t);
  p.respond('official', 503); p.respond('scale_300_150', 503);
  await until(() => p.alert('official') && p.alert('scale_300_150'));
  for (const label of ['official', 'scale_300_150']) {
    assert.match(p.alert(label).textContent, /Zápisník obchodov je nedostupný.*Čísla v paneli nie sú aktuálne/);
    assert.equal(p.w.getComputedStyle(p.alert(label)).color, 'rgb(255, 135, 152)');
    assert.equal(p.loading(label), null);
  }
});

for (const failed of ['official', 'scale_300_150']) {
  for (const failureFirst of [true, false]) {
    test(`${failed} failure survives the other response (${failureFirst ? 'failure first' : 'success first'})`, async t => {
      const p = await panel(t);
      const good = failed === 'official' ? 'scale_300_150' : 'official';
      if (failureFirst) {
        p.respond(failed, 503);
        await until(() => p.alert(failed));
        p.respond(good);
      } else {
        p.respond(good);
        await until(() => !p.loading(good));
        p.respond(failed, 503);
      }
      await until(() => !p.loading(good) && !p.loading(failed));
      assert.ok(p.alert(failed));
      assert.equal(p.alert(good), null);
    });
  }
}

test('the official test shows its failure when no scale test has started', async t => {
  const p = await panel(t, ['official']);
  p.respond('official', 503);
  await until(() => p.alert('official'));
  assert.equal(p.alert('scale_300_150'), null);
});

test('a successful retry clears only the recovered panel warning', async t => {
  const p = await panel(t);
  p.respond('official', 503); p.respond('scale_300_150', 503);
  await until(() => p.alert('official') && p.alert('scale_300_150'));
  p.card('official').querySelector('.run-button').click();
  await until(() => p.pending.official?.length);
  p.respond('official', 200, 30);
  await until(() => !p.alert('official'));
  assert.ok(p.alert('scale_300_150'));
  assert.match(p.card('official').textContent, /Paper test dokončený/);
});
