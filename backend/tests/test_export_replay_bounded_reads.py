"""Export equivalence and replay reads, using synthetic records only."""
import asyncio
from copy import deepcopy
import json
from urllib.parse import parse_qs
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app import main
from app.optimizer import assess_candidate, present_optimizer_record
from test_optimizer import candidate
from test_trade_audit import replay_fixture

EXPORT_FIELDS = {
    'version_id', 'selection_policy_version', 'qualified', 'winner', 'holdout_evaluated',
    'validation_passed', 'validation_rejection_reasons', 'walk_forward_metrics',
    'holdout_metrics', 'holdout_exposure_matched_bh_percent', 'pair', 'locked_pairs', 'settings',
}
LOCK = {'version_id': 'lock-v4', 'pairs': ['NEAR/USDT']}


def stored_candidate(job_id='valid', **changes):
    settings = main.StrategySettings(selected_pairs=['NEAR/USDT'], stake_amount=20).model_dump()
    result = candidate(settings=settings, version_id=LOCK['version_id'], selection_policy_version=4,
                       qualified=True, winner={'pair': 'NEAR/USDT', 'variant': 8},
                       holdout_evaluated=True, locked_pairs=LOCK['pairs'])
    result.update(changes)
    result.update(replay_snapshots={'unused': 'snapshot'}, trades=['unused'], variant_results=[])
    return {'id': job_id, 'finished_at': job_id, 'result': result}


def contains(value, expected):
    """JSON containment for synthetic responses, no database calls."""
    if isinstance(expected, dict):
        return isinstance(value, dict) and all(key in value and contains(value[key], item) for key, item in expected.items())
    if isinstance(expected, list):
        return isinstance(value, list) and all(any(contains(item, wanted) for item in value) for wanted in expected)
    return value == expected


class ProjectedStore:
    def __init__(self, rows, reverse_details=False):
        self.rows = deepcopy(rows)
        self.calls = []
        self.responses = []
        self.reverse_details = reverse_details

    async def __call__(self, table, query):
        assert table == 'optimizer_runs'
        params = {key: values[0] for key, values in parse_qs(query).items()}
        self.calls.append(params)
        rows = self.rows
        if 'id' in params:
            identity = params['id']
            allowed = identity[4:-1].split(',') if identity.startswith('in.(') else [identity[3:]]
            rows = [row for row in rows if row['id'] in allowed]
        if 'result->variant_results' in params:
            expected = json.loads(params['result->variant_results'][3:])
            rows = [row for row in rows if contains(row['result'].get('variant_results'), expected)]
        rows = rows[:int(params.get('limit', len(rows)))]
        if self.reverse_details and params['select'] != 'id,finished_at':
            rows = list(reversed(rows))
        projected = []
        for row in rows:
            output = {}
            for field in params['select'].split(','):
                if ':result->' in field:
                    alias, source = field.split(':result->')
                    output[alias] = deepcopy(row['result'].get(source))
                else:
                    output[field] = deepcopy(row.get(field))
            projected.append(output)
        self.responses.append(deepcopy(projected))
        return projected


def original_selection(records, lock):
    """The selection loop from main b200950, before bounded reads."""
    for record in records[:100]:
        row = (present_optimizer_record(record) or {}).get('result') or {}
        if lock and row.get('version_id') == lock['version_id'] and row.get('selection_policy_version') == 4 and row.get('qualified') and row.get('winner') and row.get('holdout_evaluated') and assess_candidate(row, row.get('locked_pairs', []))['qualified']:
            return row
    return None


def export(rows, lock=LOCK, reverse_details=False):
    store = ProjectedStore(rows, reverse_details)
    with patch('app.main.strategy_context', AsyncMock(return_value={'active_universe': lock})), \
         patch('app.main.supabase_get', AsyncMock(side_effect=store.__call__)), \
         patch('app.main.assess_candidate', wraps=assess_candidate) as assessment:
        try:
            output = asyncio.run(main.export_freqtrade(main.StrategySettings(stake_amount=7)))
        except HTTPException as error:
            assert error.status_code == 409
            output = None
    return output, store, assessment


@pytest.mark.parametrize('problem', [
    'valid', 'legacy', 'unqualified', 'missing_winner', 'other_lock', 'outside_locked_pairs',
    'no_holdout', 'missing_holdout', 'missing_pnl', 'missing_drawdown', 'nan_pnl', 'nan_drawdown',
    'missing_validation_expectancy', 'nan_validation_expectancy', 'insufficient_holdout',
    'missing_validation', 'nan_holdout_expectancy', 'missing_holdout_expectancy', 'missing_benchmark',
])
def test_export_selection_and_assessment_equal_original(problem):
    newest = stored_candidate('newest')
    row = newest['result']
    if problem == 'legacy': row['selection_policy_version'] = 3
    elif problem == 'unqualified': row['qualified'] = False
    elif problem == 'missing_winner': row.pop('winner')
    elif problem == 'other_lock': row['version_id'] = 'another-lock'
    elif problem == 'outside_locked_pairs': row['locked_pairs'] = ['UNI/USDT']
    elif problem == 'no_holdout': row['holdout_evaluated'] = False
    elif problem == 'missing_holdout': row.pop('holdout_metrics')
    elif problem == 'missing_pnl': row['holdout_metrics'].pop('realized_profit')
    elif problem == 'missing_drawdown': row['holdout_metrics'].pop('max_drawdown_percent')
    elif problem == 'nan_pnl': row['holdout_metrics']['realized_profit'] = float('nan')
    elif problem == 'nan_drawdown': row['holdout_metrics']['max_drawdown_percent'] = float('nan')
    elif problem == 'missing_validation_expectancy': row['walk_forward_metrics'].pop('expectancy')
    elif problem == 'nan_validation_expectancy': row['walk_forward_metrics']['expectancy'] = float('nan')
    elif problem == 'insufficient_holdout': row['holdout_metrics']['closed_trades'] = 9
    elif problem == 'missing_validation': row.pop('walk_forward_metrics')
    elif problem == 'nan_holdout_expectancy': row['holdout_metrics']['expectancy'] = float('nan')
    elif problem == 'missing_holdout_expectancy': row['holdout_metrics'].pop('expectancy')
    elif problem == 'missing_benchmark': row.pop('holdout_exposure_matched_bh_percent')
    expected = original_selection([newest], LOCK)
    output, store, assessment = export([newest])
    assert (output is not None) == (expected is not None)
    assert store.calls[0] == {'select': 'id,finished_at', 'order': 'finished_at.desc', 'limit': '100'}
    assert store.calls[1]['id'] == 'in.(newest)'
    assert {field.split(':')[0] for field in store.calls[1]['select'].split(',')} == {'id'} | EXPORT_FIELDS
    assert all('result' not in response and 'replay_snapshots' not in response for rows in store.responses for response in rows)
    if assessment.called:
        projected_row, pairs = assessment.call_args.args
        assert assess_candidate(projected_row, pairs) == assess_candidate(row, row['locked_pairs'])
    if expected:
        assert output['stake_amount'] == expected['settings']['stake_amount'] == 20
        assert output['dry_run'] is True and output['initial_state'] == 'stopped'
        assert output['exchange']['pair_whitelist'] == ['NEAR/USDT']
        assert output['safety']['live_trading'] is False


def test_export_finds_older_valid_candidate_even_with_shuffled_detail_response():
    newest = stored_candidate('newest')
    newest['result']['holdout_metrics']['realized_profit'] = -1
    older = stored_candidate('older')
    output, store, _ = export([newest, older], reverse_details=True)
    expected = original_selection([newest, older], LOCK)
    assert expected is older['result']
    assert output['stake_amount'] == expected['settings']['stake_amount']
    assert store.calls[1]['id'] == 'in.(newest,older)'


def test_export_keeps_newest_valid_when_details_are_shuffled():
    newest, older = stored_candidate('newest'), stored_candidate('older')
    newest['result']['settings']['stake_amount'] = 23
    output, _, _ = export([newest, older], reverse_details=True)
    assert output['stake_amount'] == 23


def test_export_never_searches_past_original_100_row_boundary():
    rows = [stored_candidate(f'bad-{i}', qualified=False) for i in range(100)] + [stored_candidate('valid-101')]
    output, store, _ = export(rows)
    assert original_selection(rows, LOCK) is None and output is None
    assert 'valid-101' not in store.calls[1]['id']
    assert len(store.responses[0]) == len(store.responses[1]) == 100


def test_inactive_lock_returns_409_without_optimizer_read():
    output, store, assessment = export([stored_candidate()], lock=None)
    assert output is None and store.calls == []
    assessment.assert_not_called()


def test_context_read_error_is_fail_closed_before_optimizer_read():
    with patch('app.main.strategy_context', AsyncMock(side_effect=httpx.ReadTimeout('unavailable'))), \
         patch('app.main.supabase_get', AsyncMock()) as read:
        with pytest.raises(httpx.ReadTimeout):
            asyncio.run(main.export_freqtrade(main.StrategySettings()))
    read.assert_not_called()


def test_empty_export_shortlist_returns_409_without_detail_read():
    output, store, _ = export([])
    assert output is None and len(store.calls) == 1


def replay(rows):
    store = ProjectedStore(rows, reverse_details=True)
    with patch('app.main.supabase_get', AsyncMock(side_effect=store.__call__)), \
         patch('app.trade_audit.simulate', side_effect=AssertionError('No simulation in preflight')) as simulate, \
         patch('app.main.load_okx_candles', AsyncMock(side_effect=AssertionError('No OKX read'))) as okx, \
         patch('app.main.supabase_upsert', AsyncMock(side_effect=AssertionError('No writes'))) as write:
        output = asyncio.run(main.validation_replay_availability())
    simulate.assert_not_called()
    okx.assert_not_called()
    write.assert_not_called()
    return output, store


def test_replay_finds_uni_and_zec_and_fetches_only_their_archives():
    targets, _ = replay_fixture()
    decoy = deepcopy(targets[0])
    decoy['id'] = 'almost-UNI'
    decoy['result']['variant_results'][0]['validation_windows'].reverse()
    rows = [stored_candidate('unrelated'), decoy, *targets]
    output, store = replay(rows)
    assert output == {'status': 'ready', 'source_job_ids': ['UNI-original', 'ZEC-original'],
                      'qualified': False, 'verdict': 'NEPREŠIEL'}
    archive_calls = [call for call in store.calls if call['select'] == 'id,result']
    assert {call['id'] for call in archive_calls} == {'eq.UNI-original', 'eq.ZEC-original'}
    assert len(archive_calls) == 2
    for call, response in zip(store.calls, store.responses):
        if call['select'] != 'id,result':
            assert all('result' not in row and 'replay_snapshots' not in row for row in response)
        else:
            assert all(row['id'] in {'UNI-original', 'ZEC-original'} for row in response)


def test_replay_preserves_ambiguity_instead_of_selecting_one_original():
    targets, _ = replay_fixture()
    duplicate = deepcopy(targets[0])
    duplicate['id'] = 'duplicate-UNI'
    output, _ = replay([duplicate, *targets])
    assert output['status'] == 'replay_unavailable'
    assert output['reason'] == 'UNI/USDT: original_report_missing_or_ambiguous'


def test_replay_keeps_the_same_100_row_boundary():
    targets, _ = replay_fixture()
    rows = [stored_candidate(f'unrelated-{i}') for i in range(100)] + targets
    output, store = replay(rows)
    assert output['status'] == 'replay_unavailable'
    assert output['source_job_ids'] == []
    assert all(call['select'] != 'id,result' for call in store.calls)


def test_replay_handles_empty_shortlist_without_metadata_or_archive_read():
    output, store = replay([])
    assert output['status'] == 'replay_unavailable'
    assert len(store.calls) == 1


def test_export_local_store_fallback_enforces_shortlist_identity_and_order():
    unrelated = stored_candidate('outside', qualified=True)
    unrelated['result']['settings']['stake_amount'] = 45
    newest = stored_candidate('newest', qualified=False)
    older = stored_candidate('older')
    async def read(table, query):
        if 'select=id,finished_at&' in query:
            return [newest, older]
        # LocalStore returns full rows and ignores the id filter/projection.
        assert parse_qs(query)['order'] == ['finished_at.desc']
        return [unrelated, older, newest]
    with patch('app.main.strategy_context', AsyncMock(return_value={'active_universe': LOCK})), \
         patch('app.main.supabase_get', AsyncMock(side_effect=read)):
        output = asyncio.run(main.export_freqtrade(main.StrategySettings()))
    assert output['stake_amount'] == older['result']['settings']['stake_amount']


def test_replay_local_store_fallback_ignores_unrelated_full_rows():
    targets, _ = replay_fixture()
    unrelated = stored_candidate('outside')
    calls = []
    async def read(table, query):
        params = parse_qs(query)
        calls.append(params)
        if params['select'] == ['id,finished_at']:
            return [unrelated, *targets]
        return [unrelated, *reversed(targets)]
    with patch('app.main.supabase_get', AsyncMock(side_effect=read)):
        output = asyncio.run(main.validation_replay_availability())
    assert output['status'] == 'ready'
    assert output['source_job_ids'] == ['UNI-original', 'ZEC-original']
    archives = [call for call in calls if call['select'] == ['id,result']]
    assert {call['id'][0] for call in archives} == {'eq.UNI-original', 'eq.ZEC-original'}
    assert all(call['order'] == ['finished_at.desc'] for call in archives)
