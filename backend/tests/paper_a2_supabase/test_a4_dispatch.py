"""A4 (NAVRH): odoslanie ticku z testovacej DB cez pg_net s tajnymi hodnotami vo Vault.

Skutocny lokalny Supabase (Vault, pg_net), A4 aplikuje postgres cez DB_URL.
Kazdy pripad bezi v transakcii s ROLLBACK: zapis do fronty pg_net je
transakcny, takze CI nikdy neposle poziadavku von. Skutocne dorucenie na
Preview overi az kratka skuska na testovacom projekte.
"""
import os
from pathlib import Path
import subprocess

import pytest

if not os.environ.get('PAPER_A2_SUPABASE'):
    pytest.skip('Requires the isolated A2 Supabase CI job', allow_module_level=True)

import psycopg
from test_local_stack import Stack

TARGET = 'https://revoltis-bot-strategy-haukro-git-claude-paper-p4-preview-haukro.vercel.app/api/paper/measure/tick'
FN = 'paper_dispatch.send_tick(uuid)'
SECRETS = ("select vault.create_secret(new_secret := repeat('c', 40), new_name := 'paper_p4_cron_secret'); "
           "select vault.create_secret(new_secret := repeat('b', 40), new_name := 'paper_p4_vercel_bypass');")
ENABLE = 'update paper_dispatch.target set enabled = true;'
AS_CRON = 'set local session authorization paper_cron_owner;'


def a4_file(name):
    for parent in Path(__file__).resolve().parents:
        candidate = parent / 'ci' / 'paper-supabase' / 'a4' / name
        if candidate.is_file():
            return candidate
    raise AssertionError('A4 file not found: ' + name)


@pytest.fixture(scope='module')
def stack():
    s = Stack()
    with psycopg.connect(s.dsn, autocommit=True) as conn:
        assert conn.execute('select current_user').fetchone()[0] == 'postgres'
        conn.execute(a4_file('a4_dispatch.sql').read_text())
    return s


def val(stack, statement):
    return stack.admin(statement).strip()


def failure(stack, statement):
    result = subprocess.run(['docker', 'exec', '-i', 'supabase_db_paper-p2-ci', 'psql', '-X', '-qAt', '-v',
                             'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', 'postgres'],
                            input=f'begin; {statement} rollback;', text=True, capture_output=True)
    assert result.returncode != 0, result.stdout
    return result.stderr


def queue_rows(stack):
    return int(val(stack, f"select count(*) from net.http_request_queue where url = '{TARGET}'"))


def test_a4_definition_acl_and_fixed_target(stack):
    d = stack.rows("select pg_get_userbyid(proowner) as owner, prosecdef, proconfig, proacl::text as acl "
                   f"from pg_proc where oid = '{FN}'::regprocedure")[0]
    assert d == {'owner': 'postgres', 'prosecdef': True, 'proconfig': ['search_path=""'],
                 'acl': '{postgres=X/postgres,paper_cron_owner=X/postgres}'}
    assert stack.rows('select url, enabled from paper_dispatch.target') == [{'url': TARGET, 'enabled': False}]
    assert 'target_url_check' in failure(stack, "update paper_dispatch.target set url = 'https://example.vercel.app/x';")
    for role in ('anon', 'authenticated', 'service_role', 'authenticator', 'paper_rpc_owner'):
        assert val(stack, f"select has_function_privilege('{role}','{FN}','EXECUTE') "
                          f"or has_schema_privilege('{role}','paper_dispatch','USAGE')") == 'f', role
        assert 'permission denied' in failure(stack, f"set local role {role}; "
                                                     "select paper_dispatch.send_tick(gen_random_uuid());")
    for table in ('paper_dispatch.target', 'paper_dispatch.sent', 'vault.decrypted_secrets'):
        assert val(stack, f"select has_table_privilege('paper_cron_owner','{table}','SELECT')") == 'f', table


def test_a4_positive_queues_only_fixed_target_with_both_secrets(stack):
    before = queue_rows(stack)
    out = val(stack, f"begin; {ENABLE} {SECRETS} {AS_CRON} "
                     "create temp table a4_req as select paper_dispatch.send_tick(public.paper_tick_start()) as id; "
                     "reset session authorization; "
                     "select q.url = '" + TARGET + "' and q.method = 'POST' "
                     "and q.headers->>'Authorization' = 'Bearer ' || repeat('c', 40) "
                     "and q.headers->>'x-vercel-protection-bypass' = repeat('b', 40) "
                     "from net.http_request_queue q join a4_req r on r.id = q.id; rollback;")
    assert out.splitlines()[-1] == 't', out
    assert queue_rows(stack) == before  # ROLLBACK: nic sa neodoslalo


@pytest.mark.parametrize('name,statement,error', [
    ('postgres_session', f"{ENABLE} {SECRETS} "
     "insert into public.paper_measure_ticks(tick_id,source,cron_job_name,backend_pid,created_at) "
     "values ('33333333-3333-3333-3333-333333333333','pg_cron','paper_tick',pg_backend_pid(),clock_timestamp()); "
     "select paper_dispatch.send_tick('33333333-3333-3333-3333-333333333333');", 'dispatch_origin_unverified'),
    ('foreign_backend_pid', f"{ENABLE} {SECRETS} "
     "insert into public.paper_measure_ticks(tick_id,source,cron_job_name,backend_pid,created_at) "
     "values ('44444444-4444-4444-4444-444444444444','pg_cron','paper_tick',1,clock_timestamp()); "
     f"{AS_CRON} select paper_dispatch.send_tick('44444444-4444-4444-4444-444444444444');", 'dispatch_tick_not_current'),
    ('stale_tick', f"{ENABLE} {SECRETS} "
     "insert into public.paper_measure_ticks(tick_id,source,cron_job_name,backend_pid,created_at) "
     "values ('55555555-5555-5555-5555-555555555555','pg_cron','paper_tick',pg_backend_pid(),clock_timestamp() - interval '11 s'); "
     f"{AS_CRON} select paper_dispatch.send_tick('55555555-5555-5555-5555-555555555555');", 'dispatch_tick_not_current'),
    ('unknown_tick', f"{ENABLE} {SECRETS} {AS_CRON} select paper_dispatch.send_tick(gen_random_uuid());", 'dispatch_unknown_tick'),
    ('replay', f"{ENABLE} {SECRETS} {AS_CRON} create temp table a4_t as select public.paper_tick_start() t; "
     "select paper_dispatch.send_tick(t) from a4_t; select paper_dispatch.send_tick(t) from a4_t;", 'dispatch_already_sent'),
    ('disabled_target', f"{SECRETS} {AS_CRON} select paper_dispatch.send_tick(public.paper_tick_start());", 'dispatch_disabled'),
    ('missing_secrets', f"{ENABLE} {AS_CRON} select paper_dispatch.send_tick(public.paper_tick_start());", 'dispatch_secret_missing'),
    ('short_secret', f"{ENABLE} select vault.create_secret(new_secret := 'short', new_name := 'paper_p4_cron_secret'); "
     "select vault.create_secret(new_secret := repeat('b', 40), new_name := 'paper_p4_vercel_bypass'); "
     f"{AS_CRON} select paper_dispatch.send_tick(public.paper_tick_start());", 'dispatch_secret_missing'),
])
def test_a4_refusals_never_queue_a_request(stack, name, statement, error):
    before = queue_rows(stack)
    assert error in failure(stack, statement), name
    assert queue_rows(stack) == before


def test_a4_reapply_is_refused(stack):
    with psycopg.connect(stack.dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException) as error:
            conn.execute(a4_file('a4_dispatch.sql').read_text())
        conn.execute('rollback')
    assert 'a4_already_applied' in str(error.value)
