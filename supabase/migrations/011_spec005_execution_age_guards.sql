-- SPEC-005 execution-age guard layer.
-- Keeps core RPC semantics intact but makes guarded RPCs the only service-role entry path.

create or replace function public.paper_ingest_signal_guarded(
  p_strategy_id text,
  p_strategy_version_id text,
  p_test_spec_id text,
  p_pair text,
  p_side text,
  p_signal_close_time timestamptz,
  p_intended_entry_time timestamptz,
  p_intended_notional numeric,
  p_source_timeframe text,
  p_execution_timeframe text,
  p_reason_code text,
  p_rule_id text,
  p_correlation_id text,
  p_blind_test_id text,
  p_generated_at timestamptz,
  p_software_commit text
)
returns jsonb
language plpgsql
security definer
set search_path=public
as $func$
declare
  v_result jsonb;
  v_signal_id uuid;
begin
  if p_intended_entry_time < p_signal_close_time then
    raise exception 'intended_entry_before_signal_close';
  end if;

  v_result := public.paper_ingest_signal(
    p_strategy_id,p_strategy_version_id,p_test_spec_id,p_pair,p_side,
    p_signal_close_time,p_intended_entry_time,p_intended_notional,
    p_source_timeframe,p_execution_timeframe,p_reason_code,p_rule_id,
    p_correlation_id,p_blind_test_id,p_generated_at,p_software_commit
  );

  v_signal_id := (v_result->>'signal_id')::uuid;

  update public.execution_outbox
  set available_at=greatest(available_at,p_intended_entry_time)
  where entity_type='signal'
    and entity_id=v_signal_id
    and event_type='RISK_EVALUATE'
    and processed_at is null;

  return v_result;
end;
$func$;

create or replace function public.paper_reserve_risk_guarded(
  p_signal_id uuid,
  p_policy_version_id uuid,
  p_account_key text default 'paper-default',
  p_worker_id text default 'risk-engine',
  p_software_commit text default 'unknown'
)
returns jsonb
language plpgsql
security definer
set search_path=public
as $func$
declare
  v_signal public.paper_signals;
  v_policy public.risk_policy_versions;
  v_age_ms numeric;
begin
  select * into v_signal
  from public.paper_signals
  where id=p_signal_id
  for share;

  if not found then raise exception 'signal_not_found'; end if;

  select * into v_policy
  from public.risk_policy_versions
  where id=p_policy_version_id;

  if not found then raise exception 'risk_policy_not_found'; end if;
  if v_policy.max_signal_age_ms is null then
    raise exception 'max_signal_age_required';
  end if;

  if clock_timestamp() < v_signal.intended_entry_time then
    return jsonb_build_object(
      'decision','REJECTED',
      'reason_code','RISK_REJECT_BEFORE_INTENDED_ENTRY',
      'guard_rejected',true
    );
  end if;

  v_age_ms := extract(epoch from (clock_timestamp()-v_signal.intended_entry_time))*1000;

  if v_age_ms > v_policy.max_signal_age_ms then
    -- Persist the rejection as the canonical PRETRADE decision so retries stay deterministic.
    insert into public.risk_decisions(
      signal_id,decision_stage,decision,policy_version_id,observed,reason_code,idempotency_key
    )
    values(
      p_signal_id,'PRETRADE','REJECTED',p_policy_version_id,
      jsonb_build_object(
        'signal_age_ms',v_age_ms,
        'max_signal_age_ms',v_policy.max_signal_age_ms,
        'intended_entry_time',v_signal.intended_entry_time
      ),
      'RISK_REJECT_SIGNAL_STALE',
      encode(extensions.digest(convert_to(
        'RISK|'||p_signal_id::text||'|PRETRADE','UTF8'
      ),'sha256'),'hex')
    )
    on conflict(signal_id,decision_stage) do nothing;

    return jsonb_build_object(
      'decision','REJECTED',
      'reason_code','RISK_REJECT_SIGNAL_STALE',
      'guard_rejected',true
    );
  end if;

  return public.paper_reserve_risk(
    p_signal_id,p_policy_version_id,p_account_key,p_worker_id,p_software_commit
  );
end;
$func$;

create or replace function public.paper_bind_execution_attempt_guarded(
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
set search_path=public
as $func$
declare
  v_order public.paper_orders;
  v_policy public.risk_policy_versions;
  v_age_ms numeric;
begin
  select * into v_order
  from public.paper_orders
  where id=p_order_id
  for share;

  if not found then raise exception 'order_not_found'; end if;

  select * into v_policy
  from public.risk_policy_versions
  where id=p_policy_version_id;

  if not found then raise exception 'risk_policy_not_found'; end if;
  if v_policy.max_order_age_ms is null then
    raise exception 'max_order_age_required';
  end if;

  v_age_ms := extract(epoch from (clock_timestamp()-v_order.created_at))*1000;
  if v_age_ms > v_policy.max_order_age_ms then
    return jsonb_build_object(
      'bound',false,
      'reason','ORDER_EXPIRED',
      'order_age_ms',v_age_ms
    );
  end if;

  return public.paper_bind_execution_attempt(
    p_outbox_id,p_lease_generation,p_order_id,p_market_snapshot_id,
    p_policy_version_id,p_attempt_seq,p_worker_id,p_software_commit
  );
end;
$func$;

create or replace function public.paper_apply_fill_guarded(
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
set search_path=public
as $func$
declare
  v_order public.paper_orders;
  v_attempt public.paper_execution_attempts;
  v_snapshot public.execution_market_snapshots;
  v_policy public.risk_policy_versions;
  v_order_age_ms numeric;
  v_book_age_ms numeric;
begin
  select * into v_order
  from public.paper_orders
  where id=p_order_id
  for share;

  if not found then raise exception 'order_not_found'; end if;

  select * into v_attempt
  from public.paper_execution_attempts
  where id=p_execution_attempt_id
    and order_id=p_order_id
  for share;

  if not found then raise exception 'execution_attempt_mismatch'; end if;

  select * into v_policy
  from public.risk_policy_versions
  where id=v_attempt.policy_version_id;

  if not found then raise exception 'risk_policy_missing'; end if;

  select * into v_snapshot
  from public.execution_market_snapshots
  where id=v_attempt.market_snapshot_id;

  if not found then raise exception 'market_snapshot_missing'; end if;

  if v_policy.max_order_age_ms is null then
    raise exception 'max_order_age_required';
  end if;
  if v_policy.max_book_age_ms is null then
    raise exception 'max_book_age_required';
  end if;

  v_order_age_ms := extract(epoch from (clock_timestamp()-v_order.created_at))*1000;
  if v_order_age_ms > v_policy.max_order_age_ms then
    return jsonb_build_object(
      'applied',false,
      'reason','ORDER_EXPIRED',
      'order_age_ms',v_order_age_ms
    );
  end if;

  v_book_age_ms := extract(epoch from (clock_timestamp()-v_snapshot.received_at))*1000;
  if v_book_age_ms > v_policy.max_book_age_ms then
    return jsonb_build_object(
      'applied',false,
      'reason','STALE_MARKET_SNAPSHOT',
      'book_age_ms',v_book_age_ms
    );
  end if;

  return public.paper_apply_fill(
    p_outbox_id,p_lease_generation,p_order_id,p_execution_attempt_id,
    p_fill_seq,p_fill_quantity,p_fill_price,p_fee_amount,p_spread_bps,
    p_impact_bps,p_filled_at,p_account_key,p_worker_id,p_software_commit
  );
end;
$func$;

-- Guarded functions are the only service-role entry points for these economic steps.
do $secure$
declare
  r record;
begin
  for r in
    select p.oid::regprocedure as signature
    from pg_proc p
    join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public'
      and p.proname in (
        'paper_ingest_signal',
        'paper_reserve_risk',
        'paper_bind_execution_attempt',
        'paper_apply_fill'
      )
  loop
    execute format('revoke all on function %s from PUBLIC,anon,authenticated,service_role',r.signature);
  end loop;

  for r in
    select p.oid::regprocedure as signature
    from pg_proc p
    join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public'
      and p.proname in (
        'paper_ingest_signal_guarded',
        'paper_reserve_risk_guarded',
        'paper_bind_execution_attempt_guarded',
        'paper_apply_fill_guarded'
      )
  loop
    execute format('revoke all on function %s from PUBLIC,anon,authenticated',r.signature);
    execute format('grant execute on function %s to service_role',r.signature);
  end loop;
end;
$secure$;
