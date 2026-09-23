// Recheck stored/legacy results too: an old `qualified` flag is not approval.
export function normalizeOptimizerResult(raw: any): any {
  raw = diagnosticResult(raw);
  const validationTrades = raw.walk_forward_metrics?.closed_trades ??
    (Array.isArray(raw.validation_windows) ? raw.validation_windows.reduce((n: number, w: any) => n + Number(w.closed_trades || 0), 0) : 0);
  const recordedHoldout = raw.holdout_metrics || (raw.selection_policy_version < 2 ? raw.validation_metrics : null) || {};
  const holdoutVisible = raw.validation_passed === true && Number(validationTrades) >= 20 &&
    (raw.selection_policy_version < 3 || raw.holdout_evaluated === true);
  const holdout = holdoutVisible ? recordedHoldout : {};
  const holdoutTrades = Number(holdout.closed_trades || 0);
  const capital = Number(raw.settings?.initial_capital || holdout.initial_capital || 0);
  const pnl = Number(holdout.realized_profit ?? NaN);
  const pnlPercent = capital > 0 ? pnl / capital * 100 : NaN;
  const dd = Number(holdout.max_drawdown_percent ?? NaN);
  const hold = Number(raw.buy_hold_percent ?? NaN);
  const inLock = Array.isArray(raw.locked_pairs) && raw.locked_pairs.includes(raw.pair);
  const enough = Number(validationTrades) >= 20 && holdoutTrades >= 10;
  const qualified = raw.selection_policy_version === 3 && raw.qualified === true && !!raw.winner &&
    raw.holdout_evaluated === true && Number(raw.profitable_validation_windows) >= 2 &&
    raw.validation_passed === true && inLock && enough &&
    [pnl, pnlPercent, dd, hold].every(Number.isFinite) && pnl > 0 && pnlPercent >= hold && dd >= 0 && dd <= 15;
  let resultStatus = 'no_valid_variant';
  let verdict = 'ŽIADNY PLATNÝ VARIANT';
  if (qualified) { resultStatus = 'candidate'; verdict = 'KANDIDÁT — pokračovať iba do paper režimu'; }
  else if (Number(validationTrades) < 20) { resultStatus = 'insufficient_trades'; verdict = 'NEDOSTATOK OBCHODOV'; }
  else if (!holdoutVisible) {
    const reasons = raw.validation_rejection_reasons || [];
    resultStatus = 'validation_failed';
    verdict = reasons.includes('drawdown') ? 'NEPREŠIEL — drawdown validácie' :
      reasons.includes('zaporny_pnl') ? 'NEPREŠIEL — PnL validácie nie je kladné' :
      reasons.includes('nestabilita') ? 'NEPREŠIEL — nestabilné WF okná' :
      Number(recordedHoldout.closed_trades) < 10 ? 'NEDOSTATOK OBCHODOV' : 'NEPREŠIEL — validácia';
  }
  else if (!enough) { resultStatus = 'insufficient_trades'; verdict = 'NEDOSTATOK OBCHODOV'; }
  else if (dd > 15) { resultStatus = 'drawdown_failed'; verdict = 'NEPREŠIEL — drawdown'; }
  else if (pnlPercent < hold) { resultStatus = 'hold_failed'; verdict = 'NEPREŠIEL — horšie ako hold'; }
  else if (pnl <= 0) { resultStatus = 'pnl_failed'; verdict = 'NEPREŠIEL — čistý zisk nie je kladný'; }
  else if (raw.selection_policy_version !== 3) { verdict = 'STARÝ VÝSLEDOK — vyžaduje nové overenie'; }
  const diagnostics = variantDiagnostics(raw);
  return { ...raw, qualified, result_status: resultStatus, verdict,
    job_verdict: qualified ? 'KANDIDÁT' : 'ŽIADNY PLATNÝ VARIANT',
    winner: qualified ? raw.winner : null, strategy_code: qualified ? raw.strategy_code : null,
    validation_trade_count: Number(validationTrades), holdout_metrics: holdout,
    holdout_visible: holdoutVisible, buy_hold_percent: holdoutVisible ? raw.buy_hold_percent : null,
    ...diagnostics,
    holdout_profit_percent: Number.isFinite(pnlPercent) ? pnlPercent : null,
    displayed_win_rate: holdoutTrades >= 20 ? `${holdout.win_rate || 0} %` : 'n/a',
  };
}

function diagnosticResult(raw: any): any {
  const rows = raw.variant_results || [];
  if (raw.qualified || !rows.length || rows.some((r: any) => r.validation_passed)) return raw;
  const usable = rows.filter((r: any) => r.validation_windows?.length === 3 && r.walk_forward_metrics);
  if (!usable.length) return raw;
  const distance = (r: any) => {
    const m = r.walk_forward_metrics;
    return [Number(!(Number(m.realized_profit) > 0)) + Number(Number(m.max_drawdown_percent) > 15) + Number(r.validation_windows.filter((w: any) => Number(w.realized_profit) > 0).length < 2),
      Math.max(0, 20 - Number(m.closed_trades)), -Number(m.realized_profit), Number(m.max_drawdown_percent)];
  };
  const selected = [...usable].sort((a, b) => { const x = distance(a), y = distance(b); return x[0] - y[0] || x[1] - y[1] || x[2] - y[2] || x[3] - y[3] || String(a.variant_id).localeCompare(String(b.variant_id)); })[0];
  return { ...raw, ...selected, qualified: false, winner: null, strategy_code: null,
    validation_rejection_reasons: selected.rejection_reasons || [], diagnostic_only: true };
}

export function variantDiagnostics(raw: any): any {
  // Legacy reports saved only top_results (at most five) and the selected
  // variant's windows. Preserve that evidence; never invent missing windows.
  const recorded = raw.variant_results || (raw.top_results || []).map((row: any) => {
    const selected = row.pair === raw.pair && row.timeframe === raw.timeframe && row.variant === raw.variant;
    const metrics = row.validation_metrics || {};
    return { ...row, walk_forward_metrics: metrics,
      validation_windows: selected ? raw.validation_windows || [] : [],
      validation_passed: selected ? raw.validation_passed : null,
      rejection_reasons: Number(metrics.closed_trades) < 20 ? ['malo_obchodov'] : [],
      legacy: true, holdout_evaluated: false,
    };
  });
  const variants = recorded.map((row: any) => {
    const eligible = row.validation_passed === true;
    const showHoldout = eligible && row.is_finalist === true && row.holdout_evaluated === true;
    return { ...row,
      trades: Array.isArray(row.trades) ? row.trades.filter((trade: any) =>
        ['wf1', 'wf2', 'wf3'].includes(trade.window) || (showHoldout && trade.window === 'holdout')) : undefined,
      holdout_evaluated: showHoldout,
      holdout_metrics: showHoldout ? row.holdout_metrics : null,
      holdout_profit_percent: showHoldout ? row.holdout_profit_percent : null,
      buy_hold_percent: showHoldout ? row.buy_hold_percent : null,
      vs_hold_percentage_points: showHoldout ? row.vs_hold_percentage_points : null,
    };
  });
  const complete = variants.length > 0 && variants.length === Number(raw.tested_combinations) &&
    variants.every((row: any) => !row.legacy && row.validation_windows?.length === 3);
  return { variant_results: variants, diagnostics_complete: complete,
    all_variants_insufficient_trades: complete && variants.every((row: any) => row.rejection_reasons?.includes('malo_obchodov')),
  };
}

export function combineOptimizerResults(results: any[]): any {
  if (!results.length) throw new Error('Optimalizácia nevrátila žiadny výsledok.');
  const rank = (row: any): number[] => {
    const metrics = row.walk_forward_metrics || {};
    if (!row.validation_passed || Number(metrics.closed_trades) < 20) return [-Infinity, -Infinity, -Infinity];
    return [Number(metrics.expectancy ?? Number(metrics.realized_profit) / Number(metrics.closed_trades)),
      metrics.payoff == null ? -Infinity : Number(metrics.payoff), -Number(metrics.max_drawdown_percent)];
  };
  const rows = results.map(normalizeOptimizerResult).sort((a, b) => {
    const aRank = rank(a), bRank = rank(b);
    return Number(b.qualified) - Number(a.qualified) || Number(b.validation_passed) - Number(a.validation_passed) ||
      (bRank[0] - aRank[0]) || (bRank[1] - aRank[1]) || (bRank[2] - aRank[2]);
  });
  const combined = { ...rows[0],
    tested_combinations: rows.reduce((sum, row) => sum + Number(row.tested_combinations || 0), 0),
    max_validation_trades: Math.max(...rows.map(row => Number(row.max_validation_trades || row.validation_trade_count || 0))),
    per_coin_results: rows.flatMap(row => row.per_coin_results || [{ pair: row.pair, timeframe: row.timeframe,
      verdict: row.verdict, walk_forward_metrics: { closed_trades: row.validation_trade_count }, holdout_metrics: row.holdout_metrics }]),
    pairs_ready: [...new Set(rows.flatMap(row => row.pairs_ready || []))],
    pairs_dropped: rows.flatMap(row => row.pairs_dropped || []),
    variant_results: rows.flatMap(row => row.variant_results || []),
    replay_snapshots: Object.assign({}, ...rows.map(row => row.replay_snapshots || {})),
    source_job_ids: [...new Set(rows.flatMap(row => row.source_job_ids || (row.source_job_id ? [row.source_job_id] : [])))],
    cost_profiles: Object.assign({}, ...rows.map(row => row.cost_profiles || {})),
  };
  return normalizeOptimizerResult(combined);
}
