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
TRIAL_FNS = ('paper_dispatch.trial_ticks(uuid[],timestamptz,timestamptz)',
             'paper_dispatch.trial_verdict(uuid[],timestamptz,timestamptz)')
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
    for fn in TRIAL_FNS:
        r = stack.rows("select prosecdef, proconfig, proacl::text as acl "
                       f"from pg_proc where oid = '{fn}'::regprocedure")[0]
        assert r == {'prosecdef': False, 'proconfig': ['search_path=""'], 'acl': '{postgres=X/postgres}'}, fn
        for role in ('anon', 'authenticated', 'service_role', 'authenticator', 'paper_rpc_owner', 'paper_cron_owner'):
            assert val(stack, f"select has_function_privilege('{role}','{fn}','EXECUTE')") == 'f', (fn, role)
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


# --- Vyhodnotenie kratkej skusky (trial_ticks / trial_verdict) -------------------------
# Stav sa stavia v transakcii s ROLLBACK priamo do skutocnych P2 tabuliek (constrainty,
# triggre claim_identity a paper_test_transition platia). Okno skusky je v roku 2001, aby
# don nezasahovali ticky ostatnych A2 testov.
import json
from uuid import uuid4

W0 = "timestamptz '2001-01-01 00:00:00+00'"
WINDOW = (W0, W0 + " + interval '8 minutes'")


def psql(statement):
    result = subprocess.run(['docker', 'exec', '-i', 'supabase_db_paper-p2-ci', 'psql', '-X', '-qAt', '-v',
                             'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', 'postgres'],
                            input=statement, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr[-1200:]
    return result.stdout.strip()


@pytest.fixture(scope='module')
def base_test(stack):
    test = stack.create_test(ended=True)  # running, shadow_until v minulosti -> da sa zastavit
    yield test
    assert stack.rpc('paper_stop_shadow', p_test_id=test['id'])['outcome'] in ('stopped', 'test_closed')


class Trial:
    """Generuje SQL pre jednu skusku; vsetko sa vykona v jednej transakcii s ROLLBACK."""

    def __init__(self, base):
        self.base, self.sql, self.k = base, [], 0

    def test(self, *, completed_at=None, created_at="timestamptz '2000-01-01 00:00:00+00'"):
        tid = str(uuid4()); self.k += 1
        status = "'running'" if completed_at is None else "'stopped'"
        completed = 'null' if completed_at is None else completed_at
        self.sql.append(
            "insert into public.paper_tests select (jsonb_populate_record(null::public.paper_tests, to_jsonb(b) || "
            f"jsonb_build_object('id','{tid}','start_ms',b.start_ms+{self.k},'created_at',{created_at},"
            f"'status',{status},'completed_at',{completed},"
            "'shadow_until_ms',floor(extract(epoch from clock_timestamp() + interval '1 hour')*1000)::bigint))).* "
            f"from public.paper_tests b where b.id='{self.base['id']}';")
        return tid

    def tick(self, at, *, request_id=None, sent=True):
        tk = str(uuid4())
        self.sql.append("insert into public.paper_measure_ticks(tick_id,source,cron_job_name,backend_pid,created_at) "
                        f"values ('{tk}','pg_cron','paper_tick',1,{at});")
        if sent:
            self.sql.append(f"insert into paper_dispatch.sent(tick_id,request_id) values ('{tk}',{request_id or 900000 + len(self.sql)});")
        return tk

    def minute_ticks(self, count=8, start=W0, skip=()):
        return [self.tick(f"{start} + interval '{5 + 60 * i} seconds'") for i in range(count) if i not in skip]

    def claim(self, test, tick, lease="interval '2 minutes'"):
        cid = str(uuid4())
        self.sql.append(
            "insert into public.paper_run_claims(id,test_id,tick_id,last_candle_open_ms,boundary_ms,test_end_ms,"
            "attempt,claimed_at,lease_expires_at) "
            f"select '{cid}','{test}',k.tick_id,x.b-300000,x.b,x.m,1,k.created_at,{lease} "
            "from public.paper_measure_ticks k, lateral (select paper_private.ms(k.created_at) m, "
            f"(floor(paper_private.ms(k.created_at)/300000.0)*300000)::bigint b) x where k.tick_id='{tick}';")
        return cid

    def run(self, test, tick, status='valid'):
        cid = self.claim(test, tick, "k.created_at + interval '2 minutes'")
        invalid = 'null' if status == 'valid' else "'cost_profile_unavailable'"
        self.sql.append(
            "insert into public.paper_runs(id,test_id,claim_id,tick_id,seq,test_end_ms,as_of_ms,last_candle_open_ms,"
            "boundary_ms,persisted_at,created_txid,candle_open_times_ms,tape_present,positions_present,trade_count,"
            "open_count,status,invalid_reason) "
            f"select gen_random_uuid(),c.test_id,c.id,c.tick_id,1,c.test_end_ms,c.test_end_ms,c.last_candle_open_ms,"
            f"c.boundary_ms,c.claimed_at,txid_current(),'{{}}',true,true,0,0,'{status}',{invalid} "
            f"from public.paper_run_claims c where c.id='{cid}';")
        self.outcome(test, tick, 'run_created')

    def outcome(self, test, tick, outcome):
        self.sql.append("insert into public.paper_tick_outcomes(tick_id,test_id,outcome,lag_ms) "
                        f"values ('{tick}','{test}','{outcome}',0);")

    def complete(self, test, ticks, *, skip=()):
        """Prvy tick vytvori platny run, ostatne no_new_candle (ako realny worker v jednom 5m okne)."""
        for i, tick in enumerate(ticks):
            if i in skip:
                continue
            self.run(test, tick) if i == 0 else self.outcome(test, tick, 'no_new_candle')

    def raw(self, statement):
        self.sql.append(statement)

    def verdict(self, tests, window=WINDOW):
        ids = '{' + ','.join(tests) + '}'
        out = psql("begin; " + "\n".join(self.sql) + " set local role postgres; "
                   f"select row_to_json(v) from paper_dispatch.trial_verdict('{ids}'::uuid[], {window[0]}, {window[1]}) v; "
                   "rollback;")
        return json.loads(out.splitlines()[-1])


def test_at75_complete_window_passes_also_after_the_test_stopped(base_test):
    for completed_at in (None, W0 + " + interval '20 minutes'"):
        t = Trial(base_test)
        test = t.test(completed_at=completed_at)
        t.complete(test, t.minute_ticks())
        v = t.verdict([test])
        assert (v['verdict'], v['reasons'], v['ticks'], v['pairs_ok'], v['pairs_total']) == ('PASS', [], 8, 8, 8), v


@pytest.mark.parametrize('case,expected', [
    ('crash_after_claim', ['claimed_unfinished']),
    ('claim_lease_active', ['in_progress', 'not_final']),
    ('worker_error', ['worker_error']),
    ('invalid_run_only', ['no_valid_run', 'run_invalid']),
    ('forged_200_for_undelivered', ['pg_net_conflict', 'undelivered']),
    ('missing_tick', ['missing_ticks']),
    ('not_dispatched', ['not_dispatched']),
    ('request_in_flight', ['requests_in_flight']),
    ('test_stopped_inside_window', ['test_not_running_whole_window']),
    ('test_created_after_window_start', ['test_not_running_whole_window']),
])
def test_at76_incomplete_or_unproven_trial_fails(base_test, case, expected):
    t = Trial(base_test)
    test = t.test(completed_at=W0 + " + interval '4 minutes'" if case == 'test_stopped_inside_window' else None,
                  **({'created_at': W0 + " + interval '1 minute'"} if case == 'test_created_after_window_start' else {}))
    if case == 'not_dispatched':
        ticks = t.minute_ticks(7) + [t.tick(W0 + " + interval '425 seconds'", sent=False)]
    else:
        ticks = t.minute_ticks(skip=(4,) if case == 'missing_tick' else ())
    special = {'crash_after_claim': 3, 'claim_lease_active': 3, 'worker_error': 2,
               'forged_200_for_undelivered': 5, 'not_dispatched': 7}.get(case)
    t.complete(test, ticks, skip=() if special is None else (special,))
    if case == 'crash_after_claim':
        t.claim(test, ticks[3], "k.created_at + interval '2 minutes'")
    elif case == 'claim_lease_active':
        t.claim(test, ticks[3], "clock_timestamp() + interval '2 minutes'")
    elif case == 'worker_error':
        t.outcome(test, ticks[2], 'error')
    elif case == 'invalid_run_only':
        t = Trial(base_test); test = t.test(); ticks = t.minute_ticks()
        t.run(test, ticks[0], status='invalid')
        for tick in ticks[1:]:
            t.outcome(test, tick, 'no_new_candle')
    elif case == 'forged_200_for_undelivered':
        t.raw("set local role anon; insert into net._http_response(id,status_code,content_type,content,timed_out) "
              "select s.request_id,200,'application/json','{\"outcome\":\"processed\"}',false "
              f"from paper_dispatch.sent s where s.tick_id='{ticks[5]}'; reset role;")
    elif case == 'request_in_flight':
        t.raw("insert into net.http_request_queue(id,method,url,headers,body,timeout_milliseconds) "
              f"select s.request_id,'POST','https://p4-preview.haukro.com/api/paper/measure/tick','{{}}',null,1000 "
              f"from paper_dispatch.sent s where s.tick_id='{ticks[1]}';")
    v = t.verdict([test])
    assert v['verdict'] == 'FAIL' and v['reasons'] == expected, (case, v)


def test_at76_partial_processing_of_several_tests_fails(base_test):
    t = Trial(base_test)
    a, b = t.test(), t.test()
    ticks = t.minute_ticks()
    t.complete(a, ticks)
    t.complete(b, ticks, skip=(6,))  # worker spracoval tick 6 iba pre test a
    v = t.verdict([a, b])
    assert (v['verdict'], v['reasons'], v['pairs_ok'], v['pairs_total']) == ('FAIL', ['undelivered'], 15, 16), v
    assert t.verdict([a])['verdict'] == 'PASS'


def test_at76_zero_ticks_and_bad_inputs_never_pass(base_test):
    t = Trial(base_test)
    test = t.test()
    v = t.verdict([test])
    assert v['verdict'] == 'FAIL' and v['reasons'] == ['no_ticks', 'no_valid_run'] and v['ticks'] == 0, v
    assert t.verdict([])['verdict'] == 'FAIL' and 'no_tests' in t.verdict([])['reasons']
    assert t.verdict([str(uuid4())])['verdict'] == 'FAIL'
    assert 'unknown_test' in t.verdict([str(uuid4())])['reasons']
    assert t.verdict([test], (WINDOW[1], WINDOW[0]))['reasons'] == ['invalid_window']


def test_at76_recent_window_is_not_final(base_test):
    t = Trial(base_test)
    test = t.test()
    ticks = [t.tick(f"clock_timestamp() - interval '{s} seconds'") for s in (150, 90, 30)]
    t.complete(test, ticks)
    v = t.verdict([test], ("clock_timestamp() - interval '170 seconds'", "clock_timestamp() + interval '1 second'"))
    assert v['verdict'] == 'FAIL' and 'not_final' in v['reasons'], v


def test_at76_verify_script_fails_closed(stack, base_test):
    script = a4_file('2_verify_dispatch.sql').read_text()
    def run_script(text):
        return subprocess.run(['docker', 'exec', '-i', 'supabase_db_paper-p2-ci', 'psql', '-X', '-qAt', '-v',
                               'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', 'postgres'],
                              input='set role postgres;\n' + text, text=True, capture_output=True, timeout=60)
    unfilled = run_script(script)
    assert unfilled.returncode != 0 and 'invalid input syntax for type uuid' in unfilled.stderr
    empty = run_script(script.replace('<SHADOW_TEST_ID>', base_test['id'])
                       .replace('<FROM_TIMESTAMPTZ>', '2001-01-01 00:00:00+00')
                       .replace('<TO_TIMESTAMPTZ>', '2001-01-01 00:08:00+00'))
    assert empty.returncode != 0 and 'A4 trial FAIL' in empty.stderr and 'no_ticks' in empty.stderr, empty.stderr[-600:]


def test_at73b_exact_tick_window_boundary_with_frozen_clock(stack, base_test):
    """Presna hranica 119 999 / 120 000 ms bez HTTP latencie: paper_private.now_ms je v ROLLBACK zmrazena."""
    f = int(val(stack, "select paper_private.now_ms()"))
    ticks = {name: str(uuid4()) for name in
             ('page_0', 'page_119999', 'page_120000', 'page_future',
              'outcome_119999', 'outcome_120000', 'claim_119999', 'claim_120000')}
    offset = {'0': 0, '119999': 119999, '120000': 120000, 'future': -1}
    inserts = ''.join(
        "insert into public.paper_measure_ticks(tick_id,source,cron_job_name,backend_pid,created_at) "
        f"values ('{tk}','pg_cron','paper_tick',1,paper_private.ts({f - offset[name.split('_')[1]]})); "
        for name, tk in ticks.items())
    test = base_test['id']
    out = psql(
        "begin; "
        "create or replace function paper_private.now_ms() returns bigint language sql volatile "
        f"set search_path = '' as $$ select {f}::bigint $$; " + inserts +
        "select json_build_object("
        + ','.join(f"'{n}', public.paper_worker_page('{ticks[n]}')->>'outcome'" for n in
                   ('page_0', 'page_119999', 'page_120000', 'page_future')) + ","
        f"'outcome_119999', public.paper_worker_outcome('{test}','{ticks['outcome_119999']}','evaluation_pending')->>'outcome',"
        f"'outcome_120000', public.paper_worker_outcome('{test}','{ticks['outcome_120000']}','evaluation_pending')->>'outcome',"
        f"'claim_119999', public.paper_claim_run('{test}','{ticks['claim_119999']}',{f // 300000 * 300000 - 300000})->>'outcome',"
        f"'claim_120000', public.paper_claim_run('{test}','{ticks['claim_120000']}',{f // 300000 * 300000 - 300000})->>'outcome'); "
        "rollback;")
    r = json.loads(out.splitlines()[-1])
    assert (r['page_0'], r['page_119999'], r['page_120000'], r['page_future']) == \
        ('ready', 'ready', 'invalid_tick', 'invalid_tick'), r
    assert (r['outcome_119999'], r['outcome_120000']) == ('evaluation_pending', 'invalid_tick'), r
    assert r['claim_119999'] != 'invalid_tick' and r['claim_120000'] == 'invalid_tick', r
    assert int(val(stack, "select abs(paper_private.now_ms() - floor(extract(epoch from clock_timestamp())*1000))")) < 5000

