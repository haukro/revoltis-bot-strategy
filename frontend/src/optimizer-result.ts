// Recheck stored/legacy results too: an old `qualified` flag is not approval.
export function normalizeOptimizerResult(raw: any): any {
  const holdout = raw.holdout_metrics || raw.validation_metrics || {};
  const validationTrades = raw.walk_forward_metrics?.closed_trades ??
    (Array.isArray(raw.validation_windows) ? raw.validation_windows.reduce((n: number, w: any) => n + Number(w.closed_trades || 0), 0) : 0);
  const holdoutTrades = Number(holdout.closed_trades || 0);
  const capital = Number(raw.settings?.initial_capital || holdout.initial_capital || 0);
  const pnl = Number(holdout.realized_profit ?? NaN);
  const pnlPercent = capital > 0 ? pnl / capital * 100 : NaN;
  const dd = Number(holdout.max_drawdown_percent ?? NaN);
  const hold = Number(raw.buy_hold_percent ?? NaN);
  const inLock = Array.isArray(raw.locked_pairs) && raw.locked_pairs.includes(raw.pair);
  const enough = Number(validationTrades) >= 20 && holdoutTrades >= 10;
  const qualified = raw.selection_policy_version === 2 && raw.qualified === true && !!raw.winner &&
    raw.validation_passed === true && inLock && enough &&
    [pnl, pnlPercent, dd, hold].every(Number.isFinite) && pnl > 0 && pnlPercent >= hold && dd >= 0 && dd <= 15;
  let resultStatus = 'no_valid_variant';
  let verdict = 'ŽIADNY PLATNÝ VARIANT';
  if (qualified) { resultStatus = 'candidate'; verdict = 'KANDIDÁT — pokračovať iba do paper režimu'; }
  else if (!enough) { resultStatus = 'insufficient_trades'; verdict = 'NEDOSTATOK OBCHODOV'; }
  else if (dd > 15) { resultStatus = 'drawdown_failed'; verdict = 'NEPREŠIEL — drawdown'; }
  else if (pnlPercent < hold) { resultStatus = 'hold_failed'; verdict = 'NEPREŠIEL — horšie ako hold'; }
  else if (pnl <= 0) { resultStatus = 'pnl_failed'; verdict = 'NEPREŠIEL — čistý zisk nie je kladný'; }
  else if (raw.selection_policy_version !== 2) { verdict = 'STARÝ VÝSLEDOK — vyžaduje nové overenie'; }
  return { ...raw, qualified, result_status: resultStatus, verdict,
    job_verdict: qualified ? 'KANDIDÁT' : 'ŽIADNY PLATNÝ VARIANT',
    winner: qualified ? raw.winner : null, strategy_code: qualified ? raw.strategy_code : null,
    validation_trade_count: Number(validationTrades), holdout_metrics: holdout,
    holdout_profit_percent: Number.isFinite(pnlPercent) ? pnlPercent : null,
    displayed_win_rate: holdoutTrades >= 20 ? `${holdout.win_rate || 0} %` : 'n/a',
  };
}

export function combineOptimizerResults(results: any[]): any {
  if (!results.length) throw new Error('Optimalizácia nevrátila žiadny výsledok.');
  const rows = results.map(normalizeOptimizerResult).sort((a, b) =>
    Number(b.qualified) - Number(a.qualified) || Number(b.score) - Number(a.score));
  return { ...rows[0],
    tested_combinations: rows.reduce((sum, row) => sum + Number(row.tested_combinations || 0), 0),
    max_validation_trades: Math.max(...rows.map(row => Number(row.max_validation_trades || row.validation_trade_count || 0))),
    per_coin_results: rows.flatMap(row => row.per_coin_results || [{ pair: row.pair, timeframe: row.timeframe,
      verdict: row.verdict, walk_forward_metrics: { closed_trades: row.validation_trade_count }, holdout_metrics: row.holdout_metrics }]),
    pairs_ready: [...new Set(rows.flatMap(row => row.pairs_ready || []))],
    pairs_dropped: rows.flatMap(row => row.pairs_dropped || []),
  };
}
