"""A4 rev2 on the disposable stack: real HTTP worker in Preview tick-token mode.

AT72: concurrent delivery of one tick token persists one claim and one run.
AT73: the token never extends the 120 s tick window enforced by the database.
Ticks are inserted by the local image administrator with a chosen created_at;
origin itself is covered by A2/A3, here only age and idempotency are tested.
"""
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
from app.paper_measurement import STEP_MS, hash_json
from app.paper_routes import tick_token
from test_paper_measurement import test_config
from test_local_stack import Stack, eventually

PORT = 8766
TICK = '/api/paper/measure/tick'


@pytest.fixture(scope='module')
def env():
    s = Stack()
    cron_secret, diagnostic_secret = 'c' * 20 + uuid4().hex, 'd' * 20 + uuid4().hex
    process_env = dict(os.environ, PAPER_MEASUREMENT_ENABLED='1', PAPER_SUPABASE_URL=s.url,
        PAPER_SUPABASE_SERVICE_ROLE_KEY=s.keys['service_role'], CRON_SECRET=cron_secret,
        PAPER_DIAGNOSTIC_SECRET=diagnostic_secret, VERCEL_GIT_COMMIT_SHA=os.environ['PAPER_P2_COMMIT'])
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server_tick_token:app', '--app-dir',
        str(Path(__file__).parent), '--host', '127.0.0.1', '--port', str(PORT), '--no-access-log'],
        env=process_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = httpx.Client(base_url=f'http://127.0.0.1:{PORT}', timeout=100, trust_env=False)
    try:
        def ready():
            assert process.poll() is None, 'CI tick-token server stopped'
            try:
                return client.get('/health').status_code == 200
            except httpx.ConnectError:
                return False
        eventually(ready, bool, timeout=15)
        yield s, client, cron_secret
    finally:
        process.terminate()
        process.wait(timeout=10)
        client.close(); s.client.close()


def create(s):
    now = s.rows('select floor(extract(epoch from clock_timestamp())*1000)::bigint as ms')[0]['ms']
    t = test_config(start_ms=now//STEP_MS*STEP_MS - 5*STEP_MS + int(uuid4().hex[:6], 16) % 100000, warmup=24)
    pair = 'VALID' + uuid4().hex[:8].upper() + '/USDT'
    t['pair'] = pair; t['settings']['selected_pairs'] = [pair]; t['settings_sha256'] = hash_json(t['settings'])
    t.update(id=str(uuid4()), variant='B', strategy_version_id='synthetic-a4-ci', lease_ms=120000,
             origin_grace_ms=120000, shadow=False, shadow_until_ms=None)
    return s.rpc('paper_create_test', p_test=t)


def new_tick(s, offset_s):
    """offset_s > 0: tick in the past, < 0: tick in the future (relative to DB clock)."""
    tick = str(uuid4())
    s.admin("insert into public.paper_measure_ticks(tick_id,source,cron_job_name,backend_pid,created_at) "
            f"values ('{tick}','pg_cron','paper_tick',0,clock_timestamp() - interval '{offset_s} s');")
    return tick


def post(env, tick, secret=None):
    return env[1].post(TICK, json={'tick_id': tick},
                       headers={'Authorization': tick_token(secret or env[2], tick)})


def evidence(s, tick, test=None):
    where = f"tick_id='{tick}'" + (f" and test_id='{test['id']}'" if test else '')
    return s.rows(f"select (select count(*) from paper_run_claims where {where}) claims, "
                  f"(select count(*) from paper_tick_outcomes where {where}) outcomes")[0]


def own(result, test):
    return next(x['outcome'] for x in result['results'] if x['test_id'] == test['id'])


def test_at72_concurrent_delivery_of_one_tick_token_persists_one_claim(env):
    s = env[0]
    t = create(s)
    tick = new_tick(s, 0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: post(env, tick), range(2)))
    assert [r.status_code for r in responses] == [200, 200], [r.text[:200] for r in responses]
    outcomes = [own(r.json(), t) for r in responses]
    # The second delivery sees the claim (tick_already_used / lease_held) or its stored outcome.
    assert 'run_created' in outcomes and set(outcomes) <= {'run_created', 'tick_already_used', 'lease_held'}, outcomes
    assert evidence(s, tick, t)['claims'] == 1
    runs = s.rows(f"select id from paper_runs where test_id='{t['id']}'")
    assert len(runs) == 1
    # Replay of the captured token inside the window: idempotent, no new writes.
    replay = post(env, tick)
    assert replay.status_code == 200 and own(replay.json(), t) in ('run_created', 'tick_already_used')
    assert evidence(s, tick, t)['claims'] == 1
    assert s.rows(f"select id from paper_runs where test_id='{t['id']}'") == runs


def test_at73_tick_window_boundaries_hold_with_a_valid_token(env):
    s = env[0]
    t = create(s)
    inside = new_tick(s, 117)        # 117 s old: inside the 0..119999 ms window
    r = post(env, inside)
    assert r.status_code == 200 and r.json()['outcome'] == 'processed', r.text[:300]
    assert any(x['test_id'] == t['id'] for x in r.json()['results'])
    assert sum(evidence(s, inside, t).values()) >= 1  # claim and/or outcome row for this tick
    before = evidence(s, inside)
    for tick in (new_tick(s, 121),   # 121 s old: outside the window
                 new_tick(s, -5),    # created in the future
                 str(uuid4())):      # valid HMAC, tick does not exist
        r = post(env, tick)
        assert r.status_code == 200 and r.json() == {'outcome': 'invalid_tick', 'results': []}, r.text[:300]
        assert evidence(s, tick) == {'claims': 0, 'outcomes': 0}
    # The same captured token is useless once its tick ages out of the window.
    eventually(lambda: s.rows(f"select clock_timestamp() - created_at > interval '120 s' as old "
                              f"from paper_measure_ticks where tick_id='{inside}'")[0]['old'], bool, timeout=10)
    r = post(env, inside)
    assert r.status_code == 200 and r.json() == {'outcome': 'invalid_tick', 'results': []}, r.text[:300]
    assert evidence(s, inside) == before


def test_tick_token_server_rejects_bearer_and_foreign_tokens_without_writes(env):
    s = env[0]
    tick = new_tick(s, 0)
    for headers in ({'Authorization': 'Bearer ' + env[2]},
                    {'Authorization': tick_token(env[2], str(uuid4()))},
                    {'Authorization': tick_token('x' * 40, tick)}, {}):
        assert env[1].post(TICK, json={'tick_id': tick}, headers=headers).status_code == 401
    assert evidence(s, tick) == {'claims': 0, 'outcomes': 0}
