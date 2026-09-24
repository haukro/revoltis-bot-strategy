import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

from app.optimizer import aggregate_metrics, optimize, walk_forward_windows
from app.simulation import simulate
from app.trade_audit import (AUDIT_TARGETS, digest, entry_path_audit, pack_snapshot, replay_validation,
                             tape_summary, trade_tape, unpack_snapshot)
from test_simulation import candles, settings


@pytest.mark.parametrize('name,close,reason,pnl', [
    ('stop', 85, 'stop_loss', -3.446703),
    ('trailing', 92, 'trailing_profit', .399451),
    ('wick', 90, 'end_of_test', -.699451),
    ('end', 91.5, 'end_of_test', .124725),
])
def test_observation_preserves_original_close_execution_and_excludes_entry_wicks(name, close, reason, pnl):
    data = candles([100.] * 25 + [90., 91., close])
    data[26].update(high=200, low=1)  # Both occur before the entry at close.
    data[27].update(high=95 if name in ('stop', 'trailing') else 91.6,
                    low=80 if name == 'wick' else min(close, 91.) - .2)
    config = settings(stop_loss_percent=5, trailing_start_percent=1 if name in ('stop', 'trailing') else 20,
                      trailing_distance_percent=1, atr_max_percent=10000)
    run = simulate({'UNI/USDT': data}, config, fee=.0015, force_close_at_end=True)
    assert len(run['trades']) == 1
    trade = run['trades'][0]
    assert (trade['entry_rate'], trade['exit_rate'], trade['exit_reason'], trade['profit_usdt']) == (91., close, reason, pnl)
    raw = trade['raw']
    assert raw['duration_min'] == 1
    assert raw['mfe'] == pytest.approx((data[27]['high'] / 91 - 1) * 100)
    assert raw['mae'] == pytest.approx((data[27]['low'] / 91 - 1) * 100)
    assert raw['sl_before_trail'] == (name == 'stop')
    assert raw['trail_active_before_exit_candle'] is False
    tape = trade_tape(run, 'UNI/USDT', '5m', 'UNI/USDT:5m:v2', 'wf2', .0015)
    assert tape[0]['window'] == 'wf2'
    assert tape[0]['exit_reason'] == {'stop_loss': 'stop_loss', 'trailing_profit': 'trailing_profit', 'end_of_test': 'end_of_test'}[reason]


def test_max_no_trail_reason_is_named_from_engine_and_keeps_hold_and_trail_flags():
    run = {"trades": [{
        "opened_at": "2026-09-01T00:00:00+00:00",
        "closed_at": "2026-09-01T08:00:00+00:00",
        "entry_rate": 100,
        "exit_rate": 99,
        "stake_amount": 50,
        "profit_usdt": -0.6,
        "exit_reason": "max_no_trail_hours",
        "raw": {
            "duration_min": 480,
            "holding_candles": 96,
            "mae": -2,
            "mfe": 1.2,
            "trailing_start_percent": 1.6,
            "trailing_start_reached": False,
            "trail_active_before_exit_candle": False,
        },
    }]}
    tape = trade_tape(run, "ZEC/USDT", "5m", "ZEC/USDT:5m:v1", "wf1", .001)
    trade = tape[0]
    assert trade["engine_exit_reason"] == "max_no_trail_hours"
    assert trade["exit_reason"] == "max_no_trail"
    assert trade["hold_minutes"] == 480
    assert trade["hold_bars"] == 96
    assert trade["trailing_activated"] is False
    summary = tape_summary(tape)
    assert summary["exit_reasons"]["max_no_trail"]["n"] == 1
    assert summary["exit_reasons"]["other"]["n"] == 0
    assert summary["timeout_negative"] == 1
    assert summary["trail_never_activated"] == 1


def test_entry_path_audit_uses_only_pre_entry_state_and_excludes_entry_candle_from_path_hits():
    data = candles([100 + i * .1 for i in range(320)])
    entry_index = 300
    entry = float(data[entry_index]["close"])
    data[entry_index].update(high=entry * 1.10, low=entry * .80)  # must not count post-entry
    data[entry_index + 1].update(high=entry * 1.005, low=entry * .985)
    data[entry_index + 2].update(high=entry * 1.02, low=entry * .995)
    trade = {
        "window": "wf1",
        "entry_ts": data[entry_index]["close_time_iso"] if "close_time_iso" in data[entry_index] else None,
    }
    from datetime import UTC, datetime
    def iso(ms):
        return datetime.fromtimestamp(ms / 1000, UTC).isoformat()
    trade = {
        "window": "wf1",
        "entry_ts": iso(data[entry_index]["close_time"]),
        "exit_ts": iso(data[entry_index + 2]["close_time"]),
        "entry_px": entry,
        "exit_px": float(data[entry_index + 2]["close"]),
        "pnl_net": 1.0,
        "mfe": 2.0,
        "mae": -1.5,
    }
    result = entry_path_audit(data, [trade], 1.6)
    row = result["trades"][0]
    assert row["pre_return_1h_pct"] is not None
    assert row["pre_return_4h_pct"] is not None
    assert row["rv_12_bars_pct"] is not None
    assert row["distance_from_24h_high_pct"] is not None
    assert row["time_to_mae_1pct_min"] == 1
    assert row["time_to_trail_start_min"] == 2
    assert row["first_path_event"] == "mae_1pct"
    assert result["summary"]["n"] == 1
    assert result["windows"]["wf1"]["wins"] == 1


def test_optimizer_keeps_validation_tape_but_never_training_or_rejected_holdout():
    data = candles(([100.] * 25 + [90., 91., 92., 93., 94.]) * 20)
    result = optimize({('UNI/USDT', '5m'): data}, settings(), 1, locked_pairs=['UNI/USDT'])
    row = result['variant_results'][0]
    assert not row['validation_passed']
    assert len(row['trades']) == row['walk_forward_metrics']['closed_trades']
    assert {t['window'] for t in row['trades']} <= {'wf1', 'wf2', 'wf3'}
    for b in row['validation_boundaries']:
        assert sum(t['window'] == b['window'] for t in row['trades']) == row['validation_windows'][int(b['window'][-1]) - 1]['closed_trades']
    snapshot = result['replay_snapshots'][row['snapshot_id']]
    restored = unpack_snapshot(snapshot)
    assert restored == data[:480]
    assert all(c['close_time'] < data[480]['open_time'] for c in restored)


def replay_fixture():
    """Synthetic tapes with the requested aggregate shapes; never market data."""
    data = candles([100.] * 600)
    windows, _ = walk_forward_windows(data)
    records, runs = [], {}
    for pair, expected in AUDIT_TARGETS.items():
        metrics = []
        for index, (n, pnl) in enumerate(expected['windows']):
            metric = {'closed_trades': n, 'realized_profit': pnl, 'max_drawdown_percent': 1,
                      'win_rate': 100 / n if pnl > 0 else 0}
            metrics.append(metric)
            run = {'metrics': dict(metric), 'trades': [
                {'opened_at': '2026-09-01T00:00:00+00:00', 'closed_at': '2026-09-01T00:05:00+00:00',
                 'entry_rate': 100, 'exit_rate': 101, 'stake_amount': 50,
                 'profit_usdt': pnl if i == 0 else 0, 'exit_reason': 'end_of_test',
                 'raw': {'duration_min': 5, 'mae': -1, 'mfe': 2, 'sl_before_trail': False,
                         'trailing_start_reached': True, 'trailing_start_percent': 1.6}}
                for i in range(n)]}
            runs[(pair, data[len(windows[index][0])]['open_time'])] = run
        config = settings(selected_pairs=[pair], timeframe='5m')
        row = {'pair': pair, 'timeframe': '5m', 'variant': 2, 'variant_id': f'{pair}:5m:v2',
               'settings': config, 'settings_sha256': digest(config), 'cost_per_side': .0015,
               'validation_passed': False, 'holdout_evaluated': False,
               'walk_forward_metrics': aggregate_metrics(metrics, 100), 'validation_windows': metrics,
               'snapshot_id': f'{pair}:5m', 'validation_boundaries': [
                   {'window': f'wf{i+1}', 'warmup_start_index': len(train) - 40,
                    'trading_start_index': len(train), 'trading_start_ms': data[len(train)]['open_time'],
                    'stop_index': len(train) - 40 + len(validation)}
                   for i, (train, validation) in enumerate(windows)]}
        records.append({'id': pair.split('/')[0] + '-original', 'result': {
            'source': 'okx', 'version_id': 'original-version', 'locked_pairs': ['XRP/USDT', 'DOGE/USDT', 'ZEC/USDT', 'UNI/USDT', 'SUI/USDT'],
            'qualified': False, 'variant_results': [row],
            'replay_snapshots': {row['snapshot_id']: pack_snapshot(data[:480], data[480]['open_time'])}}})
    return records, runs


def test_replay_only_six_validation_calls_keeps_originals_and_cannot_promote_candidate():
    records, runs = replay_fixture()
    original = deepcopy(records)
    with patch('app.trade_audit.simulate', side_effect=lambda markets, config, **kw: runs[(next(iter(markets)), kw['trading_start_time'])]) as sim, \
         patch('app.optimizer.optimize', side_effect=AssertionError('No grid')):
        result = replay_validation(records)
    assert sim.call_count == 6
    assert result['status'] == 'matched'
    assert len(result['trades']) == 44
    assert [len(pair['trades']) for pair in result['pairs']] == [21, 23]
    assert not result['qualified'] and result['strategy_code'] is None
    assert result['verdict'] == 'NEPREŠIEL'
    assert records == original


@pytest.mark.parametrize('problem', ['missing_snapshot', 'settings_changed', 'hash_changed', 'missing_window', 'wrong_variant', 'extra_holdout', 'different_version'])
def test_both_targets_preflight_before_simulation_and_never_download(problem):
    records, _ = replay_fixture()
    result = records[1]['result']
    row = result['variant_results'][0]
    if problem == 'missing_snapshot': result['replay_snapshots'] = {}
    if problem == 'settings_changed': row['settings']['stop_loss_percent'] = 1.5
    if problem == 'hash_changed': result['replay_snapshots'][row['snapshot_id']]['sha256'] = 'bad'
    if problem == 'missing_window': row['validation_boundaries'].pop()
    if problem == 'wrong_variant': row['variant'] = 1
    if problem == 'extra_holdout': row['validation_boundaries'][-1]['stop_index'] += 1
    if problem == 'different_version': result['version_id'] = 'new-version'
    with patch('app.trade_audit.simulate') as sim, patch('app.main.load_okx_candles', new=AsyncMock()) as fetch:
        assert replay_validation(records)['status'] == 'blocked'
    sim.assert_not_called()
    fetch.assert_not_called()


def test_mismatching_replay_discards_all_tapes_even_if_only_one_window_differs():
    records, runs = replay_fixture()
    next(iter(runs.values()))['metrics']['realized_profit'] += .001
    with patch('app.trade_audit.simulate', side_effect=lambda markets, config, **kw: runs[(next(iter(markets)), kw['trading_start_time'])]):
        result = replay_validation(records)
    assert result['status'] == 'mismatch'
    assert result['trades'] == []
    assert all(pair['trades'] == [] and pair['summary'] is None for pair in result['pairs'])


def test_summary_retains_losses_without_mfe_as_unknown():
    summary = tape_summary([{'pnl_net': -1, 'exit_reason': 'stop_loss', 'mfe': None}])
    assert summary['loss'] == 1 and summary['avg_loss'] == 1
    assert summary['avg_mfe_losses'] is None
    assert summary['losses_mfe_at_least_trailing_start'] is None


def test_preflight_missing_report_returns_unavailable_without_simulation():
    from app.main import validation_replay_availability
    with patch('app.main.supabase_get', AsyncMock(return_value=[])), patch('app.trade_audit.simulate') as simulation:
        result = asyncio.run(validation_replay_availability())
    assert result['status'] == 'replay_unavailable'
    assert result['source_job_ids'] == []
    assert result['qualified'] is False
    simulation.assert_not_called()


def test_api_replay_is_cached_and_cannot_change_strategy_or_start_optimizer():
    from app.main import ValidationReplayRequest, replay_original_validation, validation_replay_attempts
    records, runs = replay_fixture()
    validation_replay_attempts.clear()
    async def read(table, query):
        return records
    async def exercise():
        request = ValidationReplayRequest(source_job_ids=[r['id'] for r in records])
        first = await replay_original_validation(request)
        second = await replay_original_validation(request)
        assert first == second and first['status'] == 'matched'
    with patch('app.main.supabase_get', new=AsyncMock(side_effect=read)), \
         patch('app.main.supabase_upsert', new=AsyncMock()) as save, \
         patch('app.main.load_okx_candles', new=AsyncMock()) as fetch, \
         patch('app.main.run_optimizer_job', new=AsyncMock()) as grid, \
         patch('app.trade_audit.simulate', side_effect=lambda markets, config, **kw: runs[(next(iter(markets)), kw['trading_start_time'])]) as sim:
        asyncio.run(exercise())
    assert sim.call_count == 6 and save.await_count == 1
    assert save.call_args.args[0] == 'optimizer_runs'
    assert save.call_args.args[1]['result']['qualified'] is False
    fetch.assert_not_called()
    grid.assert_not_called()
    validation_replay_attempts.clear()
    with pytest.raises(ValueError):
        ValidationReplayRequest(source_job_ids=['a'], settings={'stop_loss_percent': 1})
