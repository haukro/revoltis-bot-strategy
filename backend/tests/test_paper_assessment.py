from copy import deepcopy

import pytest

from app.paper_assessment import prepare_assessment
from app.paper_measurement import MeasurementError, hash_json
from test_paper_measurement import evidence, test_config

COMMIT = 'a' * 40


def snapshot():
    item = evidence()
    data = {'test': test_config(), 'run': item.run, 'trades': list(item.trades),
            'open_positions': [], 'candles': list(item.candles)}
    return {**data, 'evidence_sha256': hash_json(data)}


def test_provisional_attestation_binds_snapshot_and_evaluator_source():
    source = snapshot()
    result = prepare_assessment(source, evaluator_commit=COMMIT)
    assert result['p_evidence_sha256'] == source['evidence_sha256']
    assert result['p_evaluation']['verdict'] == 'PASS'
    assert len(result['p_evaluator_sha256']) == 64
    assert result['p_evaluator_commit'] == COMMIT
    assert 'tick_origin_verified' not in result


@pytest.mark.parametrize('part', ['test', 'run', 'trades', 'candles'])
def test_mutated_snapshot_is_not_attested(part):
    source = deepcopy(snapshot())
    if isinstance(source[part], list):
        source[part].pop()
    else:
        source[part]['changed'] = True
    with pytest.raises(MeasurementError, match='evidence_changed'):
        prepare_assessment(source, evaluator_commit=COMMIT)


def test_missing_tape_is_an_invalid_provisional_assessment():
    source = snapshot()
    source['trades'] = None
    source['evidence_sha256'] = hash_json({k: v for k, v in source.items() if k != 'evidence_sha256'})
    assert prepare_assessment(source, evaluator_commit=COMMIT)['p_evaluation']['verdict'] == 'INVALID'


@pytest.mark.parametrize('commit', ['', 'unknown', True])
def test_unversioned_evaluator_cannot_attest(commit):
    with pytest.raises(MeasurementError, match='unknown_evaluator_version'):
        prepare_assessment(snapshot(), evaluator_commit=commit)
