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

type SavedPaper = { started_at?: number; completed_at?: number; pair?: string };
type SavedRun = {
  finished_at?: string;
  progress?: { paper_forward?: boolean; paper_label?: string | null; trading_start_time?: number };
  summary?: { closed_trades?: number; realized_profit?: number };
  pairs?: string[];
};
export type LegacyPaperTransition = {
  completed: boolean;
  snapshot: { finished_at: string; trades: number; pnl: number } | null;
};

// A pre-ledger summary is an archive, never a substitute for ledger metrics.
// Bind it to this test's start and label, not just the latest dashboard run.
export const legacyPaperTransition = (rows: LedgerRow[] | null, failed: boolean,
  saved: SavedPaper | null, run: SavedRun | null, label: PaperLabel): LegacyPaperTransition | null => {
  if (!saved || rows === null || rows.length > 0 || failed) return null;
  const progress = run?.progress;
  const sameLabel = progress?.paper_label === label || (label === 'official' && progress?.paper_label == null);
  const matches = Boolean(progress?.paper_forward && sameLabel && Number(saved.started_at) > 0
    && Number(progress.trading_start_time) === Number(saved.started_at) && saved.pair && run?.pairs?.includes(saved.pair));
  const trades = Number(run?.summary?.closed_trades);
  const pnl = Number(run?.summary?.realized_profit);
  const hasSnapshot = matches && Number.isInteger(trades) && trades > 0
    && run?.summary?.realized_profit != null && Number.isFinite(pnl)
    && Boolean(run?.finished_at && Number.isFinite(Date.parse(run.finished_at)));
  if (!saved.completed_at && !hasSnapshot) return null;
  return { completed: Boolean(saved.completed_at),
    snapshot: hasSnapshot ? { finished_at: run!.finished_at!, trades, pnl } : null };
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
