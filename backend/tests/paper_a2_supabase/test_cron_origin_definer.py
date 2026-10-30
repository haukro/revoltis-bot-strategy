"""A2 (NAVRH): overenie povodu ticku cez privatnu SECURITY DEFINER funkciu.

Bezi na cerstvom, nelinkovanom lokalnom Supabase s realnym pg_cron a pg_net:
P2 -> rozsirenia (supabase_admin) -> P3 -> A2, BEZ CI kandidata A1 (ziadny
USAGE/grant/politika na cron pre paper_rpc_owner).

Model spravovaneho projektu: postgres je nosuperuser s BYPASSRLS. Fixture to
nastavi pred prvym testom a na konci obnovi povodny stav lokalneho obrazu.

pg_cron ma unikatny index (jobname, username). Preto existuje jediny job
paper_tick/paper_cron_owner: skutocny job z AT53. Po overeni sa deaktivuje
a pocka sa, kym nema ziadny beh v stave starting/running. Synteticke riadky
negativnej matice (AT54) sa viazu na tento job alebo na joby inych vlastnikov,
pouzivaju PID mimo rozsahu jadra (>= 2 000 000 000) a pevny historicky cas,
takze sa nemozu zmiesat so skutocnymi behmi. Job inej databazy sa overuje
docasnou zmenou databazy skutocneho jobu v transakcii, ktora sa vrati.

Toto nenahradza spravovanu branu AT58 ani 24 h beh P4.
"""
import json
import os
from pathlib import Path
import subprocess

import pytest

if not os.environ.get('PAPER_A2_SUPABASE'):
    pytest.skip('Requires the isolated A2 Supabase CI job', allow_module_level=True)

import psycopg
from psycopg import sql
from test_local_stack import Stack, eventually

HELPER = 'paper_cron_proof.tick_run_status(uuid)'
APP_ROLES = ('anon', 'authenticated', 'service_role', 'authenticator', 'paper_cron_owner')
GRACE = 600000
T0 = "timestamptz '2026-10-30 12:00:00+00'"
SYNTHETIC_PID = 2_000_000_000
A2_FILE = '20261030200000_paper_measurement_a2_cron_origin.sql'


def repo_root():
    for parent in Path(__file__).resolve().parents:
        if (parent / 'supabase' / 'migrations' / A2_FILE).is_file():
            return parent
    raise AssertionError('A2 migration not found above ' + __file__)


@pytest.fixture(scope='module')
def stack():
    return Stack()


@pytest.fixture(scope='module', autouse=True)
def managed_role_model(stack):
    original = stack.rows("select rolbypassrls from pg_roles where rolname='postgres'")[0]['rolbypassrls']
    print('Local image postgres.rolbypassrls before A2 tests:', original)
    stack.admin('alter role postgres bypassrls;')
    try:
        yield original
    finally:
        stack.admin(f"alter role postgres {'bypassrls' if original else 'nobypassrls'};")


def val(stack, statement):
    return stack.admin(statement).strip()


def as_reader(stack, statement):
    return val(stack, f'set role paper_rpc_owner; {statement}')


def status(stack, tick_id):
    return as_reader(stack, f"select paper_cron_proof.tick_run_status('{tick_id}');")


def denied(stack, statement):
    with pytest.raises(subprocess.CalledProcessError) as failure:
        stack.admin(statement)
    return failure.value.stderr


def origin(stack, tick_id, now_expr):
    return as_reader(stack, f"select paper_private.origin('{tick_id}',({now_expr}),{GRACE});")


def created_ms(stack, tick_id):
    return int(val(stack, f"select floor(extract(epoch from created_at)*1000)::bigint "
                          f"from public.paper_measure_ticks where tick_id='{tick_id}'"))


# --- AT52: model platformy a predpoklady pomocnej funkcie -------------------------

def test_at52_platform_model_is_recorded(stack, managed_role_model):
    model = stack.rows("select rolname, rolsuper, rolbypassrls from pg_roles "
                       "where rolname in ('postgres','paper_rpc_owner','service_role') order by rolname")
    print('Role model (after managed setup):', json.dumps(model))
    print('Extensions:', json.dumps(stack.rows(
        "select extname, extversion, pg_get_userbyid(extowner) as owner from pg_extension "
        "where extname in ('pg_cron','pg_net') order by 1")))
    print('Cron ACL:', json.dumps(stack.rows(
        "select c.relname, pg_get_userbyid(c.relowner) as owner, c.relacl::text as acl "
        "from pg_class c where c.relnamespace='cron'::regnamespace and c.relkind='r' order by 1")))
    roles = {r['rolname']: r for r in model}
    assert roles['postgres']['rolsuper'] is False and roles['postgres']['rolbypassrls'] is True
    # Pomocna funkcia potrebuje iba existujuce prava postgres na cron.
    assert val(stack, "select has_schema_privilege('postgres','cron','USAGE')") == 't'
    for table in ('job', 'job_run_details'):
        assert val(stack, f"select has_table_privilege('postgres','cron.{table}','SELECT')") == 't'
    assert val(stack, "select count(*) from pg_indexes where schemaname='cron' and tablename='job' "
                      "and indexdef ilike '%unique%(jobname, username)%'") == '1'


def test_at52_helper_definition_and_acl(stack):
    d = stack.rows("select pg_get_userbyid(proowner) as owner, prosecdef, proconfig, "
                   "prorettype::regtype::text as ret, proretset, proacl::text as acl, lower(prosrc) as src "
                   f"from pg_proc where oid='{HELPER}'::regprocedure")[0]
    assert d['owner'] == 'postgres' and d['prosecdef'] is True
    assert d['proconfig'] == ['search_path=""']
    assert d['ret'] == 'text' and d['proretset'] is False
    assert d['acl'] == '{postgres=X/postgres,paper_rpc_owner=X/postgres}'
    for word in ('execute', 'insert ', 'update ', 'delete ', 'truncate', 'cron.schedule', 'cron.unschedule', 'cron.alter_job'):
        assert word not in d['src'], word
    cols = val(stack, "select string_agg(attname, ',' order by attname) from pg_attribute a, aclexplode(a.attacl) x "
                      "where a.attrelid='public.paper_measure_ticks'::regclass and x.grantee='postgres'::regrole")
    assert cols == 'backend_pid,created_at,tick_id'


# --- AT53: pozitivny pripad so skutocnym cronom -----------------------------------

@pytest.fixture(scope='module')
def real_jobs(stack):
    assert val(stack, "select count(*) from cron.job where jobname in ('paper_tick','paper_tick_other')") == '0'
    stack.admin('alter role paper_cron_owner login; grant connect on database postgres to paper_cron_owner;')
    jobs = {}
    try:
        for name in ('paper_tick', 'paper_tick_other'):
            jobs[name] = int(val(stack, sql.SQL(
                "select cron.schedule_in_database({},'* * * * *','select public.paper_tick_start()',"
                "'postgres','paper_cron_owner',true);").format(sql.Literal(name)).as_string()))
        ids = ','.join(map(str, jobs.values()))
        for job in jobs.values():
            eventually(lambda j=job: stack.rows(f"select count(*) n from cron.job_run_details "
                                                f"where jobid={j} and status='succeeded'"),
                       lambda r: r[0]['n'] > 0, timeout=150)
        for job in jobs.values():
            stack.admin(f'select cron.alter_job({job}, active:=false);')
        eventually(lambda: stack.rows(f"select count(*) n from cron.job_run_details where jobid in ({ids}) "
                                      "and status in ('starting','running')"),
                   lambda r: r[0]['n'] == 0, timeout=90)
        assert val(stack, f"select bool_and(not active) from cron.job where jobid in ({ids})") == 't'
        ticks = stack.rows("select distinct on (j.jobname) t.tick_id, j.jobname from public.paper_measure_ticks t "
                           "join cron.job_run_details d on d.job_pid=t.backend_pid and d.start_time<=t.created_at "
                           "and d.end_time>=t.created_at join cron.job j on j.jobid=d.jobid "
                           f"where d.jobid in ({ids}) and d.status='succeeded' order by j.jobname, t.created_at desc")
        real_runs = int(val(stack, f'select count(*) from cron.job_run_details where jobid in ({ids})'))
        yield {'jobs': jobs, 'ticks': {r['jobname']: r['tick_id'] for r in ticks}, 'real_runs': real_runs}
        # Pocas celeho modulu nevznikol ziadny dalsi skutocny beh.
        assert int(val(stack, f'select count(*) from cron.job_run_details where jobid in ({ids}) '
                              f'and job_pid < {SYNTHETIC_PID}')) == real_runs
    finally:
        for job in jobs.values():
            stack.admin(f'select cron.unschedule({job});')
        stack.admin('alter role paper_cron_owner nologin;')


def test_at53_real_paper_tick_is_verified(stack, real_jobs):
    tick = real_jobs['ticks']['paper_tick']
    assert status(stack, tick) == 'succeeded'
    assert origin(stack, tick, 'floor(extract(epoch from clock_timestamp())*1000)::bigint') == 'verified'


def test_at54a_real_other_job_of_same_owner_is_not_evidence(stack, real_jobs):
    tick = real_jobs['ticks']['paper_tick_other']
    assert status(stack, tick) == 'no_evidence'
    c = created_ms(stack, tick)
    assert origin(stack, tick, str(c + GRACE)) == 'pending'
    assert origin(stack, tick, str(c + GRACE + 1)) == 'unverifiable'


def test_at54b_job_in_other_database_is_not_evidence(stack, real_jobs):
    tick, job = real_jobs['ticks']['paper_tick'], real_jobs['jobs']['paper_tick']
    out = stack.admin(f"begin; update cron.job set database='paper_a2_other_db' where jobid={job}; "
                      f"set local role paper_rpc_owner; select paper_cron_proof.tick_run_status('{tick}'); rollback;").split()
    assert out == ['no_evidence'], out
    assert status(stack, tick) == 'succeeded'


# --- AT54: negativna matica (synteticke riadky) ------------------------------------

# (meno, ocakavany token, [(job, start_s, end_s, status[, user[, db]])]); poradie
# riadkov v jednom pripade urcuje poradie runid (sekvencia).
MATRIX = [
    ('pid_reuse_old_failed_new_succeeded', 'succeeded', [('good', -10, 10, 'failed'), ('good', -1, 1, 'succeeded')]),
    ('pid_reuse_old_succeeded_new_failed', 'failed', [('good', -10, 10, 'succeeded'), ('good', -1, 1, 'failed')]),
    ('pid_reuse_only_outside_interval', 'no_evidence', [('good', -3600, -3599, 'succeeded'), ('good', 60, 61, 'succeeded')]),
    ('other_job_same_owner', 'no_evidence', [('other_name', -1, 1, 'succeeded')]),
    ('same_name_other_owner', 'no_evidence', [('other_owner', -1, 1, 'succeeded', 'paper_a2_other_owner')]),
    ('log_other_database', 'no_evidence', [('good', -1, 1, 'succeeded', 'paper_cron_owner', 'paper_a2_other_db')]),
    ('log_other_user', 'no_evidence', [('good', -1, 1, 'succeeded', 'paper_a2_other_owner')]),
    ('reader_role_job', 'no_evidence', [('reader', -1, 1, 'succeeded', 'paper_rpc_owner')]),
    ('started_after_tick', 'no_evidence', [('good', 1, 2, 'succeeded')]),
    ('ended_before_tick', 'no_evidence', [('good', -2, -1, 'succeeded')]),
    ('running', 'in_progress', [('good', -1, None, 'running')]),
    ('starting', 'in_progress', [('good', -1, None, 'starting')]),
    ('succeeded_without_end_time', 'in_progress', [('good', -1, None, 'succeeded')]),
    ('failed', 'failed', [('good', -1, 1, 'failed')]),
    ('missing_log', 'no_evidence', []),
    ('same_start_higher_runid_wins', 'failed', [('good', -1, 1, 'succeeded'), ('good', -1, 1, 'failed')]),
    ('newer_running_hides_older_succeeded', 'in_progress', [('good', -10, 10, 'succeeded'), ('good', -1, None, 'running')]),
]
EXPECTED_ORIGIN = {'succeeded': ('verified', 'verified'), 'failed': ('unverifiable', 'unverifiable'),
                   'in_progress': ('pending', 'unverifiable'), 'no_evidence': ('pending', 'unverifiable')}


@pytest.fixture(scope='module')
def synthetic(stack, real_jobs):
    stack.admin("do $$ begin if not exists(select from pg_roles where rolname='paper_a2_other_owner') then "
                "create role paper_a2_other_owner nologin; end if; end $$;")
    jobs = {'good': real_jobs['jobs']['paper_tick'], 'other_name': real_jobs['jobs']['paper_tick_other']}
    created = []
    for key, user in (('other_owner', 'paper_a2_other_owner'), ('reader', 'paper_rpc_owner')):
        # Neaktivne joby; (paper_tick, iny vlastnik) nekoliduje s unikatnym indexom.
        jobs[key] = int(val(stack, "insert into cron.job(schedule,command,nodename,nodeport,database,username,active,jobname) "
                                   f"values ('0 0 31 2 *','select 1','localhost',5432,'postgres','{user}',false,'paper_tick') "
                                   'returning jobid'))
        created.append(jobs[key])
    cases = {}
    try:
        for i, (name, token, runs) in enumerate(MATRIX):
            pid = SYNTHETIC_PID + i
            tick = val(stack, "insert into public.paper_measure_ticks(source,cron_job_name,backend_pid,created_at) "
                              f"values ('pg_cron','paper_tick',{pid},{T0}) returning tick_id")
            for run in runs:
                job, start, end, state = run[:4]
                user = run[4] if len(run) > 4 else 'paper_cron_owner'
                db = run[5] if len(run) > 5 else 'postgres'
                end_sql = 'null' if end is None else f"{T0} + interval '{end} s'"
                stack.admin("insert into cron.job_run_details(jobid,job_pid,database,username,command,status,start_time,end_time) "
                            f"values ({jobs[job]},{pid},'{db}','{user}','select 1','{state}',"
                            f"{T0} + interval '{start} s',{end_sql})")
            cases[name] = (tick, token)
        yield cases
    finally:
        stack.admin(f'delete from cron.job_run_details where job_pid >= {SYNTHETIC_PID};')
        if created:
            stack.admin(f"delete from cron.job where jobid in ({','.join(map(str, created))});")


@pytest.mark.parametrize('name', [m[0] for m in MATRIX])
def test_at54_negative_matrix(stack, synthetic, name):
    tick, token = synthetic[name]
    assert status(stack, tick) == token
    c = created_ms(stack, tick)
    got = (origin(stack, tick, str(c + GRACE)), origin(stack, tick, str(c + GRACE + 1)))
    assert got == EXPECTED_ORIGIN[token], (name, got)


def test_at54_unknown_tick(stack):
    unknown = '00000000-0000-0000-0000-000000000000'
    assert status(stack, unknown) == 'no_tick'
    assert 'query returned no rows' in denied(stack, f"set role paper_rpc_owner; select paper_private.origin('{unknown}',0,{GRACE});")


# --- AT55: aplikacne roly a citacia rola -------------------------------------------

@pytest.mark.parametrize('role', APP_ROLES)
def test_at55_application_roles_cannot_call_helper_or_read_cron(stack, role, synthetic):
    tick = synthetic['failed'][0]
    assert val(stack, f"select has_function_privilege('{role}','{HELPER}','EXECUTE')") == 'f'
    assert val(stack, f"select has_schema_privilege('{role}','paper_cron_proof','USAGE')") == 'f'
    assert val(stack, f"select pg_has_role('{role}','paper_rpc_owner','USAGE') or pg_has_role('{role}','paper_rpc_owner','SET')") == 'f'
    assert 'permission denied' in denied(stack, f"set role {role}; select paper_cron_proof.tick_run_status('{tick}');")


def test_at55_helper_is_not_reachable_over_postgrest(stack):
    for role in ('anon', 'service_role'):
        response = stack.request('POST', '/rest/v1/rpc/tick_run_status', role=role,
                                 json={'p_tick_id': '00000000-0000-0000-0000-000000000000'})
        assert response.status_code in (401, 403, 404), (role, response.status_code, response.text[:200])


def test_at55_reader_has_no_direct_cron_access(stack):
    assert val(stack, "select has_schema_privilege('paper_rpc_owner','cron','USAGE')") == 'f'
    assert val(stack, "select count(*) from pg_class c, aclexplode(c.relacl) a where c.relnamespace='cron'::regnamespace "
                      "and a.grantee='paper_rpc_owner'::regrole") == '0'
    assert val(stack, "select count(*) from pg_policies where schemaname='cron' and 'paper_rpc_owner'=any(roles)") == '0'
    for statement in ('select count(*) from cron.job', 'delete from cron.job_run_details where false',
                      'select cron.unschedule(-1::bigint)', "select cron.schedule('x','* * * * *','select 1')"):
        assert 'permission denied for schema cron' in denied(stack, f'set role paper_rpc_owner; {statement};')
    assert 'permission denied' in denied(stack, "set role paper_rpc_owner; create or replace function "
                                                "paper_cron_proof.tick_run_status(p uuid) returns text language sql as $$ select 'succeeded' $$;")
    assert 'must be owner' in denied(stack, 'set role paper_rpc_owner; drop function paper_cron_proof.tick_run_status(uuid);')
    assert stack.rows("select rolcanlogin, rolbypassrls, rolsuper from pg_roles where rolname='paper_rpc_owner'") == [
        {'rolcanlogin': False, 'rolbypassrls': False, 'rolsuper': False}]


# --- AT56: model postgres s BYPASSRLS a bez neho ----------------------------------

def test_at56_bypassrls_model_both_ways(stack, synthetic):
    tick = synthetic['pid_reuse_old_failed_new_succeeded'][0]
    try:
        assert status(stack, tick) == 'succeeded'
        stack.admin('alter role postgres nobypassrls;')
        # RLS na tickoch skryje tick; ani pri viditelnom ticku nepomoze cron politika username=current_user.
        assert status(stack, tick) == 'no_tick'
        c = created_ms(stack, tick)
        assert (origin(stack, tick, str(c + 1000)), origin(stack, tick, str(c + GRACE + 1))) == ('pending', 'unverifiable')
        stack.admin('create policy paper_a2_ci_ticks on public.paper_measure_ticks for select to postgres using (true);')
        try:
            assert status(stack, tick) == 'no_evidence'
            assert origin(stack, tick, str(c + 1000)) == 'pending'
        finally:
            stack.admin('drop policy paper_a2_ci_ticks on public.paper_measure_ticks;')
    finally:
        stack.admin('alter role postgres bypassrls;')
    assert status(stack, tick) == 'succeeded'


# --- AT57: chybajuci alebo nedostupny dokaz je fail-closed -------------------------

@pytest.mark.parametrize('prep', [
    'revoke execute on function paper_cron_proof.tick_run_status(uuid) from paper_rpc_owner',
    'drop function paper_cron_proof.tick_run_status(uuid)',
    'drop schema paper_cron_proof cascade',
    'revoke usage on schema cron from postgres cascade',
    'revoke select (tick_id,backend_pid,created_at) on public.paper_measure_ticks from postgres',
    'alter table cron.job rename to job_a2_hidden',
])
def test_at57_degraded_evidence_never_verifies(stack, synthetic, prep):
    tick = synthetic['pid_reuse_old_failed_new_succeeded'][0]
    c = created_ms(stack, tick)
    out = stack.admin(f"begin; {prep}; set local role paper_rpc_owner; "
                      f"select paper_private.origin('{tick}',{c + 1000},{GRACE}); "
                      f"select paper_private.origin('{tick}',{c + GRACE + 1},{GRACE}); rollback;").split()
    assert out == ['pending', 'unverifiable'], (prep, out)
    assert origin(stack, tick, str(c + 1000)) == 'verified'


def test_at57_reapplying_a2_as_postgres_is_refused(stack):
    migration = repo_root() / 'supabase' / 'migrations' / A2_FILE
    # Rovnaka rola ako supabase db push a ako spravovany projekt: postgres cez DB_URL zasobnika.
    with psycopg.connect(stack.dsn, autocommit=True) as conn:
        assert conn.execute('select current_user').fetchone()[0] == 'postgres'
        with pytest.raises(psycopg.errors.RaiseException) as failure:
            conn.execute(migration.read_text())
        conn.execute('rollback')
    assert 'a2_already_applied' in str(failure.value)
