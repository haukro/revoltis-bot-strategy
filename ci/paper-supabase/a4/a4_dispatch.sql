-- A4 (NAVRH, neschvaleny). Odoslanie ticku z testovacej DB na izolovany Preview.
-- Iba pre nofomo-paper-validation. Zamerne NIE je v supabase/migrations, aby sa
-- nikdy nedostal do produkcie cez db push. Spusta postgres (bez superusera).
-- Tajne hodnoty nie su v tomto subore ani v prikaze cron jobu: su vo Vault.
begin;
do $$ begin
  if current_user <> 'postgres' then raise exception 'a4_requires_postgres'; end if;
  if to_regnamespace('paper_cron_proof') is null then raise exception 'a4_requires_a2'; end if;
  if to_regclass('vault.decrypted_secrets') is null then raise exception 'a4_requires_vault'; end if;
  if to_regprocedure('net.http_post(text,jsonb,jsonb,jsonb,integer)') is null then raise exception 'a4_requires_pg_net'; end if;
  if to_regnamespace('paper_dispatch') is not null then raise exception 'a4_already_applied'; end if;
end $$;

create schema paper_dispatch authorization postgres;
revoke all on schema paper_dispatch from public;
grant usage on schema paper_dispatch to paper_cron_owner;

-- Jediny povoleny ciel je pevny v obmedzeni; zmena vyzaduje novu revidovanu verziu.
create table paper_dispatch.target (
  singleton boolean primary key default true check (singleton),
  url text not null check (url = 'https://revoltis-bot-strategy-haukro-git-claude-paper-p4-preview-haukro.vercel.app/api/paper/measure/tick'),
  enabled boolean not null default false,
  updated_at timestamptz not null default clock_timestamp());
insert into paper_dispatch.target(url) values
  ('https://revoltis-bot-strategy-haukro-git-claude-paper-p4-preview-haukro.vercel.app/api/paper/measure/tick');

-- Kazdy tick sa odosle najviac raz.
create table paper_dispatch.sent (
  tick_id uuid primary key,
  request_id bigint not null,
  sent_at timestamptz not null default clock_timestamp());
revoke all on paper_dispatch.target, paper_dispatch.sent from public;

-- Vola ju iba prikaz cron jobu: select paper_dispatch.send_tick(public.paper_tick_start())
-- Tick musi vzniknut v tej istej session (rovnaky backend PID) pred chvilou.
create function paper_dispatch.send_tick(p_tick_id uuid)
returns bigint
language plpgsql volatile security definer
set search_path = ''
as $$
declare v_pid integer; v_created timestamptz; v_url text; v_secret text; v_bypass text; v_request bigint;
begin
  if session_user <> 'paper_cron_owner' then raise exception 'dispatch_origin_unverified'; end if;
  select t.backend_pid, t.created_at into v_pid, v_created
    from public.paper_measure_ticks t where t.tick_id = p_tick_id;
  if not found then raise exception 'dispatch_unknown_tick'; end if;
  if v_pid <> pg_backend_pid() or v_created > clock_timestamp()
     or clock_timestamp() - v_created > interval '10 seconds' then
    raise exception 'dispatch_tick_not_current'; end if;
  if exists(select from paper_dispatch.sent s where s.tick_id = p_tick_id) then
    raise exception 'dispatch_already_sent'; end if;
  select g.url into v_url from paper_dispatch.target g where g.enabled;
  if v_url is null then raise exception 'dispatch_disabled'; end if;
  select s.decrypted_secret into v_secret from vault.decrypted_secrets s where s.name = 'paper_p4_cron_secret';
  select s.decrypted_secret into v_bypass from vault.decrypted_secrets s where s.name = 'paper_p4_vercel_bypass';
  if coalesce(length(v_secret), 0) < 32 or coalesce(length(v_bypass), 0) < 32 then
    raise exception 'dispatch_secret_missing'; end if;
  -- Zapis do fronty pg_net je transakcny: pri chybe nizsie sa nic neodosle.
  v_request := net.http_post(
    url := v_url,
    body := jsonb_build_object('tick_id', p_tick_id),
    headers := jsonb_build_object('Content-Type', 'application/json',
                                  'Authorization', 'Bearer ' || v_secret,
                                  'x-vercel-protection-bypass', v_bypass),
    timeout_milliseconds := 60000);
  insert into paper_dispatch.sent(tick_id, request_id) values (p_tick_id, v_request);
  return v_request;
end $$;

revoke all on function paper_dispatch.send_tick(uuid) from public;
do $$ declare r text; begin
  foreach r in array array['anon','authenticated','service_role','authenticator','paper_rpc_owner'] loop
    if exists(select from pg_roles where rolname = r) then
      execute format('revoke all on function paper_dispatch.send_tick(uuid) from %I', r);
      execute format('revoke all on schema paper_dispatch from %I', r);
    end if;
  end loop;
end $$;
grant execute on function paper_dispatch.send_tick(uuid) to paper_cron_owner;

do $$ declare f oid := 'paper_dispatch.send_tick(uuid)'::regprocedure; r text; begin
  if (select pg_get_userbyid(proowner) from pg_proc where oid = f) <> 'postgres'
     or not (select prosecdef from pg_proc where oid = f)
     or (select proconfig from pg_proc where oid = f) is distinct from array['search_path=""'] then
    raise exception 'a4_definition'; end if;
  if exists(select from aclexplode((select proacl from pg_proc where oid = f)) a
            where a.grantee not in ('postgres'::regrole, 'paper_cron_owner'::regrole)) then
    raise exception 'a4_acl'; end if;
  foreach r in array array['anon','authenticated','service_role','authenticator','paper_rpc_owner'] loop
    if exists(select from pg_roles where rolname = r) and
       (has_function_privilege(r, f, 'EXECUTE') or has_schema_privilege(r, 'paper_dispatch', 'USAGE')) then
      raise exception 'a4_application_role_access %', r; end if;
  end loop;
  if has_table_privilege('paper_cron_owner', 'paper_dispatch.target', 'SELECT')
     or has_table_privilege('paper_cron_owner', 'paper_dispatch.sent', 'SELECT')
     or has_table_privilege('paper_cron_owner', 'vault.decrypted_secrets', 'SELECT') then
    raise exception 'a4_cron_owner_direct_read'; end if;
end $$;
commit;
