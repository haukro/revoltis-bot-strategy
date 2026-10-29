"""Additional integration evidence on an unlinked, disposable local Supabase.

Uses actual PostgREST, Auth, pg_cron and pg_net, with wall-clock time. Neither
application SQL nor the extension catalogs are replaced by test doubles.
This does not certify managed Supabase or the P3/P4 preview acceptance gates.
"""
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
import json
import os
import subprocess
import time

import pytest

STATUS = os.environ.get('PAPER_P2_SUPABASE_STATUS')
if not STATUS:
    pytest.skip('Requires the isolated local Supabase CI job', allow_module_level=True)

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.paper_assessment import prepare_assessment
from app.paper_measurement import GATE_MS, STEP_MS
from test_paper_measurement import evidence, test_config

DB_CONTAINER = 'supabase_db_paper-p2-ci'
HTTP_CONTAINER = 'paper-p2-ci-http'


def command(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, check=True, timeout=45, **kwargs).stdout.strip()


def eventually(read, accept, timeout=90):
    deadline = time.monotonic() + timeout
    while True:
        result = read()
        if accept(result):
            return result
        if time.monotonic() >= deadline:
            pytest.fail(f'Timed out waiting for local integration evidence: {result!r}')
        time.sleep(.5)


class Stack:
    def __init__(self):
        assert os.environ.get('GITHUB_ACTIONS') == 'true', 'CI-only test'
        self.state = json.loads(Path(STATUS).read_text())
        self.dsn = self.state['DB_URL']
        parts = conninfo_to_dict(self.dsn)
        assert parts.get('host') in ('127.0.0.1', 'localhost', '::1')
        assert parts.get('hostaddr') in (None, '127.0.0.1', '::1') and 'service' not in parts
        assert parts.get('port') == '54322' and parts.get('dbname') == 'postgres'
        self.url = self.state['API_URL'].rstrip('/')
        parsed = urlsplit(self.url)
        assert parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')
        assert parsed.port == 54321 and not parsed.username and not parsed.password
        info = json.loads(command(['docker', 'inspect', DB_CONTAINER]))[0]
        assert info['Config']['Labels'].get('com.supabase.cli.project') == 'paper-p2-ci'
        assert 'supabase/postgres' in info['Config']['Image']
        self.network = next(iter(info['NetworkSettings']['Networks']))
        # No credential is printed or copied to GitHub outputs/artifacts.
        self.keys = {'anon': self.state['ANON_KEY'], 'service_role': self.state['SERVICE_ROLE_KEY']}
        self.client = httpx.Client(base_url=self.url, timeout=35, trust_env=False)

    def admin(self, statement):
        # Container and project identity were verified above. This is the local
        # image administrator, never a connected Supabase project's SQL tool.
        return command(['docker', 'exec', '-i', DB_CONTAINER, 'psql', '-X', '-qAt',
                        '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', 'postgres'], input=statement)

    def rows(self, statement):
        return json.loads(self.admin("select coalesce(jsonb_agg(to_jsonb(x)), '[]') from (" + statement + ') x;'))

    def rows_as(self, role, statement):
        # SET ROLE changes the effective privileges even in an administrative
        # fixture connection. Only fixed test-role names are accepted here.
        assert role in ('paper_rpc_owner', 'anon', 'authenticated', 'service_role', 'authenticator')
        return json.loads(self.admin(f"set role {role}; select coalesce(jsonb_agg(to_jsonb(x)), '[]') from (" + statement + ') x;'))

    def request(self, method, path, role='service_role', **kwargs):
        return self.client.request(method, path, headers={
            'apikey': self.keys['anon'] if role == 'authenticated' else self.keys[role],
            'Authorization': 'Bearer ' + self.keys[role]}, **kwargs)

    def rpc(self, name, **kwargs):
        response = self.request('POST', '/rest/v1/rpc/' + name, json=kwargs)
        assert response.status_code == 200, (response.status_code, response.text[:600])
        return response.json()

    def create_test(self, *, ended=False):
        now = self.rows('select floor(extract(epoch from clock_timestamp())*1000)::bigint as ms')[0]['ms']
        start = now // STEP_MS * STEP_MS - GATE_MS - (2 if not ended else 3) * STEP_MS
        config = test_config(start_ms=start, warmup=0)
        config.update(id=str(uuid4()), variant='B', strategy_version_id='synthetic-local-ci',
                      lease_ms=120000, origin_grace_ms=120000,
                      shadow=True, shadow_until_ms=now - 1 if ended else now + 240000)
        return self.rpc('paper_create_test', p_test=config)


@pytest.fixture(scope='module')
def stack():
    s = Stack()
    try:
        # A real Auth token exercises authenticator -> authenticated via PostgREST.
        response = s.request('POST', '/auth/v1/signup', role='anon', json={
            'email': f'paper-ci-{uuid4().hex}@example.test', 'password': uuid4().hex + 'Aa1!'})
        assert response.status_code == 200, response.text[:300]
        s.keys['authenticated'] = response.json()['access_token']
        s.admin('''
          create function public.paper_ci_identity() returns jsonb
          language sql security invoker set search_path='' as $$
            select jsonb_build_object('session_user',session_user,'current_user',current_user)
          $$;
          revoke all on function public.paper_ci_identity() from public;
          grant execute on function public.paper_ci_identity() to anon,authenticated,service_role;
          notify pgrst, 'reload schema';
        ''')
        eventually(lambda: s.request('POST', '/rest/v1/rpc/paper_ci_identity', json={}),
                   lambda r: r.status_code == 200, timeout=20)
        yield s
    finally:
        s.client.close()


def test_exact_install_real_roles_rls_and_mixed_migration_versions(stack):
    with psycopg.connect(stack.dsn) as c:
        assert c.execute('select session_user,rolsuper from pg_roles where rolname=session_user').fetchone() == ('postgres', False)
        assert c.execute('select version from supabase_migrations.schema_migrations order by version').fetchall() == [
            ('001',), ('029',), ('20261028205717',)]
    assert stack.rows('select step,actor,superuser from paper_ci_support.install_probe order by step') == [
        {'step': 1, 'actor': 'postgres', 'superuser': False},
        {'step': 29, 'actor': 'postgres', 'superuser': False}]
    assert stack.rows("select count(*) as n from pg_class where relnamespace='public'::regnamespace "
                      "and relkind='r' and relowner='paper_rpc_owner'::regrole and relrowsecurity") == [{'n': 12}]
    for role in ('paper_rpc_owner', 'paper_cron_owner'):
        assert stack.rows(f"select pg_has_role('postgres','{role}','SET') as can_set, "
                          f"pg_has_role('postgres','{role}','USAGE') as can_use") == [{'can_set': False, 'can_use': False}]
    roles = stack.rows("select rolname,rolsuper,rolcanlogin,rolbypassrls from pg_roles "
                       "where rolname in ('postgres','authenticator','service_role','anon','authenticated') order by rolname")
    assert len(roles) == 5 and all(not r['rolsuper'] for r in roles)
    assert next(r for r in roles if r['rolname'] == 'service_role')['rolbypassrls']
    print('Actual local Supabase roles:', json.dumps(roles))


@pytest.mark.parametrize('role', ['anon', 'authenticated', 'service_role'])
def test_postgrest_uses_real_authenticator_session(stack, role):
    response = stack.request('POST', '/rest/v1/rpc/paper_ci_identity', role, json={})
    assert response.status_code == 200
    assert response.json() == {'session_user': 'authenticator', 'current_user': role}


@pytest.mark.parametrize('role', ['anon', 'authenticated', 'service_role'])
def test_http_cannot_read_tables_or_manufacture_cron_tick(stack, role):
    for method, path, payload in [('GET', '/rest/v1/paper_tests?select=id', None),
                                  ('POST', '/rest/v1/rpc/paper_tick_start', {})]:
        response = stack.request(method, path, role, **({'json': payload} if payload is not None else {}))
        assert response.status_code in (401, 403)
        assert response.json()['code'] == '42501'
    if role != 'service_role':
        response = stack.request('POST', '/rest/v1/rpc/paper_create_test', role, json={'p_test': {}})
        assert response.status_code in (401, 403) and response.json()['code'] == '42501'


def test_server_rpc_create_and_stop_shadow_over_http(stack):
    created = stack.create_test(ended=True)
    stopped = stack.rpc('paper_stop_shadow', p_test_id=created['id'])
    assert stopped['outcome'] == 'stopped'
    row = stack.rows(f"select status,authoritative_verdict_id from paper_tests where id='{created['id']}'")[0]
    assert row == {'status': 'stopped', 'authoritative_verdict_id': None}


def test_set_role_does_not_forge_session_user(stack):
    with pytest.raises(subprocess.CalledProcessError) as failure:
        stack.admin('set role paper_cron_owner; select public.paper_tick_start();')
    assert 'tick_origin_unverified' in failure.value.stderr


@pytest.fixture(scope='module')
def cron_case(stack):
    # All setup below is a CI-only candidate for P4 configuration. In particular,
    # metadata SELECT grants alone do not bypass pg_cron's per-user RLS policies.
    stack.admin('''
      create extension if not exists pg_cron with schema pg_catalog;
      create extension if not exists pg_net with schema extensions;
      grant usage on schema cron to paper_rpc_owner;
      grant select on cron.job,cron.job_run_details to paper_rpc_owner;
      alter role paper_cron_owner login;
      grant connect on database postgres to paper_cron_owner;
      grant usage on schema paper_ci_support,net to paper_cron_owner;
      grant execute on function net.http_post(text,jsonb,jsonb,jsonb,integer) to paper_cron_owner;
      create table paper_ci_support.observed(tick_id uuid, session_name text, current_name text,
                                            pid integer, request_id bigint);
      grant insert on paper_ci_support.observed to paper_cron_owner;
      create role paper_ci_other_cron_owner login noinherit;
      grant connect on database postgres to paper_ci_other_cron_owner;
    ''')
    versions = stack.rows("select extname,extversion from pg_extension where extname in ('pg_cron','pg_net') order by extname")
    assert len(versions) == 2
    print('Real extensions:', json.dumps(versions))
    print('Cron connection mode:', stack.rows("select current_setting('cron.use_background_workers') as background_workers"))
    server = Path(__file__).with_name('http_sink.py').resolve()
    job_ids = []
    try:
        command(['docker', 'run', '-d', '--rm', '--name', HTTP_CONTAINER, '--network', stack.network,
                 '--mount', f'type=bind,source={server},target=/http_sink.py,readonly',
                 'python:3.12.12-slim', 'python', '/http_sink.py'])
        def server_ready():
            return subprocess.run(['docker', 'exec', HTTP_CONTAINER, 'python', '-c',
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"],
                capture_output=True, timeout=5).returncode
        eventually(server_ready, lambda result: result == 0, timeout=20)
        cron_sql = '''with tick as materialized (select public.paper_tick_start() as id)
          insert into paper_ci_support.observed
          select id,session_user,current_user,pg_backend_pid(),net.http_post(
            url := 'http://paper-p2-ci-http:8080/tick',
            body := jsonb_build_object('tick_id',id)) from tick'''
        def schedule(name, owner, statement='select 1', active=True):
            query = sql.SQL(
                "select cron.schedule_in_database({},'* * * * *',{},'postgres',{},{});").format(
                    sql.Literal(name), sql.Literal(statement), sql.Literal(owner), sql.Literal(active)).as_string()
            if owner == 'paper_rpc_owner':
                assert not active
                # pg_cron's scheduling API requires LOGIN even for an inactive
                # job. Both role changes and creation commit together: LOGIN is
                # never visible outside this administrative fixture transaction.
                query = ('begin; alter role paper_rpc_owner login; ' + query
                         + ' alter role paper_rpc_owner nologin; commit;')
            job = int(stack.admin(query))
            job_ids.append(job)
            return job
        other_job = schedule('paper_ci_other_job', 'paper_cron_owner')
        other_owner = schedule('paper_tick', 'paper_ci_other_cron_owner')
        # pg_cron's default permissive policy exposes a user's own jobs. This
        # inactive record catches that alternate access path without running it.
        reader_job = schedule('paper_ci_reader_job', 'paper_rpc_owner', active=False)
        job_id = schedule('paper_tick', 'paper_cron_owner', cron_sql)
        def cron_evidence():
            rows = stack.rows(f"select status,return_message from cron.job_run_details where jobid={job_id} order by runid desc limit 1")
            assert not rows or rows[0]['status'] != 'failed', rows
            return rows
        eventually(cron_evidence, lambda rows: bool(rows) and rows[0]['status'] == 'succeeded')
        for job in (job_id, other_job, other_owner):
            eventually(lambda: stack.rows(f"select status from cron.job_run_details where jobid={job} order by runid desc limit 1"),
                       lambda rows: bool(rows) and rows[0]['status'] == 'succeeded', timeout=20)
            stack.admin(f'select cron.alter_job({job}, active:=false);')
        observed = stack.rows('select * from paper_ci_support.observed')
        assert len(observed) == 1
        observed = observed[0]
        assert observed['session_name'] == observed['current_name'] == 'paper_cron_owner'
        details = stack.rows(f"select d.job_pid,d.username,d.database,d.status,"
            f"d.start_time<=t.created_at and t.created_at<=d.end_time as contains_tick "
            f"from cron.job_run_details d join paper_measure_ticks t on t.backend_pid=d.job_pid "
            f"where d.jobid={job_id} and t.tick_id='{observed['tick_id']}'")
        assert details == [{'job_pid': observed['pid'], 'username': 'paper_cron_owner',
                            'database': 'postgres', 'status': 'succeeded', 'contains_tick': True}]
        delivery = eventually(lambda: stack.rows(f"select status_code,timed_out,error_msg,content from net._http_response where id={observed['request_id']}"), bool, timeout=20)[0]
        assert delivery['status_code'] == 200 and not delivery['timed_out'] and not delivery['error_msg']
        assert json.loads(delivery['content']) == {'method': 'POST', 'path': '/tick', 'body': {'tick_id': observed['tick_id']}}

        test = stack.create_test()
        now = stack.rows('select floor(extract(epoch from clock_timestamp())*1000)::bigint as ms')[0]['ms']
        result = stack.rpc('paper_claim_run', p_test_id=test['id'], p_tick_id=observed['tick_id'],
                           p_last_candle_open_ms=now // STEP_MS * STEP_MS - STEP_MS)
        assert result['outcome'] == 'claimed', result
        claim = result['claim']
        item = evidence(test, run_id=str(uuid4()), as_of=claim['test_end_ms'], pnls=[])
        stack.rpc('paper_archive_candles', p_source='okx', p_pair=test['pair'], p_timeframe='5m', p_rows=list(item.candles))
        persisted = stack.rpc('paper_persist_run', p_claim_id=claim['id'], p_lease_token=claim['lease_token'],
                              p_run=item.run, p_trades=[], p_positions=[])
        assert persisted['outcome'] == 'run_created' and persisted['run']['status'] == 'valid'
        snapshot = stack.rpc('paper_read_run', p_run_id=item.run['id'])
        args = prepare_assessment(snapshot, evaluator_commit=os.environ['PAPER_P2_COMMIT'])
        pending = stack.rpc('paper_assess_run', **args)
        assert pending == {'outcome': 'origin_pending'}
        assert stack.rows(f"select count(*) as n from paper_run_assessments where run_id='{item.run['id']}'") == [{'n': 0}]
        # pg_cron grants DELETE on run details to PUBLIC. Granting cron USAGE
        # also exposes public scheduler functions. Per-role REVOKE cannot deny
        # rights inherited from PUBLIC. This CI-only candidate removes those
        # public rights and explicitly preserves the local postgres administrator;
        # managed deployment requires a separate impact/permission review.
        inherited_delete = stack.rows("select exists(select from pg_class c, lateral aclexplode(c.relacl) a "
            "where c.oid='cron.job_run_details'::regclass and a.grantee=0 and a.privilege_type='DELETE') as allowed")
        assert inherited_delete == [{'allowed': True}]
        print('Observed pg_cron PUBLIC DELETE on cron.job_run_details; restricting CI reader privileges.')
        stack.admin('''
          revoke delete on cron.job_run_details from public;
          grant delete on cron.job_run_details to postgres;
          do $$ declare f record; begin
            for f in select p.oid::regprocedure signature from pg_proc p
              where p.pronamespace='cron'::regnamespace
                and p.proname in ('schedule','schedule_in_database','alter_job','unschedule')
            loop
              execute format('revoke execute on function %s from public',f.signature);
              execute format('grant execute on function %s to postgres',f.signature);
            end loop;
          end $$;
        ''')
        # Narrow CI-only metadata policies. No synthetic cron rows, owner changes,
        # BYPASSRLS grant, or modification of the application origin function.
        selectors = {
            'job': "jobname='paper_tick' and username='paper_cron_owner' and database=current_database()",
            'job_run_details': """username='paper_cron_owner' and database=current_database()
                and exists(select from cron.job j where j.jobid=job_run_details.jobid
                    and j.jobname='paper_tick' and j.username='paper_cron_owner'
                    and j.database=current_database())""",
        }
        for table, predicate in selectors.items():
            # A restrictive counterpart prevents an existing permissive policy
            # (including pg_cron's own-job policy) from widening this reader.
            stack.admin(f'''
              create policy paper_ci_origin_allow on cron.{table} for select to paper_rpc_owner
                using ({predicate});
              create policy paper_ci_origin_limit on cron.{table} as restrictive for select to paper_rpc_owner
                using ({predicate});
            ''')
        assessed = stack.rpc('paper_assess_run', **args)
        assert assessed['outcome'] == 'assessed'
        assert assessed['assessment']['eligibility'] == 'proven_ineligible'
        assert assessed['assessment']['tick_origin_verified'] is True
        assert assessed['verdict'] is None
        assert stack.rpc('paper_assess_run', **args) == assessed
        print('Certified local cron session, PID/time interval, pg_net HTTP delivery and idempotent assessment.')
        yield {'job_id': job_id, 'other_job': other_job, 'other_owner': other_owner,
               'reader_job': reader_job, 'assessment': assessed}
    finally:
        for job in job_ids:
            stack.admin(f'select cron.unschedule({job});')
        subprocess.run(['docker', 'rm', '-f', HTTP_CONTAINER], capture_output=True, timeout=15)


def test_real_cron_session_net_delivery_and_assessment_over_postgrest(cron_case):
    assert cron_case['assessment']['assessment']['tick_origin_verified'] is True
    assert cron_case['assessment']['assessment']['eligibility'] == 'proven_ineligible'


def test_at47_at49_assess_owner_reads_only_the_intended_job(stack, cron_case):
    expected = [{'jobid': cron_case['job_id']}]
    assert stack.rows_as('paper_rpc_owner', 'select jobid from cron.job order by jobid') == expected
    assert stack.rows_as('paper_rpc_owner', 'select distinct jobid from cron.job_run_details order by jobid') == expected
    # The two negative log samples are actual succeeded cron runs. No catalog
    # row is manufactured. The third negative job is owned by the reader itself.
    for key in ('other_job', 'other_owner'):
        assert stack.rows(f"select count(*) as n from cron.job_run_details where jobid={cron_case[key]} and status='succeeded'")[0]['n'] > 0
    assert stack.rows(f"select username from cron.job where jobid={cron_case['reader_job']}") == [{'username': 'paper_rpc_owner'}]


@pytest.mark.parametrize('role', ['anon', 'authenticated', 'service_role', 'authenticator'])
def test_at48_other_application_roles_cannot_read_cron_evidence(stack, cron_case, role):
    assert stack.rows(f"select pg_has_role('{role}','paper_rpc_owner','SET') as can_set, "
                      f"pg_has_role('{role}','paper_rpc_owner','USAGE') as can_use") == [{'can_set': False, 'can_use': False}]
    for table in ('job', 'job_run_details'):
        try:
            rows = stack.rows_as(role, f'select jobid from cron.{table}')
        except subprocess.CalledProcessError as error:
            assert 'permission denied' in error.stderr
        else:
            assert rows == [], f'{role} unexpectedly read cron evidence'


def test_at50_assess_owner_has_no_cron_table_write_privileges(stack, cron_case):
    for table in ('job', 'job_run_details'):
        for privilege in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
            assert stack.rows(f"select has_table_privilege('paper_rpc_owner','cron.{table}','{privilege}') as allowed") == [{'allowed': False}], (table, privilege)
        with pytest.raises(subprocess.CalledProcessError) as failure:
            stack.admin(f'set role paper_rpc_owner; delete from cron.{table} where false;')
        assert 'permission denied' in failure.value.stderr
    assert stack.rows("select rolsuper,rolbypassrls,rolcanlogin from pg_roles where rolname='paper_rpc_owner'") == [
        {'rolsuper': False, 'rolbypassrls': False, 'rolcanlogin': False}]


def test_at50_reader_cannot_call_cron_management_functions(stack, cron_case):
    functions = stack.rows("select p.oid::regprocedure::text as signature, "
        "has_function_privilege('paper_rpc_owner',p.oid,'EXECUTE') as reader, "
        "has_function_privilege('postgres',p.oid,'EXECUTE') as administrator "
        "from pg_proc p where p.pronamespace='cron'::regnamespace "
        "and p.proname in ('schedule','schedule_in_database','alter_job','unschedule')")
    assert functions and all(not f['reader'] and f['administrator'] for f in functions), functions
    assert stack.rows("select has_table_privilege('postgres','cron.job_run_details','DELETE') as allowed") == [{'allowed': True}]
    with pytest.raises(subprocess.CalledProcessError) as failure:
        stack.admin('set role paper_rpc_owner; select cron.unschedule(-1::bigint);')
    assert 'permission denied' in failure.value.stderr
