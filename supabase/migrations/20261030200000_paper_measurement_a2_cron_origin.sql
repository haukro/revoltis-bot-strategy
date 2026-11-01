-- B1-B8 rev3 / A2 (NAVRH, neschvalene). Fallback A1.4 pre overenie povodu ticku.
-- Nova migracia nad P2 a P3. Subory P2/P3 sa nemenia. Bez rozsirenia, cron jobu,
-- HTTP volania, vytvorenia testu ci strategickeho behu.
--
-- Model: paper_rpc_owner necita cron priamo (bez USAGE, bez grantov a politik
-- na cron). Jediny pristup k dokazu je privatna SECURITY DEFINER funkcia
-- vo vlastnictve postgres, ktora vrati iba stavovy token.
begin;

do $$ begin
  if current_user <> 'postgres' then raise exception 'a2_requires_postgres'; end if;
  -- Katalog namiesto to_regprocedure: postgres nema USAGE na paper_private.
  if not exists(select from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'paper_private' and p.proname = 'origin'
                  and pg_get_function_identity_arguments(p.oid) = 'p_tick_id uuid, p_now_ms bigint, p_grace_ms integer') then
    raise exception 'a2_requires_p2'; end if;
  if to_regprocedure('public.paper_worker_page(uuid,uuid)') is null then
    raise exception 'a2_requires_p3'; end if;
  if to_regnamespace('paper_cron_proof') is not null then
    raise exception 'a2_already_applied'; end if;
end $$;

-- Samostatna schema vo vlastnictve postgres: ACL schemy paper_private z P2
-- ostava nezmenene a jej vlastnik (paper_rpc_owner) nemoze pomocnu funkciu
-- zmazat ani nahradit.
create schema paper_cron_proof authorization postgres;
revoke all on schema paper_cron_proof from public;
grant usage on schema paper_cron_proof to paper_rpc_owner;

-- Vstupom je iba tick_id. PID a cas sa citaju z ulozeneho ticku, nikdy od volajuceho.
-- Vystup je jediny token, nikdy riadky ani polia cron logu:
--   no_tick      tick neexistuje alebo nie je citatelny
--   no_evidence  cron tabulky chybaju alebo ziadny zodpovedajuci beh
--   in_progress  najnovsi zodpovedajuci beh nie je konecny (starting/running)
--   succeeded    najnovsi zodpovedajuci beh skoncil uspesne po tick.created_at
--   failed       najnovsi zodpovedajuci beh skoncil chybou
-- Podmienky vyberu su doslovne prevzate z paper_private.origin v P2.
create function paper_cron_proof.tick_run_status(p_tick_id uuid)
returns text
language plpgsql volatile security definer
set search_path = ''
as $$
declare v_created timestamptz; v_pid integer; v_status text; v_end timestamptz;
begin
  select t.created_at, t.backend_pid into v_created, v_pid
    from public.paper_measure_ticks t where t.tick_id = p_tick_id;
  if not found then return 'no_tick'; end if;
  if to_regclass('cron.job') is null or to_regclass('cron.job_run_details') is null then
    return 'no_evidence';
  end if;
  -- Najnovsi zodpovedajuci beh: PID sa moze opakovat, starsi beh s rovnakym
  -- PID nesmie prekryt aktualny (rovnako ako v P2).
  select d.status, d.end_time into v_status, v_end
    from cron.job_run_details d join cron.job j on j.jobid = d.jobid
    where j.jobname = 'paper_tick' and j.username = 'paper_cron_owner'
      and j.database = current_database()
      and d.username = 'paper_cron_owner' and d.database = current_database()
      and d.job_pid = v_pid and d.start_time <= v_created
      and (d.end_time is null or d.end_time >= v_created)
    order by d.start_time desc, d.runid desc
    limit 1;
  if not found then return 'no_evidence'; end if;
  if v_status = 'succeeded' and v_end >= v_created then return 'succeeded'; end if;
  if v_status = 'failed' then return 'failed'; end if;
  return 'in_progress';
end $$;

revoke all on function paper_cron_proof.tick_run_status(uuid) from public;
do $$ declare r text; begin
  foreach r in array array['anon','authenticated','service_role','authenticator','paper_cron_owner'] loop
    if exists(select from pg_roles where rolname = r) then
      execute format('revoke all on function paper_cron_proof.tick_run_status(uuid) from %I', r);
      execute format('revoke all on schema paper_cron_proof from %I', r);
    end if;
  end loop;
end $$;
grant execute on function paper_cron_proof.tick_run_status(uuid) to paper_rpc_owner;

-- Docasne clenstvo (rovnaky vzor ako P3) na zmeny objektov vo vlastnictve
-- paper_rpc_owner. Na konci sa odobera.
grant paper_rpc_owner to current_user;
set local role paper_rpc_owner;

-- Vlastnik pomocnej funkcie musi precitat iba tri stlpce ticku.
grant select (tick_id, backend_pid, created_at) on public.paper_measure_ticks to postgres;

-- Rozhodovanie origin/grace je nezmenene. Zmenil sa iba zdroj dokazu:
-- namiesto priameho dotazu na cron sa vola pomocna funkcia. Chyba pristupu
-- alebo chybajuci objekt vedie, ako doteraz, k pending v lehote a potom
-- k unverifiable. Ziadny chybajuci dokaz nie je overeny povod.
create or replace function paper_private.origin(p_tick_id uuid,p_now_ms bigint,p_grace_ms integer)
returns text language plpgsql volatile set search_path = '' as $$
declare t public.paper_measure_ticks; proof text;
begin
  select * into strict t from public.paper_measure_ticks where tick_id=p_tick_id;
  begin
    proof := paper_cron_proof.tick_run_status(p_tick_id);
  exception when insufficient_privilege or undefined_table or undefined_function
                  or invalid_schema_name then
    proof := null;
  end;
  if proof = 'failed' then return 'unverifiable'; end if;
  if proof = 'succeeded' then return 'verified'; end if;
  if p_now_ms-paper_private.ms(t.created_at) between 0 and p_grace_ms then return 'pending'; end if;
  return 'unverifiable';
end $$;

reset role;
revoke paper_rpc_owner from current_user;

-- Samooverenie. Pri akejkolvek odchylke sa cela migracia vrati.
do $$ declare f oid := 'paper_cron_proof.tick_run_status(uuid)'::regprocedure; o oid;
  r text;
begin
  select p.oid into strict o from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'paper_private' and p.proname = 'origin';
  if (select pg_get_userbyid(proowner) from pg_proc where oid=f) <> 'postgres'
     or not (select prosecdef from pg_proc where oid=f)
     or (select proconfig from pg_proc where oid=f) is distinct from array['search_path=""']
     or (select prorettype from pg_proc where oid=f) <> 'text'::regtype
     or (select proretset from pg_proc where oid=f) then
    raise exception 'a2_helper_definition'; end if;
  if exists(select from aclexplode((select proacl from pg_proc where oid=f)) a
            where a.grantee not in ('postgres'::regrole, 'paper_rpc_owner'::regrole)) then
    raise exception 'a2_helper_acl'; end if;
  if not has_function_privilege('paper_rpc_owner', f, 'EXECUTE') then raise exception 'a2_reader_execute'; end if;
  if (select pg_get_userbyid(proowner) from pg_proc where oid=o) <> 'paper_rpc_owner'
     or (select prosecdef from pg_proc where oid=o)
     or exists(select from aclexplode((select proacl from pg_proc where oid=o)) a
               where a.grantee <> 'paper_rpc_owner'::regrole) then
    raise exception 'a2_origin_definition'; end if;
  foreach r in array array['anon','authenticated','service_role','authenticator','paper_cron_owner'] loop
    if exists(select from pg_roles where rolname = r) and
       (has_function_privilege(r, f, 'EXECUTE') or has_schema_privilege(r, 'paper_cron_proof', 'USAGE')
        or pg_has_role(r, 'paper_rpc_owner', 'USAGE') or pg_has_role(r, 'paper_rpc_owner', 'SET')) then
      raise exception 'a2_application_role_access %', r;
    end if;
  end loop;
  if to_regnamespace('cron') is not null then
    if has_schema_privilege('paper_rpc_owner', 'cron', 'USAGE') then raise exception 'a2_reader_cron_usage'; end if;
    if exists(select from pg_policies where schemaname='cron' and 'paper_rpc_owner' = any(roles)) then
      raise exception 'a2_reader_cron_policy'; end if;
    if exists(select from pg_class c, aclexplode(c.relacl) a
              where c.relnamespace='cron'::regnamespace and a.grantee='paper_rpc_owner'::regrole) then
      raise exception 'a2_reader_cron_grant'; end if;
  end if;
  if exists(select from pg_auth_members m where m.roleid='paper_rpc_owner'::regrole
            and m.member='postgres'::regrole and (m.set_option or m.inherit_option)) then
    raise exception 'a2_temporary_membership_left'; end if;
end $$;

commit;
