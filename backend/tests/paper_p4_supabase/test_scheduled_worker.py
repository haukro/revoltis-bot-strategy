"""Real one-minute cron -> pg_net -> worker, with unmodified wall clocks.

Short supplementary evidence only. Eleven ticks are not the 24-hour P4 gate.
No market connection, hosted project, public listener, or production secret.
"""
from collections import Counter, defaultdict
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

if not os.environ.get('PAPER_P4_SUPABASE'):
    pytest.skip('Requires isolated P4 Supabase CI', allow_module_level=True)

import httpx
from psycopg import sql
from app.paper_measurement import GATE_MS, STEP_MS, RunEvidence, hash_json, replay_run
from test_paper_measurement import test_config
from test_local_stack import Stack, command, eventually


def now_ms(stack):
    return stack.rows('select floor(extract(epoch from clock_timestamp())*1000)::bigint ms')[0]['ms']


def create_case(stack, kind, stamp):
    t = test_config(label='scale' if kind in ('SIGNAL', 'FLAT', 'GAP') else 'official',
                    start_ms=stamp // STEP_MS * STEP_MS - GATE_MS - 10 * STEP_MS)
    pair = 'P4' + kind + uuid4().hex[:8].upper() + '/USDT'
    t['pair'] = pair
    t['settings'].update(selected_pairs=[pair], initial_capital=10000., stop_loss_percent=5.)
    t['settings_sha256'] = hash_json(t['settings'])
    t.update(id=str(uuid4()), variant='B', strategy_version_id='synthetic-p4-ci',
        lease_ms=120000, origin_grace_ms=120000, shadow=kind == 'SHADOW',
        shadow_until_ms=stamp + 150000 if kind == 'SHADOW' else None)
    return stack.rpc('paper_create_test', p_test=t)


@pytest.fixture(scope='module')
def scheduled():
    s = Stack()  # Requires CI, loopback Supabase, exact project/container identity.
    network = json.loads(command(['docker', 'network', 'inspect', s.network]))[0]
    gateway = network['IPAM']['Config'][0]['Gateway']
    assert ipaddress.ip_address(gateway).is_private
    secret = uuid4().hex
    url = f'http://{gateway}:8766'
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server:app', '--app-dir',
        str(Path(__file__).parent), '--host', gateway, '--port', '8766', '--no-access-log'],
        env=dict(os.environ, PAPER_MEASUREMENT_ENABLED='1', PAPER_SUPABASE_URL=s.url,
            PAPER_SUPABASE_SERVICE_ROLE_KEY=s.keys['service_role'], CRON_SECRET=secret,
            PAPER_DIAGNOSTIC_SECRET=uuid4().hex, VERCEL_GIT_COMMIT_SHA=os.environ['PAPER_P2_COMMIT']),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = httpx.Client(base_url=url, trust_env=False, timeout=5)
    job = None
    try:
        def ready():
            assert process.poll() is None, 'CI worker stopped'
            try:
                return client.get('/health').status_code == 200
            except httpx.ConnectError:
                return False
        eventually(ready, bool, timeout=15)
        # Private fixture config prevents embedding a credential in the cron
        # command/history. The app schema, functions and ACLs are untouched.
        s.admin('''create table paper_ci_support.p4_config(url text, secret text);
          create table paper_ci_support.p4_dispatch(tick_id uuid primary key,
            session_name text, current_name text, pid integer, request_id bigint);
          revoke all on paper_ci_support.p4_config,paper_ci_support.p4_dispatch from public;
          grant select on paper_ci_support.p4_config to paper_cron_owner;
          grant insert on paper_ci_support.p4_dispatch to paper_cron_owner;''')
        s.admin(sql.SQL('insert into paper_ci_support.p4_config values({},{});').format(
            sql.Literal(url + '/api/paper/measure/tick'), sql.Literal(secret)).as_string())
        stamp = now_ms(s)
        cases = {kind: create_case(s, kind, stamp) for kind in ('CONT', 'SIGNAL', 'FLAT', 'GAP', 'LAG', 'SHADOW')}
        statement = '''with tick as materialized (select public.paper_tick_start() as id)
          insert into paper_ci_support.p4_dispatch
          select id,session_user,current_user,pg_backend_pid(),net.http_post(
            url:=config.url, body:=jsonb_build_object('tick_id',id),
            headers:=jsonb_build_object('Content-Type','application/json','Authorization','Bearer '||config.secret),
            timeout_milliseconds:=100000)
          from tick cross join paper_ci_support.p4_config config'''
        job = int(s.admin(sql.SQL("select cron.schedule_in_database('paper_tick','* * * * *',{},'postgres','paper_cron_owner',true);").format(sql.Literal(statement)).as_string()))
        assert s.rows(f'select schedule,username,database from cron.job where jobid={job}') == [
            {'schedule': '* * * * *', 'username': 'paper_cron_owner', 'database': 'postgres'}]
        progress = 0
        def delivered():
            nonlocal progress
            assert process.poll() is None, 'CI worker stopped'
            rows = s.rows('''select d.*, floor(extract(epoch from t.created_at)*1000)::bigint tick_ms,
                h.status_code,h.timed_out,h.error_msg,h.content from paper_ci_support.p4_dispatch d
                join paper_measure_ticks t using(tick_id)
                left join net._http_response h on h.id=d.request_id order by t.created_at''')
            complete = [r for r in rows if r['status_code'] is not None or r['error_msg']]
            for row in complete:
                assert row['status_code'] == 200 and not row['timed_out'] and not row['error_msg'], {
                    k: row[k] for k in ('request_id', 'status_code', 'timed_out', 'error_msg')}
                assert json.loads(row['content'])['outcome'] == 'processed'
            if len(complete) > progress:
                progress = len(complete)
                print(f'P4 real scheduled HTTP ticks completed: {progress}/11', flush=True)
            return complete
        rows = eventually(delivered, lambda r: len(r) >= 11, timeout=750)
        s.admin(f'select cron.alter_job({job},active:=false);')
        # Keep the job metadata available for origin verification until all
        # assertions/replays finish; unschedule only during fixture teardown.
        eventually(lambda: s.rows(f"select count(*) n from cron.job_run_details where jobid={job} and status='succeeded'"),
                   lambda r: r[0]['n'] == len(rows), timeout=15)
        yield {'stack': s, 'cases': cases, 'rows': rows, 'job': job, 'url': url, 'secret': secret}
    finally:
        if job is not None:
            s.admin(f'select cron.unschedule({job});')
        process.terminate()
        process.wait(timeout=10)
        client.close()
        s.client.close()


def case_rows(env, kind, table='paper_runs'):
    assert table in ('paper_runs', 'paper_tests', 'paper_verdicts', 'paper_tick_outcomes', 'paper_run_claims', 'paper_run_assessments')
    key = 'id' if table == 'paper_tests' else 'test_id'
    return env['stack'].rows(f"select * from {table} where {key}='{env['cases'][kind]['id']}'")


def test_at38_real_cron_origin_and_http_delivery_have_no_missing_minutes(scheduled):
    e = scheduled
    rows = e['rows']
    assert len(rows) == 11
    minutes = [r['tick_ms'] // 60000 for r in rows]
    assert minutes == list(range(minutes[0], minutes[0] + len(rows)))
    assert 590000 <= rows[-1]['tick_ms'] - rows[0]['tick_ms'] <= 610000
    assert all(r['session_name'] == r['current_name'] == 'paper_cron_owner' for r in rows)
    details = e['stack'].rows(f'''select d.tick_id,j.status,j.job_pid=d.pid as matching_pid,
        j.start_time<=t.created_at and t.created_at<=j.end_time as contains_tick
        from paper_ci_support.p4_dispatch d join paper_measure_ticks t using(tick_id)
        join cron.job_run_details j on j.jobid={e['job']} and j.job_pid=d.pid
        and j.start_time<=t.created_at and t.created_at<=j.end_time''')
    assert len(details) == len(rows)
    assert all(r['status'] == 'succeeded' and r['matching_pid'] and r['contains_tick'] for r in details)
    assert {r['tick_id'] for r in details} == {r['tick_id'] for r in rows}
    print('P4 evidence:', json.dumps({'ticks': len(rows), 'first_tick_ms': rows[0]['tick_ms'],
        'last_tick_ms': rows[-1]['tick_ms'], 'span_ms': rows[-1]['tick_ms']-rows[0]['tick_ms'],
        'missing_minutes': 0, 'verified_origins': len(details)}))


def test_at27_five_ticks_one_boundary_and_new_boundary_persist_exactly_once(scheduled):
    e = scheduled
    runs = sorted(case_rows(e, 'CONT'), key=lambda r: r['seq'])
    boundaries = sorted({r['tick_ms'] // STEP_MS * STEP_MS for r in e['rows']})
    assert [r['boundary_ms'] for r in runs] == boundaries
    assert [r['seq'] for r in runs] == list(range(1, len(runs) + 1))
    assert len(runs) >= 3 and all(r['status'] == 'valid' for r in runs)
    outcomes = {r['tick_id']: r['outcome'] for r in case_rows(e, 'CONT', 'paper_tick_outcomes')}
    assert set(outcomes) == {r['tick_id'] for r in e['rows']}
    grouped = defaultdict(list)
    for tick in e['rows']:
        grouped[tick['tick_ms'] // STEP_MS].append(outcomes[tick['tick_id']])
    assert any(len(group) == 5 for group in grouped.values())
    for group in grouped.values():
        assert Counter(group) == {'run_created': 1, **({'no_new_candle': len(group)-1} if len(group)>1 else {})}
    claims = {r['id']: r for r in case_rows(e, 'CONT', 'paper_run_claims')}
    assert len(claims) == len(runs)
    for run in runs:
        claim = claims[run['claim_id']]
        assert run['test_end_ms'] == run['as_of_ms'] == claim['test_end_ms']
        assert run['boundary_ms'] == run['test_end_ms'] // STEP_MS * STEP_MS
    assert all(r['eligibility'] == 'proven_ineligible' and r['tick_origin_verified']
               for r in case_rows(e, 'CONT', 'paper_run_assessments'))
    assert len(case_rows(e, 'CONT', 'paper_run_assessments')) == len(runs)
    print('P4 automatic measurements:', json.dumps({'boundaries': len(boundaries),
        'runs': len(runs), 'outcomes': dict(Counter(outcomes.values()))}))


def test_at27_old_candle_rejected_at_every_boundary(scheduled):
    assert case_rows(scheduled, 'LAG') == case_rows(scheduled, 'LAG', 'paper_run_claims') == []
    outcomes = case_rows(scheduled, 'LAG', 'paper_tick_outcomes')
    assert len(outcomes) == 11 and all(r['outcome'] == 'boundary_race' for r in outcomes)


def test_at29_scale_complete_incomplete_and_below_count_gate(scheduled):
    for kind, status, verdict in (('SIGNAL', 'completed', 'DIAGNOSTIC'), ('GAP', 'invalid', 'INVALID')):
        runs = case_rows(scheduled, kind)
        assert len(runs) == 1 and runs[0]['seq'] == 1
        assert len(case_rows(scheduled, kind, 'paper_run_claims')) == 1
        assert case_rows(scheduled, kind, 'paper_tests')[0]['status'] == status
        verdicts = case_rows(scheduled, kind, 'paper_verdicts')
        assert len(verdicts) == 1 and verdicts[0]['verdict'] == verdict
        assert verdicts[0]['candidate_run_id'] == runs[0]['id']
        assessments = case_rows(scheduled, kind, 'paper_run_assessments')
        assert len(assessments) == 1 and assessments[0]['tick_origin_verified']
        if kind == 'SIGNAL':
            assert runs[0]['status'] == 'valid' and runs[0]['trade_count'] >= 30
            assert assessments[0]['eligibility'] == 'eligible'
        else:
            assert runs[0]['status'] == 'invalid' and not runs[0]['tape_present']
            assert runs[0]['trade_count'] is None and assessments[0]['eligibility'] == 'unverifiable'
    flat = case_rows(scheduled, 'FLAT')
    assert len(flat) >= 3 and all(r['status'] == 'valid' and r['trade_count'] == 0 for r in flat)
    assert case_rows(scheduled, 'FLAT', 'paper_tests')[0]['status'] == 'running'
    assert case_rows(scheduled, 'FLAT', 'paper_verdicts') == []
    print('P4 scale: 40-trade fixture DIAGNOSTIC; missing tape INVALID; zero trades remain running.')


def test_shadow_stops_on_real_clock_without_verdict_or_later_persist(scheduled):
    t = scheduled['cases']['SHADOW']
    assert case_rows(scheduled, 'SHADOW', 'paper_tests')[0]['status'] == 'stopped'
    assert case_rows(scheduled, 'SHADOW', 'paper_verdicts') == []
    runs = case_rows(scheduled, 'SHADOW')
    assert runs and all(r['test_end_ms'] < t['shadow_until_ms'] for r in runs)
    assert all(r['test_end_ms'] < t['shadow_until_ms'] for r in case_rows(scheduled, 'SHADOW', 'paper_run_claims'))
    outcomes = case_rows(scheduled, 'SHADOW', 'paper_tick_outcomes')
    assert sum(r['outcome'] == 'shadow_ended' for r in outcomes) == 1
    assert scheduled['stack'].rows(f"select count(*) n from paper_runs where test_id='{t['id']}' and persisted_at>=to_timestamp({t['shadow_until_ms']}/1000.0)") == [{'n': 0}]


def test_at5_three_automatic_snapshots_and_scale_tape_replay_without_network(scheduled):
    selected = sorted(case_rows(scheduled, 'CONT'), key=lambda r: r['seq'])[:3]
    selected += case_rows(scheduled, 'SIGNAL')
    assert len(selected) == 4
    for row in selected:
        snap = scheduled['stack'].rpc('paper_read_run', p_run_id=row['id'])
        item = RunEvidence(snap['run'], snap['candles'], snap['trades'], snap['open_positions'], 'verified')
        assert replay_run(row['id'], read_snapshot=lambda _: (snap['test'], item))['reason'] == 'replay_match'
        assert row['code_commit'] == os.environ['PAPER_P2_COMMIT']


def test_same_tick_redelivery_and_bad_secret_do_not_write_again(scheduled):
    s = scheduled['stack']
    tick_id = scheduled['rows'][-1]['tick_id']
    counts = "select (select count(*) from paper_runs) runs,(select count(*) from paper_run_claims) claims,(select count(*) from paper_tick_outcomes) outcomes,(select count(*) from paper_verdicts) verdicts"
    before = s.rows(counts)
    for secret, status in ((scheduled['secret'], 200), ('wrong', 401)):
        request = int(s.admin(sql.SQL('''select net.http_post(url:={},body:=jsonb_build_object('tick_id',{}),
          headers:=jsonb_build_object('Content-Type','application/json','Authorization',{}),timeout_milliseconds:=100000);''').format(
            sql.Literal(scheduled['url']+'/api/paper/measure/tick'), sql.Literal(tick_id),
            sql.Literal('Bearer '+secret)).as_string()))
        response = eventually(lambda: s.rows(f'select status_code,timed_out,error_msg from net._http_response where id={request}'), bool, timeout=30)[0]
        assert response['status_code'] == status and not response['timed_out'] and not response['error_msg']
        assert s.rows(counts) == before
