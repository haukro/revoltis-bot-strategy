"""Prepare a fresh, unlinked CI project; never copy a repository's CLI state."""
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[2]
target = Path(sys.argv[1]).resolve() / 'supabase'
assert target.is_dir() and not (target / '.temp/project-ref').exists()
assert not list(target.glob('migrations/*'))
target.joinpath('config.toml').write_text('''project_id = "paper-p2-ci"
[api]
enabled = true
port = 54321
schemas = ["public"]
extra_search_path = ["public", "extensions"]
[db]
port = 54322
shadow_port = 54320
major_version = 17
[db.seed]
enabled = false
[studio]
enabled = false
[realtime]
enabled = false
[storage]
enabled = false
[local_smtp]
enabled = false
[auth]
enabled = true
site_url = "http://127.0.0.1:3000"
[auth.email]
enable_signup = true
enable_confirmations = false
[edge_runtime]
enabled = false
[analytics]
enabled = false
''')
migrations = target / 'migrations'
migrations.mkdir(exist_ok=True)
# These are naming/order probes, NOT copies or replacements of legacy 001–029.
migrations.joinpath('001_ci_order_probe.sql').write_text('''
create schema paper_ci_support;
revoke all on schema paper_ci_support from public;
create table paper_ci_support.install_probe(step integer primary key, actor text, superuser boolean);
insert into paper_ci_support.install_probe
select 1, session_user, rolsuper from pg_roles where rolname=session_user;
''')
migrations.joinpath('029_ci_order_probe.sql').write_text('''
do $$ begin
  if not exists(select from paper_ci_support.install_probe where step=1) then
    raise exception 'migration_order_wrong';
  end if;
  if to_regclass('public.paper_tests') is not null then
    raise exception 'p2_applied_before_numbered_probe';
  end if;
end $$;
insert into paper_ci_support.install_probe
select 29, session_user, rolsuper from pg_roles where rolname=session_user;
''')
source = root / 'supabase/migrations/20261028205717_paper_measurement_p2.sql'
shutil.copyfile(source, migrations / source.name)
assert source.read_bytes() == (migrations / source.name).read_bytes()
print('Prepared unlinked CI project: two synthetic ordering probes and exact P2 migration.')
