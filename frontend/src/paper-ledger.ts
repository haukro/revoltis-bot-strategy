// Frozen paper trades come from the append-only ledger, never from a recalculation.
export type PaperLabel = 'official' | 'scale_300_150';

export type LedgerRow = {
  id: string;
  paper_label: PaperLabel;
  pair: string;
  opened_at: string;
  closed_at: string;
  entry_rate: number;
  exit_rate: number;
  stake_amount: number;
  profit_usdt: number;
  exit_reason: string;
  raw?: Record<string, unknown>;
  status?: string;
};

export const fetchPaperLedger = async (label: PaperLabel, request: typeof fetch = fetch): Promise<LedgerRow[]> => {
  const response = await request(`/api/paper/ledger?label=${label}`);
  if (!response.ok) throw new Error('paper_ledger_unavailable');
  const body = await response.json();
  if (!Array.isArray(body?.trades)) throw new Error('paper_ledger_invalid');
  return body.trades.map((row: LedgerRow) => ({ ...row, status: 'closed' }));
};

// Same fields as the dashboard metrics, computed only from frozen rows.
export const ledgerMetrics = (rows: LedgerRow[], initialCapital: number) => {
  const closed = [...rows].sort((a, b) => String(a.closed_at).localeCompare(String(b.closed_at)));
  const realized = closed.reduce((sum, row) => sum + Number(row.profit_usdt || 0), 0);
  const wins = closed.filter(row => Number(row.profit_usdt || 0) > 0).length;
  let equity = initialCapital;
  let peak = equity;
  let drawdown = 0;
  for (const row of closed) {
    equity += Number(row.profit_usdt || 0);
    peak = Math.max(peak, equity);
    drawdown = Math.max(drawdown, peak ? (peak - equity) / peak * 100 : 0);
  }
  return {
    initial_capital: initialCapital,
    portfolio_value: Number((initialCapital + realized).toFixed(4)),
    realized_profit: Number(realized.toFixed(4)),
    unrealized_profit: 0,
    closed_trades: closed.length,
    win_rate: closed.length ? Number((wins / closed.length * 100).toFixed(1)) : 0,
    max_drawdown_percent: Number(drawdown.toFixed(2)),
  };
};
