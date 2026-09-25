-- SPEC-006 bootstrap/frontier primitives. Does not enable runtime binding.

create or replace function public.paper_spec006_ingest_reference_bars(p_rows jsonb)
returns jsonb
language plpgsql
security definer
set search_path=public
as $func$
declare
  r record;
  v_count integer:=0;
begin
  if jsonb_typeof(p_rows)<>'array' then raise exception 'reference_bars_array_required'; end if;

  for r in
    select *
    from jsonb_to_recordset(p_rows) as x(
      strategy_version_id text,
      pair text,
      open_time timestamptz,
      close_time timestamptz,
      open numeric,
      high numeric,
      low numeric,
      close numeric,
      source_hash text
    )
  loop
    perform public.paper_spec006_ingest_reference_bar(
      r.strategy_version_id,r.pair,r.open_time,r.close_time,
      r.open,r.high,r.low,r.close,r.source_hash
    );
    v_count:=v_count+1;
  end loop;

  return jsonb_build_object('ingested',v_count);
end;
$func$;

create or replace function public.paper_spec006_initialize_flat_epoch(
  p_strategy_version_id text,
  p_pair text,
  p_cursor_open_time timestamptz,
  p_enable_commit_time timestamptz
)
returns jsonb
language plpgsql
security definer
set search_path=public
as $func$
declare
  v_fence public.paper_strategy_execution_fence;
  v_latest timestamptz;
  v_epoch uuid:=gen_random_uuid();
begin
  insert into public.paper_strategy_execution_fence(strategy_version_id,pair)
  values(p_strategy_version_id,upper(p_pair))
  on conflict do nothing;

  select * into v_fence
  from public.paper_strategy_execution_fence
  where strategy_version_id=p_strategy_version_id and pair=upper(p_pair)
  for update;

  if exists(
    select 1 from public.paper_strategy_shadow_state
    where strategy_version_id=p_strategy_version_id and pair=upper(p_pair)
  ) then
    return jsonb_build_object('initialized',false,'reason','SHADOW_ALREADY_EXISTS');
  end if;

  if exists(
    select 1 from public.paper_strategy_runtime_bindings
    where strategy_version_id=p_strategy_version_id and enabled
  ) then
    raise exception 'runtime_binding_must_be_disabled';
  end if;

  if exists(
    select 1 from public.paper_positions
    where strategy_version_id=p_strategy_version_id and pair=upper(p_pair)
      and status in ('OPENING','OPEN','EXIT_PENDING','CLOSING')
  ) then
    raise exception 'bootstrap_paper_not_flat';
  end if;

  if exists(
    select 1 from public.paper_orders
    where strategy_version_id=p_strategy_version_id and pair=upper(p_pair)
      and status in ('CREATED','ACCEPTED','PENDING_FILL','PARTIALLY_FILLED','CANCEL_REQUESTED')
  ) then
    raise exception 'bootstrap_nonterminal_order';
  end if;

  if exists(
    select 1 from public.paper_exit_intents
    where strategy_version_id=p_strategy_version_id and pair=upper(p_pair)
      and status in ('OPEN','PAUSED','CRITICAL')
  ) then
    raise exception 'bootstrap_live_flatten_claim';
  end if;

  select max(open_time) into v_latest
  from public.paper_strategy_reference_bars_5m
  where strategy_version_id=p_strategy_version_id
    and pair=upper(p_pair)
    and close_time<=p_enable_commit_time;

  if v_latest is null or v_latest is distinct from p_cursor_open_time then
    raise exception 'bootstrap_cursor_not_frontier';
  end if;

  insert into public.paper_strategy_shadow_state(
    strategy_version_id,pair,adapter_epoch_id,data_state,reference_position_state,
    last_processed_5m_open_time,last_processed_1h_close_time,
    dispatch_frontier_5m_open_time,dispatch_enable_commit_time
  )
  values(
    p_strategy_version_id,upper(p_pair),v_epoch,'CONTIGUOUS','FLAT',
    p_cursor_open_time,date_trunc('hour',p_cursor_open_time)-interval '1 millisecond',
    p_cursor_open_time,p_enable_commit_time
  );

  return jsonb_build_object(
    'initialized',true,
    'adapter_epoch_id',v_epoch,
    'dispatch_enabled',false
  );
end;
$func$;

revoke execute on function public.paper_spec006_ingest_reference_bars(jsonb)
from public,anon,authenticated;
revoke execute on function public.paper_spec006_initialize_flat_epoch(text,text,timestamptz,timestamptz)
from public,anon,authenticated;

grant execute on function public.paper_spec006_ingest_reference_bars(jsonb) to service_role;
grant execute on function public.paper_spec006_initialize_flat_epoch(text,text,timestamptz,timestamptz)
to service_role;
