"""Install the exact migration without superuser, on a fresh CI cluster only.

Run before the transaction suite. No Supabase endpoint or credential is used.
The privileged fixture creates a disposable database and installer; the entire
migration runs with both session_user and current_user set to that installer.
"""
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import os

import pytest

DSN = os.environ.get('PAPER_P2_INSTALL_TEST_DSN')
if not DSN:
    pytest.skip('Requires a fresh isolated CI PostgreSQL cluster', allow_module_level=True)

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

MIGRATION = Path(__file__).parents[3] / 'supabase/migrations/20261028205717_paper_measurement_p2.sql'
OWNERS = ('paper_rpc_owner', 'paper_cron_owner')


def test_exact_migration_installs_without_superuser_and_preserves_legacy_acl():
    parts = conninfo_to_dict(DSN)
    if (parts.get('host') not in ('localhost', '127.0.0.1', '::1')
            or parts.get('hostaddr') not in (None, '127.0.0.1', '::1')
            or parts.get('service') is not None
            or not parts.get('dbname', '').startswith('paper_p2_test')):
        pytest.fail('Refusing non-local or non-test database')
    name = 'paper_p2_test_install_' + uuid4().hex
    installer = 'paper_test_installer_' + uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as admin:
        # Never borrow or delete owner roles belonging to another test database.
        assert not admin.execute('select rolname from pg_roles where rolname=any(%s)', (list(OWNERS),)).fetchall()
        for role in ('anon', 'authenticated', 'service_role'):
            if not admin.execute('select 1 from pg_roles where rolname=%s', (role,)).fetchone():
                admin.execute(sql.SQL('create role {} nologin {}').format(
                    sql.Identifier(role), sql.SQL('bypassrls' if role == 'service_role' else 'nobypassrls')))
        admin.execute(sql.SQL('create role {} nologin nosuperuser inherit createrole createdb bypassrls').format(
            sql.Identifier(installer)))
        admin.execute(sql.SQL('create database {} owner {}').format(sql.Identifier(name), sql.Identifier(installer)))
    try:
        test_dsn = make_conninfo(DSN, dbname=name)
        with psycopg.connect(test_dsn, autocommit=True) as connection:
            connection.execute(sql.SQL('set session authorization {}').format(sql.Identifier(installer)))
            assert connection.execute('select current_user,session_user,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls '
                                      'from pg_roles where rolname=current_user').fetchone() == (
                                          installer, installer, False, True, True, True)
            connection.execute('alter default privileges in schema public grant all on tables to anon,authenticated,service_role; '
                               'alter default privileges in schema public grant execute on functions to anon,authenticated,service_role; '
                               'create table public.legacy_control(id integer primary key); '
                               'alter table public.legacy_control enable row level security')
            legacy_before = connection.execute("select relowner,relacl,relrowsecurity from pg_class "
                                               "where oid='public.legacy_control'::regclass").fetchone()
            defaults_before = connection.execute('select defaclobjtype,defaclacl from pg_default_acl '
                                                  'where defaclrole=current_user::regrole order by defaclobjtype').fetchall()
            # No substituted role names, grants, transaction wrapper or clock hook.
            connection.execute(MIGRATION.read_text())
            assert connection.execute('select current_user,session_user').fetchone() == (installer, installer)
            assert connection.execute("select relowner,relacl,relrowsecurity from pg_class "
                                      "where oid='public.legacy_control'::regclass").fetchone() == legacy_before
            assert connection.execute('select defaclobjtype,defaclacl from pg_default_acl '
                                      'where defaclrole=current_user::regrole order by defaclobjtype').fetchall() == defaults_before
            for owner in OWNERS:
                assert connection.execute("select pg_has_role(current_user,%s,'USAGE'),pg_has_role(current_user,%s,'SET')",
                                          (owner, owner)).fetchone() == (False, False)
            assert connection.execute("select count(*) from pg_class where relnamespace='public'::regnamespace "
                                      "and relkind='r' and relowner='paper_rpc_owner'::regrole and relrowsecurity").fetchone() == (12,)
            assert connection.execute("select pg_get_userbyid(proowner) from pg_proc "
                                      "where oid='public.paper_tick_start()'::regprocedure").fetchone() == ('paper_cron_owner',)
            assert connection.execute("select has_function_privilege('service_role','public.paper_tick_start()','EXECUTE')").fetchone() == (False,)

        with psycopg.connect(test_dsn, autocommit=True) as worker:
            worker.execute('set role service_role')
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                worker.execute('select * from public.paper_tests')
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                worker.execute('select public.paper_tick_start()')
            config = {'label': 'official', 'pair': 'SYNTHETIC/USDT', 'timeframe': '5m',
                      'variant': 'B', 'strategy_version_id': 'ci-installer-only',
                      'settings': {'warmup_bars': 0}, 'settings_sha256': sha256(b'{"warmup_bars":0}').hexdigest(),
                      'start_ms': 0, 'gate_ms': 2592000000, 'gate_trades': 30, 'max_book_age_ms': 60000,
                      'lease_ms': 1000, 'origin_grace_ms': 1000, 'shadow': True, 'shadow_until_ms': 1}
            created = worker.execute('select public.paper_create_test(%s)', (Jsonb(config),)).fetchone()[0]
            assert created['status'] == 'running'
            stopped = worker.execute('select public.paper_stop_shadow(%s)', (created['id'],)).fetchone()[0]
            assert stopped['outcome'] == 'stopped'
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(sql.SQL('drop database {} with (force)').format(sql.Identifier(name)))
            for owner in OWNERS:
                admin.execute(sql.SQL('drop role if exists {}').format(sql.Identifier(owner)))
            admin.execute(sql.SQL('drop role {}').format(sql.Identifier(installer)))
