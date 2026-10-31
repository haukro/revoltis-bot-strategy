-- A4 rev2 (NAVRH, neschvaleny). Odoslanie ticku z testovacej DB na izolovany Preview.
-- rev2: do fronty pg_net nejde ziadna staticka tajna hodnota ani Vercel bypass.
-- Hlavicka nesie iba token viazany na jeden tick: HMAC-SHA256(CRON_SECRET, 'paper-tick:v1:'||tick_id).
-- Iba pre nofomo-paper-validation. Zamerne NIE je v supabase/migrations, aby sa
-- nikdy nedostal do produkcie cez db push. Spusta postgres (bez superusera).
-- CRON_SECRET je iba vo Vault; z DB odchadza len jednorazovy token.
begin;
do $$ begin
  if current_user <> 'postgres' then raise exception 'a4_requires_postgres'; end if;
  if to_regnamespace('paper_cron_proof') is null then raise exception 'a4_requires_a2'; end if;
  if to_regclass('vault.decrypted_secrets') is null then raise exception 'a4_requires_vault'; end if;
  if to_regprocedure('net.http_post(text,jsonb,jsonb,jsonb,integer)') is null then raise exception 'a4_requires_pg_net'; end if;
  if to_regprocedure('extensions.hmac(bytea,bytea,text)') is null then raise exception 'a4_requires_pgcrypto'; end if;
  if to_regnamespace('paper_dispatch') is not null then raise exception 'a4_already_applied'; end if;
end $$;

create schema paper_dispatch authorization postgres;
revoke all on schema paper_dispatch from public;
grant usage on schema paper_dispatch to paper_cron_owner;

-- Jediny povoleny ciel je pevny v obmedzeni; zmena vyzaduje novu revidovanu verziu.
-- Vlastna domena Preview vetvy: SSO ochrana projektu (all_except_custom_domains) sa na nu
-- nevztahuje, takze netreba celoprojektovy bypass. Autentifikaciu robi iba token.
create table paper_dispatch.target (
  singleton boolean primary key default true check (singleton),
  url text not null check (url = 'https://p4-preview.haukro.com/api/paper/measure/tick'),
  enabled boolean not null default false,
  updated_at timestamptz not null default clock_timestamp());
insert into paper_dispatch.target(url) values
  ('https://p4-preview.haukro.com/api/paper/measure/tick');

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
declare v_pid integer; v_created timestamptz; v_url text; v_secret text; v_token text; v_request bigint;
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
  if coalesce(length(v_secret), 0) < 32 then raise exception 'dispatch_secret_missing'; end if;
  -- Token plati iba pre tento tick; endpoint prijme len existujuci tick nie starsi ako 120 s
  -- a jeho opakovane spracovanie je idempotentne. Secret samotny DB neopusti.
  v_token := 'PaperTick v1.' || p_tick_id::text || '.' || encode(extensions.hmac(
    convert_to('paper-tick:v1:' || p_tick_id::text, 'UTF8'), convert_to(v_secret, 'UTF8'), 'sha256'), 'hex');
  v_secret := null;
  -- Zapis do fronty pg_net je transakcny: pri chybe nizsie sa nic neodosle.
  v_request := net.http_post(
    url := v_url,
    body := jsonb_build_object('tick_id', p_tick_id),
    headers := jsonb_build_object('Content-Type', 'application/json', 'Authorization', v_token),
    timeout_milliseconds := 60000);
  insert into paper_dispatch.sent(tick_id, request_id) values (p_tick_id, v_request);
  return v_request;
end $$;

-- Vyhodnotenie kratkej skusky (A4 rev2). Rozhoduju iba DB dokazy spracovania pre
-- KONKRETNE testy a casove okno skusky, nie aktualny status testov ani stav pg_net.
-- net._http_response a net.http_request_queue su zapisovatelne pre PUBLIC: stav pg_net
-- je iba informativny (pg_net_conflict, requests_in_flight), nikdy nedava PASS.
-- Stav paru (test, tick):
--   run_valid          platny run ulozeny pre tento tick (dokoncene)
--   completed_no_run   outcome no_new_candle/lease_held/evaluation_pending/boundary_race (dokoncene)
--   run_invalid        run ulozeny, ale status invalid
--   test_ended         outcome test_closed/shadow_ended
--   lease_expired, worker_error, inconsistent
--   claimed_unfinished claim bez runu a bez outcome po vyprsani lease (pad po claime)
--   in_progress        claim bez runu, lease este plati (nie je konecne)
--   pending            bez dokazu, tick mladsi ako 120 s (nie je konecne)
--   undelivered        bez dokazu, tick starsi ako 120 s
--   not_dispatched     tick bez zaznamu v paper_dispatch.sent
create function paper_dispatch.trial_ticks(p_test_ids uuid[], p_from timestamptz, p_to timestamptz)
returns table(test_id uuid, tick_id uuid, tick_created_at timestamptz, request_id bigint, state text,
              outcome text, final_at timestamptz, pg_net_status integer, in_flight boolean, pg_net_conflict boolean)
language sql stable security invoker
set search_path = ''
as $$
  with pairs as (
    select x.id as test_id, k.tick_id, k.created_at, s.request_id,
           (select r.status from public.paper_runs r where r.test_id = x.id and r.tick_id = k.tick_id) as run_status,
           (select o.outcome from public.paper_tick_outcomes o where o.test_id = x.id and o.tick_id = k.tick_id) as outcome,
           (select c.lease_expires_at from public.paper_run_claims c where c.test_id = x.id and c.tick_id = k.tick_id) as lease_until
    from (select distinct u.id from unnest(p_test_ids) as u(id)) x
    cross join public.paper_measure_ticks k
    left join paper_dispatch.sent s on s.tick_id = k.tick_id
    where k.created_at >= p_from and k.created_at < p_to
  ), classified as (
    select p.*,
      case
        when p.request_id is null then 'not_dispatched'
        when p.run_status = 'valid' then 'run_valid'
        when p.run_status is not null then 'run_invalid'
        when p.outcome in ('no_new_candle', 'lease_held', 'evaluation_pending', 'boundary_race') then 'completed_no_run'
        when p.outcome in ('test_closed', 'shadow_ended') then 'test_ended'
        when p.outcome = 'lease_expired' then 'lease_expired'
        when p.outcome = 'error' then 'worker_error'
        when p.outcome is not null then 'inconsistent'
        when p.lease_until > clock_timestamp() then 'in_progress'
        when p.lease_until is not null then 'claimed_unfinished'
        when clock_timestamp() - p.created_at < interval '120 seconds' then 'pending'
        else 'undelivered' end as state
    from pairs p
  )
  select c.test_id, c.tick_id, c.created_at, c.request_id, c.state, c.outcome,
         greatest(c.created_at + interval '120 seconds', c.lease_until),
         r.status_code,
         exists(select from net.http_request_queue q where q.id = c.request_id),
         c.state not in ('run_valid', 'completed_no_run') and coalesce(r.status_code between 200 and 299, false)
  from classified c
  left join lateral (select x.status_code from net._http_response x where x.id = c.request_id
                     order by x.created desc limit 1) r on true
  order by c.created_at, c.test_id
$$;
revoke all on function paper_dispatch.trial_ticks(uuid[], timestamptz, timestamptz) from public;

-- PASS iba ak: kazdy test je shadow a bezal cele okno (podla created_at/completed_at, nie
-- aktualneho statusu), okno ma ticky bez medzier > 90 s, kazdy par (test, tick) je dokonceny,
-- kazdy test ma aspon jeden platny run, vysledok je konecny (ticky starsie ako 120 s,
-- ziadny platny lease, ziadna poziadavka vo fronte pg_net) a pg_net nehlasi 2xx bez dokazu.
create function paper_dispatch.trial_verdict(p_test_ids uuid[], p_from timestamptz, p_to timestamptz)
returns table(verdict text, reasons text[], tests integer, ticks integer, pairs_ok integer,
              pairs_total integer, final_at timestamptz)
language plpgsql stable security invoker
set search_path = ''
as $$
declare
  r text[] := '{}'; x record; v_times timestamptz[]; v_prev timestamptz; v_t timestamptz;
  v_tests integer; v_ticks integer := 0; v_ok integer := 0; v_total integer := 0; v_final timestamptz;
begin
  v_tests := coalesce((select count(distinct u.id) from unnest(p_test_ids) u(id)), 0)::integer;
  if v_tests = 0 then r := r || 'no_tests'::text; end if;
  if p_from is null or p_to is null or p_to <= p_from then
    r := r || 'invalid_window'::text;
    return query select 'FAIL'::text, r, v_tests, 0, 0, 0, null::timestamptz;
    return;
  end if;
  for x in select u.id, t.id is not null as found, t.shadow, t.created_at, t.completed_at, t.shadow_until_ms
           from (select distinct u.id from unnest(p_test_ids) u(id)) u left join public.paper_tests t on t.id = u.id loop
    if not x.found then r := r || 'unknown_test'::text;
    elsif not x.shadow then r := r || 'not_shadow'::text;
    elsif x.created_at > p_from or (x.completed_at is not null and x.completed_at < p_to)
          or x.shadow_until_ms < floor(extract(epoch from p_to) * 1000) then
      r := r || 'test_not_running_whole_window'::text;
    end if;
  end loop;
  select array_agg(k.created_at order by k.created_at) into v_times
    from public.paper_measure_ticks k where k.created_at >= p_from and k.created_at < p_to;
  v_ticks := coalesce(cardinality(v_times), 0);
  if v_ticks = 0 then
    r := r || 'no_ticks'::text;
  else
    v_prev := p_from;
    foreach v_t in array v_times loop
      if v_t - v_prev > interval '90 seconds' then r := r || 'missing_ticks'::text; end if;
      v_prev := v_t;
    end loop;
    if p_to - v_prev > interval '90 seconds' then r := r || 'missing_ticks'::text; end if;
  end if;
  select count(*) filter (where q.state in ('run_valid', 'completed_no_run')), count(*), max(q.final_at)
    into v_ok, v_total, v_final
    from paper_dispatch.trial_ticks(p_test_ids, p_from, p_to) q;
  r := r || coalesce((select array_agg(distinct q.state) from paper_dispatch.trial_ticks(p_test_ids, p_from, p_to) q
                      where q.state not in ('run_valid', 'completed_no_run')), '{}');
  if exists(select from paper_dispatch.trial_ticks(p_test_ids, p_from, p_to) q where q.pg_net_conflict) then
    r := r || 'pg_net_conflict'::text; end if;
  if exists(select from paper_dispatch.trial_ticks(p_test_ids, p_from, p_to) q where q.in_flight) then
    r := r || 'requests_in_flight'::text; end if;
  if exists(select from (select distinct u.id from unnest(p_test_ids) u(id)) u
            where not exists(select from paper_dispatch.trial_ticks(array[u.id], p_from, p_to) q
                             where q.state = 'run_valid')) then
    r := r || 'no_valid_run'::text; end if;
  v_final := greatest(v_final, p_to + interval '120 seconds');
  if clock_timestamp() < v_final then r := r || 'not_final'::text; end if;
  r := coalesce((select array_agg(distinct e order by e) from unnest(r) e), '{}');
  return query select case when cardinality(r) = 0 then 'PASS' else 'FAIL' end, r,
                      v_tests, v_ticks, v_ok, v_total, v_final;
end $$;
revoke all on function paper_dispatch.trial_verdict(uuid[], timestamptz, timestamptz) from public;


revoke all on function paper_dispatch.send_tick(uuid) from public;
do $$ declare r text; begin
  foreach r in array array['anon','authenticated','service_role','authenticator','paper_rpc_owner'] loop
    if exists(select from pg_roles where rolname = r) then
      execute format('revoke all on function paper_dispatch.send_tick(uuid) from %I', r);
      execute format('revoke all on function paper_dispatch.trial_ticks(uuid[], timestamptz, timestamptz) from %I', r);
      execute format('revoke all on function paper_dispatch.trial_verdict(uuid[], timestamptz, timestamptz) from %I', r);
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
  if exists(select from pg_proc p, aclexplode(p.proacl) a
            where p.oid in ('paper_dispatch.trial_ticks(uuid[],timestamptz,timestamptz)'::regprocedure,
                            'paper_dispatch.trial_verdict(uuid[],timestamptz,timestamptz)'::regprocedure)
              and a.grantee <> 'postgres'::regrole)
     or exists(select from pg_proc p where p.prosecdef
               and p.oid in ('paper_dispatch.trial_ticks(uuid[],timestamptz,timestamptz)'::regprocedure,
                             'paper_dispatch.trial_verdict(uuid[],timestamptz,timestamptz)'::regprocedure)) then
    raise exception 'a4_trial_acl'; end if;
  if has_table_privilege('paper_cron_owner', 'paper_dispatch.target', 'SELECT')
     or has_table_privilege('paper_cron_owner', 'paper_dispatch.sent', 'SELECT')
     or has_table_privilege('paper_cron_owner', 'vault.decrypted_secrets', 'SELECT') then
    raise exception 'a4_cron_owner_direct_read'; end if;
end $$;
commit;
