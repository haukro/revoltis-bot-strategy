-- SPEC-006 runtime binding setup/activation gates. This migration does not create or enable a binding.

create or replace function public.paper_spec006_configure_disabled_binding(
  p_strategy_version_id text,
  p_risk_policy_version_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path=public
as $func$
declare
  v_policy public.risk_policy_versions;
  v_existing public.paper_strategy_runtime_bindings;
begin
  select * into v_policy
  from public.risk_policy_versions
  where id=p_risk_policy_version_id;
  if not found then raise exception 'risk_policy_not_found'; end if;

  select * into v_existing
  from public.paper_strategy_runtime_bindings
  where strategy_version_id=p_strategy_version_id
  for update;

  if found then
    if v_existing.risk_policy_version_id<>p_risk_policy_version_id then
      raise exception 'runtime_binding_policy_already_locked';
    end if;
    return jsonb_build_object(
      'configured',true,
      'enabled',v_existing.enabled,
      'idempotent_replay',true
    );
  end if;

  insert into public.paper_strategy_runtime_bindings(
    strategy_version_id,risk_policy_version_id,enabled
  )
  values(p_strategy_version_id,p_risk_policy_version_id,false);

  return jsonb_build_object(
    'configured',true,
    'enabled',false,
    'idempotent_replay',false
  );
end;
$func$;

create or replace function public.paper_spec006_enable_runtime_binding(
  p_strategy_version_id text,
  p_pair text
)
returns jsonb
language plpgsql
security definer
set search_path=public
as $func$
declare
  v_fence public.paper_strategy_execution_fence;
  v_shadow public.paper_strategy_shadow_state;
  v_binding public.paper_strategy_runtime_bindings;
  v_pos public.paper_positions;
  v_latest timestamptz;
  v_now timestamptz:=clock_timestamp();
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
  if not found then raise exception 'spec006_shadow_missing'; end if;

  select * into v_pos
  from public.paper_positions
  where strategy_version_id=p_strategy_version_id
    and pair=upper(p_pair)
    and status in ('OPENING','OPEN','EXIT_PENDING','CLOSING')
  for update;

  select * into v_binding
  from public.paper_strategy_runtime_bindings
  where strategy_version_id=p_strategy_version_id
  for update;
  if not found then raise exception 'runtime_binding_not_configured'; end if;

  if v_binding.enabled then
    return jsonb_build_object('enabled',true,'idempotent_replay',true);
  end if;

  if v_shadow.data_state<>'CONTIGUOUS'
     or v_shadow.reference_position_state<>'FLAT'
     or v_shadow.reference_entry_action_id is not null then
    raise exception 'activation_reference_not_safe_flat';
  end if;

  if v_pos.id is not null then
    raise exception 'activation_paper_not_flat';
  end if;

  if exists(
    select 1 from public.paper_orders
    where strategy_version_id=p_strategy_version_id
      and pair=upper(p_pair)
      and status in ('CREATED','ACCEPTED','PENDING_FILL','PARTIALLY_FILLED','CANCEL_REQUESTED')
  ) then
    raise exception 'activation_nonterminal_order';
  end if;

  if exists(
    select 1 from public.paper_entry_lifecycles l
    where l.strategy_version_id=p_strategy_version_id
      and l.pair=upper(p_pair)
      and not public.paper_spec006_entry_safe_terminal(l.entry_action_id)
  ) then
    raise exception 'activation_entry_path_not_terminal';
  end if;

  if exists(
    select 1 from public.paper_exit_intents
    where strategy_version_id=p_strategy_version_id
      and pair=upper(p_pair)
      and status in ('OPEN','PAUSED','CRITICAL')
  ) then
    raise exception 'activation_live_flatten_or_critical';
  end if;

  select max(open_time) into v_latest
  from public.paper_strategy_reference_bars_5m
  where strategy_version_id=p_strategy_version_id
    and pair=upper(p_pair)
    and close_time<=v_now;

  if v_latest is null
     or v_shadow.last_processed_5m_open_time is distinct from v_latest then
    raise exception 'activation_not_at_reference_frontier';
  end if;

  update public.paper_strategy_shadow_state
  set dispatch_frontier_5m_open_time=v_latest,
      dispatch_enable_commit_time=v_now,
      state_version=state_version+1,
      updated_at=v_now
  where strategy_version_id=p_strategy_version_id and pair=upper(p_pair);

  update public.paper_strategy_runtime_bindings
  set enabled=true
  where strategy_version_id=p_strategy_version_id;

  return jsonb_build_object('enabled',true,'idempotent_replay',false);
end;
$func$;

revoke execute on function public.paper_spec006_configure_disabled_binding(text,uuid)
from public,anon,authenticated;
revoke execute on function public.paper_spec006_enable_runtime_binding(text,text)
from public,anon,authenticated;

grant execute on function public.paper_spec006_configure_disabled_binding(text,uuid)
to service_role;
grant execute on function public.paper_spec006_enable_runtime_binding(text,text)
to service_role;
