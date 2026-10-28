"""Real PostgreSQL tests. Never connect to a Supabase project or arbitrary host.

PAPER_P2_TEST_DSN must name a disposable local database starting paper_p2_test.
Each case creates and drops its own database on that isolated cluster. Cron
catalogs and the clock are synthetic fixtures, not a certification of P4 cron.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from queue import Queue
from itertools import permutations
import os
import time
from uuid import uuid4

import pytest

DSN = os.environ.get('PAPER_P2_TEST_DSN')
if not DSN:
    pytest.skip('P2 integration requires an isolated local PostgreSQL', allow_module_level=True)
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from app.paper_assessment import prepare_assessment
from app.paper_measurement import GATE_MS, STEP_MS, hash_json
from test_paper_measurement import START, evidence, test_config

NOW = START + GATE_MS + STEP_MS
COMMIT = 'b' * 40
MIGRATION = Path(__file__).parents[3] / 'supabase/migrations/20261028205717_paper_measurement_p2.sql'
TABLES = ['paper_tests', 'paper_measure_ticks', 'paper_tick_outcomes', 'paper_run_claims',
          'paper_runs', 'paper_run_trades', 'paper_run_open_positions', 'candle_archive',
          'paper_diagnostic_runs', 'paper_diagnostic_trades', 'paper_verdicts', 'paper_run_assessments']


class Database:
    def __init__(self, dsn):
        self.dsn = dsn

    def connect(self):
        return psycopg.connect(self.dsn, autocommit=True, options='-c statement_timeout=30000')

    def query(self, query, params=()):
        with self.connect() as c:
            return c.execute(query, params or None).fetchall()

    def execute(self, query, params=()):
        with self.connect() as c:
            c.execute(query, params or None)

    def rpc(self, name, **kwargs):
        with self.connect() as c:
            c.execute('set role service_role')
            args = sql.SQL(',').join(sql.SQL('{} => %s').format(sql.Identifier(k)) for k in kwargs)
            values = [Jsonb(v) if isinstance(v, (dict, list)) else v for v in kwargs.values()]
            return c.execute(sql.SQL('select public.{}({})').format(sql.Identifier(name), args), values).fetchone()[0]

    def clock(self, stamp, step=0):
        self.execute('update test_support.clock set ms=%s,step=%s,calls=0', (stamp, step))

    def tick(self, *, at=NOW, origin='succeeded'):
        tick = str(uuid4())
        pid = int(uuid4().int % 1000000000)
        self.execute("insert into paper_measure_ticks values (%s,'pg_cron','paper_tick',%s,paper_private.ts(%s))", (tick, pid, at))
        if origin:
            self.execute("insert into cron.job_run_details(jobid,job_pid,database,username,status,start_time,end_time) "
                         "values (1,%s,current_database(),'paper_cron_owner',%s,paper_private.ts(%s),paper_private.ts(%s))",
                         (pid, origin, at - 1, at + 1 if origin in ('succeeded', 'failed') else None))
        return tick

    def create_test(self, *, pre_gate=False, label='official', shadow=False, until=None, lease=240000, grace=600000):
        t = test_config(label=label, start_ms=NOW - 10 * STEP_MS if pre_gate else START)
        t.update(id=str(uuid4()), variant='B', strategy_version_id='synthetic-p2', lease_ms=lease,
                 origin_grace_ms=grace, shadow=shadow, shadow_until_ms=until)
        return self.rpc('paper_create_test', p_test=t)

    def claim(self, t, *, at=NOW, origin='succeeded', tick=None, boundary=None):
        self.clock(at)
        tick = tick or self.tick(at=at, origin=origin)
        return self.rpc('paper_claim_run', p_test_id=t['id'], p_tick_id=tick,
                        p_last_candle_open_ms=(at // STEP_MS * STEP_MS if boundary is None else boundary) - STEP_MS)

    def persist(self, t, claim, *, pnls=None, valid=True, tape=True, positions=None, run_change=None, archive=True):
        c = claim['claim']
        item = evidence(t, run_id=str(uuid4()), as_of=c['test_end_ms'], pnls=pnls)
        row = deepcopy(item.run)
        if not valid:
            row.update(status='invalid', invalid_reason='synthetic_missing_evidence')
        if run_change:
            row.update(run_change)
        trades = list(item.trades) if tape else None
        pos = [] if positions is None else [{**p, 'run_id': row['id']} for p in positions]
        row['open_count'] = len(pos)
        if archive:
            self.rpc('paper_archive_candles', p_source='okx', p_pair=t['pair'], p_timeframe='5m', p_rows=list(item.candles))
        result = self.rpc('paper_persist_run', p_claim_id=c['id'], p_lease_token=c['lease_token'],
                          p_run=row, p_trades=trades, p_positions=pos)
        return result, row

    def assess(self, run_id):
        snap = self.rpc('paper_read_run', p_run_id=run_id)
        args = prepare_assessment(snap, evaluator_commit=COMMIT)
        return self.rpc('paper_assess_run', **args)


@pytest.fixture
def db():
    parts = conninfo_to_dict(DSN)
    if parts.get('host') not in ('localhost', '127.0.0.1', '::1') or not parts.get('dbname', '').startswith('paper_p2_test'):
        pytest.fail('Refusing non-local or non-test database')
    name = 'paper_p2_test_' + uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as admin:
        for role in ('anon', 'authenticated', 'service_role'):
            if not admin.execute('select 1 from pg_roles where rolname=%s', (role,)).fetchone():
                admin.execute(sql.SQL('create role {} nologin {}').format(
                    sql.Identifier(role), sql.SQL('bypassrls' if role == 'service_role' else 'nobypassrls')))
        admin.execute(sql.SQL('create database {}').format(sql.Identifier(name)))
    instance = Database(make_conninfo(DSN, dbname=name))
    try:
        instance.execute('alter default privileges in schema public grant all on tables to anon,authenticated,service_role; '
                         'alter default privileges in schema public grant execute on functions to anon,authenticated,service_role')
        instance.execute(MIGRATION.read_text())
        instance.execute('''
          create schema test_support;
          create table test_support.clock(ms bigint, step bigint default 0, calls integer default 0);
          insert into test_support.clock values (1702592100000,0,0);
          create or replace function paper_private.now_ms() returns bigint language plpgsql
          security definer set search_path='' as $clock$
          declare result bigint; begin
            update test_support.clock set ms=ms+step,calls=calls+1 returning ms-step into result;
            return result;
          end $clock$;
          alter function paper_private.now_ms() owner to postgres;
          grant execute on function paper_private.now_ms() to paper_rpc_owner;
          create schema cron;
          create table cron.job(jobid bigint primary key, jobname text, username text, database text);
          insert into cron.job values (1,'paper_tick','paper_cron_owner',current_database());
          create table cron.job_run_details(runid bigint generated always as identity, jobid bigint,
            job_pid integer,database text,username text,status text,start_time timestamptz,end_time timestamptz);
          grant usage on schema cron to paper_rpc_owner;
          grant select on all tables in schema cron to paper_rpc_owner;
        ''')
        instance.clock(NOW)
        yield instance
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(sql.SQL('drop database {} with (force)').format(sql.Identifier(name)))


def test_at1_at7_run_owned_tapes_and_costs_do_not_overwrite(db):
    t = db.create_test(pre_gate=True)
    first, _ = db.persist(t, db.claim(t), pnls=[1])
    second, _ = db.persist(t, db.claim(t, at=NOW + STEP_MS), pnls=[1])
    assert first['run']['seq'] == 1 and second['run']['seq'] == 2
    rows = db.query('select run_id,ordinal,closed_at_ms from paper_run_trades order by run_id')
    assert len(rows) == 2 and rows[0][1:] == rows[1][1:]
    assert db.query('select book_ts_ms from paper_runs order by seq') == [(NOW,), (NOW + STEP_MS,)]


def test_at6_archive_conflict_and_identical_retry(db):
    row = dict(evidence().candles[0])
    args = dict(p_source='okx', p_pair='TEST/USDT', p_timeframe='5m', p_rows=[row])
    assert db.rpc('paper_archive_candles', **args) == 1
    assert db.rpc('paper_archive_candles', **args) == 1
    args['p_rows'] = [{**row, 'close': 42}]
    with pytest.raises(psycopg.Error, match='candle_conflict'):
        db.rpc('paper_archive_candles', **args)
    assert float(db.query('select close from candle_archive')[0][0]) == row['close']


def test_at10_at23_duplicate_boundary_and_active_lease(db):
    t = db.create_test(pre_gate=True)
    first = db.claim(t)
    assert db.claim(t)['outcome'] == 'lease_held'
    db.persist(t, first, pnls=[])
    assert db.claim(t)['outcome'] == 'no_new_candle'
    assert db.query('select count(*) from paper_runs') == [(1,)]


@pytest.mark.parametrize('new_boundary', [False, True])
def test_at21_at22_at39_expiry_and_retry(db, new_boundary):
    t = db.create_test(pre_gate=True, lease=1000)
    first = db.claim(t)
    stamp = NOW + (STEP_MS if new_boundary else 1001)
    second = db.claim(t, at=stamp)
    assert second['claim']['attempt'] == (1 if new_boundary else 2)
    old, _ = db.persist(t, first, pnls=[], archive=False)
    assert old['outcome'] == 'lease_expired'
    fresh, _ = db.persist(t, second, pnls=[])
    assert fresh['run']['seq'] == 1
    assert db.query('select count(*) from paper_runs') == [(1,)]


@pytest.mark.parametrize('age', [-1, 120000, 120001])
def test_at25_future_or_old_tick_is_rejected(db, age):
    t = db.create_test()
    tick = db.tick(at=NOW-age)
    assert db.claim(t, tick=tick)['outcome'] == 'invalid_tick'
    assert db.query('select count(*) from paper_run_claims') == [(0,)]


def test_at25_missing_and_reused_tick(db):
    t = db.create_test()
    assert db.claim(t, tick=str(uuid4()))['outcome'] == 'invalid_tick'
    tick = db.tick()
    assert db.claim(t, tick=tick)['outcome'] == 'claimed'
    assert db.claim(t, tick=tick)['outcome'] == 'tick_already_used'


@pytest.mark.parametrize('table', ['paper_run_trades', 'paper_run_open_positions'])
def test_at26_late_insert_is_sealed(db, table):
    t = db.create_test(pre_gate=True)
    result, _ = db.persist(t, db.claim(t), pnls=[])
    with pytest.raises(psycopg.Error, match='run_sealed'):
        db.execute(sql.SQL('insert into {}(run_id,ordinal,evidence) values (%s,0,%s)').format(sql.Identifier(table)),
                   (result['run']['id'], Jsonb({})))


def test_at26_deferred_count_mismatch_rolls_back_entire_run(db):
    t = db.create_test(pre_gate=True)
    claim = db.claim(t)
    with pytest.raises(psycopg.Error, match='tape_count_mismatch'):
        db.persist(t, claim, pnls=[1], run_change={'trade_count': 2})
    assert db.query('select count(*) from paper_runs') == [(0,)]
    assert db.query('select count(*) from paper_run_trades') == [(0,)]
    assert db.query("select count(*) from paper_tick_outcomes where outcome='run_created'") == [(0,)]


def test_at28_persist_crash_idempotent_assess_and_audit(db):
    t = db.create_test()
    result, _ = db.persist(t, db.claim(t))
    rid = result['run']['id']
    assert db.query('select count(*) from paper_verdicts') == [(0,)]
    first = db.assess(rid)
    second = db.assess(rid)
    assert first == second
    assert first['verdict']['verdict'] == 'PASS'
    snap = db.rpc('paper_read_run', p_run_id=rid)
    args = prepare_assessment(snap, evaluator_commit=COMMIT)
    args.pop('p_run_id')
    db.rpc('paper_audit_run', p_verdict_id=first['verdict']['id'], **args)
    assert db.query('select kind,count(*) from paper_verdicts group by kind order by kind') == [('audit', 1), ('authoritative', 1)]
    assert db.query('select status,authoritative_verdict_id::text from paper_tests') == [('completed', first['verdict']['id'])]


def test_at34b_one_clock_observation_even_when_boundary_crosses(db):
    t = db.create_test(pre_gate=True)
    stamp = NOW + STEP_MS - 1
    tick = db.tick(at=stamp)
    db.clock(stamp, step=2)
    result = db.rpc('paper_claim_run', p_test_id=t['id'], p_tick_id=tick, p_last_candle_open_ms=NOW-STEP_MS)
    c = result['claim']
    assert c['test_end_ms'] == stamp and c['boundary_ms'] == NOW
    assert db.query('select calls from test_support.clock') == [(1,)]
    assert c['test_end_ms'] // STEP_MS * STEP_MS == c['boundary_ms']


def test_at35_claim_horizon_is_authoritative_and_remains_before_gate(db):
    t = db.create_test()
    t_id = t['id']
    stamp = t['start_ms'] + GATE_MS - 1
    claim = db.claim(t, at=stamp)
    db.clock(stamp + 2)
    result, _ = db.persist(t, claim, pnls=[], run_change={'test_end_ms': stamp+9999, 'as_of_ms': stamp+9999})
    assert result['run']['test_end_ms'] == stamp and result['run']['as_of_ms'] == stamp
    assert db.assess(result['run']['id'])['outcome'] == 'not_evaluated'
    assert db.query('select count(*) from paper_run_assessments where test_id=%s', (t_id,)) == [(0,)]


@pytest.mark.parametrize('role', ['anon', 'authenticated', 'service_role'])
def test_at38a_forbidden_tick_role(db, role):
    with db.connect() as c:
        c.execute(sql.SQL('set role {}').format(sql.Identifier(role)))
        with pytest.raises(psycopg.Error, match='permission denied'):
            c.execute('select paper_tick_start()')
    assert db.query('select count(*) from paper_measure_ticks') == [(0,)]


def test_at38a_set_role_cannot_fake_session_user(db):
    with db.connect() as c:
        c.execute('set role paper_cron_owner')
        with pytest.raises(psycopg.Error, match='tick_origin_unverified'):
            c.execute('select paper_tick_start()')
    with db.connect() as c:
        c.execute('set session authorization paper_cron_owner')
        assert c.execute('select paper_tick_start()').fetchone()[0]


@pytest.mark.parametrize('offset', [0, 1])
def test_at41_at45_shadow_exclusive_limit_even_before_stop(db, offset):
    end = NOW + 1000
    t = db.create_test(shadow=True, until=end)
    claim = db.claim(t)
    db.clock(end + offset)
    result, _ = db.persist(t, claim, pnls=[], archive=False)
    assert result['outcome'] == 'shadow_ended'
    assert db.claim(t, at=end+offset)['outcome'] == 'shadow_ended'
    assert db.rpc('paper_stop_shadow', p_test_id=t['id'])['outcome'] == 'stopped'
    assert db.query('select status,authoritative_verdict_id from paper_tests') == [('stopped', None)]
    assert db.query('select count(*) from paper_runs') == [(0,)]


def test_shadow_assessment_never_emits_authoritative_verdict(db):
    t = db.create_test(shadow=True, until=NOW+STEP_MS*2)
    result, _ = db.persist(t, db.claim(t))
    assessment = db.assess(result['run']['id'])
    assert assessment['assessment']['eligibility'] == 'eligible' and assessment['verdict'] is None
    assert db.query('select status from paper_tests') == [('running',)]


@pytest.mark.parametrize('missing_tape', [False, True])
def test_at43_pending_then_ineligible_or_unverifiable(db, missing_tape):
    t = db.create_test()
    result, _ = db.persist(t, db.claim(t), pnls=[], tape=not missing_tape)
    assert db.claim(t, at=NOW+STEP_MS)['outcome'] == 'evaluation_pending'
    assessed = db.assess(result['run']['id'])
    next_claim = db.claim(t, at=NOW+STEP_MS)
    assert next_claim['outcome'] == ('test_closed' if missing_tape else 'claimed')
    if missing_tape:
        assert assessed['verdict']['verdict'] == 'INVALID'
    else:
        assert assessed['assessment']['eligibility'] == 'proven_ineligible'


@pytest.mark.parametrize('initial', [None, 'running', 'starting'])
def test_at38e1_at46a_f_pending_does_not_write_or_extend_grace(db, initial):
    t = db.create_test(grace=1000)
    result, _ = db.persist(t, db.claim(t, origin=initial), pnls=[])
    rid = result['run']['id']
    for offset in (0, 500, 999, 1000):
        db.clock(NOW+offset)
        assert db.assess(rid)['outcome'] == 'origin_pending'
    assert db.query('select count(*) from paper_run_assessments') == [(0,)]
    assert db.query('select count(*) from paper_verdicts') == [(0,)]
    db.clock(NOW+1001)
    assessed = db.assess(rid)
    assert assessed['assessment']['eligibility'] == 'unverifiable'
    assert assessed['verdict']['invalid_reason'] == 'tick_origin_unverified'


@pytest.mark.parametrize('offset', [0, 1001])
def test_at38e2_at46b_d_failed_is_immediate_and_final(db, offset):
    t = db.create_test(grace=1000)
    result, _ = db.persist(t, db.claim(t, origin='failed'), pnls=[])
    db.clock(NOW+offset)
    answer = db.assess(result['run']['id'])
    assert answer['verdict']['verdict'] == 'INVALID'
    assert answer['assessment']['tick_origin_verified'] is False
    assert answer['assessment']['eligibility'] == 'unverifiable'


def test_at38d_at46a_success_arrives_during_grace(db):
    t = db.create_test(grace=1000)
    result, _ = db.persist(t, db.claim(t, origin='running'), pnls=[])
    assert db.assess(result['run']['id'])['outcome'] == 'origin_pending'
    db.execute("update cron.job_run_details set status='succeeded',end_time=paper_private.ts(%s)", (NOW+1,))
    answer = db.assess(result['run']['id'])
    assert answer['assessment']['eligibility'] == 'proven_ineligible'


def test_at46e_late_success_cannot_replace_invalid(db):
    t = db.create_test(grace=1000)
    result, _ = db.persist(t, db.claim(t, origin='running'), pnls=[])
    db.clock(NOW+1001)
    original = db.assess(result['run']['id'])
    db.execute("update cron.job_run_details set status='succeeded',end_time=paper_private.ts(%s)", (NOW+1,))
    assert db.assess(result['run']['id']) == original
    assert db.query('select status from paper_tests') == [('invalid',)]


def test_snapshot_hash_rejects_mutated_or_stale_attestation(db):
    t = db.create_test()
    result, _ = db.persist(t, db.claim(t), pnls=[])
    args = prepare_assessment(db.rpc('paper_read_run', p_run_id=result['run']['id']), evaluator_commit=COMMIT)
    args['p_evidence_sha256'] = '0' * 64
    with pytest.raises(psycopg.Error, match='evidence_changed'):
        db.rpc('paper_assess_run', **args)
    assert db.query('select count(*) from paper_run_assessments') == [(0,)]


def test_at36_persist_waiting_for_lock_checks_fresh_clock(db):
    t = db.create_test(pre_gate=True, lease=1000)
    claim = db.claim(t)
    ready = Queue()
    def late_persist():
        with db.connect() as c:
            c.execute('set role service_role')
            ready.put(c.info.backend_pid)
            return c.execute('select paper_persist_run(%s,%s,%s,%s,%s)',
                (claim['claim']['id'], claim['claim']['lease_token'], Jsonb({}), Jsonb([]), Jsonb([]))).fetchone()[0]
    with db.connect() as lock, ThreadPoolExecutor(max_workers=1) as pool:
        lock.execute('begin')
        lock.execute('select id from paper_tests where id=%s for update', (t['id'],))
        future = pool.submit(late_persist)
        pid = ready.get(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if db.query("select wait_event_type from pg_stat_activity where pid=%s", (pid,)) == [('Lock',)]:
                break
            time.sleep(.01)
        else:
            lock.execute('rollback')
            pytest.fail('persist did not block on test row')
        lock.execute('update test_support.clock set ms=%s', (NOW+1001,))
        lock.execute('commit')
        assert future.result(timeout=5)['outcome'] == 'lease_expired'
    assert db.query('select count(*) from paper_runs') == [(0,)]


def test_at43d_concurrent_assess_is_idempotent(db):
    t = db.create_test()
    result, _ = db.persist(t, db.claim(t))
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: db.assess(result['run']['id']), range(2)))
    assert answers[0] == answers[1]
    assert db.query('select count(*) from paper_run_assessments') == [(1,)]
    assert db.query('select count(*) from paper_verdicts') == [(1,)]


def test_acl_rls_and_default_supabase_grants_are_restricted(db):
    assert db.query("select count(*) from pg_class where relnamespace='public'::regnamespace and relkind='r' and relrowsecurity") == [(12,)]
    with db.connect() as c:
        c.execute('set role service_role')
        for table in TABLES:
            with pytest.raises(psycopg.Error, match='permission denied'):
                c.execute(sql.SQL('select * from {}').format(sql.Identifier(table)))
        with pytest.raises(psycopg.Error, match='permission denied'):
            c.execute('select paper_private.now_ms()')
    for role in ('anon','authenticated'):
        with db.connect() as c:
            c.execute(sql.SQL('set role {}').format(sql.Identifier(role)))
            with pytest.raises(psycopg.Error, match='permission denied'):
                c.execute("select paper_create_test('{}')")


def test_at2_update_delete_and_immutable_grace_on_every_table(db):
    t = db.create_test()
    pos = {'ordinal': 0, 'pair': t['pair'], 'opened_at_ms': START, 'entry_rate': 100, 'stake': 50, 'raw': {}}
    result, _ = db.persist(t, db.claim(t), positions=[pos])
    assert db.assess(result['run']['id'])['verdict']['verdict'] == 'PASS'
    diagnostic = str(uuid4())
    with db.connect() as c, c.transaction():
        c.execute("insert into paper_diagnostic_runs(id,test_id,test_end_ms,boundary_ms,payload,created_txid,trade_count) "
                  "values (%s,%s,%s,%s,'{}',txid_current(),1)", (diagnostic,t['id'],NOW,NOW))
        c.execute("insert into paper_diagnostic_trades values (%s,0,'{}')", (diagnostic,))
    for table in TABLES:
        assert db.query(sql.SQL('select count(*) from {}').format(sql.Identifier(table)))[0][0] > 0
        first_col = db.query('select attname from pg_attribute where attrelid=%s::regclass and attnum=1', (table,))[0][0]
        with pytest.raises(psycopg.Error, match='immutable'):
            db.execute(sql.SQL('update {} set {}={}').format(sql.Identifier(table),sql.Identifier(first_col),sql.Identifier(first_col)))
        with pytest.raises(psycopg.Error, match='immutable'):
            db.execute(sql.SQL('delete from {}').format(sql.Identifier(table)))
    with pytest.raises(psycopg.Error, match='immutable'):
        db.execute('update paper_tests set origin_grace_ms=origin_grace_ms+1')


def seed_defensive_claim(db, t, original):
    """Admin-only adversarial fixture for a stale/in-flight claim. Not an RPC path."""
    tick = db.tick(at=NOW+STEP_MS)
    patch = {'id': str(uuid4()), 'tick_id': tick, 'boundary_ms': NOW+STEP_MS,
             'last_candle_open_ms': NOW, 'test_end_ms': NOW+STEP_MS,
             'claimed_at': '2023-12-14T22:20:00+00:00', 'lease_expires_at': '2023-12-14T22:24:00+00:00',
             'attempt': 1, 'lease_token': str(uuid4())}
    # Derive timestamps from the synthetic numeric clock, not wall time.
    patch['claimed_at'] = db.query('select paper_private.ts(%s)::text', (NOW+STEP_MS,))[0][0]
    patch['lease_expires_at'] = db.query('select paper_private.ts(%s)::text', (NOW+STEP_MS+t['lease_ms'],))[0][0]
    db.execute('insert into paper_run_claims select (jsonb_populate_record(null::paper_run_claims,to_jsonb(c)||%s)).* '
               'from paper_run_claims c where id=%s', (Jsonb(patch),original['claim']['id']))
    return {'outcome':'claimed','claim':{**original['claim'],**patch}}


@pytest.mark.parametrize('order', list(permutations(('assess','claim','persist'))))
def test_at37_at44_every_lock_order_prevents_post_verdict_writes(db, order):
    t = db.create_test()
    first_claim = db.claim(t)
    result, _ = db.persist(t,first_claim)
    stale = seed_defensive_claim(db,t,first_claim)
    db.clock(NOW+STEP_MS)
    answers = {}
    for action in order:
        if action=='assess':
            answers[action] = db.assess(result['run']['id'])
        elif action=='claim':
            answers[action] = db.claim(t,at=NOW+STEP_MS)
        else:
            answers[action], _ = db.persist(t,stale,pnls=[],archive=False)
    assert answers['assess']['verdict']['verdict']=='PASS'
    assert answers['claim']['outcome'] in ('evaluation_pending','test_closed','no_new_candle')
    assert answers['persist']['outcome']==('test_closed' if order.index('assess')<order.index('persist') else 'run_created')
    assert db.query("select count(*) from paper_verdicts where kind='authoritative'")==[(1,)]
    assert db.query('select count(*) from paper_runs')==[(1 if order.index('assess')<order.index('persist') else 2,)]


def test_at44_out_of_order_assessment_cannot_skip_earlier_candidate(db):
    t = db.create_test()
    first_claim = db.claim(t)
    first, _ = db.persist(t,first_claim)
    second_claim = seed_defensive_claim(db,t,first_claim)
    db.clock(NOW+STEP_MS)
    second, _ = db.persist(t,second_claim,pnls=[],archive=False)
    assert db.assess(second['run']['id'])['outcome']=='assessment_out_of_order'
    assert db.query('select count(*) from paper_run_assessments')==[(0,)]
    assert db.assess(first['run']['id'])['verdict']['candidate_run_id']==first['run']['id']


def test_at44_actual_concurrent_assess_claim_persist(db):
    from threading import Barrier
    t = db.create_test()
    first_claim = db.claim(t)
    first, _ = db.persist(t,first_claim)
    stale = seed_defensive_claim(db,t,first_claim)
    db.clock(NOW+STEP_MS)
    tick = db.tick(at=NOW+STEP_MS)
    barrier = Barrier(3)
    def run(action):
        barrier.wait(timeout=10)
        if action=='assess': return db.assess(first['run']['id'])
        if action=='claim': return db.rpc('paper_claim_run',p_test_id=t['id'],p_tick_id=tick,p_last_candle_open_ms=NOW)
        return db.persist(t,stale,pnls=[],archive=False)[0]
    with ThreadPoolExecutor(max_workers=3) as pool:
        answers = list(pool.map(run,('assess','claim','persist')))
    assert answers[0]['verdict']['verdict']=='PASS'
    assert answers[1]['outcome'] in ('evaluation_pending','test_closed','no_new_candle')
    assert answers[2]['outcome'] in ('run_created','test_closed')
    assert db.query("select count(*) from paper_verdicts where kind='authoritative'")==[(1,)]
    assert db.query("select count(*) from paper_runs r join paper_tests t on t.id=r.test_id where r.persisted_at>t.completed_at")==[(0,)]
