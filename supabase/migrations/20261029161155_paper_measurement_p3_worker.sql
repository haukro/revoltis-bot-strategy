-- P3 worker access. Prepared migration; applied only to disposable CI.
-- No scheduler, extensions, test creation, or network calls.
begin;
grant paper_rpc_owner to current_user;
grant create on schema public to paper_rpc_owner;

create function public.paper_worker_page(p_tick_id uuid,p_after uuid default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare stamp bigint := paper_private.now_ms(); data jsonb;
begin
  if not exists(select from public.paper_measure_ticks where tick_id=p_tick_id
    and source='pg_cron' and stamp-paper_private.ms(created_at) between 0 and 119999) then
    return jsonb_build_object('outcome','invalid_tick');
  end if;
  select coalesce(jsonb_agg(to_jsonb(t) || jsonb_build_object(
    'pending_run_ids',coalesce((select jsonb_agg(r.id order by r.seq) from public.paper_runs r
      where r.test_id=t.id and r.test_end_ms>=t.start_ms+t.gate_ms
      and not exists(select from public.paper_run_assessments a where a.run_id=r.id)),'[]'::jsonb),
    'tick_used',exists(select from public.paper_run_claims c where c.test_id=t.id and c.tick_id=p_tick_id)
      or exists(select from public.paper_tick_outcomes o where o.test_id=t.id and o.tick_id=p_tick_id),
    'tick_outcome',(select outcome from public.paper_tick_outcomes o where o.test_id=t.id and o.tick_id=p_tick_id)
  ) order by t.id),'[]'::jsonb) into data
  from (select * from public.paper_tests where status='running' and (p_after is null or id>p_after)
        order by id limit 100) t;
  return jsonb_build_object('outcome','ready','tests',data);
end $$;

create function public.paper_diagnostic_context(p_test_id uuid)
returns jsonb language plpgsql security definer set search_path='' as $$
declare t public.paper_tests;
begin
  select * into t from public.paper_tests where id=p_test_id;
  if not found then return jsonb_build_object('outcome','unknown_test'); end if;
  return jsonb_build_object('outcome','ready','test',to_jsonb(t),'test_end_ms',paper_private.now_ms());
end $$;

create function public.paper_worker_outcome(p_test_id uuid,p_tick_id uuid,p_outcome text,
  p_reason text default null,p_claim_id uuid default null,p_lease_token uuid default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare old public.paper_tick_outcomes; c public.paper_run_claims; stamp bigint; lag bigint;
begin
  perform 1 from public.paper_tests where id=p_test_id for update;
  if not found then raise exception 'unknown_test'; end if;
  stamp := paper_private.now_ms();
  if p_outcome not in ('error','evaluation_pending','test_closed','shadow_ended','lease_expired','no_new_candle')
    or p_reason is not null and p_reason !~ '^[a-z_]{1,80}$' then raise exception 'invalid_outcome'; end if;
  select * into old from public.paper_tick_outcomes where test_id=p_test_id and tick_id=p_tick_id;
  if found then return jsonb_build_object('outcome',old.outcome); end if;
  select * into c from public.paper_run_claims where test_id=p_test_id and tick_id=p_tick_id;
  if found then
    if c.id is distinct from p_claim_id or c.lease_token is distinct from p_lease_token then
      return jsonb_build_object('outcome','tick_already_used');
    end if;
    lag := c.test_end_ms-c.boundary_ms;
  elsif not exists(select from public.paper_measure_ticks where tick_id=p_tick_id and source='pg_cron'
      and stamp-paper_private.ms(created_at) between 0 and 119999) then
    return jsonb_build_object('outcome','invalid_tick');
  end if;
  insert into public.paper_tick_outcomes(test_id,tick_id,outcome,detail,lag_ms)
    values(p_test_id,p_tick_id,p_outcome,jsonb_strip_nulls(jsonb_build_object('reason',p_reason)),lag);
  return jsonb_build_object('outcome',p_outcome);
end $$;

set local role paper_rpc_owner;
create or replace function public.paper_persist_run(p_claim_id uuid,p_lease_token uuid,p_run jsonb,
  p_trades jsonb,p_positions jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare t public.paper_tests; c public.paper_run_claims; r public.paper_runs;
  t_persist bigint; why text; item jsonb;
begin
  select * into strict c from public.paper_run_claims where id=p_claim_id;
  select * into strict t from public.paper_tests where id=c.test_id for update;
  t_persist := paper_private.now_ms();
  if c.lease_token is distinct from p_lease_token then return jsonb_build_object('outcome','invalid_token'); end if;
  if t.status <> 'running' or t.authoritative_verdict_id is not null then return jsonb_build_object('outcome','test_closed'); end if;
  if t.shadow and t_persist>=t.shadow_until_ms then return jsonb_build_object('outcome','shadow_ended'); end if;
  if paper_private.ms(c.lease_expires_at)<=t_persist then return jsonb_build_object('outcome','lease_expired'); end if;
  if exists(select from public.paper_runs where claim_id=c.id or (test_id=t.id and boundary_ms=c.boundary_ms)) then
    return jsonb_build_object('outcome','no_new_candle'); end if;
  if jsonb_typeof(p_run) is distinct from 'object' then raise exception 'invalid_run_payload'; end if;
  if p_run->>'code_commit' is null or p_run->>'code_commit' !~ '^[0-9a-f]{40}$' or
    p_run->>'engine_sha256' is null or p_run->>'engine_sha256' !~ '^[0-9a-f]{64}$' then why := 'unknown_code_version';
  elsif p_run->>'settings_sha256' is distinct from t.settings_sha256 then why := 'settings_mismatch';
  elsif jsonb_typeof(p_run->'cost_profile') is distinct from 'object' or
    p_run->>'book_ts_ms' is null or p_run->>'cost_fetched_at_ms' is null then why := 'cost_profile_unavailable';
  elsif (p_run->>'cost_fetched_at_ms')::bigint-(p_run->>'book_ts_ms')::bigint<0 then why := 'book_from_future';
  elsif (p_run->>'cost_fetched_at_ms')::bigint-(p_run->>'book_ts_ms')::bigint>60000 then why := 'book_too_old';
  elsif p_run->>'status' = 'invalid' then why := coalesce(p_run->>'invalid_reason','invalid_run');
  elsif p_trades is null or p_trades='null'::jsonb or p_run->>'trade_count' is null then why := 'missing_run_specific_tape';
  elsif p_positions is null or p_positions='null'::jsonb or p_run->>'open_count' is null then why := 'missing_open_positions';
 end if;
  r := jsonb_populate_record(null::public.paper_runs,p_run || jsonb_build_object(
    'test_id',t.id,'claim_id',c.id,'tick_id',c.tick_id,'seq',
      (select coalesce(max(seq),0)+1 from public.paper_runs where test_id=t.id),
    'test_end_ms',c.test_end_ms,'as_of_ms',c.test_end_ms,'boundary_ms',c.boundary_ms,
    'last_candle_open_ms',c.last_candle_open_ms,'persisted_at',paper_private.ts(t_persist),
    'created_txid',txid_current(),'candle_source',coalesce(p_run->>'candle_source','okx'),
    'candle_open_times_ms',coalesce((select array_agg(a.open_time_ms order by a.open_time_ms)
      from public.candle_archive a where a.source=coalesce(p_run->>'candle_source','okx')
        and a.pair=t.pair and a.timeframe=t.timeframe
        and a.open_time_ms between (p_run->>'candle_first_open_ms')::bigint
                              and (p_run->>'candle_last_open_ms')::bigint),array[]::bigint[]),
    'raw',coalesce(p_run->'raw','{}'::jsonb),
    'trade_count',case when p_trades is null or p_trades='null'::jsonb then null else p_run->'trade_count' end,
    'open_count',case when p_positions is null or p_positions='null'::jsonb then null else p_run->'open_count' end,
    'tape_present',coalesce(jsonb_typeof(p_trades)='array',false),
    'positions_present',coalesce(jsonb_typeof(p_positions)='array',false),
    'status',case when why is null then 'valid' else 'invalid' end,'invalid_reason',why));
  insert into public.paper_runs select r.*;
  for item in select value from jsonb_array_elements(coalesce(nullif(p_trades,'null'::jsonb),'[]'::jsonb)) loop
    if (item->>'run_id')::uuid is distinct from r.id then raise exception 'run_identity_mismatch'; end if;
    insert into public.paper_run_trades select x.* from jsonb_populate_record(null::public.paper_run_trades,
      item || jsonb_build_object('evidence',item)) x;
  end loop;
  for item in select value from jsonb_array_elements(coalesce(nullif(p_positions,'null'::jsonb),'[]'::jsonb)) loop
    if (item->>'run_id')::uuid is distinct from r.id then raise exception 'run_identity_mismatch'; end if;
    insert into public.paper_run_open_positions select x.* from jsonb_populate_record(null::public.paper_run_open_positions,
      item || jsonb_build_object('evidence',item)) x;
  end loop;
  perform paper_private.outcome(t.id,c.tick_id,'run_created',c.test_end_ms-c.boundary_ms);
  return jsonb_build_object('outcome','run_created','run',to_jsonb(r));
end $$;

create or replace function public.paper_persist_diagnostic(p_test_id uuid,p_run jsonb,p_trades jsonb,p_positions jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare t public.paper_tests; r public.paper_diagnostic_runs; item jsonb; stamp bigint; horizon bigint;
begin
  select * into t from public.paper_tests where id=p_test_id;
  if not found then raise exception 'unknown_test'; end if;
  if jsonb_typeof(p_run) is distinct from 'object'
    or (p_trades is not null and p_trades<>'null'::jsonb and jsonb_typeof(p_trades)<>'array')
    or (p_positions is not null and p_positions<>'null'::jsonb and jsonb_typeof(p_positions)<>'array')
    then raise exception 'invalid_diagnostic_payload'; end if;
  if (p_trades is null or p_trades='null'::jsonb or p_positions is null or p_positions='null'::jsonb)
    and p_run->>'status' is distinct from 'invalid' then raise exception 'invalid_diagnostic_payload'; end if;
  stamp := paper_private.now_ms();
  horizon := (p_run->>'test_end_ms')::bigint;
  r := jsonb_populate_record(null::public.paper_diagnostic_runs,p_run || jsonb_build_object(
    'test_id',p_test_id,'as_of_ms',horizon,'boundary_ms',floor(horizon::numeric/300000)*300000,
    'last_candle_open_ms',floor(horizon::numeric/300000)*300000-300000,
    'persisted_at',paper_private.ts(stamp),'created_txid',txid_current(),
    'candle_source',coalesce(p_run->>'candle_source','okx'),'raw',coalesce(p_run->'raw','{}'::jsonb),
    'candle_open_times_ms',coalesce((select array_agg(a.open_time_ms order by a.open_time_ms)
      from public.candle_archive a where a.source=coalesce(p_run->>'candle_source','okx')
        and a.pair=t.pair and a.timeframe=t.timeframe
        and a.open_time_ms between (p_run->>'candle_first_open_ms')::bigint
                              and (p_run->>'candle_last_open_ms')::bigint),array[]::bigint[]),
    'tape_present',coalesce(jsonb_typeof(p_trades)='array',false),
    'positions_present',coalesce(jsonb_typeof(p_positions)='array',false),
    'trade_count',case when p_trades is null or p_trades='null'::jsonb then null else p_run->'trade_count' end,
    'open_count',case when p_positions is null or p_positions='null'::jsonb then null else p_run->'open_count' end,
    'open_positions',coalesce(nullif(p_positions,'null'::jsonb),'[]'::jsonb)));
  if exists(select from jsonb_array_elements(coalesce(nullif(p_positions,'null'::jsonb),'[]'::jsonb)) e where (e->>'run_id')::uuid is distinct from r.id) then
    raise exception 'run_identity_mismatch'; end if;
  insert into public.paper_diagnostic_runs select r.*;
  for item in select value from jsonb_array_elements(coalesce(nullif(p_trades,'null'::jsonb),'[]'::jsonb)) loop
    if (item->>'run_id')::uuid is distinct from r.id then raise exception 'run_identity_mismatch'; end if;
    insert into public.paper_diagnostic_trades select x.* from jsonb_populate_record(null::public.paper_diagnostic_trades,
      item || jsonb_build_object('evidence',item)) x;
  end loop;
  return to_jsonb(r);
end $$;

reset role;

do $$ declare f record; n text; begin
  for f in select p.oid::regprocedure signature from pg_proc p where p.pronamespace='public'::regnamespace
    and p.proname in ('paper_worker_page','paper_diagnostic_context','paper_worker_outcome') loop
    execute format('alter function %s owner to paper_rpc_owner',f.signature);
    execute format('revoke all on function %s from public',f.signature);
    foreach n in array array['anon','authenticated','service_role','authenticator','paper_cron_owner'] loop
      if exists(select from pg_roles where rolname=n) then
        execute format('revoke all on function %s from %I',f.signature,n);
      end if;
    end loop;
    grant execute on function public.paper_worker_page(uuid,uuid),
      public.paper_diagnostic_context(uuid),public.paper_worker_outcome(uuid,uuid,text,text,uuid,uuid) to service_role;
  end loop;
end $$;
revoke create on schema public from paper_rpc_owner;
revoke paper_rpc_owner from current_user;
notify pgrst, 'reload schema';
commit;
