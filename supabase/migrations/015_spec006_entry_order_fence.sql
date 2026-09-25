-- SPEC-006 ENTRY order creation fence and reservation cleanup.

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
  v_res public.risk_reservations;
  v_state public.portfolio_risk_state;
  v_has_fill boolean;
  v_active_lease boolean;
  v_remaining numeric(30,12);
  v_signed numeric(30,12);
begin
  select * into v_life
  from public.paper_entry_lifecycles
  where entry_action_id=p_entry_action_id
  for update;

  if not found then return false; end if;

  if v_life.status in ('TERMINAL_FILLED','TERMINAL_NO_FILL','NEVER_CREATED_FENCED','TERMINAL_REJECTED') then
    select exists(
      select 1
      from public.paper_execution_attempts a
      join public.execution_outbox x on x.id=a.source_outbox_id
      where a.order_id=v_life.paper_entry_order_id
        and a.status='BOUND'
        and x.processed_at is null
        and x.claim_expires_at>clock_timestamp()
    ) into v_active_lease;
    return not coalesce(v_active_lease,false);
  end if;

  if v_life.paper_entry_order_id is null then
    if v_life.reservation_id is not null then
      select * into v_state
      from public.portfolio_risk_state
      where account_key='paper-default'
      for update;

      select * into v_res
      from public.risk_reservations
      where id=v_life.reservation_id
      for update;

      if found and v_res.status in ('RESERVED','PARTIALLY_CONSUMED') then
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
      end if;
    end if;

    update public.paper_entry_lifecycles
    set status='NEVER_CREATED_FENCED',
        last_error_code=p_reason,
        state_version=state_version+1,
        updated_at=clock_timestamp()
    where entry_action_id=p_entry_action_id;
    return true;
  end if;

  select * into v_order
  from public.paper_orders
  where id=v_life.paper_entry_order_id
  for update;

  if not found then return false; end if;

  select exists(
    select 1
    from public.execution_outbox x
    where x.entity_type='order'
      and x.entity_id=v_order.id
      and x.event_type='BIND_FILL_ATTEMPT'
      and x.processed_at is null
      and x.claimed_by is not null
      and x.claim_expires_at>clock_timestamp()
  ) into v_active_lease;

  if v_active_lease then return false; end if;

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

  return true;
end;
$func$;

create or replace function public.paper_spec006_create_entry_order_from_approved_signal(
  p_signal_id uuid,
  p_worker_id text default 'spec006-order-manager',
  p_software_commit text default 'unknown'
)
returns jsonb
language plpgsql
security definer
set search_path=public
as $func$
declare
  v_signal public.paper_signals;
  v_life public.paper_entry_lifecycles;
  v_fence public.paper_strategy_execution_fence;
  v_pos public.paper_positions;
  v_claim public.paper_exit_intents;
  v_result jsonb;
  v_safe boolean;
begin
  select * into v_signal
  from public.paper_signals
  where id=p_signal_id;

  if not found then raise exception 'signal_not_found'; end if;

  select * into v_life
  from public.paper_entry_lifecycles
  where paper_signal_id=p_signal_id;

  if not found then raise exception 'not_spec006_signal'; end if;

  select * into v_fence
  from public.paper_strategy_execution_fence
  where strategy_version_id=v_signal.strategy_version_id
    and pair=v_signal.pair
  for update;

  if not found then raise exception 'spec006_fence_missing'; end if;

  select * into v_pos
  from public.paper_positions
  where strategy_version_id=v_signal.strategy_version_id
    and pair=v_signal.pair
    and status in ('OPENING','OPEN','EXIT_PENDING','CLOSING')
  for update;

  select * into v_life
  from public.paper_entry_lifecycles
  where paper_signal_id=p_signal_id
  for update;

  if v_life.status in ('TERMINAL_FILLED','TERMINAL_NO_FILL','NEVER_CREATED_FENCED','TERMINAL_REJECTED') then
    return jsonb_build_object(
      'created',false,'reason','ENTRY_LIFECYCLE_TERMINAL','status',v_life.status
    );
  end if;

  if exists(
    select 1
    from public.paper_exit_intents
    where strategy_version_id=v_signal.strategy_version_id
      and pair=v_signal.pair
      and status in ('OPEN','PAUSED')
  ) then
    v_safe:=public.paper_spec006_fence_entry_lifecycle(
      v_life.entry_action_id,'ENTRY_FENCED_BY_EXIT_INTENT',p_worker_id,p_software_commit
    );
    return jsonb_build_object(
      'created',false,
      'reason',case when v_safe then 'ENTRY_FENCED' else 'WAIT_ENTRY_IN_FLIGHT' end
    );
  end if;

  if v_pos.id is not null then
    select * into v_claim
    from public.paper_exit_intents
    where claimed_position_id=v_pos.id
      and status in ('OPEN','PAUSED','CRITICAL')
    limit 1;

    if found then
      v_safe:=public.paper_spec006_fence_entry_lifecycle(
        v_life.entry_action_id,'ENTRY_FENCED_BY_POSITION_CLAIM',p_worker_id,p_software_commit
      );
      return jsonb_build_object(
        'created',false,
        'reason',case when v_safe then 'ENTRY_FENCED_POSITION_CLAIM' else 'WAIT_ENTRY_IN_FLIGHT' end
      );
    end if;

    return jsonb_build_object('created',false,'reason','PAPER_POSITION_UNCLAIMED');
  end if;

  v_result:=public.paper_create_order_from_approved_signal(
    p_signal_id,
    case when v_signal.side='LONG' then 'BUY' else 'SELL' end,
    'ENTRY',
    'MARKET',
    p_worker_id,
    p_software_commit
  );

  return jsonb_build_object(
    'created',true,
    'order_id',v_result->>'order_id',
    'status',v_result->>'status',
    'idempotent_replay',coalesce((v_result->>'idempotent_replay')::boolean,false)
  );
end;
$func$;

revoke execute on function public.paper_spec006_fence_entry_lifecycle(uuid,text,text,text)
from public,anon,authenticated;
revoke execute on function public.paper_spec006_create_entry_order_from_approved_signal(uuid,text,text)
from public,anon,authenticated;

grant execute on function public.paper_spec006_create_entry_order_from_approved_signal(uuid,text,text)
to service_role;
