-- SPEC-006 cross-transaction ENTRY/order race hardening.
-- If paper exposure appears after action dispatch but before order creation,
-- create/reuse the same durable INTEGRITY_CRITICAL owner instead of leaving
-- an unclaimed position/reservation.

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
  v_owner public.paper_position_entry_ownership;
  v_result jsonb;
  v_safe boolean;
  v_integrity_key text;
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

    select * into v_owner
    from public.paper_position_entry_ownership
    where position_id=v_pos.id;

    if found then
      v_safe:=public.paper_spec006_fence_entry_lifecycle(
        v_owner.entry_action_id,'INTEGRITY_CRITICAL_FENCE',p_worker_id,p_software_commit
      );
      if not v_safe then
        return jsonb_build_object('created',false,'reason','WAIT_OWNER_ENTRY_IN_FLIGHT');
      end if;
    end if;

    v_integrity_key:=encode(
      extensions.digest(
        convert_to(
          'INTEGRITY_CRITICAL|'||v_signal.strategy_version_id||'|'||
          v_signal.pair||'|'||v_pos.id::text,
          'UTF8'
        ),
        'sha256'
      ),
      'hex'
    );

    begin
      insert into public.paper_exit_intents(
        adapter_epoch_id,intent_origin,linked_entry_action_id,integrity_key,
        claimed_position_id,strategy_version_id,pair,locked_reduce_side,status,last_error_code
      )
      values(
        v_life.adapter_epoch_id,'INTEGRITY_CRITICAL',
        case when v_owner.position_id is not null then v_owner.entry_action_id else null end,
        v_integrity_key,v_pos.id,v_signal.strategy_version_id,v_signal.pair,
        case when v_pos.side='LONG' then 'SELL' else 'BUY' end,
        'CRITICAL','PAPER_ALREADY_OPEN_ON_ENTRY_ORDER_CREATE'
      )
      on conflict(integrity_key) where integrity_key is not null do nothing;
    exception when unique_violation then
      null;
    end;

    select * into v_claim
    from public.paper_exit_intents
    where claimed_position_id=v_pos.id
      and status in ('OPEN','PAUSED','CRITICAL')
    limit 1;

    if not found then
      return jsonb_build_object('created',false,'reason','INTEGRITY_CLAIM_RETRY');
    end if;

    v_safe:=public.paper_spec006_fence_entry_lifecycle(
      v_life.entry_action_id,'PAPER_ALREADY_OPEN_ON_ENTRY_ORDER_CREATE',p_worker_id,p_software_commit
    );
    if not v_safe then
      return jsonb_build_object('created',false,'reason','WAIT_ENTRY_IN_FLIGHT');
    end if;

    return jsonb_build_object(
      'created',false,
      'reason','INTEGRITY_CRITICAL_CLAIMED',
      'claim_status',v_claim.status
    );
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

revoke execute on function public.paper_spec006_create_entry_order_from_approved_signal(uuid,text,text)
from public,anon,authenticated;
grant execute on function public.paper_spec006_create_entry_order_from_approved_signal(uuid,text,text)
to service_role;
