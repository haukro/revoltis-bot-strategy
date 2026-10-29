-- B1-B8 rev3 / P2. Generated with Supabase CLI 2.122.0 migration new.
-- This file installs persistence only: no extension installation, cron job,
-- HTTP request, test creation, or strategic run is performed by the migration.
begin;

do $$ begin
  if not exists (select from pg_roles where rolname = 'paper_rpc_owner') then
    create role paper_rpc_owner nologin noinherit;
  end if;
  if not exists (select from pg_roles where rolname = 'paper_cron_owner') then
    create role paper_cron_owner nologin noinherit;
  end if;
end $$;
-- Temporary membership permits ownership transfer on non-superuser Supabase.
grant paper_rpc_owner, paper_cron_owner to current_user;
create schema paper_private authorization paper_rpc_owner;
revoke all on schema paper_private from public;
grant usage, create on schema public to paper_rpc_owner;
grant usage on schema public to paper_cron_owner;

create function paper_private.now_ms() returns bigint
language sql volatile set search_path = ''
as $$ select floor(extract(epoch from clock_timestamp()) * 1000)::bigint $$;
create function paper_private.ms(t timestamptz) returns bigint
language sql immutable strict set search_path = ''
as $$ select floor(extract(epoch from t) * 1000)::bigint $$;
create function paper_private.ts(ms bigint) returns timestamptz
language sql immutable strict set search_path = ''
as $$ select timestamptz 'epoch' + ms * interval '1 millisecond' $$;

create function paper_private.canonical_json(j jsonb) returns text
language plpgsql immutable strict set search_path = '' as $$
declare s text;
begin
  case jsonb_typeof(j)
    when 'object' then
      select '{' || coalesce(string_agg(to_jsonb(key)::text || ':' ||
        paper_private.canonical_json(value), ',' order by key collate "C"), '') || '}'
        into s from jsonb_each(j);
    when 'array' then
      select '[' || coalesce(string_agg(paper_private.canonical_json(value), ',' order by n), '') || ']'
        into s from jsonb_array_elements(j) with ordinality a(value,n);
    when 'number' then
      s := j::text;
      if s::numeric = 0 then return '0'; end if;
      if position('.' in s) > 0 then s := rtrim(rtrim(s, '0'), '.'); end if;
    else s := j::text;
  end case;
  return s;
end $$;
create function paper_private.hash_json(j jsonb) returns text
language sql immutable strict set search_path = ''
as $$ select encode(sha256(convert_to(paper_private.canonical_json(j), 'UTF8')), 'hex') $$;

create table public.paper_tests (
  id uuid primary key default gen_random_uuid(),
  label text not null check (label in ('official','scale')),
  pair text not null, timeframe text not null check (timeframe = '5m'),
  variant text not null check (variant = 'B'), strategy_version_id text not null,
  settings jsonb not null check (jsonb_typeof(settings) = 'object'),
  settings_sha256 text not null check (settings_sha256 ~ '^[0-9a-f]{64}$'),
  start_ms bigint not null, gate_ms bigint not null check (gate_ms = 2592000000),
  gate_trades integer not null check (gate_trades = 30),
  max_book_age_ms integer not null check (max_book_age_ms = 60000),
  lease_ms integer not null check (lease_ms > 0 and lease_ms < 300000),
  origin_grace_ms integer not null check (origin_grace_ms >= 0),
  shadow boolean not null, shadow_until_ms bigint,
  created_at timestamptz not null default clock_timestamp(),
  status text not null default 'running' check (status in ('running','completed','invalid','stopped')),
  authoritative_verdict_id uuid, completed_at timestamptz,
  unique (label, start_ms),
  check ((shadow and shadow_until_ms is not null and shadow_until_ms > start_ms) or
         (not shadow and shadow_until_ms is null)),
  check (settings_sha256 = paper_private.hash_json(settings)),
  check (settings->>'warmup_bars' is not null and (settings->>'warmup_bars')::integer >= 0)
);
create table public.paper_measure_ticks (
  tick_id uuid primary key default gen_random_uuid(),
  source text not null check (source = 'pg_cron'),
  cron_job_name text not null check (cron_job_name = 'paper_tick'),
  backend_pid integer not null,
  created_at timestamptz not null
);
create table public.paper_tick_outcomes (
  id uuid primary key default gen_random_uuid(),
  tick_id uuid not null references public.paper_measure_ticks,
  test_id uuid not null references public.paper_tests,
  outcome text not null check (outcome in ('run_created','no_new_candle','boundary_race',
    'lease_held','lease_expired','test_closed','evaluation_pending','shadow_ended','error')),
  detail jsonb not null default '{}', lag_ms bigint,
  created_at timestamptz not null default clock_timestamp(),
  unique (tick_id, test_id)
);
create table public.paper_run_claims (
  id uuid primary key default gen_random_uuid(),
  test_id uuid not null references public.paper_tests,
  tick_id uuid not null references public.paper_measure_ticks,
  last_candle_open_ms bigint not null, boundary_ms bigint not null,
  test_end_ms bigint not null, attempt integer not null check (attempt > 0),
  lease_token uuid not null default gen_random_uuid(),
  claimed_at timestamptz not null, lease_expires_at timestamptz not null,
  unique (test_id, boundary_ms, attempt), unique (test_id, tick_id),
  check (boundary_ms = last_candle_open_ms + 300000),
  check (boundary_ms = floor(test_end_ms::numeric / 300000) * 300000),
  check (paper_private.ms(claimed_at) = test_end_ms and lease_expires_at > claimed_at)
);
create table public.paper_runs (
  id uuid primary key,
  test_id uuid not null references public.paper_tests,
  claim_id uuid not null unique references public.paper_run_claims,
  tick_id uuid not null references public.paper_measure_ticks,
  seq bigint not null check (seq > 0), test_end_ms bigint not null,
  as_of_ms bigint not null, last_candle_open_ms bigint not null,
  last_candle_close_raw_ms bigint, boundary_ms bigint not null,
  persisted_at timestamptz not null, created_txid bigint not null,
  code_commit text, engine_sha256 text, settings_sha256 text, candles_sha256 text,
  candle_source text not null default 'okx', candle_first_open_ms bigint,
  candle_last_open_ms bigint, candle_count integer, expected_count integer,
  -- Freeze membership in the shared immutable archive, including any gaps.
  -- A later archive insert must never supplement evidence for an older run.
  candle_open_times_ms bigint[] not null,
  cost_profile jsonb, cost_profile_sha256 text, book_ts_ms bigint, cost_fetched_at_ms bigint,
  tape_sha256 text, trade_count integer check (trade_count >= 0),
  open_count integer check (open_count >= 0), summary jsonb, raw jsonb not null default '{}',
  tape_present boolean not null, positions_present boolean not null,
  status text not null check (status in ('valid','invalid')), invalid_reason text,
  unique (test_id, seq), unique (test_id, boundary_ms),
  check (as_of_ms = test_end_ms),
  check ((status = 'valid' and invalid_reason is null) or
         (status = 'invalid' and invalid_reason is not null)),
  check (status <> 'valid' or (tape_present and positions_present and trade_count is not null and open_count is not null))
);
create table public.paper_run_trades (
  run_id uuid not null references public.paper_runs, ordinal integer not null check (ordinal >= 0),
  pair text, opened_at_ms bigint, closed_at_ms bigint,
  entry_rate numeric, exit_rate numeric, stake numeric, profit_usdt numeric,
  exit_reason text, raw jsonb, evidence jsonb not null,
  primary key (run_id, ordinal)
);
create table public.paper_run_open_positions (
  run_id uuid not null references public.paper_runs, ordinal integer not null check (ordinal >= 0),
  pair text, opened_at_ms bigint, entry_rate numeric, stake numeric, raw jsonb,
  evidence jsonb not null, primary key (run_id, ordinal)
);
create table public.candle_archive (
  source text not null, pair text not null, timeframe text not null check (timeframe = '5m'),
  open_time_ms bigint not null check (open_time_ms % 300000 = 0), close_time_raw_ms bigint not null,
  open numeric not null, high numeric not null, low numeric not null, close numeric not null,
  volume numeric not null, quote_volume numeric not null,
  confirm integer not null check (confirm = 1), fetched_at timestamptz not null default clock_timestamp(),
  primary key (source, pair, timeframe, open_time_ms)
);
create table public.paper_diagnostic_runs (
  like public.paper_runs including defaults including constraints,
  open_positions jsonb not null check (jsonb_typeof(open_positions)='array')
);
alter table public.paper_diagnostic_runs drop column claim_id, drop column tick_id, drop column seq;
alter table public.paper_diagnostic_runs add primary key (id),
  add foreign key (test_id) references public.paper_tests;
create table public.paper_diagnostic_trades (
  like public.paper_run_trades including defaults including constraints,
  primary key (run_id, ordinal), foreign key (run_id) references public.paper_diagnostic_runs
);
create table public.paper_verdicts (
  id uuid primary key default gen_random_uuid(), test_id uuid not null references public.paper_tests,
  kind text not null check (kind in ('authoritative','audit')),
  refers_to_verdict_id uuid references public.paper_verdicts,
  candidate_run_id uuid not null references public.paper_runs,
  eligibility_trace jsonb not null, c30_ms bigint, t_star_ms bigint,
  trades_count integer, net_pnl numeric, expectancy numeric, gross_profit numeric, gross_loss numeric,
  profit_factor numeric, pf_infinite boolean, dd_field text, dd_value numeric,
  verdict text not null check (verdict in ('PASS','FAIL','INVALID','DIAGNOSTIC')),
  reason text not null, invalid_reason text, evaluator_commit text not null,
  evaluator_sha256 text not null, evidence_sha256 text not null,
  created_at timestamptz not null default clock_timestamp(),
  check ((kind = 'authoritative' and refers_to_verdict_id is null) or
         (kind = 'audit' and refers_to_verdict_id is not null))
);
create unique index paper_one_authoritative_verdict on public.paper_verdicts(test_id) where kind = 'authoritative';
alter table public.paper_tests add constraint paper_tests_verdict_fk
  foreign key (authoritative_verdict_id) references public.paper_verdicts;
create table public.paper_run_assessments (
  run_id uuid primary key references public.paper_runs,
  test_id uuid not null references public.paper_tests,
  eligibility text not null check (eligibility in ('proven_ineligible','eligible','unverifiable')),
  valid_trade_count integer, tick_origin_verified boolean not null,
  evaluator_commit text not null, evaluator_sha256 text not null,
  evidence_sha256 text not null, reason text,
  created_at timestamptz not null default clock_timestamp(),
  check (eligibility = 'unverifiable' or tick_origin_verified)
);

create function paper_private.immutable_row() returns trigger language plpgsql set search_path = '' as $$
begin raise exception 'immutable_row'; end $$;
create function paper_private.sealed_row() returns trigger language plpgsql set search_path = '' as $$
declare owner_tx bigint;
begin
  if tg_table_name = 'paper_diagnostic_trades' then
    select created_txid into owner_tx from public.paper_diagnostic_runs where id = new.run_id;
  else
    select created_txid into owner_tx from public.paper_runs where id = new.run_id;
  end if;
  if owner_tx is distinct from txid_current() then raise exception 'run_sealed'; end if;
  return new;
end $$;
create function paper_private.complete_run() returns trigger language plpgsql security definer set search_path = '' as $$
declare n integer; o integer;
begin
  if tg_table_name = 'paper_diagnostic_runs' then
    select count(*) into n from public.paper_diagnostic_trades where run_id = new.id;
    if n <> new.trade_count or jsonb_array_length(new.open_positions) <> new.open_count then
      raise exception 'tape_count_mismatch'; end if;
  else
    select count(*) into n from public.paper_run_trades where run_id = new.id;
    select count(*) into o from public.paper_run_open_positions where run_id = new.id;
    if (new.trade_count is not null and n <> new.trade_count) or
       (new.open_count is not null and o <> new.open_count) then
      raise exception 'tape_count_mismatch';
    end if;
  end if;
  return null;
end $$;
create function paper_private.run_identity() returns trigger language plpgsql set search_path = '' as $$
declare c public.paper_run_claims;
begin
  select * into strict c from public.paper_run_claims where id=new.claim_id;
  if (new.test_id,new.tick_id,new.boundary_ms,new.test_end_ms,new.last_candle_open_ms)
    is distinct from (c.test_id,c.tick_id,c.boundary_ms,c.test_end_ms,c.last_candle_open_ms)
    or new.created_txid <> txid_current() then raise exception 'claim_identity_mismatch'; end if;
  return new;
end $$;
create trigger claim_identity before insert on public.paper_runs
  for each row execute function paper_private.run_identity();
create function paper_private.test_transition() returns trigger language plpgsql set search_path = '' as $$
declare v public.paper_verdicts;
begin
  if tg_op = 'DELETE' then raise exception 'immutable_row'; end if;
  if current_user <> 'paper_rpc_owner' or old.status <> 'running' or new.status = 'running'
    or (to_jsonb(new) - array['status','authoritative_verdict_id','completed_at']) <>
       (to_jsonb(old) - array['status','authoritative_verdict_id','completed_at'])
    or new.completed_at is null then raise exception 'immutable_test'; end if;
  if new.status = 'stopped' then
    if not old.shadow or new.authoritative_verdict_id is not null or
      paper_private.ms(new.completed_at) < old.shadow_until_ms then raise exception 'invalid_stop'; end if;
  else
    select * into v from public.paper_verdicts where id = new.authoritative_verdict_id;
    if old.shadow or v.test_id is distinct from old.id or v.kind <> 'authoritative' or
      new.status <> (case when v.verdict = 'INVALID' then 'invalid' else 'completed' end) then
      raise exception 'invalid_transition';
    end if;
  end if;
  return new;
end $$;
create trigger paper_test_transition before update or delete on public.paper_tests
  for each row execute function paper_private.test_transition();
do $$ declare n text; begin
  foreach n in array array['paper_tests','paper_measure_ticks','paper_tick_outcomes','paper_run_claims',
    'paper_runs','paper_run_trades','paper_run_open_positions','candle_archive','paper_diagnostic_runs',
    'paper_diagnostic_trades','paper_verdicts','paper_run_assessments'] loop
    execute format('alter table public.%I owner to paper_rpc_owner', n);
    execute format('alter table public.%I enable row level security', n);
    execute format('revoke all on public.%I from public', n);
    if n <> 'paper_tests' then
      execute format('create trigger immutable_row before update or delete on public.%I for each row execute function paper_private.immutable_row()', n);
    end if;
  end loop;
  foreach n in array array['paper_run_trades','paper_run_open_positions','paper_diagnostic_trades'] loop
    execute format('create trigger run_sealed before insert on public.%I for each row execute function paper_private.sealed_row()', n);
  end loop;
  foreach n in array array['paper_runs','paper_diagnostic_runs'] loop
    execute format('create constraint trigger complete_run after insert on public.%I deferrable initially deferred for each row execute function paper_private.complete_run()', n);
  end loop;
end $$;

create function public.paper_create_test(p_test jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare t public.paper_tests;
begin
  t := jsonb_populate_record(null::public.paper_tests, p_test || jsonb_build_object(
    'id', coalesce(p_test->>'id', gen_random_uuid()::text), 'created_at', clock_timestamp(),
    'status','running','authoritative_verdict_id',null,'completed_at',null));
  insert into public.paper_tests select t.*;
  return to_jsonb(t);
end $$;

-- P4 will install the cron job and pg_net dispatch. P2 does not schedule or send.
create function public.paper_tick_start() returns uuid
language plpgsql security definer set search_path = '' as $$
declare tick uuid := gen_random_uuid();
begin
  if session_user <> 'paper_cron_owner' then raise exception 'tick_origin_unverified'; end if;
  insert into public.paper_measure_ticks(tick_id,source,cron_job_name,backend_pid,created_at)
    values (tick,'pg_cron','paper_tick',pg_backend_pid(),clock_timestamp());
  return tick;
end $$;
grant insert on public.paper_measure_ticks to paper_cron_owner;
create policy paper_cron_insert on public.paper_measure_ticks for insert to paper_cron_owner
  with check (session_user = 'paper_cron_owner' and source = 'pg_cron' and cron_job_name = 'paper_tick');

create function paper_private.outcome(p_test uuid,p_tick uuid,p_outcome text,p_lag bigint)
returns jsonb language plpgsql set search_path = '' as $$
begin
  insert into public.paper_tick_outcomes(test_id,tick_id,outcome,lag_ms)
    values (p_test,p_tick,p_outcome,p_lag) on conflict (tick_id,test_id) do nothing;
  return jsonb_build_object('outcome',p_outcome);
end $$;

create function public.paper_claim_run(p_test_id uuid,p_tick_id uuid,p_last_candle_open_ms bigint)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare t public.paper_tests; tick public.paper_measure_ticks; c public.paper_run_claims;
  t_claim bigint; b bigint := p_last_candle_open_ms + 300000; lag bigint; outcome text;
begin
  select * into strict t from public.paper_tests where id = p_test_id for update;
  t_claim := paper_private.now_ms(); -- exactly one observation, after the lock
  lag := t_claim - b;
  select * into tick from public.paper_measure_ticks where tick_id = p_tick_id;
  if tick.tick_id is null or tick.source <> 'pg_cron' or
    t_claim - paper_private.ms(tick.created_at) not between 0 and 119999 then
    return jsonb_build_object('outcome','invalid_tick');
  end if;
  if exists(select from public.paper_run_claims where test_id=p_test_id and tick_id=p_tick_id) or
    exists(select from public.paper_tick_outcomes where test_id=p_test_id and tick_id=p_tick_id) then
    return jsonb_build_object('outcome','tick_already_used');
  end if;
  if t.status <> 'running' or t.authoritative_verdict_id is not null then outcome := 'test_closed';
  elsif t.shadow and t_claim >= t.shadow_until_ms then outcome := 'shadow_ended';
  elsif b is null or b <> floor(t_claim::numeric/300000)*300000 then outcome := 'boundary_race';
  elsif exists(select from public.paper_runs where test_id=t.id and boundary_ms=b) then outcome := 'no_new_candle';
  elsif exists(select from public.paper_runs r where r.test_id=t.id and r.test_end_ms>=t.start_ms+t.gate_ms
    and not exists(select from public.paper_run_assessments a where a.run_id=r.id)) then outcome := 'evaluation_pending';
  elsif exists(select from public.paper_run_claims q where q.test_id=t.id and paper_private.ms(q.lease_expires_at)>t_claim
    and not exists(select from public.paper_runs r where r.claim_id=q.id)) then outcome := 'lease_held';
  elsif exists(select from public.paper_runs r where r.test_id=t.id and r.boundary_ms>b) then outcome := 'boundary_race';
  end if;
  if outcome is not null then return paper_private.outcome(t.id,p_tick_id,outcome,lag); end if;
  insert into public.paper_run_claims(test_id,tick_id,last_candle_open_ms,boundary_ms,test_end_ms,
    attempt,claimed_at,lease_expires_at)
    select t.id,p_tick_id,p_last_candle_open_ms,b,t_claim,coalesce(max(attempt),0)+1,
      paper_private.ts(t_claim),paper_private.ts(t_claim+t.lease_ms)
    from public.paper_run_claims where test_id=t.id and boundary_ms=b returning * into c;
  return jsonb_build_object('outcome','claimed','claim',to_jsonb(c));
end $$;

create function public.paper_archive_candles(p_source text,p_pair text,p_timeframe text,p_rows jsonb)
returns integer language plpgsql security definer set search_path = '' as $$
declare item jsonb; old public.candle_archive; incoming public.candle_archive; n integer := 0;
begin
  if jsonb_typeof(p_rows) is distinct from 'array' then raise exception 'invalid_candles'; end if;
  for item in select value from jsonb_array_elements(p_rows) loop
    incoming := jsonb_populate_record(null::public.candle_archive, item || jsonb_build_object(
      'source',p_source,'pair',p_pair,'timeframe',p_timeframe,'fetched_at',clock_timestamp()));
    insert into public.candle_archive select incoming.* on conflict do nothing;
    select * into strict old from public.candle_archive where source=p_source and pair=p_pair
      and timeframe=p_timeframe and open_time_ms=incoming.open_time_ms;
    if to_jsonb(old)-'fetched_at' <> to_jsonb(incoming)-'fetched_at' then raise exception 'candle_conflict'; end if;
    n := n + 1;
  end loop;
  return n;
end $$;

create function public.paper_persist_run(p_claim_id uuid,p_lease_token uuid,p_run jsonb,
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
  elsif p_trades is null or p_trades='null'::jsonb or p_run->>'trade_count' is null then why := 'missing_run_specific_tape';
  elsif p_positions is null or p_positions='null'::jsonb or p_run->>'open_count' is null then why := 'missing_open_positions';
  elsif p_run->>'status' = 'invalid' then why := coalesce(p_run->>'invalid_reason','invalid_run'); end if;
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

create function public.paper_persist_diagnostic(p_test_id uuid,p_run jsonb,p_trades jsonb,p_positions jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare t public.paper_tests; r public.paper_diagnostic_runs; item jsonb; stamp bigint; horizon bigint;
begin
  select * into t from public.paper_tests where id=p_test_id;
  if not found then raise exception 'unknown_test'; end if;
  if jsonb_typeof(p_run) is distinct from 'object' or jsonb_typeof(p_trades) is distinct from 'array'
    or jsonb_typeof(p_positions) is distinct from 'array' then raise exception 'invalid_diagnostic_payload'; end if;
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
    'tape_present',true,'positions_present',true,'open_positions',p_positions));
  if exists(select from jsonb_array_elements(p_positions) e where (e->>'run_id')::uuid is distinct from r.id) then
    raise exception 'run_identity_mismatch'; end if;
  insert into public.paper_diagnostic_runs select r.*;
  for item in select value from jsonb_array_elements(p_trades) loop
    if (item->>'run_id')::uuid is distinct from r.id then raise exception 'run_identity_mismatch'; end if;
    insert into public.paper_diagnostic_trades select x.* from jsonb_populate_record(null::public.paper_diagnostic_trades,
      item || jsonb_build_object('evidence',item)) x;
  end loop;
  return to_jsonb(r);
end $$;

create function public.paper_stop_shadow(p_test_id uuid) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare t public.paper_tests; stamp bigint;
begin
  select * into strict t from public.paper_tests where id=p_test_id for update;
  stamp := paper_private.now_ms();
  if t.status <> 'running' then return jsonb_build_object('outcome','test_closed','status',t.status); end if;
  if not t.shadow then return jsonb_build_object('outcome','not_shadow'); end if;
  if stamp<t.shadow_until_ms then return jsonb_build_object('outcome','shadow_active'); end if;
  update public.paper_tests set status='stopped',completed_at=paper_private.ts(stamp) where id=t.id;
  return jsonb_build_object('outcome','stopped');
end $$;

create function paper_private.snapshot(p_run_id uuid) returns jsonb
language plpgsql volatile set search_path = '' set timezone = 'UTC' as $$
declare r public.paper_runs; t public.paper_tests; data jsonb;
begin
  select * into strict r from public.paper_runs where id=p_run_id;
  select * into strict t from public.paper_tests where id=r.test_id;
  -- Operational test state is not measurement evidence: completion/stop must
  -- not change the snapshot digest recorded by an assessment or audit.
  select jsonb_build_object('test',to_jsonb(t)-array['status','authoritative_verdict_id','completed_at'],'run',to_jsonb(r),
    'trades',case when r.tape_present then coalesce(
      (select jsonb_agg(evidence order by ordinal) from public.paper_run_trades where run_id=r.id),'[]'::jsonb) end,
    'open_positions',case when r.positions_present then coalesce(
      (select jsonb_agg(evidence order by ordinal) from public.paper_run_open_positions where run_id=r.id),'[]'::jsonb) end,
    'candles',coalesce((select jsonb_agg(to_jsonb(c)-array['source','pair','timeframe','fetched_at'] order by open_time_ms)
      from public.candle_archive c where c.source=r.candle_source and c.pair=t.pair and c.timeframe=t.timeframe
        and c.open_time_ms=any(r.candle_open_times_ms)),'[]'::jsonb)) into data;
  return data;
end $$;
create function public.paper_read_run(p_run_id uuid) returns jsonb
language sql security definer set search_path = '' as $$
  select data || jsonb_build_object('evidence_sha256',paper_private.hash_json(data))
  from (select paper_private.snapshot(p_run_id) data) s
$$;

create function paper_private.origin(p_tick_id uuid,p_now_ms bigint,p_grace_ms integer)
returns text language plpgsql volatile set search_path = '' as $$
declare t public.paper_measure_ticks; succeeded boolean := false; failed boolean := false;
begin
  select * into strict t from public.paper_measure_ticks where tick_id=p_tick_id;
  if to_regclass('cron.job') is not null and to_regclass('cron.job_run_details') is not null then
    -- Bind the most recent matching execution. PIDs can be reused; an older
    -- failed execution must not contaminate the current successful/pending one.
    execute $query$
      select d.status='succeeded' and d.end_time >= $1, d.status='failed'
      from cron.job_run_details d join cron.job j using (jobid)
      where j.jobname='paper_tick' and j.username='paper_cron_owner'
        and j.database=current_database() and d.username='paper_cron_owner'
        and d.database=current_database() and d.job_pid=$2 and d.start_time <= $1
        and (d.end_time is null or d.end_time >= $1)
      order by d.start_time desc, d.runid desc limit 1
    $query$ into succeeded,failed using t.created_at,t.backend_pid;
  end if;
  if failed then return 'unverifiable'; end if;
  if succeeded then return 'verified'; end if;
  if p_now_ms-paper_private.ms(t.created_at) between 0 and p_grace_ms then return 'pending'; end if;
  return 'unverifiable';
exception when insufficient_privilege or undefined_table then
  if p_now_ms-paper_private.ms(t.created_at) between 0 and p_grace_ms then return 'pending'; end if;
  return 'unverifiable';
end $$;

create function paper_private.assessment_response(p_run_id uuid) returns jsonb
language sql volatile set search_path = '' as $$
  select jsonb_build_object('outcome','assessed','assessment',to_jsonb(a),'verdict',
    (select to_jsonb(v) from public.paper_verdicts v where v.test_id=a.test_id and v.kind='authoritative'))
  from public.paper_run_assessments a where a.run_id=p_run_id
$$;

-- Only the trusted server may attest a P1 evaluator result. The evidence digest
-- binds it to the exact immutable snapshot. Origin and chronology are checked
-- here, never accepted as caller-supplied booleans. This is not a client API.
create function public.paper_assess_run(p_run_id uuid,p_evidence_sha256 text,
  p_evaluation jsonb,p_evaluator_commit text,p_evaluator_sha256 text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare t public.paper_tests; r public.paper_runs; stamp bigint; origin text;
  data jsonb; digest text; entry jsonb; eligibility text; why text; trace jsonb; v public.paper_verdicts;
  valid_count integer; verdict text;
begin
  select * into strict r from public.paper_runs where id=p_run_id;
  select * into strict t from public.paper_tests where id=r.test_id for update;
  stamp := paper_private.now_ms();
  if exists(select from public.paper_run_assessments where run_id=r.id) then
    return paper_private.assessment_response(r.id); end if;
  if t.status <> 'running' or t.authoritative_verdict_id is not null then return jsonb_build_object('outcome','test_closed'); end if;
  if r.test_end_ms<t.start_ms+t.gate_ms then return jsonb_build_object('outcome','not_evaluated'); end if;
  if exists(select from public.paper_runs earlier where earlier.test_id=t.id and earlier.seq<r.seq
    and earlier.test_end_ms>=t.start_ms+t.gate_ms and not exists(
      select from public.paper_run_assessments a where a.run_id=earlier.id)) then
    return jsonb_build_object('outcome','assessment_out_of_order'); end if;
  origin := paper_private.origin(r.tick_id,stamp,t.origin_grace_ms);
  if origin='pending' then return jsonb_build_object('outcome','origin_pending'); end if;
  if p_evaluator_commit is null or p_evaluator_commit !~ '^[0-9a-f]{40}$' or
    p_evaluator_sha256 is null or p_evaluator_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'unknown_evaluator_version'; end if;
  data := paper_private.snapshot(r.id);
  digest := paper_private.hash_json(data);
  if origin='unverifiable' then
    eligibility := 'unverifiable'; verdict := 'INVALID'; why := 'tick_origin_unverified';
    p_evaluation := jsonb_build_object('verdict','INVALID','reason','unverifiable_candidate',
      'invalid_reason',why,'candidate_run_id',r.id);
  else
    if p_evidence_sha256 is distinct from digest then raise exception 'evidence_changed'; end if;
    if jsonb_typeof(p_evaluation) is distinct from 'object' or
      jsonb_array_length(p_evaluation->'eligibility_trace') is distinct from 1 then raise exception 'invalid_assessment'; end if;
    entry := p_evaluation->'eligibility_trace'->0;
    eligibility := entry->>'eligibility'; verdict := p_evaluation->>'verdict';
    why := p_evaluation->>'invalid_reason';
    if (entry->>'run_id')::uuid is distinct from r.id or (entry->>'seq')::bigint is distinct from r.seq or
      eligibility is null or eligibility not in ('proven_ineligible','eligible','unverifiable') then
      raise exception 'invalid_assessment'; end if;
    if eligibility='unverifiable' then
      if verdict is distinct from 'INVALID' or why is null or
        (p_evaluation->>'candidate_run_id')::uuid is distinct from r.id then raise exception 'invalid_assessment'; end if;
    else
      select count(*) into valid_count from public.paper_run_trades where run_id=r.id
        and opened_at_ms>=t.start_ms and closed_at_ms between t.start_ms and r.as_of_ms
        and exit_reason<>'end_of_test' and profit_usdt is not null;
      if r.status<>'valid' or not r.tape_present or not r.positions_present then raise exception 'invalid_assessment'; end if;
      if eligibility='proven_ineligible' then
        if valid_count>=t.gate_trades or verdict is distinct from 'NOT_EVALUATED' or
          p_evaluation->>'candidate_run_id' is not null then raise exception 'invalid_assessment'; end if;
      elsif valid_count<t.gate_trades or (p_evaluation->>'candidate_run_id')::uuid is distinct from r.id or
        verdict is null or (t.label='scale' and verdict<>'DIAGNOSTIC') or
        (t.label='official' and verdict not in ('PASS','FAIL')) then raise exception 'invalid_assessment'; end if;
    end if;
  end if;
  insert into public.paper_run_assessments(run_id,test_id,eligibility,valid_trade_count,tick_origin_verified,
    evaluator_commit,evaluator_sha256,evidence_sha256,reason)
    values (r.id,t.id,eligibility,valid_count,origin='verified',p_evaluator_commit,p_evaluator_sha256,digest,why);
  if eligibility<>'proven_ineligible' and not t.shadow then
    select jsonb_agg(jsonb_strip_nulls(jsonb_build_object('run_id',a.run_id,'seq',q.seq,
      'eligibility',a.eligibility,'reason',a.reason)) order by q.seq) into trace
      from public.paper_run_assessments a join public.paper_runs q on q.id=a.run_id where a.test_id=t.id;
    v := jsonb_populate_record(null::public.paper_verdicts,p_evaluation || jsonb_build_object(
      'id',gen_random_uuid(),'test_id',t.id,'kind','authoritative','refers_to_verdict_id',null,
      'candidate_run_id',r.id,'eligibility_trace',trace,'evaluator_commit',p_evaluator_commit,
      'evaluator_sha256',p_evaluator_sha256,'evidence_sha256',digest,'created_at',paper_private.ts(stamp)));
    insert into public.paper_verdicts select v.*;
    update public.paper_tests set status=case when v.verdict='INVALID' then 'invalid' else 'completed' end,
      authoritative_verdict_id=v.id,completed_at=paper_private.ts(stamp) where id=t.id;
  end if;
  return paper_private.assessment_response(r.id);
end $$;

create function public.paper_audit_run(p_verdict_id uuid,p_evidence_sha256 text,p_evaluation jsonb,
  p_evaluator_commit text,p_evaluator_sha256 text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare original public.paper_verdicts; v public.paper_verdicts; digest text;
begin
  select * into strict original from public.paper_verdicts where id=p_verdict_id and kind='authoritative';
  digest := paper_private.hash_json(paper_private.snapshot(original.candidate_run_id));
  if p_evidence_sha256 is distinct from digest then raise exception 'evidence_changed'; end if;
  if p_evaluator_commit is null or p_evaluator_commit !~ '^[0-9a-f]{40}$' or
    p_evaluator_sha256 is null or p_evaluator_sha256 !~ '^[0-9a-f]{64}$' or
    (p_evaluation->>'candidate_run_id')::uuid is distinct from original.candidate_run_id then
    raise exception 'invalid_audit'; end if;
  v := jsonb_populate_record(null::public.paper_verdicts,p_evaluation || jsonb_build_object(
    'id',gen_random_uuid(),'test_id',original.test_id,'kind','audit','refers_to_verdict_id',original.id,
    'candidate_run_id',original.candidate_run_id,'evaluator_commit',p_evaluator_commit,
    'evaluator_sha256',p_evaluator_sha256,'evidence_sha256',digest,'created_at',clock_timestamp()));
  insert into public.paper_verdicts select v.*;
  return to_jsonb(v);
end $$;

-- Scope every grant/revoke to the new objects; legacy RPCs/defaults are untouched.
do $$ declare f record; n text; role_name text;
  rpc_names text[] := array['paper_create_test','paper_claim_run','paper_archive_candles',
    'paper_persist_run','paper_persist_diagnostic','paper_stop_shadow','paper_read_run','paper_assess_run','paper_audit_run'];
begin
  for f in select p.oid::regprocedure signature,p.proname,nspname from pg_proc p
    join pg_namespace n on n.oid=p.pronamespace
    where nspname='paper_private' or (nspname='public' and (p.proname=any(rpc_names) or p.proname='paper_tick_start')) loop
    execute format('alter function %s owner to paper_rpc_owner',f.signature);
    execute format('revoke all on function %s from public',f.signature);
    foreach role_name in array array['anon','authenticated','service_role','paper_cron_owner'] loop
      if exists(select from pg_roles where rolname=role_name) then
        execute format('revoke all on function %s from %I',f.signature,role_name);
      end if;
    end loop;
    if f.nspname='public' and f.proname=any(rpc_names) and exists(select from pg_roles where rolname='service_role') then
      execute format('grant execute on function %s to service_role',f.signature);
    end if;
  end loop;
  foreach n in array array['paper_tests','paper_measure_ticks','paper_tick_outcomes','paper_run_claims',
    'paper_runs','paper_run_trades','paper_run_open_positions','candle_archive','paper_diagnostic_runs',
    'paper_diagnostic_trades','paper_verdicts','paper_run_assessments'] loop
    foreach role_name in array array['anon','authenticated','service_role'] loop
      if exists(select from pg_roles where rolname=role_name) then
        execute format('revoke all on public.%I from %I',n,role_name);
      end if;
    end loop;
  end loop;
end $$;
grant create on schema public to paper_cron_owner;
alter function public.paper_tick_start() owner to paper_cron_owner;
revoke create on schema public from paper_cron_owner, paper_rpc_owner;
-- The tick owner has EXECUTE by ownership. HTTP workers have none.
revoke paper_rpc_owner, paper_cron_owner from current_user;
commit;
