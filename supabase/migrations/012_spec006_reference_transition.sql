-- IMPLEMENTATION-SPEC-006 reference-bar store + atomic frozen-B transition.
-- Runtime binding is not created or enabled by this migration.

create table if not exists public.paper_strategy_reference_bars_5m (
  strategy_version_id text not null,
  pair text not null,
  open_time timestamptz not null,
  close_time timestamptz not null,
  open numeric(30,12) not null check (open > 0),
  high numeric(30,12) not null check (high > 0),
  low numeric(30,12) not null check (low > 0),
  close numeric(30,12) not null check (close > 0),
  source_hash text not null,
  created_at timestamptz not null default now(),
  primary key(strategy_version_id,pair,open_time),
  check (close_time = open_time + interval '5 minutes' - interval '1 millisecond'),
  check (high >= greatest(open,close,low)),
  check (low <= least(open,close,high))
);

alter table public.paper_strategy_reference_bars_5m enable row level security;
revoke all on table public.paper_strategy_reference_bars_5m from public,anon,authenticated;
grant all on table public.paper_strategy_reference_bars_5m to service_role;

create or replace function public.paper_spec006_ingest_reference_bar(
  p_strategy_version_id text, p_pair text,
  p_open_time timestamptz, p_close_time timestamptz,
  p_open numeric, p_high numeric, p_low numeric, p_close numeric,
  p_source_hash text
) returns jsonb
language plpgsql security definer set search_path=public
as $func$
declare v_existing public.paper_strategy_reference_bars_5m;
begin
  if p_strategy_version_id is null or btrim(p_strategy_version_id)='' then raise exception 'strategy_version_required'; end if;
  if p_pair is null or btrim(p_pair)='' then raise exception 'pair_required'; end if;
  if p_close_time <> p_open_time + interval '5 minutes' - interval '1 millisecond' then raise exception 'invalid_5m_bounds'; end if;
  if p_open<=0 or p_high<=0 or p_low<=0 or p_close<=0 then raise exception 'invalid_ohlc'; end if;
  if p_high<greatest(p_open,p_close,p_low) or p_low>least(p_open,p_close,p_high) then raise exception 'invalid_ohlc_range'; end if;
  if p_source_hash is null or length(p_source_hash)<>64 then raise exception 'source_hash_invalid'; end if;
  select * into v_existing from public.paper_strategy_reference_bars_5m
   where strategy_version_id=p_strategy_version_id and pair=upper(p_pair) and open_time=p_open_time;
  if found then
    if v_existing.source_hash<>p_source_hash or v_existing.open<>p_open or v_existing.high<>p_high or v_existing.low<>p_low or v_existing.close<>p_close or v_existing.close_time<>p_close_time then raise exception 'immutable_reference_bar_conflict'; end if;
    return jsonb_build_object('inserted',false,'idempotent_replay',true);
  end if;
  insert into public.paper_strategy_reference_bars_5m(strategy_version_id,pair,open_time,close_time,open,high,low,close,source_hash)
  values(p_strategy_version_id,upper(p_pair),p_open_time,p_close_time,p_open,p_high,p_low,p_close,p_source_hash);
  return jsonb_build_object('inserted',true,'idempotent_replay',false);
end;$func$;

create or replace function public.paper_spec006_atr_at(
  p_strategy_version_id text, p_pair text, p_hour_close timestamptz
) returns numeric
language plpgsql stable security definer set search_path=public
as $func$
declare
  r record; v_prev_close numeric; v_tr numeric; v_count integer:=0; v_sum numeric:=0; v_atr numeric;
begin
  for r in
    select date_trunc('hour',open_time) as hour_start,
           max(high) as high, min(low) as low,
           (array_agg(close order by open_time desc))[1] as close,
           date_trunc('hour',open_time)+interval '1 hour'-interval '1 millisecond' as hour_close
    from public.paper_strategy_reference_bars_5m
    where strategy_version_id=p_strategy_version_id and pair=upper(p_pair)
      and open_time <= p_hour_close
    group by date_trunc('hour',open_time)
    having count(*)=12
       and min(open_time)=date_trunc('hour',min(open_time))
       and max(close_time)=date_trunc('hour',max(open_time))+interval '1 hour'-interval '1 millisecond'
    order by hour_start
  loop
    if v_prev_close is null then v_prev_close:=r.close; continue; end if;
    v_tr:=greatest(r.high-r.low,abs(r.high-v_prev_close),abs(r.low-v_prev_close));
    v_count:=v_count+1;
    if v_count<=24 then
      v_sum:=v_sum+v_tr;
      if v_count=24 then v_atr:=v_sum/24; end if;
    else
      v_atr:=((23*v_atr)+v_tr)/24;
    end if;
    v_prev_close:=r.close;
    if r.hour_close=p_hour_close then return v_atr; end if;
  end loop;
  return null;
end;$func$;

create or replace function public.paper_spec006_signal_at(
  p_strategy_version_id text, p_pair text, p_hour_close timestamptz
) returns text
language plpgsql stable security definer set search_path=public
as $func$
declare v_cur_close numeric; v_prior_high numeric; v_prior_low numeric; v_count integer; v_min_close timestamptz; v_max_close timestamptz;
begin
  with h as (
    select date_trunc('hour',open_time) as hour_start,
           max(high) as high,min(low) as low,
           (array_agg(close order by open_time desc))[1] as close,
           date_trunc('hour',open_time)+interval '1 hour'-interval '1 millisecond' as hour_close
    from public.paper_strategy_reference_bars_5m
    where strategy_version_id=p_strategy_version_id and pair=upper(p_pair) and open_time<=p_hour_close
    group by date_trunc('hour',open_time)
    having count(*)=12
       and min(open_time)=date_trunc('hour',min(open_time))
       and max(close_time)=date_trunc('hour',max(open_time))+interval '1 hour'-interval '1 millisecond'
  ), cur as (select close from h where hour_close=p_hour_close), prior as (
    select * from h where hour_close<p_hour_close order by hour_close desc limit 24
  )
  select (select close from cur),count(*),max(high),min(low),min(hour_close),max(hour_close)
    into v_cur_close,v_count,v_prior_high,v_prior_low,v_min_close,v_max_close from prior;
  if v_cur_close is null or v_count<>24 then return null; end if;
  if v_max_close<>p_hour_close-interval '1 hour' or v_min_close<>p_hour_close-interval '24 hours' then return null; end if;
  if v_cur_close>v_prior_high then return 'LONG'; end if;
  if v_cur_close<v_prior_low then return 'SHORT'; end if;
  return null;
end;$func$;

create or replace function public.paper_spec006_action_key(
  p_strategy_version_id text,p_pair text,p_action_type text,
  p_decision_time timestamptz,p_execution_time timestamptz,p_side text,p_reason text
) returns text
language sql immutable set search_path=public,extensions
as $func$
  select encode(digest(convert_to('STRATEGY_ACTION|'||p_strategy_version_id||'|'||upper(p_pair)||'|'||p_action_type||'|'||floor(extract(epoch from p_decision_time)*1000)::bigint||'|'||floor(extract(epoch from p_execution_time)*1000)::bigint||'|'||p_side||'|'||p_reason,'UTF8'),'sha256'),'hex')
$func$;

create or replace function public.paper_spec006_commit_reference_bar(
  p_strategy_version_id text,p_pair text,p_expected_open_time timestamptz,p_software_commit text
) returns jsonb
language plpgsql security definer set search_path=public,extensions
as $func$
declare
  v_fence public.paper_strategy_execution_fence; v_shadow public.paper_strategy_shadow_state; v_bar public.paper_strategy_reference_bars_5m;
  v_expected timestamptz; v_hour_close timestamptz; v_signal text; v_atr numeric; v_stop numeric; v_candidate numeric;
  v_had_position boolean; v_emit boolean; v_before jsonb; v_entry_id uuid; v_exit_id uuid; v_side text; v_reason text;
  v_exit_price numeric; v_exit_time timestamptz; v_entry_key text; v_exit_key text; v_outbox_key text;
  v_actions integer:=0;
begin
  insert into public.paper_strategy_execution_fence(strategy_version_id,pair) values(p_strategy_version_id,upper(p_pair)) on conflict do nothing;
  select * into v_fence from public.paper_strategy_execution_fence where strategy_version_id=p_strategy_version_id and pair=upper(p_pair) for update;
  select * into v_shadow from public.paper_strategy_shadow_state where strategy_version_id=p_strategy_version_id and pair=upper(p_pair) for update;
  if not found then raise exception 'spec006_shadow_missing'; end if;
  if v_shadow.data_state<>'CONTIGUOUS' then return jsonb_build_object('applied',false,'reason','REFERENCE_INVALID'); end if;
  if v_shadow.last_processed_5m_open_time is null then raise exception 'spec006_cursor_missing'; end if;
  v_expected:=v_shadow.last_processed_5m_open_time+interval '5 minutes';
  if p_expected_open_time<>v_expected then return jsonb_build_object('applied',false,'reason','CURSOR_MOVED','expected_open_time',v_expected); end if;
  select * into v_bar from public.paper_strategy_reference_bars_5m where strategy_version_id=p_strategy_version_id and pair=upper(p_pair) and open_time=v_expected;
  if not found then
    update public.paper_strategy_shadow_state set data_state='INVALID',invalid_reason='MISSING_EXACT_5M',invalid_at_5m_open_time=v_expected,state_version=state_version+1,updated_at=clock_timestamp() where strategy_version_id=p_strategy_version_id and pair=upper(p_pair);
    return jsonb_build_object('applied',true,'data_state','INVALID','reason','MISSING_EXACT_5M');
  end if;
  v_emit:=not (
    (v_shadow.dispatch_frontier_5m_open_time is not null and v_bar.open_time<=v_shadow.dispatch_frontier_5m_open_time)
    or (v_shadow.dispatch_enable_commit_time is not null and v_bar.close_time<=v_shadow.dispatch_enable_commit_time)
  );
  v_before:=to_jsonb(v_shadow);
  v_hour_close:=date_trunc('hour',v_bar.open_time)-interval '1 millisecond';
  if v_bar.open_time=date_trunc('hour',v_bar.open_time) then v_signal:=public.paper_spec006_signal_at(p_strategy_version_id,p_pair,v_hour_close); end if;
  v_had_position:=v_shadow.reference_position_state in ('LONG','SHORT');

  if v_had_position then
    v_atr:=public.paper_spec006_atr_at(p_strategy_version_id,p_pair,v_hour_close);
    if v_atr is not null then
      if v_shadow.reference_position_state='LONG' then v_candidate:=v_shadow.peak_high-2*v_atr; v_stop:=greatest(v_shadow.active_stop,v_candidate);
      else v_candidate:=v_shadow.trough_low+2*v_atr; v_stop:=least(v_shadow.active_stop,v_candidate); end if;
      v_shadow.active_stop:=v_stop;
    end if;
    v_stop:=v_shadow.active_stop;
    v_exit_price:=null; v_reason:=null; v_exit_time:=null;
    if v_shadow.reference_position_state='LONG' then
      if v_bar.open<v_stop then v_exit_price:=v_bar.open;v_reason:='chandelier_stop_gap';v_exit_time:=v_bar.open_time;
      elsif v_bar.low<=v_stop then v_exit_price:=v_stop;v_reason:='chandelier_stop';v_exit_time:=v_bar.close_time; end if;
    else
      if v_bar.open>v_stop then v_exit_price:=v_bar.open;v_reason:='chandelier_stop_gap';v_exit_time:=v_bar.open_time;
      elsif v_bar.high>=v_stop then v_exit_price:=v_stop;v_reason:='chandelier_stop';v_exit_time:=v_bar.close_time; end if;
    end if;
    if v_exit_price is not null then
      v_side:=v_shadow.reference_position_state;
      if v_emit and v_shadow.reference_entry_action_id is null then raise exception 'reference_cycle_missing_action_identity'; end if;
      if v_emit then
        v_exit_key:=public.paper_spec006_action_key(p_strategy_version_id,p_pair,'EXIT_TO_FLAT',v_exit_time,v_exit_time,v_side,v_reason);
        insert into public.paper_strategy_actions(adapter_epoch_id,strategy_version_id,pair,action_type,position_side,reference_decision_time,required_execution_time,reference_price,reason_code,rule_id,reference_atr,active_stop,linked_entry_action_id,shadow_state_before,shadow_state_after,idempotency_key,software_commit)
        values(v_shadow.adapter_epoch_id,p_strategy_version_id,upper(p_pair),'EXIT_TO_FLAT',v_side,v_exit_time,v_exit_time,v_exit_price,v_reason,'TSMOM_B_V1',v_atr,v_stop,v_shadow.reference_entry_action_id,v_before,jsonb_build_object('reference_position_state','FLAT'),v_exit_key,p_software_commit)
        on conflict(idempotency_key) do update set idempotency_key=excluded.idempotency_key returning id into v_exit_id;
        insert into public.paper_exit_intents(adapter_epoch_id,intent_origin,exit_action_id,linked_entry_action_id,strategy_version_id,pair,reference_position_side,locked_reduce_side,status)
        values(v_shadow.adapter_epoch_id,'REFERENCE_EXIT',v_exit_id,v_shadow.reference_entry_action_id,p_strategy_version_id,upper(p_pair),v_side,case when v_side='LONG' then 'SELL' else 'BUY' end,'OPEN')
        on conflict(exit_action_id) where exit_action_id is not null do nothing;
        v_outbox_key:=encode(digest(convert_to('OUTBOX|STRATEGY_ACTION_DISPATCH|strategy_action|'||v_exit_id::text,'UTF8'),'sha256'),'hex');
        insert into public.execution_outbox(event_type,entity_type,entity_id,correlation_id,payload,idempotency_key) values('STRATEGY_ACTION_DISPATCH','strategy_action',v_exit_id,v_exit_key,'{}'::jsonb,v_outbox_key) on conflict(idempotency_key) do nothing;
        v_actions:=v_actions+1;
      end if;
      v_shadow.reference_position_state:='FLAT'; v_shadow.reference_entry_action_id:=null; v_shadow.reference_signal_close_time:=null; v_shadow.reference_entry_time:=null; v_shadow.reference_entry_price:=null; v_shadow.reference_entry_atr:=null; v_shadow.active_stop:=null; v_shadow.peak_high:=null; v_shadow.trough_low:=null;
    end if;
  end if;

  if v_signal is not null and not v_had_position and v_shadow.reference_position_state='FLAT' then
    v_atr:=public.paper_spec006_atr_at(p_strategy_version_id,p_pair,v_hour_close);
    if v_atr is null or v_atr<=0 then raise exception 'signal_without_valid_atr'; end if;
    v_side:=v_signal;
    v_stop:=case when v_side='LONG' then v_bar.open-2*v_atr else v_bar.open+2*v_atr end;
    if v_emit then
      v_entry_key:=public.paper_spec006_action_key(p_strategy_version_id,p_pair,'ENTRY',v_hour_close,v_bar.open_time,v_side,case when v_side='LONG' then 'BREAKOUT_LONG' else 'BREAKOUT_SHORT' end);
      insert into public.paper_strategy_actions(adapter_epoch_id,strategy_version_id,pair,action_type,position_side,reference_decision_time,required_execution_time,reference_price,reason_code,rule_id,reference_atr,active_stop,shadow_state_before,shadow_state_after,idempotency_key,software_commit)
      values(v_shadow.adapter_epoch_id,p_strategy_version_id,upper(p_pair),'ENTRY',v_side,v_hour_close,v_bar.open_time,v_bar.open,case when v_side='LONG' then 'BREAKOUT_LONG' else 'BREAKOUT_SHORT' end,'TSMOM_B_V1',v_atr,v_stop,v_before,jsonb_build_object('reference_position_state',v_side),v_entry_key,p_software_commit)
      on conflict(idempotency_key) do update set idempotency_key=excluded.idempotency_key returning id into v_entry_id;
    end if;
    v_shadow.reference_position_state:=v_side; v_shadow.reference_entry_action_id:=v_entry_id; v_shadow.reference_signal_close_time:=v_hour_close; v_shadow.reference_entry_time:=v_bar.open_time; v_shadow.reference_entry_price:=v_bar.open; v_shadow.reference_entry_atr:=v_atr; v_shadow.active_stop:=v_stop; v_shadow.peak_high:=v_bar.open; v_shadow.trough_low:=v_bar.open;
    if (v_side='LONG' and v_bar.low<=v_stop) or (v_side='SHORT' and v_bar.high>=v_stop) then
      if v_emit then
        v_exit_key:=public.paper_spec006_action_key(p_strategy_version_id,p_pair,'EXIT_TO_FLAT',v_bar.close_time,v_bar.close_time,v_side,'initial_stop');
        insert into public.paper_strategy_actions(adapter_epoch_id,strategy_version_id,pair,action_type,position_side,reference_decision_time,required_execution_time,reference_price,reason_code,rule_id,reference_atr,active_stop,linked_entry_action_id,shadow_state_before,shadow_state_after,idempotency_key,software_commit)
        values(v_shadow.adapter_epoch_id,p_strategy_version_id,upper(p_pair),'EXIT_TO_FLAT',v_side,v_bar.close_time,v_bar.close_time,v_stop,'initial_stop','TSMOM_B_V1',v_atr,v_stop,v_entry_id,v_before,jsonb_build_object('reference_position_state','FLAT'),v_exit_key,p_software_commit)
        on conflict(idempotency_key) do update set idempotency_key=excluded.idempotency_key returning id into v_exit_id;
        insert into public.paper_entry_lifecycles(entry_action_id,adapter_epoch_id,strategy_version_id,pair,status) values(v_entry_id,v_shadow.adapter_epoch_id,p_strategy_version_id,upper(p_pair),'NEVER_CREATED_FENCED') on conflict(entry_action_id) do nothing;
        insert into public.paper_exit_intents(adapter_epoch_id,intent_origin,exit_action_id,linked_entry_action_id,strategy_version_id,pair,reference_position_side,locked_reduce_side,status) values(v_shadow.adapter_epoch_id,'REFERENCE_EXIT',v_exit_id,v_entry_id,p_strategy_version_id,upper(p_pair),v_side,case when v_side='LONG' then 'SELL' else 'BUY' end,'OPEN') on conflict(exit_action_id) where exit_action_id is not null do nothing;
        v_outbox_key:=encode(digest(convert_to('OUTBOX|STRATEGY_ACTION_DISPATCH|strategy_action|'||v_entry_id::text,'UTF8'),'sha256'),'hex'); insert into public.execution_outbox(event_type,entity_type,entity_id,correlation_id,payload,idempotency_key) values('STRATEGY_ACTION_DISPATCH','strategy_action',v_entry_id,v_entry_key,'{}'::jsonb,v_outbox_key) on conflict(idempotency_key) do nothing;
        v_outbox_key:=encode(digest(convert_to('OUTBOX|STRATEGY_ACTION_DISPATCH|strategy_action|'||v_exit_id::text,'UTF8'),'sha256'),'hex'); insert into public.execution_outbox(event_type,entity_type,entity_id,correlation_id,payload,idempotency_key) values('STRATEGY_ACTION_DISPATCH','strategy_action',v_exit_id,v_exit_key,'{}'::jsonb,v_outbox_key) on conflict(idempotency_key) do nothing;
        v_actions:=v_actions+2;
      end if;
      v_shadow.reference_position_state:='FLAT'; v_shadow.reference_entry_action_id:=null; v_shadow.reference_signal_close_time:=null; v_shadow.reference_entry_time:=null; v_shadow.reference_entry_price:=null; v_shadow.reference_entry_atr:=null; v_shadow.active_stop:=null; v_shadow.peak_high:=null; v_shadow.trough_low:=null;
    else
      if v_emit then
        insert into public.paper_entry_lifecycles(entry_action_id,adapter_epoch_id,strategy_version_id,pair,status) values(v_entry_id,v_shadow.adapter_epoch_id,p_strategy_version_id,upper(p_pair),'PENDING_DISPATCH') on conflict(entry_action_id) do nothing;
        v_outbox_key:=encode(digest(convert_to('OUTBOX|STRATEGY_ACTION_DISPATCH|strategy_action|'||v_entry_id::text,'UTF8'),'sha256'),'hex'); insert into public.execution_outbox(event_type,entity_type,entity_id,correlation_id,payload,idempotency_key) values('STRATEGY_ACTION_DISPATCH','strategy_action',v_entry_id,v_entry_key,'{}'::jsonb,v_outbox_key) on conflict(idempotency_key) do nothing;
        v_actions:=v_actions+1;
      end if;
    end if;
  end if;

  if v_shadow.reference_position_state in ('LONG','SHORT') then v_shadow.peak_high:=greatest(v_shadow.peak_high,v_bar.high); v_shadow.trough_low:=least(v_shadow.trough_low,v_bar.low); end if;
  if not v_emit then
    v_shadow.dispatch_frontier_5m_open_time:=greatest(coalesce(v_shadow.dispatch_frontier_5m_open_time,v_bar.open_time),v_bar.open_time);
  end if;
  update public.paper_strategy_shadow_state set
    data_state=v_shadow.data_state,reference_position_state=v_shadow.reference_position_state,reference_entry_action_id=v_shadow.reference_entry_action_id,
    reference_signal_close_time=v_shadow.reference_signal_close_time,reference_entry_time=v_shadow.reference_entry_time,reference_entry_price=v_shadow.reference_entry_price,reference_entry_atr=v_shadow.reference_entry_atr,
    active_stop=v_shadow.active_stop,peak_high=v_shadow.peak_high,trough_low=v_shadow.trough_low,last_processed_5m_open_time=v_bar.open_time,
    last_processed_1h_close_time=v_hour_close,dispatch_frontier_5m_open_time=v_shadow.dispatch_frontier_5m_open_time,invalid_reason=null,invalid_at_5m_open_time=null,state_version=state_version+1,updated_at=clock_timestamp()
  where strategy_version_id=p_strategy_version_id and pair=upper(p_pair);
  return jsonb_build_object('applied',true,'emitted_actions',v_actions,'reference_position_state',v_shadow.reference_position_state,'cursor',v_bar.open_time,'dispatch_emitted',v_emit);
end;$func$;

alter table public.paper_strategy_reference_bars_5m enable row level security;
revoke execute on function public.paper_spec006_ingest_reference_bar(text,text,timestamptz,timestamptz,numeric,numeric,numeric,numeric,text) from public,anon,authenticated;
revoke execute on function public.paper_spec006_atr_at(text,text,timestamptz) from public,anon,authenticated;
revoke execute on function public.paper_spec006_signal_at(text,text,timestamptz) from public,anon,authenticated;
revoke execute on function public.paper_spec006_action_key(text,text,text,timestamptz,timestamptz,text,text) from public,anon,authenticated;
revoke execute on function public.paper_spec006_commit_reference_bar(text,text,timestamptz,text) from public,anon,authenticated;
grant execute on function public.paper_spec006_ingest_reference_bar(text,text,timestamptz,timestamptz,numeric,numeric,numeric,numeric,text) to service_role;
grant execute on function public.paper_spec006_commit_reference_bar(text,text,timestamptz,text) to service_role;