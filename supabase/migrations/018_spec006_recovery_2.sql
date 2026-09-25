-- Synced from live Supabase migration 20260925092503 (spec006_recovery).
-- IMPLEMENTATION-SPEC-006. Runtime B remains disabled.

-- SPEC-006 sticky INVALID recovery commit primitives. No official B reads/scoring.

create or replace function public.paper_spec006_commit_recovery_same_cycle(
  p_strategy_version_id text,
  p_pair text,
  p_expected_entry_action_id uuid,
  p_cutoff_open_time timestamptz,
  p_enable_commit_time timestamptz,
  p_reference_state jsonb,
  p_software_commit text
)
returns jsonb
language plpgsql
security definer
set search_path=public
as $func$
declare
  v_fence public.paper_strategy_execution_fence;
  v_shadow public.paper_strategy_shadow_state;
  v_pos public.paper_positions;
  v_owner public.paper_position_entry_ownership;
  v_latest timestamptz;
  v_side text;
  v_safe boolean;
begin
  select * into v_fence
  from public.paper_strategy_execution_fence
  where strategy_version_id=p_strategy_version_id and pair=upper(p_pair)
  for update;
  if not found then raise exception 'spec006_fence_missing'; end if;

  select * into v_shadow
  from public.paper_strategy_shadow_state
  where strategy_version_id=p_strategy_version_id and pair=upper(p_pair)
  for update;
  if not found or v_shadow.data_state<>'INVALID' then
    raise exception 'spec006_recovery_requires_invalid';
  end if;

  if v_shadow.reference_entry_action_id is distinct from p_expected_entry_action_id
     or p_expected_entry_action_id is null then
    raise exception 'recovery_cycle_identity_mismatch';
  end if;

  v_side:=p_reference_state->>'reference_position_state';
  if v_side not in ('LONG','SHORT') or v_side<>v_shadow.reference_position_state then
    raise exception 'recovery_reference_side_mismatch';
  end if;

  select max(open_time) into v_latest
  from public.paper_strategy_reference_bars_5m
  where strategy_version_id=p_strategy_version_id
    and pair=upper(p_pair)
    and close_time<=p_enable_commit_time;

  if v_latest is null or v_latest is distinct from p_cutoff_open_time then
    raise exception 'recovery_cutoff_not_frontier';
  end if;

  select * into v_pos
  from public.paper_positions
  where strategy_version_id=p_strategy_version_id
    and pair=upper(p_pair)
    and status in ('OPENING','OPEN','EXIT_PENDING','CLOSING')
  for update;

  if not found or v_pos.side<>v_side then
    return jsonb_build_object('resumed',false,'reason','PAPER_POSITION_MISMATCH');
  end if;

  select * into v_owner
  from public.paper_position_entry_ownership
  where position_id=v_pos.id;

  if not found or v_owner.entry_action_id<>p_expected_entry_action_id then
    return jsonb_build_object('resumed',false,'reason','PAPER_CYCLE_OWNERSHIP_MISMATCH');
  end if;

  if exists(
    select 1 from public.paper_exit_intents
    where claimed_position_id=v_pos.id and status in ('OPEN','PAUSED','CRITICAL')
  ) then
    return jsonb_build_object('resumed',false,'reason','LIVE_FLATTEN_OR_CRITICAL_CLAIM');
  end if;

  v_safe:=public.paper_spec006_fence_entry_lifecycle(
    p_expected_entry_action_id,
    'RECOVERY_RESUME_FENCE_ENTRY_REMAINDER',
    'spec006-recovery',
    p_software_commit
  );
  if not v_safe then
    return jsonb_build_object('resumed',false,'reason','WAIT_ENTRY_IN_FLIGHT');
  end if;

  update public.paper_strategy_shadow_state
  set data_state='CONTIGUOUS',
      reference_position_state=v_side,
      reference_entry_action_id=p_expected_entry_action_id,
      reference_signal_close_time=(p_reference_state->>'reference_signal_close_time')::timestamptz,
      reference_entry_time=(p_reference_state->>'reference_entry_time')::timestamptz,
      reference_entry_price=(p_reference_state->>'reference_entry_price')::numeric,
      reference_entry_atr=(p_reference_state->>'reference_entry_atr')::numeric,
      active_stop=(p_reference_state->>'active_stop')::numeric,
      peak_high=(p_reference_state->>'peak_high')::numeric,
      trough_low=(p_reference_state->>'trough_low')::numeric,
      last_processed_5m_open_time=p_cutoff_open_time,
      last_processed_1h_close_time=date_trunc('hour',p_cutoff_open_time)-interval '1 millisecond',
      dispatch_frontier_5m_open_time=p_cutoff_open_time,
      dispatch_enable_commit_time=p_enable_commit_time,
      invalid_reason=null,
      invalid_at_5m_open_time=null,
      state_version=state_version+1,
      updated_at=clock_timestamp()
  where strategy_version_id=p_strategy_version_id and pair=upper(p_pair);

  return jsonb_build_object('resumed',true,'dispatch_enabled',true);
end;
$func$;

create or replace function public.paper_spec006_commit_recovery_flat(
  p_strategy_version_id text,
  p_pair text,
  p_cutoff_open_time timestamptz,
  p_enable_commit_time timestamptz
)
returns jsonb
language plpgsql
security definer
set search_path=public,extensions
as $func$
declare
  v_fence public.paper_strategy_execution_fence;
  v_shadow public.paper_strategy_shadow_state;
  v_pos public.paper_positions;
  v_latest timestamptz;
  v_existing public.paper_exit_intents;
  v_key text;
  v_intent_id uuid;
  v_unsafe_entry boolean;
begin
  select * into v_fence
  from public.paper_strategy_execution_fence
  where strategy_version_id=p_strategy_version_id and pair=upper(p_pair)
  for update;
  if not found then raise exception 'spec006_fence_missing'; end if;

  select * into v_shadow
  from public.paper_strategy_shadow_state
  where strategy_version_id=p_strategy_version_id and pair=upper(p_pair)
  for update;
  if not found or v_shadow.data_state<>'INVALID' then
    raise exception 'spec006_recovery_requires_invalid';
  end if;

  select max(open_time) into v_latest
  from public.paper_strategy_reference_bars_5m
  where strategy_version_id=p_strategy_version_id
    and pair=upper(p_pair)
    and close_time<=p_enable_commit_time;

  if v_latest is null or v_latest is distinct from p_cutoff_open_time then
    raise exception 'recovery_cutoff_not_frontier';
  end if;

  select * into v_pos
  from public.paper_positions
  where strategy_version_id=p_strategy_version_id
    and pair=upper(p_pair)
    and status in ('OPENING','OPEN','EXIT_PENDING','CLOSING')
  for update;

  select exists(
    select 1
    from public.paper_entry_lifecycles l
    where l.strategy_version_id=p_strategy_version_id
      and l.pair=upper(p_pair)
      and not public.paper_spec006_entry_safe_terminal(l.entry_action_id)
  ) into v_unsafe_entry;

  update public.paper_strategy_shadow_state
  set reference_position_state='FLAT',
      reference_entry_action_id=null,
      reference_signal_close_time=null,
      reference_entry_time=null,
      reference_entry_price=null,
      reference_entry_atr=null,
      active_stop=null,
      peak_high=null,
      trough_low=null,
      last_processed_5m_open_time=p_cutoff_open_time,
      last_processed_1h_close_time=date_trunc('hour',p_cutoff_open_time)-interval '1 millisecond',
      dispatch_frontier_5m_open_time=p_cutoff_open_time,
      dispatch_enable_commit_time=p_enable_commit_time,
      state_version=state_version+1,
      updated_at=clock_timestamp()
  where strategy_version_id=p_strategy_version_id and pair=upper(p_pair);

  if v_pos.id is null then
    if v_unsafe_entry then
      return jsonb_build_object('resumed',false,'reason','UNSAFE_ENTRY_PATHS');
    end if;
    if exists(
      select 1 from public.paper_exit_intents
      where strategy_version_id=p_strategy_version_id
        and pair=upper(p_pair)
        and status in ('OPEN','PAUSED','CRITICAL')
    ) then
      return jsonb_build_object('resumed',false,'reason','LIVE_FLATTEN_OR_CRITICAL_CLAIM');
    end if;

    update public.paper_strategy_shadow_state
    set data_state='CONTIGUOUS',
        invalid_reason=null,
        invalid_at_5m_open_time=null,
        state_version=state_version+1,
        updated_at=clock_timestamp()
    where strategy_version_id=p_strategy_version_id and pair=upper(p_pair);

    return jsonb_build_object('resumed',true,'dispatch_enabled',true);
  end if;

  select * into v_existing
  from public.paper_exit_intents
  where claimed_position_id=v_pos.id
    and status in ('OPEN','PAUSED','CRITICAL')
  limit 1;

  if found then
    return jsonb_build_object(
      'resumed',false,
      'reason',case when v_existing.status='CRITICAL' then 'CRITICAL_OWNER_BLOCKS_RECOVERY' else 'WAIT_EXISTING_OWNER' end,
      'exit_intent_id',v_existing.id
    );
  end if;

  if exists(
    select 1 from public.paper_exit_intents
    where strategy_version_id=p_strategy_version_id
      and pair=upper(p_pair)
      and status='CRITICAL'
      and claimed_position_id is null
  ) then
    return jsonb_build_object('resumed',false,'reason','MALFORMED_CRITICAL_BLOCKS_RECOVERY');
  end if;

  v_key:=encode(
    digest(
      convert_to(
        'INVALID_RECOVERY|'||v_shadow.adapter_epoch_id::text||'|'||
        p_strategy_version_id||'|'||upper(p_pair)||'|'||
        floor(extract(epoch from v_shadow.invalid_at_5m_open_time)*1000)::bigint,
        'UTF8'
      ),
      'sha256'
    ),
    'hex'
  );

  insert into public.paper_exit_intents(
    adapter_epoch_id,intent_origin,recovery_key,claimed_position_id,
    strategy_version_id,pair,locked_reduce_side,status,last_error_code
  )
  values(
    v_shadow.adapter_epoch_id,'INVALID_RECOVERY',v_key,v_pos.id,
    p_strategy_version_id,upper(p_pair),
    case when v_pos.side='LONG' then 'SELL' else 'BUY' end,
    'OPEN','INVALID_RECOVERY_FLATTEN'
  )
  on conflict(recovery_key) where recovery_key is not null
  do update set recovery_key=excluded.recovery_key
  returning id into v_intent_id;

  return jsonb_build_object(
    'resumed',false,
    'reason','RECOVERY_FLATTEN_REQUIRED',
    'exit_intent_id',v_intent_id
  );
end;
$func$;

revoke execute on function public.paper_spec006_commit_recovery_same_cycle(text,text,uuid,timestamptz,timestamptz,jsonb,text)
from public,anon,authenticated;
revoke execute on function public.paper_spec006_commit_recovery_flat(text,text,timestamptz,timestamptz)
from public,anon,authenticated;

grant execute on function public.paper_spec006_commit_recovery_same_cycle(text,text,uuid,timestamptz,timestamptz,jsonb,text)
to service_role;
grant execute on function public.paper_spec006_commit_recovery_flat(text,text,timestamptz,timestamptz)
to service_role;

