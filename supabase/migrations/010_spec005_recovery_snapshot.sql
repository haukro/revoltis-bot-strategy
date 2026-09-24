-- IMPLEMENTATION-SPEC-005 recovery + runtime binding + snapshot ingestion.
-- No alpha changes. No live routing.

alter table public.risk_policy_versions
  add column if not exists fee_rate numeric(18,12),
  add column if not exists execution_role text;

alter table public.risk_policy_versions
  drop constraint if exists risk_policy_fee_rate_nonnegative,
  drop constraint if exists risk_policy_execution_role_valid;

alter table public.risk_policy_versions
  add constraint risk_policy_fee_rate_nonnegative
    check (fee_rate is null or fee_rate >= 0),
  add constraint risk_policy_execution_role_valid
    check (execution_role is null or execution_role in ('TAKER'));

create table if not exists public.paper_strategy_runtime_bindings (
  strategy_version_id text primary key,
  risk_policy_version_id uuid not null references public.risk_policy_versions(id),
  enabled boolean not null default false,
  created_at timestamptz not null default now(),
  locked_at timestamptz not null default now()
);

alter table public.paper_strategy_runtime_bindings enable row level security;
revoke all on table public.paper_strategy_runtime_bindings from anon,authenticated;
grant all on table public.paper_strategy_runtime_bindings to service_role;

create or replace function public.paper_bind_runtime_policy(
  p_strategy_version_id text,
  p_risk_policy_version_id uuid,
  p_enabled boolean default false
)
returns public.paper_strategy_runtime_bindings
language plpgsql
security definer
set search_path=public
as $func$
declare
  v_policy public.risk_policy_versions;
  v_row public.paper_strategy_runtime_bindings;
begin
  select * into v_policy
  from public.risk_policy_versions
  where id=p_risk_policy_version_id;

  if not found then raise exception 'risk_policy_not_found'; end if;
  if v_policy.fee_rate is null then raise exception 'risk_policy_fee_rate_required'; end if;
  if v_policy.execution_role is distinct from 'TAKER' then
    raise exception 'risk_policy_taker_role_required';
  end if;
  if v_policy.max_signal_age_ms is null or v_policy.max_order_age_ms is null or v_policy.max_book_age_ms is null then
    raise exception 'risk_policy_execution_ages_required';
  end if;

  insert into public.paper_strategy_runtime_bindings(
    strategy_version_id,risk_policy_version_id,enabled
  )
  values(p_strategy_version_id,p_risk_policy_version_id,p_enabled)
  on conflict(strategy_version_id) do update
  set risk_policy_version_id=excluded.risk_policy_version_id,
      enabled=excluded.enabled,
      locked_at=clock_timestamp()
  returning * into v_row;

  return v_row;
end;
$func$;

create or replace function public.paper_ingest_market_snapshot(
  p_pair text,
  p_provider text,
  p_provider_ts_ms bigint,
  p_received_at timestamptz,
  p_bid_levels jsonb,
  p_ask_levels jsonb,
  p_best_bid numeric,
  p_best_ask numeric,
  p_snapshot_hash text,
  p_software_commit text
)
returns public.execution_market_snapshots
language plpgsql
security definer
set search_path=public
as $func$
declare
  v_row public.execution_market_snapshots;
begin
  if p_pair is null or btrim(p_pair)='' then raise exception 'pair_required'; end if;
  if p_provider is null or btrim(p_provider)='' then raise exception 'provider_required'; end if;
  if p_provider_ts_ms<=0 then raise exception 'provider_timestamp_invalid'; end if;
  if p_received_at is null then raise exception 'received_at_required'; end if;
  if jsonb_typeof(p_bid_levels)<>'array' or jsonb_array_length(p_bid_levels)=0 then
    raise exception 'bid_levels_required';
  end if;
  if jsonb_typeof(p_ask_levels)<>'array' or jsonb_array_length(p_ask_levels)=0 then
    raise exception 'ask_levels_required';
  end if;
  if p_best_bid<=0 or p_best_ask<=0 or p_best_bid>=p_best_ask then
    raise exception 'invalid_two_sided_book';
  end if;
  if p_snapshot_hash is null or length(p_snapshot_hash)<>64 then
    raise exception 'snapshot_hash_invalid';
  end if;

  select * into v_row
  from public.execution_market_snapshots
  where provider=lower(p_provider)
    and pair=upper(p_pair)
    and provider_ts_ms=p_provider_ts_ms
    and snapshot_hash=p_snapshot_hash;

  if found then return v_row; end if;

  insert into public.execution_market_snapshots(
    pair,provider,provider_ts_ms,received_at,bid_levels,ask_levels,
    best_bid,best_ask,snapshot_hash,software_commit
  )
  values(
    upper(p_pair),lower(p_provider),p_provider_ts_ms,p_received_at,p_bid_levels,p_ask_levels,
    p_best_bid,p_best_ask,p_snapshot_hash,p_software_commit
  )
  returning * into v_row;

  return v_row;
end;
$func$;

-- Serialize order creation against stale-reservation release using the same portfolio lock.
create or replace function public.paper_create_order_from_approved_signal(
  p_signal_id uuid,
  p_side text,
  p_intent_type text,
  p_order_type text default 'MARKET',
  p_worker_id text default 'order-manager',
  p_software_commit text default 'unknown'
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $func$
declare
  v_signal public.paper_signals;
  v_decision public.risk_decisions;
  v_policy public.risk_policy_versions;
  v_ks public.kill_switch_state;
  v_risk_state public.portfolio_risk_state;
  v_res public.risk_reservations;
  v_order public.paper_orders;
  v_key text;
  v_outbox_key text;
  v_from text;
begin
  if p_side not in ('BUY','SELL') then raise exception 'invalid_order_side'; end if;
  if p_intent_type <> 'ENTRY' then raise exception 'phase2_entry_only'; end if;
  if p_order_type <> 'MARKET' then raise exception 'unsupported_order_type'; end if;

  select * into v_signal from public.paper_signals where id = p_signal_id;
  if not found then raise exception 'signal_not_found'; end if;

  select * into v_decision
  from public.risk_decisions
  where signal_id = p_signal_id and decision_stage='PRETRADE';
  if not found or v_decision.decision <> 'APPROVED' then
    raise exception 'signal_not_risk_approved';
  end if;

  select * into v_policy from public.risk_policy_versions where id=v_decision.policy_version_id;
  if not found then raise exception 'risk_policy_not_found'; end if;

  select * into v_ks
  from public.kill_switch_state
  where account_key='paper-default'
  for update;

  if v_ks.state <> 'RUNNING' then
    raise exception 'new_entry_blocked_kill_switch:%',v_ks.state;
  end if;

  select * into v_risk_state
  from public.portfolio_risk_state
  where account_key='paper-default'
  for update;

  if not found then raise exception 'portfolio_risk_state_missing'; end if;

  select * into v_res
  from public.risk_reservations
  where signal_id=p_signal_id
  for update;

  if not found or v_res.status not in ('RESERVED','PARTIALLY_CONSUMED') then
    raise exception 'active_risk_reservation_required';
  end if;

  if v_res.expires_at is not null and v_res.expires_at<clock_timestamp() then
    raise exception 'risk_reservation_expired';
  end if;

  v_key := encode(digest(convert_to(
    'ORDER|' || p_signal_id::text || '|' || p_intent_type || '|' || p_order_type,
    'UTF8'
  ), 'sha256'), 'hex');

  select * into v_order from public.paper_orders where idempotency_key = v_key;
  if found then
    return jsonb_build_object('order_id', v_order.id, 'status', v_order.status, 'idempotent_replay', true);
  end if;

  insert into public.paper_orders(
    signal_id, strategy_version_id, pair, side, intent_type, order_type,
    intended_notional, status, idempotency_key
  )
  values (
    p_signal_id, v_signal.strategy_version_id, v_signal.pair, p_side, p_intent_type, p_order_type,
    v_signal.intended_notional, 'CREATED', v_key
  )
  returning * into v_order;

  update public.risk_reservations
  set expires_at = case
      when v_policy.max_order_age_ms is not null and v_policy.max_order_age_ms > 0
        then clock_timestamp() + (v_policy.max_order_age_ms * interval '1 millisecond')
      else expires_at
    end,
    state_version=state_version+1
  where id=v_res.id;

  insert into public.paper_order_events(order_id, from_state, to_state, reason_code)
  values (v_order.id, null, 'CREATED', 'ORDER_CREATED');

  v_from := v_order.status;
  update public.paper_orders
  set status='ACCEPTED', accepted_at=clock_timestamp(), state_version=state_version+1, updated_at=clock_timestamp()
  where id=v_order.id
  returning * into v_order;

  insert into public.paper_order_events(order_id, from_state, to_state, reason_code)
  values (v_order.id, v_from, 'ACCEPTED', 'RISK_APPROVED');

  v_outbox_key := encode(digest(convert_to(
    'OUTBOX|BIND_FILL_ATTEMPT|order|' || v_order.id::text || '|attempt:1',
    'UTF8'
  ), 'sha256'), 'hex');

  insert into public.execution_outbox(
    event_type, entity_type, entity_id, correlation_id, payload, idempotency_key
  )
  values (
    'BIND_FILL_ATTEMPT', 'order', v_order.id, v_signal.correlation_id,
    jsonb_build_object('order_id', v_order.id, 'attempt_seq', 1), v_outbox_key
  )
  on conflict (idempotency_key) do nothing;

  perform public.paper_append_audit(
    'ORDER_ACCEPTED', 'order', v_order.id, v_signal.correlation_id,
    v_signal.strategy_version_id, v_signal.signal_close_time,
    clock_timestamp(), clock_timestamp(), null, to_jsonb(v_order), null,
    'RISK_APPROVED', p_worker_id, p_software_commit, v_signal.correlation_id
  );

  return jsonb_build_object('order_id', v_order.id, 'status', v_order.status, 'idempotent_replay', false);
end;
$func$;

-- Harden stale reservation release against create-order race.
create or replace function public.paper_release_stale_reservation(
  p_reservation_id uuid,
  p_worker_id text default 'recovery-worker',
  p_software_commit text default 'unknown',
  p_account_key text default 'paper-default'
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $func$
declare
  v_res public.risk_reservations;
  v_signal public.paper_signals;
  v_state public.portfolio_risk_state;
  v_remaining numeric(30,12);
  v_signed numeric(30,12);
  v_action_key text;
  v_issue_id uuid;
begin
  select * into v_state
  from public.portfolio_risk_state
  where account_key=p_account_key
  for update;

  if not found then raise exception 'portfolio_risk_state_missing'; end if;

  select * into v_res
  from public.risk_reservations
  where id=p_reservation_id
  for update;

  if not found then raise exception 'reservation_not_found'; end if;

  if v_res.status not in ('RESERVED','PARTIALLY_CONSUMED') then
    return jsonb_build_object('released',false,'idempotent_replay',true,'status',v_res.status);
  end if;

  if v_res.expires_at is null or v_res.expires_at>=clock_timestamp() then
    return jsonb_build_object('released',false,'reason','RESERVATION_NOT_STALE');
  end if;

  if exists(
    select 1
    from public.paper_orders o
    where o.signal_id=v_res.signal_id
      and o.status in ('CREATED','ACCEPTED','PENDING_FILL','PARTIALLY_FILLED','CANCEL_REQUESTED')
  ) then
    return jsonb_build_object('released',false,'reason','ACTIVE_ORDER_EXISTS');
  end if;

  select * into v_signal from public.paper_signals where id=v_res.signal_id;
  if not found then raise exception 'signal_not_found'; end if;

  v_remaining := greatest(0,v_res.reserved_notional-v_res.consumed_notional);
  v_signed := case when v_res.side='LONG' then v_remaining else -v_remaining end;

  update public.risk_reservations
  set status='RELEASED',
      released_at=clock_timestamp(),
      release_reason='STALE_RESERVATION_RECOVERY',
      state_version=state_version+1
  where id=v_res.id;

  update public.portfolio_risk_state
  set reserved_gross_notional=greatest(0,reserved_gross_notional-v_remaining),
      reserved_net_notional=reserved_net_notional-v_signed,
      state_version=state_version+1,
      updated_at=clock_timestamp()
  where account_key=p_account_key;

  insert into public.reconciliation_issues(
    issue_type,severity,entity_type,entity_id,details,resolved_at,check_code,fingerprint
  )
  values(
    'CHK_RES_ORDER','WARNING','risk_reservation',v_res.id,
    jsonb_build_object('expired_at',v_res.expires_at,'remaining_notional',v_remaining),
    clock_timestamp(),
    'CHK_RES_ORDER',
    public.paper_recon_issue_fingerprint(
      'CHK_RES_ORDER','risk_reservation',v_res.id,
      jsonb_build_object('reservation_id',v_res.id,'recovery','STALE_RESERVATION')
    )
  )
  returning id into v_issue_id;

  v_action_key := encode(
    extensions.digest(
      convert_to('RECOVERY|'||v_issue_id::text||'|RELEASE_STALE_RESERVATION','UTF8'),
      'sha256'
    ),
    'hex'
  );

  insert into public.recovery_actions(
    reconciliation_issue_id,repair_type,status,state_before,state_after,idempotency_key,applied_at
  )
  values(
    v_issue_id,'RELEASE_STALE_RESERVATION','APPLIED',to_jsonb(v_res),
    jsonb_build_object('status','RELEASED','remaining_released',v_remaining),
    v_action_key,clock_timestamp()
  )
  on conflict(idempotency_key) do nothing;

  perform public.paper_append_audit(
    'STALE_RESERVATION_RELEASED','risk_reservation',v_res.id,v_signal.correlation_id,
    v_signal.strategy_version_id,v_signal.signal_close_time,
    clock_timestamp(),clock_timestamp(),to_jsonb(v_res),
    jsonb_build_object('status','RELEASED','remaining_released',v_remaining),
    null,'STALE_RESERVATION_RECOVERY',p_worker_id,p_software_commit,v_signal.correlation_id
  );

  return jsonb_build_object(
    'released',true,
    'remaining_released',v_remaining,
    'reconciliation_issue_id',v_issue_id
  );
end;
$func$;

create or replace function public.paper_recovery_candidates(
  p_account_key text default 'paper-default',
  p_limit integer default 50
)
returns jsonb
language sql
security definer
set search_path=public
as $func$
with ks as (
  select state from public.kill_switch_state where account_key=p_account_key
),
stale_res as (
  select r.id
  from public.risk_reservations r
  where r.status in ('RESERVED','PARTIALLY_CONSUMED')
    and r.expires_at is not null
    and r.expires_at<clock_timestamp()
    and not exists(
      select 1 from public.paper_orders o
      where o.signal_id=r.signal_id
        and o.status in ('CREATED','ACCEPTED','PENDING_FILL','PARTIALLY_FILLED','CANCEL_REQUESTED')
    )
  order by r.expires_at,r.id
  limit greatest(1,least(p_limit,500))
),
kill_cancel as (
  select o.id
  from public.paper_orders o
  cross join ks
  where ks.state in ('HALTED','RECOVERY_PENDING')
    and o.status in ('CREATED','ACCEPTED','PENDING_FILL','PARTIALLY_FILLED','CANCEL_REQUESTED')
    and not exists(
      select 1 from public.execution_outbox x
      where x.entity_type='order'
        and x.entity_id=o.id
        and x.event_type='BIND_FILL_ATTEMPT'
        and x.processed_at is null
        and x.claimed_by is not null
        and x.claim_expires_at>clock_timestamp()
    )
  order by o.created_at,o.id
  limit greatest(1,least(p_limit,500))
),
order_timeout as (
  select o.id
  from public.paper_orders o
  join public.risk_reservations r on r.signal_id=o.signal_id
  join public.risk_policy_versions p on p.id=r.policy_version_id
  cross join ks
  where ks.state in ('RUNNING','HALT_NEW_ENTRIES')
    and o.status in ('CREATED','ACCEPTED','PENDING_FILL','PARTIALLY_FILLED')
    and p.max_order_age_ms is not null
    and extract(epoch from (clock_timestamp()-o.created_at))*1000>=p.max_order_age_ms
    and not exists(
      select 1 from public.execution_outbox x
      where x.entity_type='order'
        and x.entity_id=o.id
        and x.event_type='BIND_FILL_ATTEMPT'
        and x.processed_at is null
        and x.claimed_by is not null
        and x.claim_expires_at>clock_timestamp()
    )
  order by o.created_at,o.id
  limit greatest(1,least(p_limit,500))
)
select jsonb_build_object(
  'stale_reservations',coalesce((select jsonb_agg(id) from stale_res),'[]'::jsonb),
  'kill_switch_cancel_orders',coalesce((select jsonb_agg(id) from kill_cancel),'[]'::jsonb),
  'expired_orders',coalesce((select jsonb_agg(id) from order_timeout),'[]'::jsonb)
);
$func$;

create or replace function public.paper_recover_order(
  p_order_id uuid,
  p_action text,
  p_worker_id text default 'recovery-worker',
  p_software_commit text default 'unknown',
  p_account_key text default 'paper-default'
)
returns jsonb
language plpgsql
security definer
set search_path=public,extensions
as $func$
declare
  v_order public.paper_orders;
  v_ks public.kill_switch_state;
  v_res public.risk_reservations;
  v_policy public.risk_policy_versions;
  v_issue_id uuid;
  v_fingerprint text;
  v_action_key text;
  v_terminal jsonb;
  v_target text;
  v_reason text;
begin
  if p_action not in ('KILL_SWITCH_CANCEL','ORDER_TIMEOUT') then
    raise exception 'invalid_recovery_action';
  end if;

  select * into v_ks
  from public.kill_switch_state
  where account_key=p_account_key
  for update;

  select * into v_order
  from public.paper_orders
  where id=p_order_id
  for update;

  if not found then raise exception 'order_not_found'; end if;

  if v_order.status in ('FILLED','CANCELLED','REJECTED','EXPIRED','FAILED') then
    return jsonb_build_object('applied',false,'idempotent_replay',true,'status',v_order.status);
  end if;

  if exists(
    select 1 from public.execution_outbox x
    where x.entity_type='order'
      and x.entity_id=p_order_id
      and x.event_type='BIND_FILL_ATTEMPT'
      and x.processed_at is null
      and x.claimed_by is not null
      and x.claim_expires_at>clock_timestamp()
  ) then
    return jsonb_build_object('applied',false,'reason','UNEXPIRED_EXECUTION_LEASE');
  end if;

  if p_action='KILL_SWITCH_CANCEL' then
    if v_ks.state not in ('HALTED','RECOVERY_PENDING') then
      return jsonb_build_object('applied',false,'reason','KILL_SWITCH_NOT_HALTED');
    end if;
    v_target:='CANCELLED';
    v_reason:='KILL_SWITCH_CANCELLED';
  else
    if v_ks.state not in ('RUNNING','HALT_NEW_ENTRIES') then
      return jsonb_build_object('applied',false,'reason','ORDER_TIMEOUT_NOT_ALLOWED_IN_STATE');
    end if;

    select * into v_res from public.risk_reservations where signal_id=v_order.signal_id;
    if not found then raise exception 'reservation_missing'; end if;
    select * into v_policy from public.risk_policy_versions where id=v_res.policy_version_id;
    if not found or v_policy.max_order_age_ms is null then
      return jsonb_build_object('applied',false,'reason','ORDER_AGE_POLICY_DISABLED');
    end if;
    if extract(epoch from (clock_timestamp()-v_order.created_at))*1000<v_policy.max_order_age_ms then
      return jsonb_build_object('applied',false,'reason','ORDER_NOT_EXPIRED');
    end if;

    v_target:='EXPIRED';
    v_reason:='ORDER_AGE_EXPIRED';
  end if;

  v_fingerprint:=public.paper_recon_issue_fingerprint(
    case when p_action='KILL_SWITCH_CANCEL' then 'REC_KILL_SWITCH_CANCEL' else 'REC_ORDER_TIMEOUT' end,
    'order',p_order_id,
    jsonb_build_object('order_id',p_order_id,'action',p_action,'reason',v_reason)
  );

  insert into public.reconciliation_issues(
    issue_type,severity,entity_type,entity_id,details,check_code,fingerprint
  )
  values(
    case when p_action='KILL_SWITCH_CANCEL' then 'REC_KILL_SWITCH_CANCEL' else 'REC_ORDER_TIMEOUT' end,
    'WARNING','order',p_order_id,
    jsonb_build_object('order_status',v_order.status,'action',p_action),
    case when p_action='KILL_SWITCH_CANCEL' then 'REC_KILL_SWITCH_CANCEL' else 'REC_ORDER_TIMEOUT' end,
    v_fingerprint
  )
  on conflict do nothing;

  select id into v_issue_id
  from public.reconciliation_issues
  where resolved_at is null
    and check_code=case when p_action='KILL_SWITCH_CANCEL' then 'REC_KILL_SWITCH_CANCEL' else 'REC_ORDER_TIMEOUT' end
    and entity_type='order'
    and entity_id=p_order_id
    and fingerprint=v_fingerprint
  limit 1;

  v_terminal:=public.paper_terminalize_order(
    p_order_id,v_target,v_reason,p_worker_id,p_software_commit,p_account_key
  );

  update public.reconciliation_issues
  set resolved_at=clock_timestamp()
  where id=v_issue_id;

  v_action_key:=encode(
    digest(
      convert_to('RECOVERY|'||v_issue_id::text||'|'||p_action,'UTF8'),
      'sha256'
    ),
    'hex'
  );

  insert into public.recovery_actions(
    reconciliation_issue_id,repair_type,status,state_before,state_after,idempotency_key,applied_at
  )
  values(
    v_issue_id,p_action,'APPLIED',to_jsonb(v_order),v_terminal,v_action_key,clock_timestamp()
  )
  on conflict(idempotency_key) do nothing;

  return jsonb_build_object(
    'applied',true,
    'action',p_action,
    'order_id',p_order_id,
    'terminal',v_terminal,
    'reconciliation_issue_id',v_issue_id
  );
end;
$func$;

do $secure$
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
    execute format('revoke all on function %s from PUBLIC,anon,authenticated',r.signature);
    execute format('grant execute on function %s to service_role',r.signature);
  end loop;
end;
$secure$;
