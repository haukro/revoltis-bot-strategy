-- SPEC-004 post-implementation hardening (Grok P1-P5).
-- No alpha changes. No live routing.

alter table public.execution_outbox
  add column if not exists lease_generation bigint not null default 0;

alter table public.execution_outbox
  drop constraint if exists execution_outbox_lease_generation_nonnegative;
alter table public.execution_outbox
  add constraint execution_outbox_lease_generation_nonnegative
  check (lease_generation >= 0);

alter table public.risk_reservations
  add column if not exists expires_at timestamptz;

alter table public.paper_execution_attempts
  add column if not exists source_outbox_id uuid references public.execution_outbox(id),
  add column if not exists source_lease_generation bigint;

create index if not exists risk_reservations_stale_idx
on public.risk_reservations(expires_at, status)
where status in ('RESERVED','PARTIALLY_CONSUMED');

-- Claim now creates a monotonically increasing lease generation.
create or replace function public.paper_claim_outbox(
  p_worker_id text,
  p_limit integer default 20,
  p_lease_seconds integer default 30
)
returns setof public.execution_outbox
language plpgsql
security definer
set search_path = public
as $func$
begin
  if p_worker_id is null or btrim(p_worker_id) = '' then
    raise exception 'worker_id_required';
  end if;
  if p_limit < 1 or p_limit > 100 then
    raise exception 'invalid_claim_limit';
  end if;
  if p_lease_seconds < 1 or p_lease_seconds > 3600 then
    raise exception 'invalid_lease_seconds';
  end if;

  return query
  with candidates as (
    select id
    from public.execution_outbox
    where processed_at is null
      and available_at <= clock_timestamp()
      and (claimed_at is null or claim_expires_at < clock_timestamp())
    order by available_at, created_at, id
    limit p_limit
    for update skip locked
  )
  update public.execution_outbox o
  set claimed_at = clock_timestamp(),
      claim_expires_at = clock_timestamp() + make_interval(secs => p_lease_seconds),
      claimed_by = p_worker_id,
      attempt_count = o.attempt_count + 1,
      lease_generation = o.lease_generation + 1
  from candidates c
  where o.id = c.id
  returning o.*;
end;
$func$;

-- Old ack signature must not remain callable.
drop function if exists public.paper_ack_outbox(uuid,text,text);

create function public.paper_ack_outbox(
  p_outbox_id uuid,
  p_worker_id text,
  p_lease_generation bigint,
  p_error text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $func$
declare
  v_row public.execution_outbox;
begin
  select * into v_row
  from public.execution_outbox
  where id = p_outbox_id
  for update;

  if not found then
    raise exception 'outbox_not_found';
  end if;

  if v_row.claimed_by is distinct from p_worker_id
     or v_row.lease_generation <> p_lease_generation then
    return jsonb_build_object(
      'acknowledged', false,
      'reason', 'STALE_LEASE',
      'processed', v_row.processed_at is not null,
      'lease_generation', v_row.lease_generation
    );
  end if;

  if v_row.processed_at is not null then
    return jsonb_build_object(
      'acknowledged', true,
      'idempotent_replay', true,
      'processed', true,
      'lease_generation', v_row.lease_generation
    );
  end if;

  if v_row.claim_expires_at is null or v_row.claim_expires_at < clock_timestamp() then
    return jsonb_build_object(
      'acknowledged', false,
      'reason', 'LEASE_EXPIRED',
      'processed', false,
      'lease_generation', v_row.lease_generation
    );
  end if;

  if p_error is null then
    update public.execution_outbox
    set processed_at = clock_timestamp(),
        last_error = null,
        claim_expires_at = null
    where id = p_outbox_id;
    return jsonb_build_object(
      'acknowledged', true,
      'idempotent_replay', false,
      'processed', true,
      'lease_generation', p_lease_generation
    );
  end if;

  update public.execution_outbox
  set last_error = p_error,
      claimed_at = null,
      claim_expires_at = null,
      claimed_by = null
  where id = p_outbox_id;

  return jsonb_build_object(
    'acknowledged', true,
    'released_for_retry', true,
    'processed', false,
    'lease_generation', p_lease_generation
  );
end;
$func$;

-- Risk reservation now has a structural expiry. A policy used for an entry
-- reservation must define max_signal_age_ms or max_order_age_ms.
create or replace function public.paper_reserve_risk(
  p_signal_id uuid,
  p_policy_version_id uuid,
  p_account_key text default 'paper-default',
  p_worker_id text default 'risk-engine',
  p_software_commit text default 'unknown'
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $func$
declare
  v_signal public.paper_signals;
  v_policy public.risk_policy_versions;
  v_state public.portfolio_risk_state;
  v_existing public.risk_decisions;
  v_ks public.kill_switch_state;
  v_active_count integer;
  v_pair_count integer;
  v_asset_reserved numeric(30,12);
  v_asset_used numeric(30,12);
  v_signed numeric(30,12);
  v_reason text := 'RISK_APPROVED';
  v_decision_id uuid;
  v_reservation_id uuid;
  v_reservation_ttl_ms bigint;
  v_expires_at timestamptz;
begin
  select * into v_signal from public.paper_signals where id = p_signal_id;
  if not found then raise exception 'signal_not_found'; end if;

  select * into v_existing
  from public.risk_decisions
  where signal_id = p_signal_id and decision_stage = 'PRETRADE';
  if found then
    return jsonb_build_object(
      'decision', v_existing.decision,
      'reason_code', v_existing.reason_code,
      'decision_id', v_existing.id,
      'idempotent_replay', true
    );
  end if;

  select * into v_policy from public.risk_policy_versions where id = p_policy_version_id;
  if not found then raise exception 'risk_policy_not_found'; end if;

  v_reservation_ttl_ms := coalesce(v_policy.max_signal_age_ms, v_policy.max_order_age_ms);
  if v_reservation_ttl_ms is null or v_reservation_ttl_ms <= 0 then
    raise exception 'risk_policy_missing_reservation_ttl';
  end if;
  v_expires_at := clock_timestamp() + (v_reservation_ttl_ms * interval '1 millisecond');

  insert into public.portfolio_risk_state(account_key)
  values (p_account_key)
  on conflict (account_key) do nothing;

  select * into v_state
  from public.portfolio_risk_state
  where account_key = p_account_key
  for update;

  select * into v_ks
  from public.kill_switch_state
  where account_key = p_account_key
  for update;

  if not found or v_ks.state <> 'RUNNING' then
    v_reason := 'RISK_REJECT_KILL_SWITCH';
  elsif v_state.critical_reconciliation then
    v_reason := 'RISK_REJECT_RECONCILIATION';
  elsif v_policy.max_position_notional is not null
        and v_signal.intended_notional > v_policy.max_position_notional then
    v_reason := 'RISK_REJECT_POSITION_NOTIONAL';
  end if;

  select count(*) into v_active_count
  from public.paper_positions
  where status in ('OPENING','OPEN','EXIT_PENDING','CLOSING');

  v_active_count := v_active_count + (
    select count(*) from public.risk_reservations
    where status in ('RESERVED','PARTIALLY_CONSUMED')
  );

  if v_reason = 'RISK_APPROVED'
     and v_policy.max_concurrent_positions is not null
     and v_active_count >= v_policy.max_concurrent_positions then
    v_reason := 'RISK_REJECT_MAX_CONCURRENT';
  end if;

  select count(*) into v_pair_count
  from public.paper_positions
  where strategy_version_id = v_signal.strategy_version_id
    and pair = v_signal.pair
    and status in ('OPENING','OPEN','EXIT_PENDING','CLOSING');

  v_pair_count := v_pair_count + (
    select count(*) from public.risk_reservations
    where strategy_version_id = v_signal.strategy_version_id
      and pair = v_signal.pair
      and status in ('RESERVED','PARTIALLY_CONSUMED')
  );

  if v_reason = 'RISK_APPROVED'
     and v_policy.max_open_per_strategy_pair is not null
     and v_pair_count >= v_policy.max_open_per_strategy_pair then
    v_reason := 'RISK_REJECT_MAX_OPEN_PAIR';
  end if;

  if v_reason = 'RISK_APPROVED'
     and v_policy.max_total_gross_exposure is not null
     and (v_state.used_gross_notional + v_state.reserved_gross_notional + v_signal.intended_notional)
         > v_policy.max_total_gross_exposure then
    v_reason := 'RISK_REJECT_GROSS_EXPOSURE';
  end if;

  v_signed := case when v_signal.side = 'LONG'
                   then v_signal.intended_notional
                   else -v_signal.intended_notional end;

  if v_reason = 'RISK_APPROVED'
     and v_policy.max_net_exposure is not null
     and abs(v_state.used_net_notional + v_state.reserved_net_notional + v_signed)
         > v_policy.max_net_exposure then
    v_reason := 'RISK_REJECT_NET_EXPOSURE';
  end if;

  select coalesce(sum(gross_entry_notional),0) into v_asset_used
  from public.paper_positions
  where pair = v_signal.pair
    and status in ('OPENING','OPEN','EXIT_PENDING','CLOSING');

  select coalesce(sum(reserved_notional - consumed_notional),0) into v_asset_reserved
  from public.risk_reservations
  where pair = v_signal.pair
    and status in ('RESERVED','PARTIALLY_CONSUMED');

  if v_reason = 'RISK_APPROVED'
     and v_policy.max_asset_exposure is not null
     and (v_asset_used + v_asset_reserved + v_signal.intended_notional)
         > v_policy.max_asset_exposure then
    v_reason := 'RISK_REJECT_ASSET_EXPOSURE';
  end if;

  if v_state.risk_utc_day <> (clock_timestamp() at time zone 'utc')::date then
    v_state.realized_pnl_utc_day := 0;
    v_state.risk_utc_day := (clock_timestamp() at time zone 'utc')::date;
  end if;

  if v_reason = 'RISK_APPROVED'
     and v_policy.max_daily_realized_loss is not null
     and v_state.realized_pnl_utc_day <= -v_policy.max_daily_realized_loss then
    v_reason := 'RISK_REJECT_DAILY_LOSS';
  end if;

  if v_reason = 'RISK_APPROVED'
     and v_policy.max_intraday_drawdown is not null
     and v_state.intraday_peak_equity is not null
     and v_state.internal_marked_equity is not null
     and v_state.intraday_peak_equity > 0
     and ((v_state.intraday_peak_equity - v_state.internal_marked_equity) / v_state.intraday_peak_equity)
         >= v_policy.max_intraday_drawdown then
    v_reason := 'RISK_REJECT_INTRADAY_DRAWDOWN';
  end if;

  insert into public.risk_decisions(
    signal_id, decision_stage, decision, policy_version_id,
    observed, reason_code, idempotency_key
  )
  values (
    p_signal_id, 'PRETRADE',
    case when v_reason = 'RISK_APPROVED' then 'APPROVED' else 'REJECTED' end,
    p_policy_version_id,
    jsonb_build_object(
      'used_gross_notional', v_state.used_gross_notional,
      'reserved_gross_notional', v_state.reserved_gross_notional,
      'used_net_notional', v_state.used_net_notional,
      'reserved_net_notional', v_state.reserved_net_notional,
      'realized_pnl_utc_day', v_state.realized_pnl_utc_day,
      'risk_utc_day', v_state.risk_utc_day
    ),
    v_reason,
    encode(extensions.digest(convert_to('RISK|' || p_signal_id::text || '|PRETRADE','UTF8'),'sha256'),'hex')
  )
  returning id into v_decision_id;

  if v_reason = 'RISK_APPROVED' then
    insert into public.risk_reservations(
      signal_id, strategy_version_id, pair, side,
      reserved_notional, consumed_notional, status, policy_version_id, expires_at
    )
    values (
      p_signal_id, v_signal.strategy_version_id, v_signal.pair, v_signal.side,
      v_signal.intended_notional, 0, 'RESERVED', p_policy_version_id, v_expires_at
    )
    returning id into v_reservation_id;

    update public.portfolio_risk_state
    set reserved_gross_notional = reserved_gross_notional + v_signal.intended_notional,
        reserved_net_notional = reserved_net_notional + v_signed,
        risk_utc_day = v_state.risk_utc_day,
        realized_pnl_utc_day = v_state.realized_pnl_utc_day,
        state_version = state_version + 1,
        updated_at = clock_timestamp()
    where account_key = p_account_key;
  else
    update public.portfolio_risk_state
    set risk_utc_day = v_state.risk_utc_day,
        realized_pnl_utc_day = v_state.realized_pnl_utc_day,
        state_version = state_version + 1,
        updated_at = clock_timestamp()
    where account_key = p_account_key;
  end if;

  perform public.paper_append_audit(
    'RISK_DECISION', 'signal', p_signal_id, v_signal.correlation_id,
    v_signal.strategy_version_id, v_signal.signal_close_time,
    clock_timestamp(), clock_timestamp(), null,
    jsonb_build_object('decision', case when v_reason='RISK_APPROVED' then 'APPROVED' else 'REJECTED' end,
                       'reason_code', v_reason,
                       'reservation_id', v_reservation_id,
                       'reservation_expires_at', case when v_reason='RISK_APPROVED' then v_expires_at else null end),
    null, v_reason, p_worker_id, p_software_commit, v_signal.correlation_id
  );

  return jsonb_build_object(
    'decision', case when v_reason='RISK_APPROVED' then 'APPROVED' else 'REJECTED' end,
    'reason_code', v_reason,
    'decision_id', v_decision_id,
    'reservation_id', v_reservation_id,
    'reservation_expires_at', case when v_reason='RISK_APPROVED' then v_expires_at else null end,
    'idempotent_replay', false
  );
end;
$func$;

-- Extend reservation expiry when an order is actually created.
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
  v_order public.paper_orders;
  v_key text;
  v_outbox_key text;
  v_from text;
begin
  if p_side not in ('BUY','SELL') then raise exception 'invalid_order_side'; end if;
  if p_intent_type not in ('ENTRY','EXIT','EMERGENCY_EXIT') then raise exception 'invalid_intent_type'; end if;
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

  select * into v_ks from public.kill_switch_state where account_key='paper-default' for update;
  if p_intent_type='ENTRY' and v_ks.state <> 'RUNNING' then
    raise exception 'new_entry_blocked_kill_switch:%', v_ks.state;
  end if;
  if p_intent_type <> 'ENTRY' and v_ks.state in ('HALTED','RECOVERY_PENDING') then
    raise exception 'new_order_blocked_kill_switch:%', v_ks.state;
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

  if p_intent_type='ENTRY' then
    update public.risk_reservations
    set expires_at = case
      when v_policy.max_order_age_ms is not null and v_policy.max_order_age_ms > 0
        then clock_timestamp() + (v_policy.max_order_age_ms * interval '1 millisecond')
      else expires_at
    end,
    state_version=state_version+1
    where signal_id=p_signal_id and status in ('RESERVED','PARTIALLY_CONSUMED');
  end if;

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

-- Old bind signature is intentionally removed so every bind carries a lease token.
drop function if exists public.paper_bind_execution_attempt(uuid,uuid,uuid,integer,text,text);

create function public.paper_bind_execution_attempt(
  p_outbox_id uuid,
  p_lease_generation bigint,
  p_order_id uuid,
  p_market_snapshot_id uuid,
  p_policy_version_id uuid,
  p_attempt_seq integer,
  p_worker_id text default 'execution-worker',
  p_software_commit text default 'unknown'
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $func$
declare
  v_outbox public.execution_outbox;
  v_order public.paper_orders;
  v_signal public.paper_signals;
  v_attempt public.paper_execution_attempts;
  v_ks public.kill_switch_state;
  v_key text;
  v_from text;
begin
  if p_attempt_seq < 1 then raise exception 'invalid_attempt_seq'; end if;

  select * into v_outbox
  from public.execution_outbox
  where id=p_outbox_id
  for update;

  if not found then raise exception 'outbox_not_found'; end if;

  if v_outbox.claimed_by is distinct from p_worker_id
     or v_outbox.lease_generation <> p_lease_generation
     or v_outbox.claim_expires_at is null
     or v_outbox.claim_expires_at < clock_timestamp() then
    return jsonb_build_object(
      'bound', false,
      'reason', 'STALE_LEASE',
      'lease_generation', v_outbox.lease_generation
    );
  end if;

  if v_outbox.processed_at is not null then
    return jsonb_build_object('bound', false, 'reason', 'OUTBOX_ALREADY_PROCESSED');
  end if;

  if v_outbox.event_type <> 'BIND_FILL_ATTEMPT'
     or v_outbox.entity_type <> 'order'
     or v_outbox.entity_id <> p_order_id then
    raise exception 'outbox_binding_mismatch';
  end if;

  select * into v_order from public.paper_orders where id=p_order_id for update;
  if not found then raise exception 'order_not_found'; end if;
  if v_order.status not in ('ACCEPTED','PENDING_FILL','PARTIALLY_FILLED') then
    raise exception 'order_state_disallows_attempt:%', v_order.status;
  end if;
  if v_order.cancel_requested_at is not null then
    raise exception 'attempt_blocked_after_cancel_request';
  end if;

  select * into v_ks from public.kill_switch_state where account_key='paper-default' for update;
  if v_ks.state in ('HALTED','RECOVERY_PENDING') then
    raise exception 'attempt_blocked_kill_switch:%', v_ks.state;
  end if;

  if not exists(select 1 from public.execution_market_snapshots where id=p_market_snapshot_id) then
    raise exception 'market_snapshot_not_found';
  end if;
  if not exists(select 1 from public.risk_policy_versions where id=p_policy_version_id) then
    raise exception 'policy_not_found';
  end if;

  v_key := encode(digest(convert_to(
    'FILL_ATTEMPT|' || p_order_id::text || '|' || p_attempt_seq::text,
    'UTF8'
  ), 'sha256'), 'hex');

  select * into v_attempt
  from public.paper_execution_attempts
  where idempotency_key=v_key
  for update;

  if found then
    if v_attempt.policy_version_id <> p_policy_version_id then
      raise exception 'attempt_policy_mismatch';
    end if;

    update public.paper_execution_attempts
    set source_outbox_id=p_outbox_id,
        source_lease_generation=p_lease_generation
    where id=v_attempt.id
    returning * into v_attempt;

    return jsonb_build_object(
      'execution_attempt_id', v_attempt.id,
      'market_snapshot_id', v_attempt.market_snapshot_id,
      'status', v_attempt.status,
      'idempotent_replay', true,
      'lease_generation', p_lease_generation
    );
  end if;

  insert into public.paper_execution_attempts(
    order_id, attempt_seq, market_snapshot_id, policy_version_id,
    status, idempotency_key, source_outbox_id, source_lease_generation
  )
  values (
    p_order_id, p_attempt_seq, p_market_snapshot_id, p_policy_version_id,
    'BOUND', v_key, p_outbox_id, p_lease_generation
  )
  returning * into v_attempt;

  if v_order.status='ACCEPTED' then
    v_from := v_order.status;
    update public.paper_orders
    set status='PENDING_FILL', pending_at=clock_timestamp(), state_version=state_version+1, updated_at=clock_timestamp()
    where id=p_order_id
    returning * into v_order;

    insert into public.paper_order_events(order_id, from_state, to_state, reason_code, payload)
    values (p_order_id, v_from, 'PENDING_FILL', 'EXECUTION_ATTEMPT_BOUND',
            jsonb_build_object('execution_attempt_id', v_attempt.id));
  end if;

  select * into v_signal from public.paper_signals where id=v_order.signal_id;

  perform public.paper_append_audit(
    'EXECUTION_ATTEMPT_BOUND', 'order', p_order_id, v_signal.correlation_id,
    v_order.strategy_version_id, v_signal.signal_close_time,
    clock_timestamp(), clock_timestamp(), null,
    jsonb_build_object(
      'execution_attempt_id', v_attempt.id,
      'snapshot_id', p_market_snapshot_id,
      'attempt_seq', p_attempt_seq,
      'outbox_id', p_outbox_id,
      'lease_generation', p_lease_generation
    ),
    null, 'EXECUTION_ATTEMPT_BOUND', p_worker_id, p_software_commit, v_signal.correlation_id
  );

  return jsonb_build_object(
    'execution_attempt_id', v_attempt.id,
    'market_snapshot_id', v_attempt.market_snapshot_id,
    'status', v_attempt.status,
    'idempotent_replay', false,
    'lease_generation', p_lease_generation
  );
end;
$func$;

-- Old apply signature is removed so economic effects require the active lease token.
drop function if exists public.paper_apply_fill(uuid,uuid,integer,numeric,numeric,numeric,numeric,numeric,timestamptz,text,text,text);

create function public.paper_apply_fill(
  p_outbox_id uuid,
  p_lease_generation bigint,
  p_order_id uuid,
  p_execution_attempt_id uuid,
  p_fill_seq integer,
  p_fill_quantity numeric,
  p_fill_price numeric,
  p_fee_amount numeric,
  p_spread_bps numeric,
  p_impact_bps numeric,
  p_filled_at timestamptz,
  p_account_key text default 'paper-default',
  p_worker_id text default 'fill-engine',
  p_software_commit text default 'unknown'
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $func$
declare
  v_outbox public.execution_outbox;
  v_order public.paper_orders;
  v_attempt public.paper_execution_attempts;
  v_snapshot public.execution_market_snapshots;
  v_policy public.risk_policy_versions;
  v_signal public.paper_signals;
  v_res public.risk_reservations;
  v_state public.portfolio_risk_state;
  v_ks public.kill_switch_state;
  v_pos public.paper_positions;
  v_fill_id uuid;
  v_fill_key text;
  v_fill_notional numeric(30,12);
  v_new_filled_notional numeric(30,12);
  v_new_status text;
  v_signed numeric(30,12);
  v_consume numeric(30,12);
  v_alloc_entry_fee numeric(30,12);
  v_realized numeric(30,12);
  v_before jsonb;
begin
  if p_fill_seq < 1 or p_fill_quantity <= 0 or p_fill_price <= 0 or p_fee_amount < 0 then
    raise exception 'invalid_fill_input';
  end if;

  v_fill_key := encode(
    extensions.digest(
      convert_to('FILL|' || p_order_id::text || '|' || p_fill_seq::text || '|' || p_execution_attempt_id::text, 'UTF8'),
      'sha256'
    ),
    'hex'
  );

  select * into v_outbox
  from public.execution_outbox
  where id=p_outbox_id
  for update;

  if not found then raise exception 'outbox_not_found'; end if;

  if v_outbox.claimed_by is distinct from p_worker_id
     or v_outbox.lease_generation <> p_lease_generation then
    return jsonb_build_object(
      'applied', false,
      'reason', 'STALE_LEASE',
      'lease_generation', v_outbox.lease_generation
    );
  end if;

  select id into v_fill_id
  from public.paper_fills
  where idempotency_key=v_fill_key;

  if found then
    return jsonb_build_object(
      'applied', false,
      'fill_id', v_fill_id,
      'idempotent_replay', true,
      'lease_generation', p_lease_generation
    );
  end if;

  if v_outbox.processed_at is not null then
    raise exception 'outbox_already_processed_without_matching_fill';
  end if;

  if v_outbox.claim_expires_at is null or v_outbox.claim_expires_at < clock_timestamp() then
    return jsonb_build_object(
      'applied', false,
      'reason', 'LEASE_EXPIRED',
      'lease_generation', v_outbox.lease_generation
    );
  end if;

  if v_outbox.event_type <> 'BIND_FILL_ATTEMPT'
     or v_outbox.entity_type <> 'order'
     or v_outbox.entity_id <> p_order_id then
    raise exception 'outbox_binding_mismatch';
  end if;

  select * into v_order from public.paper_orders where id = p_order_id for update;
  if not found then raise exception 'order_not_found'; end if;

  select * into v_ks from public.kill_switch_state where account_key = p_account_key for update;
  if not found then raise exception 'kill_switch_state_missing'; end if;
  if v_ks.state in ('HALTED','RECOVERY_PENDING') then
    raise exception 'fill_blocked_kill_switch:%', v_ks.state;
  end if;

  if v_order.status not in ('ACCEPTED','PENDING_FILL','PARTIALLY_FILLED') then
    raise exception 'order_state_disallows_fill:%', v_order.status;
  end if;

  if v_order.cancel_requested_at is not null then
    raise exception 'fill_blocked_after_cancel_request';
  end if;

  select * into v_attempt
  from public.paper_execution_attempts
  where id = p_execution_attempt_id
  for update;

  if not found or v_attempt.order_id <> p_order_id then
    raise exception 'execution_attempt_mismatch';
  end if;

  if v_attempt.source_outbox_id is distinct from p_outbox_id
     or v_attempt.source_lease_generation is distinct from p_lease_generation then
    return jsonb_build_object(
      'applied', false,
      'reason', 'STALE_ATTEMPT_LEASE',
      'attempt_lease_generation', v_attempt.source_lease_generation,
      'lease_generation', p_lease_generation
    );
  end if;

  if v_attempt.status = 'APPLIED' then
    raise exception 'attempt_applied_without_matching_fill';
  end if;
  if v_attempt.status <> 'BOUND' then
    raise exception 'execution_attempt_not_bound';
  end if;

  select * into v_snapshot
  from public.execution_market_snapshots
  where id = v_attempt.market_snapshot_id;
  if not found then raise exception 'market_snapshot_missing'; end if;

  select * into v_policy
  from public.risk_policy_versions
  where id = v_attempt.policy_version_id;
  if not found then raise exception 'risk_policy_missing'; end if;

  if v_policy.max_book_age_ms is not null
     and extract(epoch from (clock_timestamp() - v_snapshot.received_at)) * 1000 > v_policy.max_book_age_ms then
    raise exception 'stale_market_snapshot';
  end if;

  select * into v_signal from public.paper_signals where id = v_order.signal_id;
  if not found then raise exception 'signal_missing'; end if;

  if exists (
    select 1 from public.paper_fills
    where order_id = p_order_id and fill_seq = p_fill_seq
  ) then
    raise exception 'fill_seq_collision';
  end if;

  insert into public.portfolio_risk_state(account_key)
  values (p_account_key)
  on conflict (account_key) do nothing;

  select * into v_state
  from public.portfolio_risk_state
  where account_key = p_account_key
  for update;

  select * into v_res
  from public.risk_reservations
  where signal_id = v_order.signal_id
  for update;

  v_fill_notional := p_fill_quantity * p_fill_price;
  v_new_filled_notional := v_order.filled_notional + v_fill_notional;
  v_new_status := case
    when v_new_filled_notional + 0.00000001 >= v_order.intended_notional then 'FILLED'
    else 'PARTIALLY_FILLED'
  end;

  if v_new_status = 'PARTIALLY_FILLED' and not v_policy.partial_fill_allowed then
    raise exception 'partial_fill_not_allowed';
  end if;

  if v_order.intent_type='ENTRY' then
    if v_res.id is null then
      raise exception 'entry_fill_without_risk_reservation';
    end if;
    if v_fill_notional > (v_res.reserved_notional - v_res.consumed_notional) + 0.00000001 then
      raise exception 'fill_exceeds_reserved_notional';
    end if;
  end if;

  insert into public.paper_fills(
    order_id, execution_attempt_id, strategy_version_id, pair,
    fill_seq, fill_quantity, fill_price, fee_amount,
    spread_bps, impact_bps, filled_at, idempotency_key
  )
  values (
    p_order_id, p_execution_attempt_id, v_order.strategy_version_id, v_order.pair,
    p_fill_seq, p_fill_quantity, p_fill_price, p_fee_amount,
    p_spread_bps, p_impact_bps, p_filled_at, v_fill_key
  )
  returning id into v_fill_id;

  update public.paper_orders
  set filled_quantity = filled_quantity + p_fill_quantity,
      filled_notional = v_new_filled_notional,
      status = v_new_status,
      terminal_at = case when v_new_status='FILLED' then clock_timestamp() else terminal_at end,
      state_version = state_version + 1,
      updated_at = clock_timestamp()
  where id = p_order_id;

  insert into public.paper_order_events(order_id, from_state, to_state, reason_code, payload)
  values (
    p_order_id, v_order.status, v_new_status, 'FILL_APPLIED',
    jsonb_build_object('fill_id', v_fill_id, 'fill_seq', p_fill_seq, 'execution_attempt_id', p_execution_attempt_id)
  );

  select * into v_pos
  from public.paper_positions
  where strategy_version_id = v_order.strategy_version_id
    and pair = v_order.pair
    and status in ('OPENING','OPEN','EXIT_PENDING','CLOSING')
  for update;

  if v_order.intent_type = 'ENTRY' then
    if v_pos.id is null then
      insert into public.paper_positions(
        strategy_version_id, pair, side, quantity, average_entry_price,
        gross_entry_notional, cumulative_fees, entry_fees, remaining_entry_fees,
        cumulative_execution_cost, realized_pnl, status, opened_at
      )
      values (
        v_order.strategy_version_id, v_order.pair,
        case when v_order.side='BUY' then 'LONG' else 'SHORT' end,
        p_fill_quantity, p_fill_price, v_fill_notional,
        p_fee_amount, p_fee_amount, p_fee_amount,
        abs(p_impact_bps) * v_fill_notional / 10000,
        0, 'OPEN', p_filled_at
      )
      returning * into v_pos;

      insert into public.paper_position_events(position_id, event_type, state_before, state_after)
      values (v_pos.id, 'POSITION_OPENED', null, to_jsonb(v_pos));
    else
      if (v_pos.side='LONG' and v_order.side <> 'BUY')
         or (v_pos.side='SHORT' and v_order.side <> 'SELL') then
        raise exception 'entry_side_conflicts_with_position';
      end if;

      v_before := to_jsonb(v_pos);

      update public.paper_positions
      set average_entry_price =
            ((quantity * average_entry_price) + (p_fill_quantity * p_fill_price))
            / (quantity + p_fill_quantity),
          quantity = quantity + p_fill_quantity,
          gross_entry_notional = gross_entry_notional + v_fill_notional,
          cumulative_fees = cumulative_fees + p_fee_amount,
          entry_fees = entry_fees + p_fee_amount,
          remaining_entry_fees = remaining_entry_fees + p_fee_amount,
          cumulative_execution_cost = cumulative_execution_cost + abs(p_impact_bps) * v_fill_notional / 10000,
          state_version = state_version + 1,
          updated_at = clock_timestamp()
      where id = v_pos.id
      returning * into v_pos;

      insert into public.paper_position_events(position_id, event_type, state_before, state_after)
      values (v_pos.id, 'ENTRY_FILL_APPLIED', v_before, to_jsonb(v_pos));
    end if;

    v_consume := v_fill_notional;
    v_signed := case when v_res.side='LONG' then v_consume else -v_consume end;

    update public.risk_reservations
    set consumed_notional = consumed_notional + v_consume,
        status = case
          when consumed_notional + v_consume + 0.00000001 >= reserved_notional then 'CONSUMED'
          else 'PARTIALLY_CONSUMED'
        end,
        consumed_at = clock_timestamp(),
        state_version = state_version + 1
    where id = v_res.id;

    update public.portfolio_risk_state
    set reserved_gross_notional = greatest(0, reserved_gross_notional - v_consume),
        used_gross_notional = used_gross_notional + v_consume,
        reserved_net_notional = reserved_net_notional - v_signed,
        used_net_notional = used_net_notional + v_signed,
        state_version = state_version + 1,
        updated_at = clock_timestamp()
    where account_key = p_account_key;

  else
    if v_pos.id is null then raise exception 'exit_fill_without_position'; end if;
    if p_fill_quantity > v_pos.quantity + 0.00000001 then raise exception 'exit_fill_exceeds_position'; end if;
    if (v_pos.side='LONG' and v_order.side <> 'SELL')
       or (v_pos.side='SHORT' and v_order.side <> 'BUY') then
      raise exception 'exit_side_conflicts_with_position';
    end if;

    v_before := to_jsonb(v_pos);
    v_alloc_entry_fee := case
      when v_pos.quantity > 0 then v_pos.remaining_entry_fees * p_fill_quantity / v_pos.quantity
      else 0
    end;

    v_realized := case when v_pos.side='LONG'
      then (p_fill_price - v_pos.average_entry_price) * p_fill_quantity - v_alloc_entry_fee - p_fee_amount
      else (v_pos.average_entry_price - p_fill_price) * p_fill_quantity - v_alloc_entry_fee - p_fee_amount
    end;

    v_consume := case when v_pos.quantity > 0
      then v_pos.gross_entry_notional * p_fill_quantity / v_pos.quantity
      else 0
    end;

    v_signed := case when v_pos.side='LONG' then v_consume else -v_consume end;

    update public.paper_positions
    set quantity = greatest(0, quantity - p_fill_quantity),
        gross_entry_notional = greatest(0, gross_entry_notional - v_consume),
        cumulative_fees = cumulative_fees + p_fee_amount,
        exit_fees = exit_fees + p_fee_amount,
        remaining_entry_fees = greatest(0, remaining_entry_fees - v_alloc_entry_fee),
        cumulative_execution_cost = cumulative_execution_cost + abs(p_impact_bps) * v_fill_notional / 10000,
        realized_pnl = realized_pnl + v_realized,
        status = case when quantity - p_fill_quantity <= 0.00000001 then 'CLOSED' else 'OPEN' end,
        closed_at = case when quantity - p_fill_quantity <= 0.00000001 then p_filled_at else closed_at end,
        state_version = state_version + 1,
        updated_at = clock_timestamp()
    where id = v_pos.id
    returning * into v_pos;

    insert into public.paper_position_events(position_id, event_type, state_before, state_after)
    values (v_pos.id, 'EXIT_FILL_APPLIED', v_before, to_jsonb(v_pos));

    if v_state.risk_utc_day <> (p_filled_at at time zone 'utc')::date then
      v_state.realized_pnl_utc_day := 0;
      v_state.risk_utc_day := (p_filled_at at time zone 'utc')::date;
    end if;

    update public.portfolio_risk_state
    set used_gross_notional = greatest(0, used_gross_notional - v_consume),
        used_net_notional = used_net_notional - v_signed,
        realized_pnl_utc_day = v_state.realized_pnl_utc_day + v_realized,
        risk_utc_day = v_state.risk_utc_day,
        state_version = state_version + 1,
        updated_at = clock_timestamp()
    where account_key = p_account_key;
  end if;

  update public.paper_execution_attempts
  set status = 'APPLIED',
      applied_at = clock_timestamp()
  where id = p_execution_attempt_id;

  if v_new_status = 'PARTIALLY_FILLED' then
    insert into public.execution_outbox(
      event_type, entity_type, entity_id, correlation_id, payload, idempotency_key
    )
    values (
      'BIND_FILL_ATTEMPT', 'order', p_order_id, v_signal.correlation_id,
      jsonb_build_object(
        'order_id', p_order_id,
        'attempt_seq', v_attempt.attempt_seq + 1,
        'remaining_notional', greatest(0, v_order.intended_notional - v_new_filled_notional)
      ),
      encode(extensions.digest(convert_to(
        'OUTBOX|BIND_FILL_ATTEMPT|order|' || p_order_id::text || '|attempt:' || (v_attempt.attempt_seq + 1)::text,
        'UTF8'
      ), 'sha256'), 'hex')
    )
    on conflict (idempotency_key) do nothing;
  end if;

  perform public.paper_append_audit(
    'FILL_APPLIED', 'order', p_order_id, v_signal.correlation_id,
    v_order.strategy_version_id, v_signal.signal_close_time,
    clock_timestamp(), clock_timestamp(),
    to_jsonb(v_order),
    jsonb_build_object('fill_id', v_fill_id, 'order_status', v_new_status, 'position_id', v_pos.id),
    v_snapshot.snapshot_hash, 'FILL_APPLIED', p_worker_id, p_software_commit, v_signal.correlation_id
  );

  return jsonb_build_object(
    'applied', true,
    'fill_id', v_fill_id,
    'order_status', v_new_status,
    'position_id', v_pos.id,
    'realized_pnl_delta', coalesce(v_realized, 0),
    'idempotent_replay', false,
    'lease_generation', p_lease_generation
  );
end;
$func$;

-- Detection-only primitive for future reconciliation worker.
create or replace function public.paper_find_stale_reservations(p_limit integer default 100)
returns setof public.risk_reservations
language sql
security definer
set search_path = public
as $func$
  select r.*
  from public.risk_reservations r
  where r.status in ('RESERVED','PARTIALLY_CONSUMED')
    and r.expires_at is not null
    and r.expires_at < clock_timestamp()
  order by r.expires_at, r.id
  limit greatest(1, least(p_limit, 1000));
$func$;

-- Explicit, idempotent recovery action. Reconciliation itself never mutates state.
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
  select * into v_res
  from public.risk_reservations
  where id=p_reservation_id
  for update;

  if not found then raise exception 'reservation_not_found'; end if;

  if v_res.status not in ('RESERVED','PARTIALLY_CONSUMED') then
    return jsonb_build_object(
      'released', false,
      'idempotent_replay', true,
      'status', v_res.status
    );
  end if;

  if v_res.expires_at is null or v_res.expires_at >= clock_timestamp() then
    return jsonb_build_object('released', false, 'reason', 'RESERVATION_NOT_STALE');
  end if;

  if exists (
    select 1
    from public.paper_orders o
    where o.signal_id=v_res.signal_id
      and o.status in ('CREATED','ACCEPTED','PENDING_FILL','PARTIALLY_FILLED','CANCEL_REQUESTED')
  ) then
    return jsonb_build_object('released', false, 'reason', 'ACTIVE_ORDER_EXISTS');
  end if;

  select * into v_signal from public.paper_signals where id=v_res.signal_id;
  if not found then raise exception 'signal_not_found'; end if;

  select * into v_state
  from public.portfolio_risk_state
  where account_key=p_account_key
  for update;

  v_remaining := greatest(0, v_res.reserved_notional-v_res.consumed_notional);
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
    issue_type,severity,entity_type,entity_id,details,resolved_at
  )
  values(
    'STALE_RISK_RESERVATION','WARNING','risk_reservation',v_res.id,
    jsonb_build_object('expired_at',v_res.expires_at,'remaining_notional',v_remaining),
    clock_timestamp()
  )
  returning id into v_issue_id;

  v_action_key := encode(
    extensions.digest(
      convert_to('RECOVERY|' || v_issue_id::text || '|RELEASE_STALE_RESERVATION','UTF8'),
      'sha256'
    ),
    'hex'
  );

  insert into public.recovery_actions(
    reconciliation_issue_id,repair_type,status,state_before,state_after,
    idempotency_key,applied_at
  )
  values(
    v_issue_id,'RELEASE_STALE_RESERVATION','APPLIED',
    to_jsonb(v_res),
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
    'released', true,
    'remaining_released', v_remaining,
    'reconciliation_issue_id', v_issue_id
  );
end;
$func$;

-- SECURITY DEFINER functions are internal-only.
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
    execute format('revoke all on function %s from PUBLIC, anon, authenticated', r.signature);
    execute format('grant execute on function %s to service_role', r.signature);
  end loop;
end;
$revoke$;
