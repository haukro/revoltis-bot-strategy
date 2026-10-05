import test from 'node:test';
import assert from 'node:assert/strict';
import { fetchPaperLedger, ledgerMetrics, legacyPaperTransition } from '../src/paper-ledger.ts';

const row = (profit, closedAt) => ({ id: `official|HYPE/USDT|${closedAt}`, paper_label: 'official', pair: 'HYPE/USDT', opened_at: closedAt, closed_at: closedAt, entry_rate: 90, exit_rate: 91, stake_amount: 50, profit_usdt: profit, exit_reason: 'trailing_profit' });
const reply = (ok, body) => async () => ({ ok, json: async () => body });

test('legacy summaries must match the label, pair and exact original start', () => {
  const saved = { started_at: 1790407139654, pair: 'HYPE/USDT' };
  const run = { pairs: ['HYPE/USDT'], finished_at: '2026-10-05T17:55:49Z',
    summary: { closed_trades: 8, realized_profit: .5726 },
    progress: { paper_forward: true, paper_label: 'official', trading_start_time: saved.started_at } };
  assert.equal(legacyPaperTransition([], false, saved, run, 'official').snapshot.pnl, .5726);
  assert.equal(legacyPaperTransition([], false, saved, run, 'scale_300_150'), null);
  assert.equal(legacyPaperTransition([], false, { ...saved, started_at: saved.started_at + 1 }, run, 'official'), null);
  assert.equal(legacyPaperTransition([], false, { ...saved, pair: 'BTC/USDT' }, run, 'official'), null);
  assert.equal(legacyPaperTransition([], false, saved, { ...run, summary: { closed_trades: 8, realized_profit: null } }, 'official'), null);
});

test('older unlabelled official summaries cannot become Scale results', () => {
  const saved = { started_at: 1790407139654, pair: 'HYPE/USDT' };
  const run = { pairs: ['HYPE/USDT'], finished_at: '2026-10-05T17:55:49Z',
    summary: { closed_trades: 8, realized_profit: .5726 },
    progress: { paper_forward: true, trading_start_time: saved.started_at } };
  assert.equal(legacyPaperTransition([], false, saved, run, 'official').snapshot.trades, 8);
  assert.equal(legacyPaperTransition([], false, saved, run, 'scale_300_150'), null);
});

test('metrics come only from frozen ledger rows', () => {
  const rows = [row(0.257, '2026-09-26T22:39:59Z'), row(-0.365, '2026-09-27T21:09:59Z'), row(0.744, '2026-09-28T16:44:59Z')];
  const m = ledgerMetrics(rows, 100);
  assert.equal(m.closed_trades, 3);
  assert.equal(m.realized_profit, 0.636);
  assert.equal(m.portfolio_value, 100.636);
  assert.equal(m.win_rate, 66.7);
  assert.equal(m.unrealized_profit, 0);
});

test('drawdown is measured on closed trades in time order', () => {
  const m = ledgerMetrics([row(-5, '2026-09-27T00:00:00Z'), row(2, '2026-09-26T00:00:00Z')], 100);
  assert.equal(m.max_drawdown_percent, 4.9);
});

test('empty ledger shows zero trades, not a recalculated result', () => {
  const m = ledgerMetrics([], 300);
  assert.deepEqual([m.closed_trades, m.realized_profit, m.portfolio_value, m.win_rate], [0, 0, 300, 0]);
});

test('fetch returns closed rows and requests the right label', async () => {
  let url = '';
  const rows = await fetchPaperLedger('scale_300_150', async (u) => { url = u; return { ok: true, json: async () => ({ trades: [row(1, '2026-09-27T00:00:00Z')] }) }; });
  assert.equal(url, '/api/paper/ledger?label=scale_300_150');
  assert.equal(rows[0].status, 'closed');
});

test('unavailable or malformed ledger is an error, never an empty success', async () => {
  await assert.rejects(fetchPaperLedger('official', reply(false, {})), /paper_ledger_unavailable/);
  await assert.rejects(fetchPaperLedger('official', reply(true, { nope: 1 })), /paper_ledger_invalid/);
});
