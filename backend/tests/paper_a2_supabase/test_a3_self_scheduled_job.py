"""A3 (NAVRH): job paper_tick si naplanuje sam paper_cron_owner.

Na spravovanom Supabase postgres (bez superusera) nesmie volat
cron.schedule_in_database pre inu rolu ("must be superuser to create a job for
another role", zistene 30. 10. 2026). Skuska spusta rovnake tri SQL fazy
(ci/paper-supabase/a3/*.sql) ako rola postgres cez DB_URL, teda rovnako ako na
spravovanom projekte. Lokalny pg_hba sa moze lisit od spravovaneho, preto CI
nedokazuje autentifikaciu cron pripojenia na spravovanej platforme.
"""
import json
import os
from pathlib import Path
import subprocess

import pytest

if not os.environ.get('PAPER_A2_SUPABASE'):
    pytest.skip('Requires the isolated A2 Supabase CI job', allow_module_level=True)

import psycopg
from test_local_stack import Stack, eventually

PHASES = ('1_schedule.sql', '2_verify.sql', '3_stop.sql')


def phase_dir():
    for parent in Path(__file__).resolve().parents:
        candidate = parent / 'ci' / 'paper-supabase' / 'a3'
        if all((candidate / name).is_file() for name in PHASES):
            return candidate
    raise AssertionError('A3 phase files not found')


@pytest.fixture(scope='module')
def stack():
    return Stack()


def as_postgres(stack, text):
    with psycopg.connect(stack.dsn, autocommit=True) as conn:
        assert conn.execute('select current_user').fetchone()[0] == 'postgres'
        cur = conn.execute(text)
        return cur.fetchall() if cur.description else None


def run_phase(stack, name):
    return as_postgres(stack, (phase_dir() / name).read_text())


def test_a3_cross_role_scheduling_is_refused_for_postgres(stack):
    with pytest.raises(psycopg.Error) as failure:
        as_postgres(stack, "select cron.schedule_in_database('paper_a3_probe','* * * * *','select 1',"
                           "'postgres','paper_cron_owner',true)")
    assert 'must be superuser to create a job for another role' in str(failure.value)
    assert stack.rows("select count(*) n from cron.job where jobname='paper_a3_probe'") == [{'n': 0}]


def test_a3_self_scheduled_job_runs_and_is_verified(stack):
    assert stack.rows("select count(*) n from cron.job where jobname='paper_tick'") == [{'n': 0}]
    try:
        run_phase(stack, PHASES[0])
        job = stack.rows("select jobid, username, database, active, command from cron.job where jobname='paper_tick'")
        assert len(job) == 1 and job[0]['username'] == 'paper_cron_owner' and job[0]['active'] is True
        assert job[0]['database'] == 'postgres' and job[0]['command'] == 'select public.paper_tick_start()'
        jobid = job[0]['jobid']
        # Docasne prava zanikli v tej istej transakcii.
        assert stack.rows("select has_schema_privilege('paper_cron_owner','cron','USAGE') u") == [{'u': False}]
        assert stack.rows("select count(*) n from pg_auth_members where roleid='paper_cron_owner'::regrole "
                          "and member='postgres'::regrole and (set_option or inherit_option)") == [{'n': 0}]
        eventually(lambda: stack.rows(f"select count(*) n from cron.job_run_details where jobid={jobid} "
                                      "and status in ('succeeded','failed')"),
                   lambda r: r[0]['n'] > 0, timeout=150)
        last = stack.rows(f"select status, return_message from cron.job_run_details where jobid={jobid} "
                          "and status in ('succeeded','failed') order by runid desc limit 1")[0]
        print('A3 first finished run:', json.dumps(last))
        assert last['status'] == 'succeeded', last
        verify = run_phase(stack, PHASES[1])[0][0]
        verify = json.loads(verify) if isinstance(verify, str) else verify
        print('A3 verify:', json.dumps(verify, default=str))
        assert any(t['proof'] == 'succeeded' for t in verify['ticks']), verify
        tick = next(t['tick_id'] for t in verify['ticks'] if t['proof'] == 'succeeded')
        assert stack.admin(f"set role paper_rpc_owner; select paper_private.origin('{tick}',"
                           "floor(extract(epoch from clock_timestamp())*1000)::bigint,600000);").strip() == 'verified'
        # Pocas skusky nema cron rola pristup k vlastnym logom ani sprave jobov.
        for statement in ('delete from cron.job_run_details where false', 'select cron.unschedule(-1::bigint)'):
            result = subprocess.run(
                ['docker', 'exec', '-i', 'supabase_db_paper-p2-ci', 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                 '-U', 'supabase_admin', '-d', 'postgres'], input=f'set role paper_cron_owner; {statement};',
                text=True, capture_output=True)
            assert result.returncode != 0 and 'permission denied for schema cron' in result.stderr, result.stderr
    finally:
        if stack.rows("select count(*) n from cron.job where jobname='paper_tick' and username='paper_cron_owner'")[0]['n']:
            run_phase(stack, PHASES[2])
    assert stack.rows("select count(*) n from cron.job where jobname='paper_tick'") == [{'n': 0}]
    assert stack.rows("select rolcanlogin, has_schema_privilege('paper_cron_owner','cron','USAGE') u "
                      "from pg_roles where rolname='paper_cron_owner'") == [{'rolcanlogin': False, 'u': False}]
