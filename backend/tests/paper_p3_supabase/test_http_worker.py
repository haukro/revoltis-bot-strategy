"""P3 HTTP acceptance on the disposable stack, never a connected project."""
import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

if not os.environ.get('PAPER_P3_SUPABASE'):
    pytest.skip('Requires isolated P3 Supabase CI', allow_module_level=True)

import httpx
from psycopg import sql
from app.paper_measurement import GATE_MS, STEP_MS, hash_json
from test_paper_measurement import evidence, test_config
from test_local_stack import Stack, eventually


@pytest.fixture(scope='module')
def env():
    s = Stack()  # Enforces CI, loopback, exact container/project identity.
    cron_secret, diagnostic_secret = uuid4().hex, uuid4().hex
    process_env = dict(os.environ, PAPER_MEASUREMENT_ENABLED='1', PAPER_SUPABASE_URL=s.url,
        PAPER_SUPABASE_SERVICE_ROLE_KEY=s.keys['service_role'], CRON_SECRET=cron_secret,
        PAPER_DIAGNOSTIC_SECRET=diagnostic_secret, VERCEL_GIT_COMMIT_SHA=os.environ['PAPER_P2_COMMIT'])
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server:app', '--app-dir',
        str(Path(__file__).parent), '--host', '127.0.0.1', '--port', '8765', '--no-access-log'],
        env=process_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = httpx.Client(base_url='http://127.0.0.1:8765', timeout=100, trust_env=False)
    job = None
    try:
        def ready():
            assert process.poll() is None, 'CI HTTP server stopped'
            try:
                return client.get('/health').status_code == 200
            except httpx.ConnectError:
                return False
        eventually(ready, bool, timeout=15)
        # A1 CI policies/ACLs already installed by the preceding P2 suite.
        # Genuine scheduled ticks, wall clock, no manufactured origin/catalog.
        s.admin('''create table paper_ci_support.p3_ticks(id uuid);
          grant insert on paper_ci_support.p3_ticks to paper_cron_owner;''')
        statement = "insert into paper_ci_support.p3_ticks values(public.paper_tick_start()),(public.paper_tick_start());"
        job = int(s.admin(sql.SQL("select cron.schedule_in_database('paper_tick','* * * * *',{},'postgres','paper_cron_owner',true);")
                          .format(sql.Literal(statement)).as_string()))
        rows = eventually(lambda: s.rows('select id from paper_ci_support.p3_ticks'), lambda r: len(r) >= 2)
        s.admin(f'select cron.alter_job({job},active:=false);')
        eventually(lambda: s.rows(f"select status from cron.job_run_details where jobid={job}"),
                   lambda r: bool(r) and r[-1]['status']=='succeeded', timeout=10)
        yield s, client, cron_secret, diagnostic_secret, [r['id'] for r in rows]
    finally:
        if job is not None:
            s.admin(f'select cron.unschedule({job});')
        process.terminate()
        process.wait(timeout=10)
        client.close(); s.client.close()


def create(env, prefix='VALID', *, after_gate=False, shadow=False, ended=False):
    s = env[0]
    now = s.rows('select floor(extract(epoch from clock_timestamp())*1000)::bigint as ms')[0]['ms']
    start = now//STEP_MS*STEP_MS - (GATE_MS if after_gate else 0) - 5*STEP_MS + int(uuid4().hex[:6],16)%100000
    t = test_config(start_ms=start, warmup=24)
    if prefix == 'CLOSED':
        t['settings']['stop_loss_percent'] = 5
    pair = prefix + uuid4().hex[:8].upper() + '/USDT'
    t['pair'] = pair; t['settings']['selected_pairs'] = [pair]; t['settings_sha256'] = hash_json(t['settings'])
    t.update(id=str(uuid4()), variant='B', strategy_version_id='synthetic-p3-ci', lease_ms=120000,
        origin_grace_ms=120000, shadow=shadow,
        shadow_until_ms=(now-1 if ended else now+240000) if shadow else None)
    return s.rpc('paper_create_test', p_test=t)


def tick(env, which=0):
    r = env[1].post('/api/paper/measure/tick', headers={'Authorization': 'Bearer '+env[2]},
                    json={'tick_id': env[4][which]})
    assert r.status_code == 200, r.text
    return r.json()


def run_row(env, test):
    rows = env[0].rows(f"select * from paper_runs where test_id='{test['id']}'")
    assert len(rows) == 1
    return rows[0]


def test_at9_real_http_denies_missing_wrong_or_cross_use_secret_without_writes(env):
    s, client, cron, diagnostic, ticks = env
    before = s.rows('select (select count(*) from paper_runs) runs,(select count(*) from paper_run_claims) claims,(select count(*) from paper_tick_outcomes) outcomes')
    for secret in ('', 'wrong', diagnostic):
        r = client.post('/api/paper/measure/tick', json={'tick_id': ticks[0]},
                        headers={'Authorization': 'Bearer '+secret} if secret else {})
        assert r.status_code == 401
    assert s.rows('select (select count(*) from paper_runs) runs,(select count(*) from paper_run_claims) claims,(select count(*) from paper_tick_outcomes) outcomes') == before


def test_at24_diagnostic_then_automatic_same_boundary_stay_separate(env):
    t = create(env)
    r = env[1].post('/api/paper/diagnostic/run', json={'test_id': t['id']},
                    headers={'Authorization': 'Bearer '+env[3]})
    assert r.status_code == 200 and r.json()['status'] == 'valid', r.text
    diagnostic = env[0].rows(f"select boundary_ms from paper_diagnostic_runs where test_id='{t['id']}'")
    assert len(diagnostic) == 1
    assert not env[0].rows(f"select id from paper_run_claims where test_id='{t['id']}'")
    tick(env)
    row = run_row(env, t)
    assert row['status'] == 'valid' and row['boundary_ms'] == diagnostic[0]['boundary_ms']
    assert row['trade_count'] == row['open_count'] == 0 and row['tape_present'] and row['positions_present']
    assert row['code_commit'] == os.environ['PAPER_P2_COMMIT']
    assert env[0].rows(f"select id from paper_verdicts where test_id='{t['id']}'") == []


def test_at8_two_concurrent_ticks_persist_one_run(env):
    t = create(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda which: tick(env, which), [0, 1]))
    row = run_row(env, t)
    assert row['status'] == 'valid'
    outcomes = [next(x['outcome'] for x in result['results'] if x['test_id'] == t['id']) for result in results]
    assert 'run_created' in outcomes
    assert set(outcomes) <= {'run_created', 'lease_held', 'no_new_candle'}
    assert env[0].rows(f"select count(*) n from paper_run_claims where test_id='{t['id']}'") == [{'n': 1}]
    tick(env)
    assert run_row(env, t)['id'] == row['id']


@pytest.mark.parametrize('prefix,reason', [('GAP','incomplete_candles'), ('NOCOST','cost_profile_unavailable'), ('OPEN','missing_open_positions')])
def test_at3_at11_invalid_evidence_remains_missing_in_database(env, prefix, reason):
    t = create(env, prefix)
    tick(env)
    row = run_row(env, t)
    assert row['status'] == 'invalid' and row['invalid_reason'] == reason
    if prefix == 'OPEN':
        assert row['raw']['engine_open_count'] > 0
        assert row['positions_present'] is False and row['open_count'] is None
    else:
        assert row['tape_present'] is False and row['trade_count'] is None
    assert env[0].rows(f"select id from paper_verdicts where test_id='{t['id']}'") == []


def test_at35_http_caller_cannot_choose_horizon(env):
    r = env[1].post('/api/paper/measure/tick', json={'tick_id': env[4][0], 'test_end_ms': 999},
                    headers={'Authorization': 'Bearer '+env[2]})
    assert r.status_code == 422
    rows = env[0].rows('select count(*) n from paper_runs r join paper_run_claims c on c.id=r.claim_id where r.test_end_ms<>c.test_end_ms or r.as_of_ms<>c.test_end_ms')
    assert rows == [{'n': 0}]


def test_actual_closed_trade_is_run_owned_and_replayable(env):
    from app.paper_measurement import RunEvidence, replay_run
    t = create(env, 'CLOSED')
    tick(env)
    row = run_row(env, t)
    assert row['status'] == 'valid' and row['trade_count'] > 0 and row['open_count'] == 0
    snapshot = env[0].rpc('paper_read_run', p_run_id=row['id'])
    tape = snapshot['trades']
    assert hash_json(tape) == row['tape_sha256']
    assert all(x['run_id'] == row['id'] and x['closed_at_ms'] <= row['test_end_ms'] for x in tape)
    item = RunEvidence(snapshot['run'], snapshot['candles'], tape, snapshot['open_positions'], 'verified')
    result = replay_run(row['id'], read_snapshot=lambda _: (snapshot['test'], item))
    assert result['reason'] == 'replay_match'


def test_shadow_stop_precedes_new_claim(env):
    t = create(env, shadow=True, ended=True)
    tick(env)
    assert env[0].rows(f"select status,authoritative_verdict_id from paper_tests where id='{t['id']}'") == [{'status':'stopped','authoritative_verdict_id':None}]
    assert env[0].rows(f"select id from paper_run_claims where test_id='{t['id']}'") == []


def test_failed_latest_fetch_has_durable_error_outcome(env):
    t = create(env, 'ERROR')
    tick(env)
    assert env[0].rows(f"select outcome,detail from paper_tick_outcomes where test_id='{t['id']}'") == [{'outcome':'error','detail':{'reason':'worker_failed'}}]
    assert env[0].rows(f"select id from paper_run_claims where test_id='{t['id']}'") == []


def test_at28_next_tick_recovers_persist_before_assess(env):
    # Persist a synthetic run through real HTTP RPCs, deliberately omit assess.
    t = create(env, after_gate=True, shadow=True)
    s = env[0]
    now = s.rows('select floor(extract(epoch from clock_timestamp())*1000)::bigint as ms')[0]['ms']
    claim = s.rpc('paper_claim_run', p_test_id=t['id'], p_tick_id=env[4][0],
                  p_last_candle_open_ms=now//STEP_MS*STEP_MS-STEP_MS)['claim']
    item = evidence(t, run_id=str(uuid4()), as_of=claim['test_end_ms'], pnls=[])
    s.rpc('paper_archive_candles',p_source='okx',p_pair=t['pair'],p_timeframe='5m',p_rows=list(item.candles))
    assert s.rpc('paper_persist_run',p_claim_id=claim['id'],p_lease_token=claim['lease_token'],p_run=item.run,p_trades=[],p_positions=[])['outcome']=='run_created'
    tick(env, 1)
    assert s.rows(f"select eligibility,tick_origin_verified from paper_run_assessments where run_id='{item.run['id']}'") == [{'eligibility':'proven_ineligible','tick_origin_verified':True}]
    assert s.rows(f"select id from paper_verdicts where test_id='{t['id']}'") == []


@pytest.mark.parametrize('role', ['anon','authenticated'])
def test_new_worker_rpcs_are_not_public(env, role):
    s = env[0]
    for name in ('paper_worker_page(uuid,uuid)','paper_diagnostic_context(uuid)',
                 'paper_worker_outcome(uuid,uuid,text,text,uuid,uuid)'):
        assert s.rows(f"select has_function_privilege('{role}','public.{name}','EXECUTE') allowed") == [{'allowed':False}]
    assert s.rows("select pg_has_role('postgres','paper_rpc_owner','SET') allowed") == [{'allowed':False}]
