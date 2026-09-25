-- Synced from live Supabase migration 20260925091120 (spec006_fence_apply).
-- IMPLEMENTATION-SPEC-006. Runtime B remains disabled.

-- IMPLEMENTATION-SPEC-006 fence-aware bind/apply. Does NOT call legacy paper_apply_fill.

create or replace function public.paper_spec006_bind_execution_attempt(
  p_outbox_id uuid,p_lease_generation bigint,p_order_id uuid,p_market_snapshot_id uuid,
  p_policy_version_id uuid,p_attempt_seq integer,p_worker_id text,p_software_commit text
) returns jsonb
language plpgsql security definer set search_path=public
as $func$
declare v_order public.paper_orders; v_fence public.paper_strategy_execution_fence; v_pos public.paper_positions;
begin
  select * into v_order from public.paper_orders where id=p_order_id;
  if not found then raise exception 'order_not_found'; end if;
  if v_order.exit_intent_id is null and not exists(select 1 from public.paper_entry_lifecycles where paper_entry_order_id=p_order_id) then
    raise exception 'not_spec006_order';
  end if;
  select * into v_fence from public.paper_strategy_execution_fence where strategy_version_id=v_order.strategy_version_id and pair=v_order.pair for update;
  if not found then raise exception 'spec006_fence_missing'; end if;
  select * into v_pos from public.paper_positions where strategy_version_id=v_order.strategy_version_id and pair=v_order.pair and status in ('OPENING','OPEN','EXIT_PENDING','CLOSING') for update;
  if v_order.intent_type='ENTRY' and v_pos.id is not null and exists(select 1 from public.paper_exit_intents where claimed_position_id=v_pos.id and status='CRITICAL') then
    return jsonb_build_object('bound',false,'reason','ENTRY_FENCED_CRITICAL_CLAIM');
  end if;
  return public.paper_bind_execution_attempt(p_outbox_id,p_lease_generation,p_order_id,p_market_snapshot_id,p_policy_version_id,p_attempt_seq,p_worker_id,p_software_commit);
end;$func$;

create or replace function public.paper_spec006_apply_fill(
  p_outbox_id uuid,p_lease_generation bigint,p_order_id uuid,p_execution_attempt_id uuid,p_fill_seq integer,
  p_fill_quantity numeric,p_fill_price numeric,p_fee_amount numeric,p_spread_bps numeric,p_impact_bps numeric,
  p_filled_at timestamptz,p_account_key text default 'paper-default',p_worker_id text default 'spec006-fill',p_software_commit text default 'unknown'
) returns jsonb
language plpgsql security definer set search_path=public,extensions
as $func$
declare
  v_probe public.paper_orders; v_fence public.paper_strategy_execution_fence; v_pos public.paper_positions; v_owner public.paper_position_entry_ownership;
  v_life public.paper_entry_lifecycles; v_intent public.paper_exit_intents; v_outbox public.execution_outbox; v_order public.paper_orders;
  v_attempt public.paper_execution_attempts; v_snapshot public.execution_market_snapshots; v_policy public.risk_policy_versions;
  v_signal public.paper_signals; v_res public.risk_reservations; v_state public.portfolio_risk_state; v_ks public.kill_switch_state;
  v_fill_id uuid; v_fill_key text; v_fill_notional numeric(30,12); v_new_filled_notional numeric(30,12); v_new_status text;
  v_signed numeric(30,12); v_consume numeric(30,12); v_alloc_entry_fee numeric(30,12); v_realized numeric(30,12);
  v_before jsonb; v_residual numeric(30,12); v_outbox_key text; v_safe boolean;
begin
  if p_fill_seq<1 or p_fill_quantity<=0 or p_fill_price<=0 or p_fee_amount<0 then raise exception 'invalid_fill_input'; end if;
  select * into v_probe from public.paper_orders where id=p_order_id;
  if not found then raise exception 'order_not_found'; end if;
  if v_probe.exit_intent_id is null then
    select * into v_life from public.paper_entry_lifecycles where paper_entry_order_id=p_order_id;
    if not found then raise exception 'not_spec006_order'; end if;
  else
    select * into v_intent from public.paper_exit_intents where id=v_probe.exit_intent_id;
    if not found then raise exception 'exit_intent_missing'; end if;
  end if;
  select * into v_fence from public.paper_strategy_execution_fence where strategy_version_id=v_probe.strategy_version_id and pair=v_probe.pair for update;
  if not found then raise exception 'spec006_fence_missing'; end if;
  select * into v_pos from public.paper_positions where strategy_version_id=v_probe.strategy_version_id and pair=v_probe.pair and status in ('OPENING','OPEN','EXIT_PENDING','CLOSING') for update;

  if v_probe.intent_type='ENTRY' then
    select * into v_life from public.paper_entry_lifecycles where paper_entry_order_id=p_order_id for update;
    if v_life.status<>'ORDER_ACTIVE' then return jsonb_build_object('applied',false,'reason','ENTRY_LIFECYCLE_NOT_ACTIVE'); end if;
    if exists(select 1 from public.paper_exit_intents where strategy_version_id=v_probe.strategy_version_id and pair=v_probe.pair and status in ('OPEN','PAUSED')) then
      return jsonb_build_object('applied',false,'reason','ENTRY_FENCED_BY_EXIT_INTENT');
    end if;
    if v_pos.id is not null and exists(select 1 from public.paper_exit_intents where claimed_position_id=v_pos.id and status='CRITICAL') then
      return jsonb_build_object('applied',false,'reason','ENTRY_FENCED_CRITICAL_CLAIM');
    end if;
    if v_pos.id is not null then
      select * into v_owner from public.paper_position_entry_ownership where position_id=v_pos.id;
      if not found or v_owner.entry_action_id<>v_life.entry_action_id or v_owner.paper_entry_order_id<>p_order_id then
        return jsonb_build_object('applied',false,'reason','ENTRY_POSITION_OWNERSHIP_MISMATCH');
      end if;
    end if;
  else
    if v_probe.intent_type<>'EXIT' or v_probe.exit_intent_id is null then raise exception 'unsupported_spec006_intent_type'; end if;
    select * into v_intent from public.paper_exit_intents where id=v_probe.exit_intent_id for update;
    if v_intent.status<>'OPEN' then return jsonb_build_object('applied',false,'reason','EXIT_INTENT_NOT_OPEN','status',v_intent.status); end if;
    if v_pos.id is null then return jsonb_build_object('applied',false,'reason','EXIT_NO_POSITION'); end if;
    if v_intent.claimed_position_id is distinct from v_pos.id then return jsonb_build_object('applied',false,'reason','EXIT_POSITION_CLAIM_MISMATCH'); end if;
    if (v_pos.side='LONG' and v_probe.side<>'SELL') or (v_pos.side='SHORT' and v_probe.side<>'BUY') then return jsonb_build_object('applied',false,'reason','EXIT_REDUCE_SIDE_MISMATCH'); end if;
    if p_fill_quantity>v_pos.quantity+0.00000001 then return jsonb_build_object('applied',false,'reason','EXIT_OVER_CLOSE_REJECTED'); end if;
  end if;

  v_fill_key:=encode(digest(convert_to('FILL|'||p_order_id::text||'|'||p_fill_seq::text||'|'||p_execution_attempt_id::text,'UTF8'),'sha256'),'hex');
  select id into v_fill_id from public.paper_fills where idempotency_key=v_fill_key;
  if found then return jsonb_build_object('applied',false,'fill_id',v_fill_id,'idempotent_replay',true,'lease_generation',p_lease_generation); end if;
  select * into v_outbox from public.execution_outbox where id=p_outbox_id for update;
  if not found then raise exception 'outbox_not_found'; end if;
  if v_outbox.claimed_by is distinct from p_worker_id or v_outbox.lease_generation<>p_lease_generation then return jsonb_build_object('applied',false,'reason','STALE_LEASE','lease_generation',v_outbox.lease_generation); end if;
  if v_outbox.processed_at is not null then raise exception 'outbox_already_processed_without_matching_fill'; end if;
  if v_outbox.claim_expires_at is null or v_outbox.claim_expires_at<clock_timestamp() then return jsonb_build_object('applied',false,'reason','LEASE_EXPIRED','lease_generation',v_outbox.lease_generation); end if;
  if v_outbox.event_type<>'BIND_FILL_ATTEMPT' or v_outbox.entity_type<>'order' or v_outbox.entity_id<>p_order_id then raise exception 'outbox_binding_mismatch'; end if;
  select * into v_order from public.paper_orders where id=p_order_id for update;
  if v_order.status not in ('ACCEPTED','PENDING_FILL','PARTIALLY_FILLED') then return jsonb_build_object('applied',false,'reason','ORDER_STATE_DISALLOWS_FILL','status',v_order.status); end if;
  if v_order.cancel_requested_at is not null then return jsonb_build_object('applied',false,'reason','FILL_BLOCKED_AFTER_CANCEL'); end if;
  select * into v_ks from public.kill_switch_state where account_key=p_account_key for update;
  if not found then raise exception 'kill_switch_state_missing'; end if;
  if v_ks.state in ('HALTED','RECOVERY_PENDING') then return jsonb_build_object('applied',false,'reason','FILL_BLOCKED_KILL_SWITCH','state',v_ks.state); end if;
  select * into v_attempt from public.paper_execution_attempts where id=p_execution_attempt_id for update;
  if not found or v_attempt.order_id<>p_order_id then raise exception 'execution_attempt_mismatch'; end if;
  if v_attempt.source_outbox_id is distinct from p_outbox_id or v_attempt.source_lease_generation is distinct from p_lease_generation then return jsonb_build_object('applied',false,'reason','STALE_ATTEMPT_LEASE'); end if;
  if v_attempt.status<>'BOUND' then return jsonb_build_object('applied',false,'reason','ATTEMPT_NOT_BOUND','status',v_attempt.status); end if;
  select * into v_snapshot from public.execution_market_snapshots where id=v_attempt.market_snapshot_id;
  if not found then raise exception 'market_snapshot_missing'; end if;
  select * into v_policy from public.risk_policy_versions where id=v_attempt.policy_version_id;
  if not found then raise exception 'risk_policy_missing'; end if;
  if v_policy.max_book_age_ms is not null and extract(epoch from(clock_timestamp()-v_snapshot.received_at))*1000>v_policy.max_book_age_ms then return jsonb_build_object('applied',false,'reason','STALE_MARKET_SNAPSHOT'); end if;
  select * into v_signal from public.paper_signals where id=v_order.signal_id;
  if not found then raise exception 'signal_missing'; end if;
  if exists(select 1 from public.paper_fills where order_id=p_order_id and fill_seq=p_fill_seq) then raise exception 'fill_seq_collision'; end if;
  insert into public.portfolio_risk_state(account_key) values(p_account_key) on conflict(account_key) do nothing;
  select * into v_state from public.portfolio_risk_state where account_key=p_account_key for update;
  if v_order.intent_type='ENTRY' then
    select * into v_res from public.risk_reservations where signal_id=v_order.signal_id for update;
    if not found then raise exception 'entry_fill_without_risk_reservation'; end if;
  end if;
  v_fill_notional:=p_fill_quantity*p_fill_price;
  v_new_filled_notional:=v_order.filled_notional+v_fill_notional;
  if v_order.intent_type='ENTRY' then
    if v_fill_notional>(v_res.reserved_notional-v_res.consumed_notional)+0.00000001 then raise exception 'fill_exceeds_reserved_notional'; end if;
    v_new_status:=case when v_new_filled_notional+0.00000001>=v_order.intended_notional then 'FILLED' else 'PARTIALLY_FILLED' end;
    if v_new_status='PARTIALLY_FILLED' and not v_policy.partial_fill_allowed then raise exception 'partial_fill_not_allowed'; end if;
  else
    v_new_status:=case when v_pos.quantity-p_fill_quantity<=0.00000001 then 'FILLED' else 'PARTIALLY_FILLED' end;
  end if;

  insert into public.paper_fills(order_id,execution_attempt_id,strategy_version_id,pair,fill_seq,fill_quantity,fill_price,fee_amount,spread_bps,impact_bps,filled_at,idempotency_key)
  values(p_order_id,p_execution_attempt_id,v_order.strategy_version_id,v_order.pair,p_fill_seq,p_fill_quantity,p_fill_price,p_fee_amount,p_spread_bps,p_impact_bps,p_filled_at,v_fill_key)
  returning id into v_fill_id;
  update public.paper_orders set filled_quantity=filled_quantity+p_fill_quantity,filled_notional=v_new_filled_notional,status=v_new_status,terminal_at=case when v_new_status='FILLED' then clock_timestamp() else terminal_at end,state_version=state_version+1,updated_at=clock_timestamp() where id=p_order_id;
  insert into public.paper_order_events(order_id,from_state,to_state,reason_code,payload) values(p_order_id,v_order.status,v_new_status,'FILL_APPLIED',jsonb_build_object('fill_id',v_fill_id,'fill_seq',p_fill_seq,'execution_attempt_id',p_execution_attempt_id));

  if v_order.intent_type='ENTRY' then
    if v_pos.id is null then
      insert into public.paper_positions(strategy_version_id,pair,side,quantity,average_entry_price,gross_entry_notional,cumulative_fees,entry_fees,remaining_entry_fees,cumulative_execution_cost,realized_pnl,status,opened_at)
      values(v_order.strategy_version_id,v_order.pair,case when v_order.side='BUY' then 'LONG' else 'SHORT' end,p_fill_quantity,p_fill_price,v_fill_notional,p_fee_amount,p_fee_amount,p_fee_amount,abs(p_impact_bps)*v_fill_notional/10000,0,'OPEN',p_filled_at)
      returning * into v_pos;
      insert into public.paper_position_entry_ownership(position_id,entry_action_id,paper_entry_order_id,strategy_version_id,pair) values(v_pos.id,v_life.entry_action_id,p_order_id,v_order.strategy_version_id,v_order.pair);
      insert into public.paper_position_events(position_id,event_type,state_before,state_after) values(v_pos.id,'POSITION_OPENED',null,to_jsonb(v_pos));
    else
      v_before:=to_jsonb(v_pos);
      update public.paper_positions set average_entry_price=((quantity*average_entry_price)+(p_fill_quantity*p_fill_price))/(quantity+p_fill_quantity),quantity=quantity+p_fill_quantity,gross_entry_notional=gross_entry_notional+v_fill_notional,cumulative_fees=cumulative_fees+p_fee_amount,entry_fees=entry_fees+p_fee_amount,remaining_entry_fees=remaining_entry_fees+p_fee_amount,cumulative_execution_cost=cumulative_execution_cost+abs(p_impact_bps)*v_fill_notional/10000,state_version=state_version+1,updated_at=clock_timestamp() where id=v_pos.id returning * into v_pos;
      insert into public.paper_position_events(position_id,event_type,state_before,state_after) values(v_pos.id,'ENTRY_FILL_APPLIED',v_before,to_jsonb(v_pos));
    end if;
    v_consume:=v_fill_notional; v_signed:=case when v_res.side='LONG' then v_consume else -v_consume end;
    update public.risk_reservations set consumed_notional=consumed_notional+v_consume,status=case when consumed_notional+v_consume+0.00000001>=reserved_notional then 'CONSUMED' else 'PARTIALLY_CONSUMED' end,consumed_at=clock_timestamp(),state_version=state_version+1 where id=v_res.id;
    update public.portfolio_risk_state set reserved_gross_notional=greatest(0,reserved_gross_notional-v_consume),used_gross_notional=used_gross_notional+v_consume,reserved_net_notional=reserved_net_notional-v_signed,used_net_notional=used_net_notional+v_signed,state_version=state_version+1,updated_at=clock_timestamp() where account_key=p_account_key;
    if v_new_status='FILLED' then update public.paper_entry_lifecycles set status='TERMINAL_FILLED',state_version=state_version+1,updated_at=clock_timestamp() where entry_action_id=v_life.entry_action_id; end if;
  else
    v_before:=to_jsonb(v_pos);
    v_alloc_entry_fee:=case when v_pos.quantity>0 then v_pos.remaining_entry_fees*p_fill_quantity/v_pos.quantity else 0 end;
    v_realized:=case when v_pos.side='LONG' then (p_fill_price-v_pos.average_entry_price)*p_fill_quantity-v_alloc_entry_fee-p_fee_amount else (v_pos.average_entry_price-p_fill_price)*p_fill_quantity-v_alloc_entry_fee-p_fee_amount end;
    v_consume:=case when v_pos.quantity>0 then v_pos.gross_entry_notional*p_fill_quantity/v_pos.quantity else 0 end;
    v_signed:=case when v_pos.side='LONG' then v_consume else -v_consume end;
    update public.paper_positions set quantity=greatest(0,quantity-p_fill_quantity),gross_entry_notional=greatest(0,gross_entry_notional-v_consume),cumulative_fees=cumulative_fees+p_fee_amount,exit_fees=exit_fees+p_fee_amount,remaining_entry_fees=greatest(0,remaining_entry_fees-v_alloc_entry_fee),cumulative_execution_cost=cumulative_execution_cost+abs(p_impact_bps)*v_fill_notional/10000,realized_pnl=realized_pnl+v_realized,status=case when quantity-p_fill_quantity<=0.00000001 then 'CLOSED' else 'OPEN' end,closed_at=case when quantity-p_fill_quantity<=0.00000001 then p_filled_at else closed_at end,state_version=state_version+1,updated_at=clock_timestamp() where id=v_pos.id returning * into v_pos;
    insert into public.paper_position_events(position_id,event_type,state_before,state_after) values(v_pos.id,'EXIT_FILL_APPLIED',v_before,to_jsonb(v_pos));
    if v_state.risk_utc_day<>(p_filled_at at time zone 'utc')::date then v_state.realized_pnl_utc_day:=0;v_state.risk_utc_day:=(p_filled_at at time zone 'utc')::date; end if;
    update public.portfolio_risk_state set used_gross_notional=greatest(0,used_gross_notional-v_consume),used_net_notional=used_net_notional-v_signed,realized_pnl_utc_day=v_state.realized_pnl_utc_day+v_realized,risk_utc_day=v_state.risk_utc_day,state_version=state_version+1,updated_at=clock_timestamp() where account_key=p_account_key;
  end if;

  update public.paper_execution_attempts set status='APPLIED',applied_at=clock_timestamp() where id=p_execution_attempt_id;
  if v_new_status='PARTIALLY_FILLED' then
    v_outbox_key:=encode(digest(convert_to('OUTBOX|BIND_FILL_ATTEMPT|order|'||p_order_id::text||'|attempt:'||(v_attempt.attempt_seq+1)::text,'UTF8'),'sha256'),'hex');
    insert into public.execution_outbox(event_type,entity_type,entity_id,correlation_id,payload,idempotency_key)
    values('BIND_FILL_ATTEMPT','order',p_order_id,v_signal.correlation_id,
      case when v_order.intent_type='EXIT' then jsonb_build_object('order_id',p_order_id,'attempt_seq',v_attempt.attempt_seq+1,'spec006_exit_intent_id',v_order.exit_intent_id,'remaining_base_qty',greatest(0,v_pos.quantity))
           else jsonb_build_object('order_id',p_order_id,'attempt_seq',v_attempt.attempt_seq+1,'remaining_notional',greatest(0,v_order.intended_notional-v_new_filled_notional)) end,
      v_outbox_key) on conflict(idempotency_key) do nothing;
  end if;
  if v_order.intent_type='EXIT' and v_new_status='FILLED' then
    if v_intent.intent_origin='REFERENCE_EXIT' then v_safe:=public.paper_spec006_entry_safe_terminal(v_intent.linked_entry_action_id);
    else select not exists(select 1 from public.paper_entry_lifecycles l where l.strategy_version_id=v_intent.strategy_version_id and l.pair=v_intent.pair and not public.paper_spec006_entry_safe_terminal(l.entry_action_id)) into v_safe; end if;
    if v_safe then update public.paper_exit_intents set status='SATISFIED',satisfied_at=clock_timestamp(),state_version=state_version+1,updated_at=clock_timestamp() where id=v_intent.id; end if;
  end if;
  perform public.paper_append_audit('FILL_APPLIED','order',p_order_id,v_signal.correlation_id,v_order.strategy_version_id,v_signal.signal_close_time,clock_timestamp(),clock_timestamp(),to_jsonb(v_order),jsonb_build_object('fill_id',v_fill_id,'order_status',v_new_status,'position_id',v_pos.id),v_snapshot.snapshot_hash,'FILL_APPLIED',p_worker_id,p_software_commit,v_signal.correlation_id);
  return jsonb_build_object('applied',true,'fill_id',v_fill_id,'order_status',v_new_status,'position_id',v_pos.id,'realized_pnl_delta',coalesce(v_realized,0),'idempotent_replay',false,'lease_generation',p_lease_generation);
end;$func$;

revoke execute on function public.paper_spec006_bind_execution_attempt(uuid,bigint,uuid,uuid,uuid,integer,text,text) from public,anon,authenticated;
revoke execute on function public.paper_spec006_apply_fill(uuid,bigint,uuid,uuid,integer,numeric,numeric,numeric,numeric,numeric,timestamptz,text,text,text) from public,anon,authenticated;
grant execute on function public.paper_spec006_bind_execution_attempt(uuid,bigint,uuid,uuid,uuid,integer,text,text) to service_role;
grant execute on function public.paper_spec006_apply_fill(uuid,bigint,uuid,uuid,integer,numeric,numeric,numeric,numeric,numeric,timestamptz,text,text,text) to service_role;
