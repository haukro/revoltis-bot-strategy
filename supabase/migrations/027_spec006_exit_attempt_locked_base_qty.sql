-- Synced from live Supabase migration 20260925102625 (spec006_exit_attempt_locked_base_qty).
-- IMPLEMENTATION-SPEC-006. Runtime B remains disabled.

CREATE OR REPLACE FUNCTION public.paper_spec006_queue_exit_attempt(p_exit_intent_id uuid, p_software_commit text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'extensions'
AS $function$
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
      'spec006_exit_intent_id',v_intent.id,
      'remaining_base_qty',v_pos.quantity
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
$function$

