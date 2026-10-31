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

TARGET = 'https://p4-preview.haukro.com/api/paper/measure/tick'
FN = 'paper_dispatch.send_tick(uuid)'
REPORT = 'paper_dispatch.delivery_report()'
# Pevny vektor: HMAC-SHA256(key='k'*40, 'paper-tick:v1:'||KAT_TICK), rovnaky ako v backend unit teste.
KAT_TICK = '00000000-0000-0000-0000-000000000001'
KAT_TOKEN = 'PaperTick v1.' + KAT_TICK + '.8ec3a7b53733daee250202a8cac55df6792ac9c468954e2e39ed7bcd6343f324'
SECRETS = "select vault.create_secret(new_secret := repeat('c', 40), new_name := 'paper_p4_cron_secret');"
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
    r = stack.rows("select prosecdef, proconfig, proacl::text as acl "
                   f"from pg_proc where oid = '{REPORT}'::regprocedure")[0]
    assert r == {'prosecdef': False, 'proconfig': ['search_path=""'], 'acl': '{postgres=X/postgres}'}
    for role in ('anon', 'authenticated', 'service_role', 'authenticator', 'paper_rpc_owner', 'paper_cron_owner'):
        assert val(stack, f"select has_function_privilege('{role}','{REPORT}','EXECUTE')") == 'f', role
    for table in ('paper_dispatch.target', 'paper_dispatch.sent', 'vault.decrypted_secrets'):
        assert val(stack, f"select has_table_privilege('paper_cron_owner','{table}','SELECT')") == 'f', table


def test_a4_positive_queues_only_fixed_target_with_tick_token(stack):
    from app.paper_routes import tick_token
    before = queue_rows(stack)
    # net.http_request_queue.body je bytea (pg_net 0.20), preto convert_from(...)::jsonb.
    out = val(stack, f"begin; {ENABLE} {SECRETS} {AS_CRON} "
                     "create temp table a4_req as select paper_dispatch.send_tick(public.paper_tick_start()) as id; "
                     "reset session authorization; "
                     "select q.url = '" + TARGET + "' and q.method = 'POST' "
                     "and not (q.headers ? 'x-vercel-protection-bypass') "
                     "and strpos(q.headers::text, repeat('c', 40)) = 0, "
                     "q.headers->>'Authorization', convert_from(q.body, 'UTF8')::jsonb->>'tick_id' "
                     "from net.http_request_queue q join a4_req r on r.id = q.id; rollback;")
    checks, header, tick = out.splitlines()[-1].split('|')
    assert checks == 't', out
    # Rovnaky token ako vypocita endpoint (Python hmac == pgcrypto hmac); secret nie je v hlavickach.
    assert header == tick_token('c' * 40, tick), header[:24]
    assert queue_rows(stack) == before  # ROLLBACK: nic sa neodoslalo


def test_a4_known_answer_vector_through_send_tick(stack):
    """Pevny vektor cez skutocnu send_tick (pgcrypto), nie iba Python == Python."""
    from app.paper_routes import tick_token
    assert tick_token('k' * 40, KAT_TICK) == KAT_TOKEN
    out = val(stack, f"begin; {ENABLE} "
                     "select vault.create_secret(new_secret := repeat('k', 40), new_name := 'paper_p4_cron_secret'); "
                     "insert into public.paper_measure_ticks(tick_id,source,cron_job_name,backend_pid,created_at) "
                     f"values ('{KAT_TICK}','pg_cron','paper_tick',pg_backend_pid(),clock_timestamp()); "
                     f"{AS_CRON} create temp table a4_kat as select paper_dispatch.send_tick('{KAT_TICK}') as id; "
                     "reset session authorization; "
                     "select q.headers->>'Authorization' from net.http_request_queue q join a4_kat k on k.id = q.id; "
                     "rollback;")
    assert out.splitlines()[-1] == KAT_TOKEN


def test_a4_tick_just_inside_ten_second_window_is_sent(stack):
    before = queue_rows(stack)
    out = val(stack, f"begin; {ENABLE} {SECRETS} "
                     "insert into public.paper_measure_ticks(tick_id,source,cron_job_name,backend_pid,created_at) "
                     "values ('66666666-6666-6666-6666-666666666666','pg_cron','paper_tick',pg_backend_pid(),"
                     "clock_timestamp() - interval '9 s'); "
                     f"{AS_CRON} select paper_dispatch.send_tick('66666666-6666-6666-6666-666666666666') > 0; rollback;")
    assert out.splitlines()[-1] == 't', out
    assert queue_rows(stack) == before


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
    ('future_tick', f"{ENABLE} {SECRETS} "
     "insert into public.paper_measure_ticks(tick_id,source,cron_job_name,backend_pid,created_at) "
     "values ('77777777-7777-7777-7777-777777777777','pg_cron','paper_tick',pg_backend_pid(),clock_timestamp() + interval '1 s'); "
     f"{AS_CRON} select paper_dispatch.send_tick('77777777-7777-7777-7777-777777777777');", 'dispatch_tick_not_current'),
    ('unknown_tick', f"{ENABLE} {SECRETS} {AS_CRON} select paper_dispatch.send_tick(gen_random_uuid());", 'dispatch_unknown_tick'),
    ('replay', f"{ENABLE} {SECRETS} {AS_CRON} create temp table a4_t as select public.paper_tick_start() t; "
     "select paper_dispatch.send_tick(t) from a4_t; select paper_dispatch.send_tick(t) from a4_t;", 'dispatch_already_sent'),
    ('disabled_target', f"{SECRETS} {AS_CRON} select paper_dispatch.send_tick(public.paper_tick_start());", 'dispatch_disabled'),
    ('missing_secrets', f"{ENABLE} {AS_CRON} select paper_dispatch.send_tick(public.paper_tick_start());", 'dispatch_secret_missing'),
    ('short_secret', f"{ENABLE} select vault.create_secret(new_secret := 'short', new_name := 'paper_p4_cron_secret'); "
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


def report_case(stack, prelude):
    """Stav postavi v transakcii s ROLLBACK a vrati delivery_report() ako postgres."""
    out = val(stack, "begin; " + prelude + " set local role postgres; "
                     "select coalesce(jsonb_agg(to_jsonb(x) - 'sent_at' - 'request_id' - 'tick_age_ms'), '[]') "
                     f"from {REPORT} x; rollback;")
    import json
    return {row['tick_id']: row for row in json.loads(out.splitlines()[-1])}


def sent_tick(tick, age_s, request_id):
    return ("insert into public.paper_measure_ticks(tick_id,source,cron_job_name,backend_pid,created_at) "
            f"values ('{tick}','pg_cron','paper_tick',1,clock_timestamp() - interval '{age_s} s'); "
            f"insert into paper_dispatch.sent(tick_id,request_id) values ('{tick}',{request_id}); ")


def forged_response(role, request_id, status):
    # net._http_response je zapisovatelna pre PUBLIC: sfalsovana odpoved z aplikacnej roly.
    return (f"set local role {role}; insert into net._http_response(id,status_code,content_type,content,timed_out) "
            f"values ({request_id},{status},'application/json','{{\"outcome\":\"processed\"}}',false); reset role; ")


def test_a4_delivery_report_uses_only_database_evidence(stack):
    """AT74: ulozeny a odoslany tick bez DB dokazu je nedoruceny aj pri sfalsovanej 2xx odpovedi."""
    test = stack.create_test(ended=True)  # running, kym ho nezastavi paper_stop_shadow
    try:
        delivered, forged, young, refuted = (f'{n}{n}{n}{n}{n}{n}{n}{n}-0000-4000-8000-00000000000{n}' for n in (1, 2, 3, 4))
        state = (sent_tick(delivered, 200, 910001) + sent_tick(forged, 200, 910002)
                 + sent_tick(young, 30, 910003) + sent_tick(refuted, 200, 910004)
                 + "insert into public.paper_tick_outcomes(tick_id,test_id,outcome,lag_ms) values "
                 f"('{delivered}','{test['id']}','no_new_candle',0),('{refuted}','{test['id']}','no_new_candle',0); "
                 + forged_response('authenticated', 910002, 200) + forged_response('anon', 910003, 200)
                 + forged_response('anon', 910004, 401))
        report = report_case(stack, state)
        assert report[delivered] == {'tick_id': delivered, 'db_evidence': 1, 'pg_net_status': None,
                                     'delivery': 'delivered', 'pg_net_conflict': False}
        assert report[forged] == {'tick_id': forged, 'db_evidence': 0, 'pg_net_status': 200,
                                  'delivery': 'undelivered', 'pg_net_conflict': True}
        assert report[young]['delivery'] == 'pending' and report[young]['pg_net_conflict'] is True
        assert report[refuted]['delivery'] == 'delivered' and report[refuted]['pg_net_status'] == 401
        # Bez bezaceho testu sa nedorucenie neda odlisit od prazdneho spracovania.
        stopped = report_case(stack, state + "update public.paper_tests set status = 'stopped' where status = 'running'; ")
        assert stopped[forged]['delivery'] == 'unverifiable' and stopped[forged]['pg_net_conflict'] is True
        assert stopped[delivered]['delivery'] == 'delivered'
    finally:
        assert stack.rpc('paper_stop_shadow', p_test_id=test['id'])['outcome'] in ('stopped', 'test_closed')
    assert val(stack, "select count(*) from paper_dispatch.sent") == '0'
