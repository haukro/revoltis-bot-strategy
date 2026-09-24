-- IMPLEMENTATION-SPEC-005 Phase 2 operational core.
-- No alpha changes. No live routing.

create table if not exists public.ops_policy_versions (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  warning_book_age_ms bigint,
  halt_book_age_ms bigint,
  warning_queue_backlog integer,
  halt_queue_backlog integer,
  warning_worker_lag_ms bigint,
  halt_worker_lag_ms bigint,
  reconciliation_warning_age_ms bigint,
  reconciliation_critical_age_ms bigint,
  max_worker_batch integer,
  market_book_depth integer,
  created_at timestamptz not null default now(),
  locked_at timestamptz not null default now(),
  check (warning_book_age_ms is null or warning_book_age_ms >= 0),
  check (halt_book_age_ms is null or halt_book_age_ms >= 0),
  check (warning_queue_backlog is null or warning_queue_backlog >= 0),
  check (halt_queue_backlog is null or halt_queue_backlog >= 0),
  check (warning_worker_lag_ms is null or warning_worker_lag_ms >= 0),
  check (halt_worker_lag_ms is null or halt_worker_lag_ms >= 0),
  check (reconciliation_warning_age_ms is null or reconciliation_warning_age_ms >= 0),
  check (reconciliation_critical_age_ms is null or reconciliation_critical_age_ms >= 0),
  check (max_worker_batch is null or max_worker_batch > 0),
  check (market_book_depth is null or market_book_depth > 0)
);

create table if not exists public.ops_policy_state (
  account_key text primary key,
  policy_version_id uuid references public.ops_policy_versions(id),
  updated_at timestamptz not null default now()
);

insert into public.ops_policy_state(account_key)
values ('paper-default')
on conflict (account_key) do nothing;


insert into public.blind_test_policies(
  blind_test_id,strategy_version_id,blind_until,allowed_categories,created_at,locked_at
)
values(
  'TEST-SPEC-002-forward-2026',
  'TEST-SPEC-002',
  '2026-12-23T11:59:59.999Z'::timestamptz,
  '["system_health","market_health","queue_health","worker_health","reconciliation_counts","audit_integrity","kill_switch","blind_status"]'::jsonb,
  now(),now()
)
on conflict(blind_test_id) do nothing;

create table if not exists public.worker_heartbeats (
  worker_name text primary key,
  last_run_id uuid,
  last_started_at timestamptz,
  last_completed_at timestamptz,
  last_success_at timestamptz,
  status text not null check (status in ('IDLE','RUNNING','DEGRADED','FAILED')),
  claimed_count bigint not null default 0,
  processed_count bigint not null default 0,
  last_error_code text,
  software_commit text not null,
  updated_at timestamptz not null default now()
);

alter table public.reconciliation_runs
  add column if not exists account_key text not null default 'paper-default',
  add column if not exists snapshot_at timestamptz,
  add column if not exists audit_integrity_passed boolean,
  add column if not exists software_commit text,
  add column if not exists summary jsonb not null default '{}'::jsonb;

create unique index if not exists reconciliation_one_running_per_account
on public.reconciliation_runs(account_key)
where status='RUNNING';

alter table public.reconciliation_issues
  add column if not exists check_code text,
  add column if not exists fingerprint text;

update public.reconciliation_issues
set check_code=coalesce(check_code,issue_type),
    fingerprint=coalesce(fingerprint,encode(extensions.digest(convert_to(
      coalesce(issue_type,'') || '|' ||
      coalesce(entity_type,'') || '|' ||
      coalesce(entity_id::text,'') || '|' ||
      coalesce(details::text,'{}'),
      'UTF8'
    ),'sha256'),'hex'))
where check_code is null or fingerprint is null;

alter table public.reconciliation_issues
  alter column check_code set not null,
  alter column fingerprint set not null;

create unique index if not exists reconciliation_one_open_fingerprint
on public.reconciliation_issues(
  check_code,
  coalesce(entity_type,''),
  coalesce(entity_id,'00000000-0000-0000-0000-000000000000'::uuid),
  fingerprint
)
where resolved_at is null;

create index if not exists worker_heartbeats_status_idx
on public.worker_heartbeats(status, updated_at);

-- Versioned ops policy activation.
create or replace function public.paper_activate_ops_policy(
  p_policy_version_id uuid,
  p_account_key text default 'paper-default'
)
returns jsonb
language plpgsql
security definer
set search_path=public
as $func$
begin
  if not exists(select 1 from public.ops_policy_versions where id=p_policy_version_id) then
    raise exception 'ops_policy_not_found';
  end if;

  insert into public.ops_policy_state(account_key,policy_version_id,updated_at)
  values(p_account_key,p_policy_version_id,clock_timestamp())
  on conflict(account_key) do update
  set policy_version_id=excluded.policy_version_id,
      updated_at=excluded.updated_at;

  return jsonb_build_object(
    'account_key',p_account_key,
    'policy_version_id',p_policy_version_id,
    'activated',true
  );
end;
$func$;

-- Heartbeats are internal-only; blind API never exposes lifetime counters.
create or replace function public.paper_record_worker_heartbeat(
  p_worker_name text,
  p_run_id uuid,
  p_phase text,
  p_status text,
  p_claimed_count bigint,
  p_processed_count bigint,
  p_error_code text,
  p_software_commit text
)
returns public.worker_heartbeats
language plpgsql
security definer
set search_path=public
as $func$
declare
  v_row public.worker_heartbeats;
begin
  if p_phase not in ('START','COMPLETE') then raise exception 'invalid_heartbeat_phase'; end if;
  if p_status not in ('IDLE','RUNNING','DEGRADED','FAILED') then raise exception 'invalid_worker_status'; end if;

  insert into public.worker_heartbeats(
    worker_name,last_run_id,last_started_at,last_completed_at,last_success_at,status,
    claimed_count,processed_count,last_error_code,software_commit,updated_at
  )
  values(
    p_worker_name,p_run_id,
    case when p_phase='START' then clock_timestamp() else null end,
    case when p_phase='COMPLETE' then clock_timestamp() else null end,
    case when p_phase='COMPLETE' and p_status in ('IDLE','RUNNING') and p_error_code is null then clock_timestamp() else null end,
    p_status,
    greatest(coalesce(p_claimed_count,0),0),
    greatest(coalesce(p_processed_count,0),0),
    p_error_code,p_software_commit,clock_timestamp()
  )
  on conflict(worker_name) do update
  set last_run_id=excluded.last_run_id,
      last_started_at=case when p_phase='START' then clock_timestamp() else public.worker_heartbeats.last_started_at end,
      last_completed_at=case when p_phase='COMPLETE' then clock_timestamp() else public.worker_heartbeats.last_completed_at end,
      last_success_at=case
        when p_phase='COMPLETE' and p_status in ('IDLE','RUNNING') and p_error_code is null
          then clock_timestamp()
        else public.worker_heartbeats.last_success_at
      end,
      status=excluded.status,
      claimed_count=public.worker_heartbeats.claimed_count+greatest(coalesce(p_claimed_count,0),0),
      processed_count=public.worker_heartbeats.processed_count+greatest(coalesce(p_processed_count,0),0),
      last_error_code=excluded.last_error_code,
      software_commit=excluded.software_commit,
      updated_at=clock_timestamp()
  returning * into v_row;

  return v_row;
end;
$func$;

-- Authoritative kill-switch state writer.
create or replace function public.paper_set_kill_switch_state(
  p_target_state text,
  p_reason_code text,
  p_actor text,
  p_software_commit text,
  p_account_key text default 'paper-default'
)
returns jsonb
language plpgsql
security definer
set search_path=public
as $func$
declare
  v_state public.kill_switch_state;
  v_event_id uuid;
  v_latest_recon public.reconciliation_runs;
  v_latest_ks timestamptz;
  v_latest_recovery timestamptz;
  v_open_critical bigint;
  v_live_execution_leases bigint;
  v_allowed boolean := false;
begin
  if p_target_state not in ('RUNNING','HALT_NEW_ENTRIES','HALTED','RECOVERY_PENDING') then
    raise exception 'invalid_kill_switch_target';
  end if;
  if p_actor is null or btrim(p_actor)='' then raise exception 'actor_required'; end if;
  if p_reason_code is null or btrim(p_reason_code)='' then raise exception 'reason_required'; end if;

  select * into v_state
  from public.kill_switch_state
  where account_key=p_account_key
  for update;

  if not found then raise exception 'kill_switch_state_missing'; end if;

  if v_state.state=p_target_state then
    return jsonb_build_object(
      'changed',false,
      'idempotent_replay',true,
      'state',v_state.state,
      'state_version',v_state.state_version
    );
  end if;

  v_allowed := case
    when v_state.state='RUNNING' and p_target_state in ('HALT_NEW_ENTRIES','HALTED') then true
    when v_state.state='HALT_NEW_ENTRIES' and p_target_state in ('HALTED','RECOVERY_PENDING') then true
    when v_state.state='HALTED' and p_target_state='RECOVERY_PENDING' then true
    when v_state.state='RECOVERY_PENDING' and p_target_state in ('HALTED','RUNNING') then true
    else false
  end;

  if not v_allowed then
    raise exception 'invalid_kill_switch_transition:%->%',v_state.state,p_target_state;
  end if;

  if p_target_state='RUNNING' then
    if v_state.state <> 'RECOVERY_PENDING' then
      raise exception 'run_enable_requires_recovery_pending';
    end if;

    select * into v_latest_recon
    from public.reconciliation_runs
    where account_key=p_account_key
      and status='PASSED'
    order by completed_at desc nulls last
    limit 1;

    if not found or v_latest_recon.completed_at is null then
      raise exception 'fresh_passed_reconciliation_required';
    end if;

    select max(created_at) into v_latest_ks
    from public.kill_switch_events
    where account_key=p_account_key;

    select max(applied_at) into v_latest_recovery
    from public.recovery_actions
    where status='APPLIED';

    if v_latest_ks is not null and v_latest_recon.completed_at <= v_latest_ks then
      raise exception 'reconciliation_older_than_kill_switch_event';
    end if;

    if v_latest_recovery is not null and v_latest_recon.completed_at <= v_latest_recovery then
      raise exception 'reconciliation_older_than_recovery_action';
    end if;

    if coalesce(v_latest_recon.audit_integrity_passed,false) is not true then
      raise exception 'audit_integrity_not_passed';
    end if;

    select count(*) into v_open_critical
    from public.reconciliation_issues
    where severity='CRITICAL' and resolved_at is null;

    if v_open_critical > 0 then
      raise exception 'open_critical_reconciliation_issues:%',v_open_critical;
    end if;

    select count(*) into v_live_execution_leases
    from public.execution_outbox
    where processed_at is null
      and event_type='BIND_FILL_ATTEMPT'
      and claimed_by is not null
      and claim_expires_at > clock_timestamp();

    if v_live_execution_leases > 0 then
      raise exception 'unexpired_execution_leases:%',v_live_execution_leases;
    end if;
  end if;

  insert into public.kill_switch_events(
    account_key,from_state,to_state,reason_code,triggered_by,payload
  )
  values(
    p_account_key,v_state.state,p_target_state,p_reason_code,p_actor,
    jsonb_build_object('software_commit',p_software_commit,'prior_state_version',v_state.state_version)
  )
  returning id into v_event_id;

  update public.kill_switch_state
  set state=p_target_state,
      reason_code=p_reason_code,
      state_version=state_version+1,
      updated_at=clock_timestamp()
  where account_key=p_account_key
  returning * into v_state;

  perform public.paper_append_audit(
    'KILL_SWITCH_TRANSITION','kill_switch_event',v_event_id,
    'kill-switch:'||p_account_key,null,null,
    clock_timestamp(),clock_timestamp(),
    null,
    jsonb_build_object(
      'account_key',p_account_key,
      'state',p_target_state,
      'reason_code',p_reason_code,
      'state_version',v_state.state_version
    ),
    null,p_reason_code,p_actor,p_software_commit,'kill-switch:'||p_account_key
  );

  return jsonb_build_object(
    'changed',true,
    'state',v_state.state,
    'state_version',v_state.state_version,
    'event_id',v_event_id
  );
end;
$func$;

create or replace function public.paper_request_recovery(
  p_reason_code text,
  p_actor text,
  p_software_commit text,
  p_account_key text default 'paper-default'
)
returns jsonb
language sql
security definer
set search_path=public
as $func$
  select public.paper_set_kill_switch_state(
    'RECOVERY_PENDING',p_reason_code,p_actor,p_software_commit,p_account_key
  );
$func$;

create or replace function public.paper_enable_after_recovery(
  p_actor text,
  p_software_commit text,
  p_account_key text default 'paper-default'
)
returns jsonb
language sql
security definer
set search_path=public
as $func$
  select public.paper_set_kill_switch_state(
    'RUNNING','RECOVERY_VALIDATED',p_actor,p_software_commit,p_account_key
  );
$func$;


create or replace function public.paper_ops_health_snapshot(
  p_account_key text default 'paper-default'
)
returns jsonb
language sql
security definer
set search_path=public
as $func$
with active_policy as (
  select p.*
  from public.ops_policy_state s
  join public.ops_policy_versions p on p.id=s.policy_version_id
  where s.account_key=p_account_key
),
ks as (
  select state,reason_code,updated_at
  from public.kill_switch_state
  where account_key=p_account_key
),
q as (
  select count(*)::bigint as backlog
  from public.execution_outbox
  where processed_at is null
),
latest_recon as (
  select status,completed_at,audit_integrity_passed
  from public.reconciliation_runs
  where account_key=p_account_key
  order by completed_at desc nulls last,started_at desc
  limit 1
),
issues as (
  select check_code,severity,count(*)::bigint as count
  from public.reconciliation_issues
  where resolved_at is null
  group by check_code,severity
  order by severity,check_code
),
workers as (
  select jsonb_agg(
    jsonb_build_object(
      'worker_name',w.worker_name,
      'status',w.status,
      'last_success_age_category',
        case
          when w.last_success_at is null then 'NEVER'
          when p.warning_worker_lag_ms is null and p.halt_worker_lag_ms is null then 'UNCONFIGURED'
          when p.halt_worker_lag_ms is not null
               and extract(epoch from (clock_timestamp()-w.last_success_at))*1000 >= p.halt_worker_lag_ms then 'STALE'
          when p.warning_worker_lag_ms is not null
               and extract(epoch from (clock_timestamp()-w.last_success_at))*1000 >= p.warning_worker_lag_ms then 'LATE'
          else 'FRESH'
        end,
      'last_error_code',w.last_error_code,
      'software_commit',w.software_commit
    )
    order by w.worker_name
  ) as rows
  from public.worker_heartbeats w
  left join active_policy p on true
),
market as (
  select jsonb_object_agg(status,cnt) as counts
  from (
    select status,count(*)::bigint as cnt
    from public.market_data_health
    group by status
  ) x
),
blind as (
  select blind_test_id,strategy_version_id,blind_until
  from public.blind_test_policies
  where strategy_version_id='TEST-SPEC-002'
  order by blind_until desc
  limit 1
)
select jsonb_build_object(
  'kill_switch',coalesce((select to_jsonb(ks) from ks),'{}'::jsonb),
  'queue',jsonb_build_object('current_backlog',(select backlog from q)),
  'workers',coalesce((select rows from workers),'[]'::jsonb),
  'reconciliation',jsonb_build_object(
    'latest',coalesce((select to_jsonb(latest_recon) from latest_recon),'{}'::jsonb),
    'open_issue_counts',coalesce((select jsonb_agg(to_jsonb(issues)) from issues),'[]'::jsonb)
  ),
  'market_health_counts',coalesce((select counts from market),'{}'::jsonb),
  'blind',coalesce((select to_jsonb(blind) from blind),'{}'::jsonb),
  'live_trading',false
);
$func$;

-- Single-writer enforcement: service role can read but not directly mutate KS state.
revoke insert,update,delete,truncate on table public.kill_switch_state from service_role,anon,authenticated;
grant select on table public.kill_switch_state to service_role;

-- Phase 2 operational tables are service-role only.
do $secure$
declare
  t text;
begin
  foreach t in array array['ops_policy_versions','ops_policy_state','worker_heartbeats']
  loop
    execute format('alter table public.%I enable row level security',t);
    execute format('revoke all on table public.%I from anon,authenticated',t);
    execute format('grant all on table public.%I to service_role',t);
  end loop;
end;
$secure$;

-- Reconciliation tables remain private.
revoke all on table public.reconciliation_runs from anon,authenticated;
revoke all on table public.reconciliation_issues from anon,authenticated;
grant all on table public.reconciliation_runs to service_role;
grant all on table public.reconciliation_issues to service_role;

-- All paper_* RPCs remain service-role only after adding Phase 2 functions.
do $revoke$
declare
  r record;
begin
  for r in
    select p.oid::regprocedure as signature
    from pg_proc p
    join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public'
      and p.proname like 'paper_%'
  loop
    execute format('revoke all on function %s from PUBLIC, anon, authenticated',r.signature);
    execute format('grant execute on function %s to service_role',r.signature);
  end loop;
end;
$revoke$;
