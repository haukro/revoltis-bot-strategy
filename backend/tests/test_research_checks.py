import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.costs import book_costs, net_return
from app.main import app, return_correlation, buy_hold_risk_metrics, load_okx_candles, okx_candle_cache
from app.optimizer import present_optimizer_record
from app.simulation import simulate
from test_simulation import candles, settings


def book():
    return {'asks': [['100', '.16'], ['101', '10']], 'bids': [['99.9', '10']], 'ts': '1700000000000'}


def test_book_walk_uses_weighted_fills_and_reports_thin_l1_and_each_cost():
    c = book_costs(book(), 50)
    assert c['buy_vwap'] == pytest.approx(50 / (.16 + 34 / 101))
    assert c['buy_levels'] == 2 and c['sell_levels'] == 1
    assert c['ask_l1_usdt'] == 16 and c['thin_L1']
    assert c['half_spread'] == pytest.approx(.1 / 199.9)
    assert c['buy_impact'] == pytest.approx((c['buy_vwap'] - 100) / 99.95)
    assert c['entry_cost_rate'] == c['fee_taker'] + c['half_spread'] + c['buy_impact']
    assert c['fee_taker'] == .001 and c['fee_maker'] == .0008 and c['role'] == 'taker'
    deeper = book_costs({'asks': [['100', '100']], 'bids': [['99.9', '100']]}, 50)
    assert deeper['buy_impact'] == 0 and not deeper['thin_L1']
    assert c['entry_cost_rate'] > deeper['entry_cost_rate']


@pytest.mark.parametrize('bad', [
    {'asks': [], 'bids': []},
    {'asks': [['100', '.01']], 'bids': [['99', '10']]},
    {'asks': [['100', '10']], 'bids': [['101', '10']]},
    {'asks': [['NaN', '10']], 'bids': [['99', '10']]},
])
def test_bad_or_shallow_book_has_no_invented_zero_cost_fallback(bad):
    with pytest.raises(ValueError):
        book_costs(bad, 50)


def test_new_cost_model_changes_only_net_accounting_not_exit_signal():
    data = candles([100.] * 25 + [90., 91., 85.])
    config = settings(stop_loss_percent=5, atr_max_percent=10000)
    profile = book_costs(book(), 50)
    legacy = simulate({'UNI/USDT': data}, config, fee=.0015, force_close_at_end=True)
    updated = simulate({'UNI/USDT': data}, config, cost_models={'UNI/USDT': profile}, force_close_at_end=True)
    old, new = legacy['trades'][0], updated['trades'][0]
    assert [(old[k], new[k]) for k in ('entry_rate', 'exit_rate', 'exit_reason')] == [(91., 91.), (85., 85.), ('stop_loss', 'stop_loss')]
    assert new['profit_usdt'] == pytest.approx(round(50 * net_return(85 / 91, profile), 6))
    assert new['raw']['cost_components'] == profile
    assert 'cost_components' not in old['raw']


def test_buy_hold_risk_uses_same_cost_model_and_marks_drawdown_from_initial_capital():
    profile = {
        "entry_cost_rate": 0.001,
        "exit_cost_rate": 0.001,
    }
    data = [
        {"close": 100},
        {"close": 120},
        {"close": 90},
        {"close": 110},
    ]
    result = buy_hold_risk_metrics(data, profile, 100)
    expected_return = net_return(110 / 100, profile) * 100
    assert result["return_percent"] == pytest.approx(round(expected_return, 4))
    equity_120 = 100 * (1 + net_return(1.2, profile))
    equity_90 = 100 * (1 + net_return(.9, profile))
    expected_dd = (equity_120 - equity_90) / equity_120 * 100
    assert result["max_drawdown_percent"] == pytest.approx(round(expected_dd, 4))
    assert result["bars"] == 4


def test_diagnostic_moves_to_uni_17_positive_without_running_or_promoting_anything():
    rows = []
    for pair, n, pnl, bar in [('XRP/USDT', 5, 1.0137, '15m'), ('UNI/USDT', 17, 4.146, '15m'), ('UNI/USDT', 21, -.6733, '5m'), ('ZEC/USDT', 23, -.6865, '5m')]:
        rows.append({'pair': pair, 'timeframe': bar, 'variant': 2, 'variant_id': f'{pair}:{bar}:v2',
                     'validation_passed': False, 'holdout_evaluated': False,
                     'walk_forward_metrics': {'closed_trades': n, 'realized_profit': pnl, 'max_drawdown_percent': 4},
                     'validation_windows': [{'realized_profit': 2}, {'realized_profit': -.1}, {'realized_profit': 2}],
                     'rejection_reasons': ['malo_obchodov'] if n < 20 else ['zaporny_pnl']})
    record = {'result': {'selection_policy_version': 3, 'pair': 'XRP/USDT', 'qualified': False, 'variant_results': rows, 'locked_pairs': ['UNI/USDT', 'ZEC/USDT', 'XRP/USDT']}}
    result = present_optimizer_record(record)['result']
    assert result['pair'] == 'UNI/USDT' and result['timeframe'] == '15m'
    assert result['walk_forward_metrics']['closed_trades'] == 17
    assert result['winner'] is None and not result['qualified']
    assert record['result']['pair'] == 'XRP/USDT'


def test_production_without_supabase_cannot_claim_durable_lock_save(monkeypatch):
    monkeypatch.setenv('VERCEL', '1')
    monkeypatch.delenv('SUPABASE_URL', raising=False)
    monkeypatch.delenv('SUPABASE_SERVICE_ROLE_KEY', raising=False)
    with patch('app.main.supabase_upsert', AsyncMock()) as save, TestClient(app) as client:
        assert client.get('/api/health').json()['persistence']['production_ready'] is False
        response = client.post('/api/strategy-versions', json={'name': 'No fake persistence', 'settings': {}})
        assert response.status_code == 503
        assert 'persistence_unavailable' in response.text
        save.assert_not_called()


def test_failed_lock_has_no_freqtrade_export():
    with patch('app.main.supabase_get', AsyncMock(return_value=[])), TestClient(app) as client:
        response = client.post('/api/export/freqtrade', json={})
        assert response.status_code == 409


def test_cost_inspection_is_read_only_and_partial_failure_is_explicit():
    with patch('app.main.load_pair_cost_model', AsyncMock(side_effect=[book_costs(book(), 50), ValueError('book unavailable')])), \
         patch('app.main.load_okx_candles', AsyncMock()) as candles_fetch, \
         patch('app.main.supabase_upsert', AsyncMock()) as write, TestClient(app) as client:
        result = client.get('/api/market/cost-model?pairs=UNI/USDT,ZEC/USDT').json()
        assert result['status'] == 'degraded_no_pick'
        assert result['source'] == 'okx'
        assert list(result['profiles']) == ['UNI/USDT']
        candles_fetch.assert_not_called()
        write.assert_not_called()


def test_correlation_aligns_by_timestamp_and_does_not_use_unmatched_returns():
    left = {str(i): float(i % 3) for i in range(30)}
    right = {str(i): float(i % 3) for i in range(10, 40)}
    assert return_correlation(left, right) == pytest.approx(1.)
    with pytest.raises(ValueError):
        return_correlation(left, {'later': 1.})


def test_past_candle_window_anchors_history_at_requested_end_and_skips_live_endpoint():
    step = 300_000
    start = 1_700_000_000_000
    last_open = start + 2 * step
    end = last_open + step - 1
    rows = [
        [str(ts), "100", "101", "99", "100", "1", "0", "100", "1"]
        for ts in (last_open, start + step, start)
    ]
    calls = []

    def respond(request):
        calls.append((request.url.path, dict(request.url.params)))
        assert request.url.path.endswith("/history-candles")
        assert int(request.url.params["after"]) == end + step
        return httpx.Response(200, json={"code": "0", "data": rows})

    real_client = httpx.AsyncClient
    okx_candle_cache.clear()
    with patch("app.main.httpx.AsyncClient", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(respond))), \
         patch("app.main.asyncio.sleep", AsyncMock()):
        result = asyncio.run(load_okx_candles("ZEC/USDT", "5m", 3, start, end))
    assert [row["open_time"] for row in result] == [start, start + step, last_open]
    assert all(not path.endswith("/market/candles") for path, _ in calls)
    okx_candle_cache.clear()


def test_universe_rejects_wide_bonk_and_leveraged_tokens_and_partial_outage_returns_no_pick():
    now = int(datetime.now(UTC).timestamp() * 1000)
    ids = ['BONK-USDT', 'BTC3L-USDT', 'UNI-USDT']
    instruments = [{'instId': p, 'baseCcy': p.split('-')[0], 'state': 'live', 'listTime': str(now - 100 * 86400000), 'minSz': '0'} for p in ids]
    tickers = [{'instId': p, 'last': '100', 'open24h': '100', 'bidPx': '99.945' if p == 'BONK-USDT' else '99.99',
                'askPx': '100.055' if p == 'BONK-USDT' else '100.01', 'volCcy24h': '5000000', 'ts': str(now)} for p in ids]
    def respond(request):
        assert request.url.host == 'www.okx.com'
        if request.url.path.endswith('/instruments'):
            return httpx.Response(200, json={'code': '0', 'data': instruments})
        if request.url.path.endswith('/tickers'):
            return httpx.Response(200, json={'code': '0', 'data': tickers})
        return httpx.Response(503)
    real_client = httpx.AsyncClient
    with patch('app.main.httpx.AsyncClient', side_effect=lambda **kw: real_client(transport=httpx.MockTransport(respond))), \
         patch('app.main.asyncio.sleep', AsyncMock()), patch('app.main.load_okx_candles', AsyncMock()) as fetch, TestClient(app) as client:
        result = client.post('/api/market/universe/pick', json={}).json()
    assert result['status'] == 'degraded_no_pick' and result['picks'] == []
    rejected = {x['pair']: x['reasons'] for x in result['rejected']}
    assert 'wide_spread' in rejected['BONK/USDT']
    assert 'leveraged_token' in rejected['BTC3L/USDT']
    fetch.assert_not_called()
