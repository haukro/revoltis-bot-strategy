-- Synced from live Supabase migration 20260925092710 (spec006_recovery_hardening).
-- IMPLEMENTATION-SPEC-006. Runtime B remains disabled.

-- SPEC-006 hardening: recovery lifecycle fencing and hidden-cycle INVALID invariant.

create or replace function public.paper_spec006_shadow_cycle_guard()
returns trigger
language plpgsql
set search_path=public
as $func$
begin
  if new.reference_position_state in ('LONG','SHORT')
     and new.reference_entry_action_id is null then
    new.data_state:='INVALID';
    new.invalid_reason:=coalesce(new.invalid_reason,'FRONTIER_HIDDEN_CYCLE');
    new.invalid_at_5m_open_time:=coalesce(new.invalid_at_5m_open_time,new.last_processed_5m_open_time);
  end if;
  return new;
end;
$func$;

drop trigger if exists paper_spec006_shadow_cycle_guard_trg
on public.paper_strategy_shadow_state;
create trigger paper_spec006_shadow_cycle_guard_trg
before insert or update on public.paper_strategy_shadow_state
for each row execute function public.paper_spec006_shadow_cycle_guard();

create or replace function public.paper_spec006_release_lifecycle_reservation(
  p_entry_action_id uuid,
  p_reason text
)
returns boolean
language plpgsql
security definer
set search_path=public
as $func$
declare
  v_life public.paper_entry_lifecycles;
  v_res public.risk_reservations;
  v_state public.portfolio_risk_state;
  v_remaining numeric(30,12);
  v_signed numeric(30,12);
begin
  select * into v_life
  from public.paper_entry_lifecycles
  where entry_action_id=p_entry_action_id
  for update;
  if not found or v_life.reservation_id is null then return true; end if;

  select * into v_state
  from public.portfolio_risk_state
  where account_key='paper-default'
  for update;

  select * into v_res
  from public.risk_reservations
  where id=v_life.reservation_id
  for update;

  if not found or v_res.status not in ('RESERVED','PARTIALLY_CONSUMED') then
    return true;
  end if;

  v_remaining:=greatest(0,v_res.reserved_notional-v_res.consumed_notional);
  v_signed:=case when v_res.side='LONG' then v_remaining else -v_remaining end;

  update public.risk_reservations
  set status='RELEASED',
      released_at=clock_timestamp(),
      release_reason=p_reason,
      state_version=state_version+1
  where id=v_res.id;

  if v_state.account_key is not null then
    update public.portfolio_risk_state
    set reserved_gross_notional=greatest(0,reserved_gross_notional-v_remaining),
        reserved_net_notional=reserved_net_notional-v_signed,
        state_version=state_version+1,
        updated_at=clock_timestamp()
    where account_key='paper-default';
  end if;

  return true;
end;
$func$;

create or replace function public.paper_spec006_fence_entry_lifecycle(
  p_entry_action_id uuid,
  p_reason text,
  p_worker_id text,
  p_software_commit text
)
returns boolean
language plpgsql
security definer
set search_path=public
as $func$
declare
  v_life public.paper_entry_lifecycles;
  v_order public.paper_orders;
  v_has_fill boolean;
  v_active_lease boolean;
begin
  select * into v_life
  from public.paper_entry_lifecycles
  where entry_action_id=p_entry_action_id
  for update;
  if not found then return false; end if;

  if v_life.paper_entry_order_id is not null then
    select exists(
      select 1
      from public.execution_outbox x
      where x.entity_type='order'
        and x.entity_id=v_life.paper_entry_order_id
        and x.event_type='BIND_FILL_ATTEMPT'
        and x.processed_at is null
        and x.claimed_by is not null
        and x.claim_expires_at>clock_timestamp()
    ) into v_active_lease;
    if v_active_lease then return false; end if;
  end if;

  perform public.paper_spec006_release_lifecycle_reservation(
    p_entry_action_id,p_reason
  );

  if v_life.status in ('TERMINAL_FILLED','TERMINAL_NO_FILL','NEVER_CREATED_FENCED','TERMINAL_REJECTED') then
    return public.paper_spec006_entry_safe_terminal(p_entry_action_id);
  end if;

  if v_life.paper_entry_order_id is null then
    update public.paper_entry_lifecycles
    set status='NEVER_CREATED_FENCED',
        last_error_code=p_reason,
        state_version=state_version+1,
        updated_at=clock_timestamp()
    where entry_action_id=p_entry_action_id;
    return public.paper_spec006_entry_safe_terminal(p_entry_action_id);
  end if;

  select * into v_order
  from public.paper_orders
  where id=v_life.paper_entry_order_id
  for update;
  if not found then return false; end if;

  update public.execution_outbox
  set processed_at=coalesce(processed_at,clock_timestamp()),
      last_error=coalesce(last_error,p_reason)
  where entity_type='order'
    and entity_id=v_order.id
    and event_type='BIND_FILL_ATTEMPT'
    and processed_at is null
    and (claimed_by is null or claim_expires_at<=clock_timestamp());

  if v_order.status not in ('FILLED','CANCELLED','REJECTED','EXPIRED','FAILED') then
    perform public.paper_terminalize_order(
      v_order.id,'FAILED',p_reason,p_worker_id,p_software_commit,'paper-default'
    );
  end if;

  select exists(
    select 1 from public.paper_fills where order_id=v_order.id
  ) into v_has_fill;

  update public.paper_entry_lifecycles
  set status=case when v_has_fill then 'TERMINAL_FILLED' else 'TERMINAL_REJECTED' end,
      last_error_code=p_reason,
      state_version=state_version+1,
      updated_at=clock_timestamp()
  where entry_action_id=p_entry_action_id;

  return public.paper_spec006_entry_safe_terminal(p_entry_action_id);
end;
$func$;

create or replace function public.paper_spec006_fence_all_entry_lifecycles(
  p_strategy_version_id text,
  p_pair text,
  p_reason text,
  p_worker_id text,
  p_software_commit text
)
returns boolean
language plpgsql
security definer
set search_path=public
as $func$
declare
  r record;
  v_safe boolean;
begin
  for r in
    select entry_action_id
    from public.paper_entry_lifecycles
    where strategy_version_id=p_strategy_version_id
      and pair=upper(p_pair)
    order by created_at,entry_action_id
  loop
    v_safe:=public.paper_spec006_fence_entry_lifecycle(
      r.entry_action_id,p_reason,p_worker_id,p_software_commit
    );
    if not v_safe then return false; end if;
  end loop;
  return true;
end;
$func$;

create or replace function public.paper_spec006_queue_exit_attempt(
  p_exit_intent_id uuid,
  p_software_commit text
)
returns jsonb
language plpgsql
security definer
set search_path=public,extensions
as $func$
declare
  v_intent public.paper_exit_intents;
  v_pos public.paper_positions;
  v_owner public.paper_position_entry_ownership;
  v_life public.paper_entry_lifecycles;
  v_order public.paper_orders;
  v_signal public.paper_signals;
  v_ks public.kill_switch_state;
  v_fence public.paper_strategy_execution_fence;
  v_safe boolean;
  v_seq integer;
  v_key text;
  v_outbox_key text;
  v_existing_claim uuid;
begin
  select * into v_intent
  from public.paper_exit_intents
  where id=p_exit_intent_id;
  if not found or v_intent.intent_origin not in ('REFERENCE_EXIT','INVALID_RECOVERY') then
    raise exception 'flatten_intent_not_found';
  end if;

  select * into v_fence
  from public.paper_strategy_execution_fence
  where strategy_version_id=v_intent.strategy_version_id
    and pair=v_intent.pair
  for update;
  if not found then raise exception 'spec006_fence_missing'; end if;

  select * into v_pos
  from public.paper_positions
  where strategy_version_id=v_intent.strategy_version_id
    and pair=v_intent.pair
    and status in ('OPENING','OPEN','EXIT_PENDING','CLOSING')
  for update;

  select * into v_intent
  from public.paper_exit_intents
  where id=p_exit_intent_id
  for update;

  select * into v_ks
  from public.kill_switch_state
  where account_key='paper-default'
  for update;

  if v_ks.state in ('HALTED','RECOVERY_PENDING') then
    update public.paper_exit_intents
    set status='PAUSED',state_version=state_version+1,updated_at=clock_timestamp()
    where id=v_intent.id;
    return jsonb_build_object('queued',false,'reason','PAUSED_KILL_SWITCH');
  end if;

  if v_intent.status='PAUSED' then
    update public.paper_exit_intents
    set status='OPEN',state_version=state_version+1,updated_at=clock_timestamp()
    where id=v_intent.id;
    v_intent.status:='OPEN';
  end if;

  if v_intent.status not in ('OPEN','PAUSED') then
    return jsonb_build_object('queued',false,'reason','INTENT_NOT_LIVE','status',v_intent.status);
  end if;

  if v_intent.intent_origin='REFERENCE_EXIT' then
    v_safe:=public.paper_spec006_fence_entry_lifecycle(
      v_intent.linked_entry_action_id,
      'EXIT_INTENT_FENCE',
      'spec006-flatten',
      p_software_commit
    );
  else
    v_safe:=public.paper_spec006_fence_all_entry_lifecycles(
      v_intent.strategy_version_id,
      v_intent.pair,
      'INVALID_RECOVERY_FENCE',
      'spec006-flatten',
      p_software_commit
    );
  end if;

  if not v_safe then
    return jsonb_build_object('queued',false,'reason','WAIT_ENTRY_IN_FLIGHT');
  end if;

  if v_pos.id is null then
    if v_intent.intent_origin='REFERENCE_EXIT'
       and not public.paper_spec006_entry_safe_terminal(v_intent.linked_entry_action_id) then
      return jsonb_build_object('queued',false,'reason','ENTRY_NOT_SAFE_TERMINAL');
    end if;

    if v_intent.intent_origin='INVALID_RECOVERY'
       and exists(
         select 1
         from public.paper_entry_lifecycles l
         where l.strategy_version_id=v_intent.strategy_version_id
           and l.pair=v_intent.pair
           and not public.paper_spec006_entry_safe_terminal(l.entry_action_id)
       ) then
      return jsonb_build_object('queued',false,'reason','ENTRY_NOT_SAFE_TERMINAL');
    end if;

    update public.paper_exit_intents
    set status='SATISFIED',
        satisfied_at=clock_timestamp(),
        state_version=state_version+1,
        updated_at=clock_timestamp()
    where id=v_intent.id;

    return jsonb_build_object('queued',false,'reason','SATISFIED_NO_POSITION');
  end if;

  select * into v_owner
  from public.paper_position_entry_ownership
  where position_id=v_pos.id;

  if v_intent.intent_origin='REFERENCE_EXIT'
     and (v_owner.entry_action_id is null or v_owner.entry_action_id<>v_intent.linked_entry_action_id) then
    select id into v_existing_claim
    from public.paper_exit_intents
    where claimed_position_id=v_pos.id
      and status in ('OPEN','PAUSED','CRITICAL')
      and id<>v_intent.id
    limit 1;

    if v_existing_claim is not null then
      return jsonb_build_object('queued',false,'reason','WAIT_OTHER_OWNER');
    end if;

    update public.paper_exit_intents
    set claimed_position_id=v_pos.id,
        status='CRITICAL',
        last_error_code='CROSS_CYCLE_POSITION',
        state_version=state_version+1,
        updated_at=clock_timestamp()
    where id=v_intent.id;

    return jsonb_build_object('queued',false,'reason','CRITICAL_CROSS_CYCLE');
  end if;

  select id into v_existing_claim
  from public.paper_exit_intents
  where claimed_position_id=v_pos.id
    and status in ('OPEN','PAUSED','CRITICAL')
    and id<>v_intent.id
  limit 1;

  if v_existing_claim is not null then
    return jsonb_build_object('queued',false,'reason','WAIT_OTHER_OWNER');
  end if;

  update public.paper_exit_intents
  set claimed_position_id=v_pos.id,
      state_version=state_version+1,
      updated_at=clock_timestamp()
  where id=v_intent.id;

  if (v_pos.side='LONG' and v_intent.locked_reduce_side<>'SELL')
     or (v_pos.side='SHORT' and v_intent.locked_reduce_side<>'BUY') then
    update public.paper_exit_intents
    set status='CRITICAL',
        last_error_code='REDUCE_SIDE_MISMATCH',
        state_version=state_version+1,
        updated_at=clock_timestamp()
    where id=v_intent.id;
    return jsonb_build_object('queued',false,'reason','CRITICAL_SIDE_MISMATCH');
  end if;

  if v_intent.paper_exit_order_id is null then
    if v_intent.intent_origin='REFERENCE_EXIT' then
      select * into v_life
      from public.paper_entry_lifecycles
      where entry_action_id=v_intent.linked_entry_action_id;
      if v_life.paper_signal_id is null then raise exception 'linked_entry_signal_missing'; end if;
      select * into v_signal from public.paper_signals where id=v_life.paper_signal_id;
    else
      select s.* into v_signal
      from public.paper_signals s
      join public.paper_entry_lifecycles l on l.paper_signal_id=s.id
      join public.paper_position_entry_ownership o on o.entry_action_id=l.entry_action_id
      where o.position_id=v_pos.id
      limit 1;
    end if;

    if v_signal.id is null then raise exception 'position_signal_missing'; end if;

    v_key:=encode(
      digest(convert_to('ORDER|EXIT_INTENT|'||v_intent.id::text||'|MARKET','UTF8'),'sha256'),
      'hex'
    );

    insert into public.paper_orders(
      signal_id,strategy_version_id,pair,side,intent_type,order_type,
      intended_notional,intended_quantity,status,idempotency_key,accepted_at,exit_intent_id
    )
    values(
      v_signal.id,v_intent.strategy_version_id,v_intent.pair,
      v_intent.locked_reduce_side,'EXIT','MARKET',
      greatest(v_pos.quantity*v_pos.average_entry_price,0.00000001),
      v_pos.quantity,'ACCEPTED',v_key,clock_timestamp(),v_intent.id
    )
    on conflict(exit_intent_id) where exit_intent_id is not null
    do update set exit_intent_id=excluded.exit_intent_id
    returning * into v_order;

    insert into public.paper_order_events(order_id,from_state,to_state,reason_code)
    values(v_order.id,null,'ACCEPTED','SPEC006_REDUCE_ONLY_EXIT');

    update public.paper_exit_intents
    set paper_exit_order_id=v_order.id,
        state_version=state_version+1,
        updated_at=clock_timestamp()
    where id=v_intent.id;
  else
    select * into v_order
    from public.paper_orders
    where id=v_intent.paper_exit_order_id
    for update;
  end if;

  if exists(
    select 1
    from public.execution_outbox
    where event_type='BIND_FILL_ATTEMPT'
      and entity_type='order'
      and entity_id=v_order.id
      and processed_at is null
  ) then
    return jsonb_build_object(
      'queued',false,'reason','ATTEMPT_ALREADY_QUEUED','order_id',v_order.id
    );
  end if;

  select coalesce(max(attempt_seq),0)+1 into v_seq
  from public.paper_execution_attempts
  where order_id=v_order.id;

  v_outbox_key:=encode(
    digest(
      convert_to(
        'OUTBOX|BIND_FILL_ATTEMPT|order|'||v_order.id::text||'|attempt:'||v_seq,
        'UTF8'
      ),
      'sha256'
    ),
    'hex'
  );

  insert into public.execution_outbox(
    event_type,entity_type,entity_id,correlation_id,payload,idempotency_key
  )
  values(
    'BIND_FILL_ATTEMPT','order',v_order.id,v_signal.correlation_id,
    jsonb_build_object(
      'order_id',v_order.id,
      'attempt_seq',v_seq,
      'spec006_exit_intent_id',v_intent.id
    ),
    v_outbox_key
  )
  on conflict(idempotency_key) do nothing;

  return jsonb_build_object(
    'queued',true,
    'order_id',v_order.id,
    'attempt_seq',v_seq,
    'remaining_base_qty',v_pos.quantity
  );
end;
$func$;

revoke execute on function public.paper_spec006_shadow_cycle_guard()
from public,anon,authenticated;
revoke execute on function public.paper_spec006_release_lifecycle_reservation(uuid,text)
from public,anon,authenticated;
revoke execute on function public.paper_spec006_fence_all_entry_lifecycles(text,text,text,text,text)
from public,anon,authenticated;

