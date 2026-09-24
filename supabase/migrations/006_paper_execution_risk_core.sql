-- IMPLEMENTATION-SPEC-004 core paper execution / risk infrastructure.
-- Strategy-neutral. No live routing. No alpha changes.

create extension if not exists pgcrypto with schema extensions;

create or replace function public.paper_reject_immutable_mutation()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  raise exception 'immutable_table:%', tg_table_name;
end;
$$;

create table if not exists public.execution_market_snapshots (
  id uuid primary key default gen_random_uuid(),
  pair text not null,
  provider text not null default 'okx',
  provider_ts_ms bigint not null,
  received_at timestamptz not null,
  bid_levels jsonb not null,
  ask_levels jsonb not null,
  best_bid numeric(30, 12) not null check (best_bid > 0),
  best_ask numeric(30, 12) not null check (best_ask > 0),
  snapshot_hash text not null,
  software_commit text not null,
  created_at timestamptz not null default now(),
  unique(provider, pair, provider_ts_ms, snapshot_hash)
);

create table if not exists public.paper_signals (
  id uuid primary key default gen_random_uuid(),
  signal_id text not null unique,
  strategy_id text not null,
  strategy_version_id text not null,
  test_spec_id text,
  pair text not null,
  side text not null check (side in ('LONG','SHORT')),
  signal_close_time timestamptz not null,
  intended_entry_time timestamptz not null,
  intended_notional numeric(30, 12) not null check (intended_notional > 0),
  source_timeframe text not null,
  execution_timeframe text not null,
  reason_code text not null,
  rule_id text not null,
  correlation_id text not null,
  blind_test_id text,
  generated_at timestamptz not null,
  persisted_at timestamptz not null default now(),
  idempotency_key text not null unique,
  software_commit text not null
);

create table if not exists public.risk_policy_versions (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  max_position_notional numeric(30, 12),
  max_total_gross_exposure numeric(30, 12),
  max_net_exposure numeric(30, 12),
  max_asset_exposure numeric(30, 12),
  max_concurrent_positions integer,
  max_open_per_strategy_pair integer,
  max_daily_realized_loss numeric(30, 12),
  max_intraday_drawdown numeric(18, 10),
  max_book_age_ms bigint,
  max_signal_age_ms bigint,
  max_order_age_ms bigint,
  max_spread_bps numeric(18, 8),
  min_book_depth_multiple numeric(18, 8),
  abnormal_slippage_bps numeric(18, 8),
  partial_fill_allowed boolean not null default false,
  created_at timestamptz not null default now(),
  locked_at timestamptz not null default now(),
  check (max_concurrent_positions is null or max_concurrent_positions > 0),
  check (max_open_per_strategy_pair is null or max_open_per_strategy_pair > 0),
  check (max_book_age_ms is null or max_book_age_ms >= 0),
  check (max_signal_age_ms is null or max_signal_age_ms >= 0),
  check (max_order_age_ms is null or max_order_age_ms >= 0)
);

create table if not exists public.portfolio_risk_state (
  account_key text primary key,
  used_gross_notional numeric(30, 12) not null default 0,
  reserved_gross_notional numeric(30, 12) not null default 0,
  used_net_notional numeric(30, 12) not null default 0,
  reserved_net_notional numeric(30, 12) not null default 0,
  realized_pnl_utc_day numeric(30, 12) not null default 0,
  risk_utc_day date not null default (now() at time zone 'utc')::date,
  intraday_peak_equity numeric(30, 12),
  internal_marked_equity numeric(30, 12),
  critical_reconciliation boolean not null default false,
  state_version bigint not null default 1,
  updated_at timestamptz not null default now()
);

insert into public.portfolio_risk_state(account_key)
values ('paper-default')
on conflict (account_key) do nothing;

create table if not exists public.risk_decisions (
  id uuid primary key default gen_random_uuid(),
  signal_id uuid not null references public.paper_signals(id),
  decision_stage text not null default 'PRETRADE',
  decision text not null check (decision in ('APPROVED','REJECTED')),
  policy_version_id uuid not null references public.risk_policy_versions(id),
  observed jsonb not null default '{}'::jsonb,
  reason_code text not null,
  idempotency_key text not null unique,
  created_at timestamptz not null default now(),
  unique(signal_id, decision_stage)
);

create table if not exists public.risk_reservations (
  id uuid primary key default gen_random_uuid(),
  signal_id uuid not null unique references public.paper_signals(id),
  strategy_version_id text not null,
  pair text not null,
  side text not null check (side in ('LONG','SHORT')),
  reserved_notional numeric(30, 12) not null check (reserved_notional > 0),
  consumed_notional numeric(30, 12) not null default 0 check (consumed_notional >= 0),
  status text not null check (status in ('RESERVED','PARTIALLY_CONSUMED','CONSUMED','RELEASED')),
  policy_version_id uuid not null references public.risk_policy_versions(id),
  created_at timestamptz not null default now(),
  consumed_at timestamptz,
  released_at timestamptz,
  release_reason text,
  state_version bigint not null default 1,
  check (consumed_notional <= reserved_notional)
);

create table if not exists public.paper_orders (
  id uuid primary key default gen_random_uuid(),
  signal_id uuid not null references public.paper_signals(id),
  strategy_version_id text not null,
  pair text not null,
  side text not null check (side in ('BUY','SELL')),
  intent_type text not null check (intent_type in ('ENTRY','EXIT','EMERGENCY_EXIT')),
  order_type text not null default 'MARKET' check (order_type in ('MARKET')),
  intended_notional numeric(30, 12) not null check (intended_notional > 0),
  intended_quantity numeric(30, 12),
  filled_quantity numeric(30, 12) not null default 0,
  filled_notional numeric(30, 12) not null default 0,
  status text not null check (status in ('CREATED','ACCEPTED','PENDING_FILL','PARTIALLY_FILLED','FILLED','CANCEL_REQUESTED','CANCELLED','REJECTED','EXPIRED','FAILED')),
  rejection_reason text,
  idempotency_key text not null unique,
  accepted_at timestamptz,
  pending_at timestamptz,
  cancel_requested_at timestamptz,
  terminal_at timestamptz,
  state_version bigint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.paper_order_events (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references public.paper_orders(id),
  from_state text,
  to_state text not null,
  reason_code text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.execution_outbox (
  id uuid primary key default gen_random_uuid(),
  event_type text not null,
  entity_type text not null,
  entity_id uuid not null,
  correlation_id text not null,
  payload jsonb not null default '{}'::jsonb,
  available_at timestamptz not null default now(),
  claimed_at timestamptz,
  claim_expires_at timestamptz,
  claimed_by text,
  attempt_count integer not null default 0,
  processed_at timestamptz,
  last_error text,
  idempotency_key text not null unique,
  created_at timestamptz not null default now()
);

create table if not exists public.paper_execution_attempts (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references public.paper_orders(id),
  attempt_seq integer not null check (attempt_seq > 0),
  market_snapshot_id uuid not null references public.execution_market_snapshots(id),
  policy_version_id uuid not null references public.risk_policy_versions(id),
  status text not null check (status in ('BOUND','APPLIED','REJECTED')),
  rejection_reason text,
  idempotency_key text not null unique,
  created_at timestamptz not null default now(),
  applied_at timestamptz,
  unique(order_id, attempt_seq)
);

create table if not exists public.paper_fills (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references public.paper_orders(id),
  execution_attempt_id uuid not null references public.paper_execution_attempts(id),
  strategy_version_id text not null,
  pair text not null,
  fill_seq integer not null check (fill_seq > 0),
  fill_quantity numeric(30, 12) not null check (fill_quantity > 0),
  fill_price numeric(30, 12) not null check (fill_price > 0),
  fee_amount numeric(30, 12) not null default 0 check (fee_amount >= 0),
  spread_bps numeric(18, 8) not null default 0,
  impact_bps numeric(18, 8) not null default 0,
  filled_at timestamptz not null,
  idempotency_key text not null unique,
  created_at timestamptz not null default now(),
  unique(order_id, fill_seq)
);

create table if not exists public.paper_positions (
  id uuid primary key default gen_random_uuid(),
  strategy_version_id text not null,
  pair text not null,
  side text not null check (side in ('LONG','SHORT')),
  quantity numeric(30, 12) not null default 0 check (quantity >= 0),
  average_entry_price numeric(30, 12) not null default 0,
  gross_entry_notional numeric(30, 12) not null default 0,
  cumulative_fees numeric(30, 12) not null default 0,
  entry_fees numeric(30, 12) not null default 0,
  exit_fees numeric(30, 12) not null default 0,
  remaining_entry_fees numeric(30, 12) not null default 0,
  cumulative_execution_cost numeric(30, 12) not null default 0,
  realized_pnl numeric(30, 12) not null default 0,
  unrealized_pnl numeric(30, 12),
  status text not null check (status in ('OPENING','OPEN','EXIT_PENDING','CLOSING','CLOSED','RECONCILIATION_ERROR')),
  opened_at timestamptz not null,
  updated_at timestamptz not null default now(),
  closed_at timestamptz,
  state_version bigint not null default 1
);

create unique index if not exists paper_positions_one_active_per_strategy_pair
on public.paper_positions(strategy_version_id, pair)
where status in ('OPENING','OPEN','EXIT_PENDING','CLOSING');

create table if not exists public.paper_position_events (
  id uuid primary key default gen_random_uuid(),
  position_id uuid not null references public.paper_positions(id),
  event_type text not null,
  state_before jsonb,
  state_after jsonb not null,
  created_at timestamptz not null default now()
);

create table if not exists public.kill_switch_state (
  account_key text primary key,
  state text not null check (state in ('RUNNING','HALT_NEW_ENTRIES','HALTED','RECOVERY_PENDING')),
  reason_code text not null,
  state_version bigint not null default 1,
  updated_at timestamptz not null default now()
);

insert into public.kill_switch_state(account_key, state, reason_code)
values ('paper-default', 'RUNNING', 'INITIAL_STATE')
on conflict (account_key) do nothing;

create table if not exists public.kill_switch_events (
  id uuid primary key default gen_random_uuid(),
  account_key text not null,
  from_state text,
  to_state text not null,
  reason_code text not null,
  triggered_by text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.market_data_health (
  pair text not null,
  timeframe text not null,
  status text not null check (status in ('HEALTHY','STALE','DEGRADED','HALTED')),
  latency_ms bigint,
  missing_bars_count bigint not null default 0,
  last_market_ts timestamptz,
  updated_at timestamptz not null default now(),
  primary key(pair, timeframe)
);

create table if not exists public.audit_stream_heads (
  stream_key text primary key,
  last_sequence bigint not null default 0,
  last_hash text,
  updated_at timestamptz not null default now()
);

create table if not exists public.execution_audit (
  event_id uuid primary key,
  event_type text not null,
  entity_type text not null,
  entity_id uuid not null,
  correlation_id text not null,
  strategy_version_id text,
  market_time timestamptz,
  received_at timestamptz not null,
  decision_at timestamptz,
  persisted_at timestamptz not null default now(),
  state_before jsonb,
  state_after jsonb,
  input_snapshot_hash text,
  reason_code text not null,
  worker_id text not null,
  software_commit text not null,
  stream_key text not null,
  stream_sequence bigint not null,
  prev_hash text,
  event_hash text not null unique,
  unique(stream_key, stream_sequence)
);

create table if not exists public.reconciliation_runs (
  id uuid primary key default gen_random_uuid(),
  status text not null check (status in ('RUNNING','PASSED','WARNING','FAILED')),
  checks_performed integer not null default 0,
  issues_found integer not null default 0,
  started_at timestamptz not null default now(),
  completed_at timestamptz
);

create table if not exists public.reconciliation_issues (
  id uuid primary key default gen_random_uuid(),
  run_id uuid references public.reconciliation_runs(id),
  issue_type text not null,
  severity text not null check (severity in ('WARNING','CRITICAL')),
  entity_type text,
  entity_id uuid,
  details jsonb not null default '{}'::jsonb,
  resolved_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists public.recovery_actions (
  id uuid primary key default gen_random_uuid(),
  reconciliation_issue_id uuid not null references public.reconciliation_issues(id),
  repair_type text not null,
  status text not null check (status in ('PLANNED','APPLIED','FAILED')),
  state_before jsonb,
  state_after jsonb,
  idempotency_key text not null unique,
  applied_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists public.blind_test_policies (
  blind_test_id text primary key,
  strategy_version_id text not null,
  blind_until timestamptz not null,
  allowed_categories jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  locked_at timestamptz not null default now()
);

create index if not exists execution_outbox_claim_idx
on public.execution_outbox(processed_at, available_at, claim_expires_at);

create index if not exists paper_orders_status_idx
on public.paper_orders(status, updated_at);

create index if not exists paper_fills_order_idx
on public.paper_fills(order_id, fill_seq);

create index if not exists risk_reservations_status_idx
on public.risk_reservations(status, strategy_version_id, pair);

create index if not exists execution_audit_stream_idx
on public.execution_audit(stream_key, stream_sequence);

create index if not exists reconciliation_issues_open_idx
on public.reconciliation_issues(severity, created_at)
where resolved_at is null;

drop trigger if exists paper_signals_immutable on public.paper_signals;
create trigger paper_signals_immutable
before update or delete on public.paper_signals
for each row execute function public.paper_reject_immutable_mutation();

drop trigger if exists risk_decisions_immutable on public.risk_decisions;
create trigger risk_decisions_immutable
before update or delete on public.risk_decisions
for each row execute function public.paper_reject_immutable_mutation();

drop trigger if exists execution_market_snapshots_immutable on public.execution_market_snapshots;
create trigger execution_market_snapshots_immutable
before update or delete on public.execution_market_snapshots
for each row execute function public.paper_reject_immutable_mutation();

drop trigger if exists paper_fills_immutable on public.paper_fills;
create trigger paper_fills_immutable
before update or delete on public.paper_fills
for each row execute function public.paper_reject_immutable_mutation();

drop trigger if exists paper_order_events_immutable on public.paper_order_events;
create trigger paper_order_events_immutable
before update or delete on public.paper_order_events
for each row execute function public.paper_reject_immutable_mutation();

drop trigger if exists paper_position_events_immutable on public.paper_position_events;
create trigger paper_position_events_immutable
before update or delete on public.paper_position_events
for each row execute function public.paper_reject_immutable_mutation();

drop trigger if exists kill_switch_events_immutable on public.kill_switch_events;
create trigger kill_switch_events_immutable
before update or delete on public.kill_switch_events
for each row execute function public.paper_reject_immutable_mutation();

drop trigger if exists execution_audit_immutable on public.execution_audit;
create trigger execution_audit_immutable
before update or delete on public.execution_audit
for each row execute function public.paper_reject_immutable_mutation();

create or replace function public.paper_claim_outbox(
  p_worker_id text,
  p_limit integer default 20,
  p_lease_seconds integer default 30
)
returns setof public.execution_outbox
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_worker_id is null or btrim(p_worker_id) = '' then
    raise exception 'worker_id_required';
  end if;
  if p_limit < 1 or p_limit > 100 then
    raise exception 'invalid_claim_limit';
  end if;
  if p_lease_seconds < 1 or p_lease_seconds > 3600 then
    raise exception 'invalid_lease_seconds';
  end if;

  return query
  with candidates as (
    select id
    from public.execution_outbox
    where processed_at is null
      and available_at <= clock_timestamp()
      and (claimed_at is null or claim_expires_at < clock_timestamp())
    order by available_at, created_at, id
    limit p_limit
    for update skip locked
  )
  update public.execution_outbox o
  set claimed_at = clock_timestamp(),
      claim_expires_at = clock_timestamp() + make_interval(secs => p_lease_seconds),
      claimed_by = p_worker_id,
      attempt_count = o.attempt_count + 1
  from candidates c
  where o.id = c.id
  returning o.*;
end;
$$;

create or replace function public.paper_ack_outbox(
  p_outbox_id uuid,
  p_worker_id text,
  p_error text default null
)
returns public.execution_outbox
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row public.execution_outbox;
begin
  select * into v_row
  from public.execution_outbox
  where id = p_outbox_id
  for update;

  if not found then
    raise exception 'outbox_not_found';
  end if;

  if v_row.processed_at is not null then
    return v_row;
  end if;

  if v_row.claimed_by is distinct from p_worker_id then
    raise exception 'outbox_claim_owner_mismatch';
  end if;

  if p_error is null then
    update public.execution_outbox
    set processed_at = clock_timestamp(),
        last_error = null,
        claim_expires_at = null
    where id = p_outbox_id
    returning * into v_row;
  else
    update public.execution_outbox
    set last_error = p_error,
        claimed_at = null,
        claim_expires_at = null,
        claimed_by = null
    where id = p_outbox_id
    returning * into v_row;
  end if;

  return v_row;
end;
$$;

create or replace function public.paper_append_audit(
  p_event_type text,
  p_entity_type text,
  p_entity_id uuid,
  p_correlation_id text,
  p_strategy_version_id text,
  p_market_time timestamptz,
  p_received_at timestamptz,
  p_decision_at timestamptz,
  p_state_before jsonb,
  p_state_after jsonb,
  p_input_snapshot_hash text,
  p_reason_code text,
  p_worker_id text,
  p_software_commit text,
  p_stream_key text
)
returns public.execution_audit
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  v_head public.audit_stream_heads;
  v_seq bigint;
  v_prev_hash text;
  v_hash text;
  v_event public.execution_audit;
  v_event_id uuid := gen_random_uuid();
begin
  insert into public.audit_stream_heads(stream_key, last_sequence, last_hash)
  values (p_stream_key, 0, null)
  on conflict (stream_key) do nothing;

  select * into v_head
  from public.audit_stream_heads
  where stream_key = p_stream_key
  for update;

  v_seq := v_head.last_sequence + 1;
  v_prev_hash := v_head.last_hash;

  v_hash := encode(
    digest(
      convert_to(
        concat_ws('|',
          p_stream_key,
          v_seq::text,
          coalesce(v_prev_hash, ''),
          p_event_type,
          p_entity_type,
          p_entity_id::text,
          p_correlation_id,
          coalesce(p_strategy_version_id, ''),
          coalesce(p_market_time::text, ''),
          p_received_at::text,
          coalesce(p_decision_at::text, ''),
          coalesce(p_state_before::text, 'null'),
          coalesce(p_state_after::text, 'null'),
          coalesce(p_input_snapshot_hash, ''),
          p_reason_code,
          p_worker_id,
          p_software_commit
        ),
        'UTF8'
      ),
      'sha256'
    ),
    'hex'
  );

  insert into public.execution_audit(
    event_id, event_type, entity_type, entity_id, correlation_id,
    strategy_version_id, market_time, received_at, decision_at,
    state_before, state_after, input_snapshot_hash, reason_code,
    worker_id, software_commit, stream_key, stream_sequence,
    prev_hash, event_hash
  )
  values (
    v_event_id, p_event_type, p_entity_type, p_entity_id, p_correlation_id,
    p_strategy_version_id, p_market_time, p_received_at, p_decision_at,
    p_state_before, p_state_after, p_input_snapshot_hash, p_reason_code,
    p_worker_id, p_software_commit, p_stream_key, v_seq,
    v_prev_hash, v_hash
  )
  returning * into v_event;

  update public.audit_stream_heads
  set last_sequence = v_seq,
      last_hash = v_hash,
      updated_at = clock_timestamp()
  where stream_key = p_stream_key;

  return v_event;
end;
$$;


create or replace function public.paper_ingest_signal(
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
set search_path = public, extensions
as $
declare
  v_key text;
  v_signal_id text;
  v_row public.paper_signals;
  v_outbox_key text;
begin
  if p_side not in ('LONG','SHORT') then raise exception 'invalid_signal_side'; end if;
  if p_intended_notional <= 0 then raise exception 'invalid_signal_notional'; end if;
  if p_strategy_version_id is null or btrim(p_strategy_version_id) = '' then raise exception 'strategy_version_required'; end if;

  v_key := encode(digest(convert_to(
    'SIGNAL|' || p_strategy_version_id || '|' || p_pair || '|' || p_side || '|' ||
    to_char(p_signal_close_time at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') || '|' || p_rule_id,
    'UTF8'
  ), 'sha256'), 'hex');
  v_signal_id := 'sig_' || v_key;

  select * into v_row from public.paper_signals where idempotency_key = v_key;
  if found then
    return jsonb_build_object('signal_id', v_row.id, 'canonical_signal_id', v_row.signal_id, 'idempotent_replay', true);
  end if;

  insert into public.paper_signals(
    signal_id, strategy_id, strategy_version_id, test_spec_id, pair, side,
    signal_close_time, intended_entry_time, intended_notional,
    source_timeframe, execution_timeframe, reason_code, rule_id,
    correlation_id, blind_test_id, generated_at, idempotency_key, software_commit
  )
  values (
    v_signal_id, p_strategy_id, p_strategy_version_id, p_test_spec_id, p_pair, p_side,
    p_signal_close_time, p_intended_entry_time, p_intended_notional,
    p_source_timeframe, p_execution_timeframe, p_reason_code, p_rule_id,
    p_correlation_id, p_blind_test_id, p_generated_at, v_key, p_software_commit
  )
  returning * into v_row;

  v_outbox_key := encode(digest(convert_to(
    'OUTBOX|RISK_EVALUATE|signal|' || v_row.id::text || '|PRETRADE',
    'UTF8'
  ), 'sha256'), 'hex');

  insert into public.execution_outbox(
    event_type, entity_type, entity_id, correlation_id, payload, idempotency_key
  )
  values (
    'RISK_EVALUATE', 'signal', v_row.id, p_correlation_id,
    jsonb_build_object('signal_id', v_row.id), v_outbox_key
  )
  on conflict (idempotency_key) do nothing;

  perform public.paper_append_audit(
    'SIGNAL_PERSISTED', 'signal', v_row.id, p_correlation_id,
    p_strategy_version_id, p_signal_close_time, clock_timestamp(), clock_timestamp(),
    null, to_jsonb(v_row), null, p_reason_code, 'signal-ingestor', p_software_commit, p_correlation_id
  );

  return jsonb_build_object('signal_id', v_row.id, 'canonical_signal_id', v_row.signal_id, 'idempotent_replay', false);
end;
$;

create or replace function public.paper_create_order_from_approved_signal(
  p_signal_id uuid,
  p_side text,
  p_intent_type text,
  p_order_type text default 'MARKET',
  p_worker_id text default 'order-manager',
  p_software_commit text default 'unknown'
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $
declare
  v_signal public.paper_signals;
  v_decision public.risk_decisions;
  v_ks public.kill_switch_state;
  v_order public.paper_orders;
  v_key text;
  v_outbox_key text;
  v_from text;
begin
  if p_side not in ('BUY','SELL') then raise exception 'invalid_order_side'; end if;
  if p_intent_type not in ('ENTRY','EXIT','EMERGENCY_EXIT') then raise exception 'invalid_intent_type'; end if;
  if p_order_type <> 'MARKET' then raise exception 'unsupported_order_type'; end if;

  select * into v_signal from public.paper_signals where id = p_signal_id;
  if not found then raise exception 'signal_not_found'; end if;

  select * into v_decision
  from public.risk_decisions
  where signal_id = p_signal_id and decision_stage='PRETRADE';
  if not found or v_decision.decision <> 'APPROVED' then
    raise exception 'signal_not_risk_approved';
  end if;

  select * into v_ks from public.kill_switch_state where account_key='paper-default' for update;
  if p_intent_type='ENTRY' and v_ks.state <> 'RUNNING' then
    raise exception 'new_entry_blocked_kill_switch:%', v_ks.state;
  end if;
  if p_intent_type <> 'ENTRY' and v_ks.state in ('HALTED','RECOVERY_PENDING') then
    raise exception 'new_order_blocked_kill_switch:%', v_ks.state;
  end if;

  v_key := encode(digest(convert_to(
    'ORDER|' || p_signal_id::text || '|' || p_intent_type || '|' || p_order_type,
    'UTF8'
  ), 'sha256'), 'hex');

  select * into v_order from public.paper_orders where idempotency_key = v_key;
  if found then
    return jsonb_build_object('order_id', v_order.id, 'status', v_order.status, 'idempotent_replay', true);
  end if;

  insert into public.paper_orders(
    signal_id, strategy_version_id, pair, side, intent_type, order_type,
    intended_notional, status, idempotency_key
  )
  values (
    p_signal_id, v_signal.strategy_version_id, v_signal.pair, p_side, p_intent_type, p_order_type,
    v_signal.intended_notional, 'CREATED', v_key
  )
  returning * into v_order;

  insert into public.paper_order_events(order_id, from_state, to_state, reason_code)
  values (v_order.id, null, 'CREATED', 'ORDER_CREATED');

  v_from := v_order.status;
  update public.paper_orders
  set status='ACCEPTED', accepted_at=clock_timestamp(), state_version=state_version+1, updated_at=clock_timestamp()
  where id=v_order.id
  returning * into v_order;

  insert into public.paper_order_events(order_id, from_state, to_state, reason_code)
  values (v_order.id, v_from, 'ACCEPTED', 'RISK_APPROVED');

  v_outbox_key := encode(digest(convert_to(
    'OUTBOX|BIND_FILL_ATTEMPT|order|' || v_order.id::text || '|attempt:1',
    'UTF8'
  ), 'sha256'), 'hex');

  insert into public.execution_outbox(
    event_type, entity_type, entity_id, correlation_id, payload, idempotency_key
  )
  values (
    'BIND_FILL_ATTEMPT', 'order', v_order.id, v_signal.correlation_id,
    jsonb_build_object('order_id', v_order.id, 'attempt_seq', 1), v_outbox_key
  )
  on conflict (idempotency_key) do nothing;

  perform public.paper_append_audit(
    'ORDER_ACCEPTED', 'order', v_order.id, v_signal.correlation_id,
    v_signal.strategy_version_id, v_signal.signal_close_time,
    clock_timestamp(), clock_timestamp(), null, to_jsonb(v_order), null,
    'RISK_APPROVED', p_worker_id, p_software_commit, v_signal.correlation_id
  );

  return jsonb_build_object('order_id', v_order.id, 'status', v_order.status, 'idempotent_replay', false);
end;
$;

create or replace function public.paper_bind_execution_attempt(
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
set search_path = public, extensions
as $
declare
  v_order public.paper_orders;
  v_signal public.paper_signals;
  v_attempt public.paper_execution_attempts;
  v_ks public.kill_switch_state;
  v_key text;
  v_from text;
begin
  if p_attempt_seq < 1 then raise exception 'invalid_attempt_seq'; end if;

  select * into v_order from public.paper_orders where id=p_order_id for update;
  if not found then raise exception 'order_not_found'; end if;
  if v_order.status not in ('ACCEPTED','PENDING_FILL','PARTIALLY_FILLED') then
    raise exception 'order_state_disallows_attempt:%', v_order.status;
  end if;
  if v_order.cancel_requested_at is not null then
    raise exception 'attempt_blocked_after_cancel_request';
  end if;

  select * into v_ks from public.kill_switch_state where account_key='paper-default' for update;
  if v_ks.state in ('HALTED','RECOVERY_PENDING') then
    raise exception 'attempt_blocked_kill_switch:%', v_ks.state;
  end if;

  if not exists(select 1 from public.execution_market_snapshots where id=p_market_snapshot_id) then
    raise exception 'market_snapshot_not_found';
  end if;
  if not exists(select 1 from public.risk_policy_versions where id=p_policy_version_id) then
    raise exception 'policy_not_found';
  end if;

  v_key := encode(digest(convert_to(
    'FILL_ATTEMPT|' || p_order_id::text || '|' || p_attempt_seq::text,
    'UTF8'
  ), 'sha256'), 'hex');

  select * into v_attempt from public.paper_execution_attempts where idempotency_key=v_key;
  if found then
    return jsonb_build_object('execution_attempt_id', v_attempt.id, 'status', v_attempt.status, 'idempotent_replay', true);
  end if;

  insert into public.paper_execution_attempts(
    order_id, attempt_seq, market_snapshot_id, policy_version_id,
    status, idempotency_key
  )
  values (
    p_order_id, p_attempt_seq, p_market_snapshot_id, p_policy_version_id,
    'BOUND', v_key
  )
  returning * into v_attempt;

  if v_order.status='ACCEPTED' then
    v_from := v_order.status;
    update public.paper_orders
    set status='PENDING_FILL', pending_at=clock_timestamp(), state_version=state_version+1, updated_at=clock_timestamp()
    where id=p_order_id
    returning * into v_order;

    insert into public.paper_order_events(order_id, from_state, to_state, reason_code, payload)
    values (p_order_id, v_from, 'PENDING_FILL', 'EXECUTION_ATTEMPT_BOUND',
            jsonb_build_object('execution_attempt_id', v_attempt.id));
  end if;

  select * into v_signal from public.paper_signals where id=v_order.signal_id;

  perform public.paper_append_audit(
    'EXECUTION_ATTEMPT_BOUND', 'order', p_order_id, v_signal.correlation_id,
    v_order.strategy_version_id, v_signal.signal_close_time,
    clock_timestamp(), clock_timestamp(), null,
    jsonb_build_object('execution_attempt_id', v_attempt.id, 'snapshot_id', p_market_snapshot_id, 'attempt_seq', p_attempt_seq),
    null, 'EXECUTION_ATTEMPT_BOUND', p_worker_id, p_software_commit, v_signal.correlation_id
  );

  return jsonb_build_object('execution_attempt_id', v_attempt.id, 'status', v_attempt.status, 'idempotent_replay', false);
end;
$;

create or replace function public.paper_reserve_risk(
  p_signal_id uuid,
  p_policy_version_id uuid,
  p_account_key text default 'paper-default',
  p_worker_id text default 'risk-engine',
  p_software_commit text default 'unknown'
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_signal public.paper_signals;
  v_policy public.risk_policy_versions;
  v_state public.portfolio_risk_state;
  v_existing public.risk_decisions;
  v_ks public.kill_switch_state;
  v_active_count integer;
  v_pair_count integer;
  v_asset_reserved numeric(30,12);
  v_asset_used numeric(30,12);
  v_signed numeric(30,12);
  v_reason text := 'RISK_APPROVED';
  v_decision_id uuid;
  v_reservation_id uuid;
begin
  select * into v_signal from public.paper_signals where id = p_signal_id;
  if not found then raise exception 'signal_not_found'; end if;

  select * into v_existing
  from public.risk_decisions
  where signal_id = p_signal_id and decision_stage = 'PRETRADE';
  if found then
    return jsonb_build_object(
      'decision', v_existing.decision,
      'reason_code', v_existing.reason_code,
      'decision_id', v_existing.id,
      'idempotent_replay', true
    );
  end if;

  select * into v_policy from public.risk_policy_versions where id = p_policy_version_id;
  if not found then raise exception 'risk_policy_not_found'; end if;

  insert into public.portfolio_risk_state(account_key)
  values (p_account_key)
  on conflict (account_key) do nothing;

  select * into v_state
  from public.portfolio_risk_state
  where account_key = p_account_key
  for update;

  select * into v_ks
  from public.kill_switch_state
  where account_key = p_account_key
  for update;

  if not found or v_ks.state <> 'RUNNING' then
    v_reason := 'RISK_REJECT_KILL_SWITCH';
  elsif v_state.critical_reconciliation then
    v_reason := 'RISK_REJECT_RECONCILIATION';
  elsif v_policy.max_position_notional is not null
        and v_signal.intended_notional > v_policy.max_position_notional then
    v_reason := 'RISK_REJECT_POSITION_NOTIONAL';
  end if;

  select count(*) into v_active_count
  from public.paper_positions
  where status in ('OPENING','OPEN','EXIT_PENDING','CLOSING');

  v_active_count := v_active_count + (
    select count(*) from public.risk_reservations
    where status in ('RESERVED','PARTIALLY_CONSUMED')
  );

  if v_reason = 'RISK_APPROVED'
     and v_policy.max_concurrent_positions is not null
     and v_active_count >= v_policy.max_concurrent_positions then
    v_reason := 'RISK_REJECT_MAX_CONCURRENT';
  end if;

  select count(*) into v_pair_count
  from public.paper_positions
  where strategy_version_id = v_signal.strategy_version_id
    and pair = v_signal.pair
    and status in ('OPENING','OPEN','EXIT_PENDING','CLOSING');

  v_pair_count := v_pair_count + (
    select count(*) from public.risk_reservations
    where strategy_version_id = v_signal.strategy_version_id
      and pair = v_signal.pair
      and status in ('RESERVED','PARTIALLY_CONSUMED')
  );

  if v_reason = 'RISK_APPROVED'
     and v_policy.max_open_per_strategy_pair is not null
     and v_pair_count >= v_policy.max_open_per_strategy_pair then
    v_reason := 'RISK_REJECT_MAX_OPEN_PAIR';
  end if;

  if v_reason = 'RISK_APPROVED'
     and v_policy.max_total_gross_exposure is not null
     and (v_state.used_gross_notional + v_state.reserved_gross_notional + v_signal.intended_notional)
         > v_policy.max_total_gross_exposure then
    v_reason := 'RISK_REJECT_GROSS_EXPOSURE';
  end if;

  v_signed := case when v_signal.side = 'LONG'
                   then v_signal.intended_notional
                   else -v_signal.intended_notional end;

  if v_reason = 'RISK_APPROVED'
     and v_policy.max_net_exposure is not null
     and abs(v_state.used_net_notional + v_state.reserved_net_notional + v_signed)
         > v_policy.max_net_exposure then
    v_reason := 'RISK_REJECT_NET_EXPOSURE';
  end if;

  select coalesce(sum(gross_entry_notional),0) into v_asset_used
  from public.paper_positions
  where pair = v_signal.pair
    and status in ('OPENING','OPEN','EXIT_PENDING','CLOSING');

  select coalesce(sum(reserved_notional - consumed_notional),0) into v_asset_reserved
  from public.risk_reservations
  where pair = v_signal.pair
    and status in ('RESERVED','PARTIALLY_CONSUMED');

  if v_reason = 'RISK_APPROVED'
     and v_policy.max_asset_exposure is not null
     and (v_asset_used + v_asset_reserved + v_signal.intended_notional)
         > v_policy.max_asset_exposure then
    v_reason := 'RISK_REJECT_ASSET_EXPOSURE';
  end if;

  if v_state.risk_utc_day <> (clock_timestamp() at time zone 'utc')::date then
    v_state.realized_pnl_utc_day := 0;
    v_state.risk_utc_day := (clock_timestamp() at time zone 'utc')::date;
  end if;

  if v_reason = 'RISK_APPROVED'
     and v_policy.max_daily_realized_loss is not null
     and v_state.realized_pnl_utc_day <= -v_policy.max_daily_realized_loss then
    v_reason := 'RISK_REJECT_DAILY_LOSS';
  end if;

  if v_reason = 'RISK_APPROVED'
     and v_policy.max_intraday_drawdown is not null
     and v_state.intraday_peak_equity is not null
     and v_state.internal_marked_equity is not null
     and v_state.intraday_peak_equity > 0
     and ((v_state.intraday_peak_equity - v_state.internal_marked_equity) / v_state.intraday_peak_equity)
         >= v_policy.max_intraday_drawdown then
    v_reason := 'RISK_REJECT_INTRADAY_DRAWDOWN';
  end if;

  insert into public.risk_decisions(
    signal_id, decision_stage, decision, policy_version_id,
    observed, reason_code, idempotency_key
  )
  values (
    p_signal_id, 'PRETRADE',
    case when v_reason = 'RISK_APPROVED' then 'APPROVED' else 'REJECTED' end,
    p_policy_version_id,
    jsonb_build_object(
      'used_gross_notional', v_state.used_gross_notional,
      'reserved_gross_notional', v_state.reserved_gross_notional,
      'used_net_notional', v_state.used_net_notional,
      'reserved_net_notional', v_state.reserved_net_notional,
      'realized_pnl_utc_day', v_state.realized_pnl_utc_day,
      'risk_utc_day', v_state.risk_utc_day
    ),
    v_reason,
    encode(extensions.digest(convert_to('RISK|' || p_signal_id::text || '|PRETRADE','UTF8'),'sha256'),'hex')
  )
  returning id into v_decision_id;

  if v_reason = 'RISK_APPROVED' then
    insert into public.risk_reservations(
      signal_id, strategy_version_id, pair, side,
      reserved_notional, consumed_notional, status, policy_version_id
    )
    values (
      p_signal_id, v_signal.strategy_version_id, v_signal.pair, v_signal.side,
      v_signal.intended_notional, 0, 'RESERVED', p_policy_version_id
    )
    returning id into v_reservation_id;

    update public.portfolio_risk_state
    set reserved_gross_notional = reserved_gross_notional + v_signal.intended_notional,
        reserved_net_notional = reserved_net_notional + v_signed,
        risk_utc_day = v_state.risk_utc_day,
        realized_pnl_utc_day = v_state.realized_pnl_utc_day,
        state_version = state_version + 1,
        updated_at = clock_timestamp()
    where account_key = p_account_key;
  else
    update public.portfolio_risk_state
    set risk_utc_day = v_state.risk_utc_day,
        realized_pnl_utc_day = v_state.realized_pnl_utc_day,
        state_version = state_version + 1,
        updated_at = clock_timestamp()
    where account_key = p_account_key;
  end if;

  perform public.paper_append_audit(
    'RISK_DECISION', 'signal', p_signal_id, v_signal.correlation_id,
    v_signal.strategy_version_id, v_signal.signal_close_time,
    clock_timestamp(), clock_timestamp(), null,
    jsonb_build_object('decision', case when v_reason='RISK_APPROVED' then 'APPROVED' else 'REJECTED' end,
                       'reason_code', v_reason,
                       'reservation_id', v_reservation_id),
    null, v_reason, p_worker_id, p_software_commit, v_signal.correlation_id
  );

  return jsonb_build_object(
    'decision', case when v_reason='RISK_APPROVED' then 'APPROVED' else 'REJECTED' end,
    'reason_code', v_reason,
    'decision_id', v_decision_id,
    'reservation_id', v_reservation_id,
    'idempotent_replay', false
  );
end;
$$;

create or replace function public.paper_apply_fill(
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
set search_path = public
as $$
declare
  v_order public.paper_orders;
  v_attempt public.paper_execution_attempts;
  v_snapshot public.execution_market_snapshots;
  v_policy public.risk_policy_versions;
  v_signal public.paper_signals;
  v_res public.risk_reservations;
  v_state public.portfolio_risk_state;
  v_ks public.kill_switch_state;
  v_pos public.paper_positions;
  v_fill_id uuid;
  v_fill_key text;
  v_fill_notional numeric(30,12);
  v_new_filled_notional numeric(30,12);
  v_new_status text;
  v_signed numeric(30,12);
  v_consume numeric(30,12);
  v_alloc_entry_fee numeric(30,12);
  v_realized numeric(30,12);
  v_before jsonb;
  v_after jsonb;
begin
  if p_fill_seq < 1 or p_fill_quantity <= 0 or p_fill_price <= 0 or p_fee_amount < 0 then
    raise exception 'invalid_fill_input';
  end if;

  select * into v_order from public.paper_orders where id = p_order_id for update;
  if not found then raise exception 'order_not_found'; end if;

  select * into v_ks from public.kill_switch_state where account_key = p_account_key for update;
  if not found then raise exception 'kill_switch_state_missing'; end if;
  if v_ks.state in ('HALTED','RECOVERY_PENDING') then
    raise exception 'fill_blocked_kill_switch:%', v_ks.state;
  end if;

  if v_order.status not in ('ACCEPTED','PENDING_FILL','PARTIALLY_FILLED') then
    raise exception 'order_state_disallows_fill:%', v_order.status;
  end if;

  if v_order.cancel_requested_at is not null then
    raise exception 'fill_blocked_after_cancel_request';
  end if;

  select * into v_attempt
  from public.paper_execution_attempts
  where id = p_execution_attempt_id
  for update;

  if not found or v_attempt.order_id <> p_order_id then
    raise exception 'execution_attempt_mismatch';
  end if;
  if v_attempt.status = 'APPLIED' then
    select id into v_fill_id
    from public.paper_fills
    where execution_attempt_id = p_execution_attempt_id
      and fill_seq = p_fill_seq;
    if found then
      return jsonb_build_object('fill_id', v_fill_id, 'idempotent_replay', true);
    end if;
    raise exception 'attempt_applied_without_fill';
  end if;
  if v_attempt.status <> 'BOUND' then
    raise exception 'execution_attempt_not_bound';
  end if;

  select * into v_snapshot
  from public.execution_market_snapshots
  where id = v_attempt.market_snapshot_id;
  if not found then raise exception 'market_snapshot_missing'; end if;

  select * into v_policy
  from public.risk_policy_versions
  where id = v_attempt.policy_version_id;
  if not found then raise exception 'risk_policy_missing'; end if;

  if v_policy.max_book_age_ms is not null
     and extract(epoch from (clock_timestamp() - v_snapshot.received_at)) * 1000 > v_policy.max_book_age_ms then
    raise exception 'stale_market_snapshot';
  end if;

  if v_order.cancel_requested_at is not null
     and v_snapshot.received_at > v_order.cancel_requested_at then
    raise exception 'snapshot_after_cancel_request';
  end if;

  select * into v_signal from public.paper_signals where id = v_order.signal_id;
  if not found then raise exception 'signal_missing'; end if;

  v_fill_key := encode(
    extensions.digest(
      convert_to('FILL|' || p_order_id::text || '|' || p_fill_seq::text || '|' || p_execution_attempt_id::text, 'UTF8'),
      'sha256'
    ),
    'hex'
  );

  select id into v_fill_id from public.paper_fills where idempotency_key = v_fill_key;
  if found then
    return jsonb_build_object('fill_id', v_fill_id, 'idempotent_replay', true);
  end if;

  if exists (
    select 1 from public.paper_fills
    where order_id = p_order_id and fill_seq = p_fill_seq
  ) then
    raise exception 'fill_seq_collision';
  end if;

  insert into public.portfolio_risk_state(account_key)
  values (p_account_key)
  on conflict (account_key) do nothing;

  select * into v_state
  from public.portfolio_risk_state
  where account_key = p_account_key
  for update;

  select * into v_res
  from public.risk_reservations
  where signal_id = v_order.signal_id
  for update;

  v_fill_notional := p_fill_quantity * p_fill_price;
  v_new_filled_notional := v_order.filled_notional + v_fill_notional;
  v_new_status := case
    when v_new_filled_notional + 0.00000001 >= v_order.intended_notional then 'FILLED'
    else 'PARTIALLY_FILLED'
  end;

  if v_new_status = 'PARTIALLY_FILLED' and not v_policy.partial_fill_allowed then
    raise exception 'partial_fill_not_allowed';
  end if;

  insert into public.paper_fills(
    order_id, execution_attempt_id, strategy_version_id, pair,
    fill_seq, fill_quantity, fill_price, fee_amount,
    spread_bps, impact_bps, filled_at, idempotency_key
  )
  values (
    p_order_id, p_execution_attempt_id, v_order.strategy_version_id, v_order.pair,
    p_fill_seq, p_fill_quantity, p_fill_price, p_fee_amount,
    p_spread_bps, p_impact_bps, p_filled_at, v_fill_key
  )
  returning id into v_fill_id;

  update public.paper_orders
  set filled_quantity = filled_quantity + p_fill_quantity,
      filled_notional = v_new_filled_notional,
      status = v_new_status,
      terminal_at = case when v_new_status='FILLED' then clock_timestamp() else terminal_at end,
      state_version = state_version + 1,
      updated_at = clock_timestamp()
  where id = p_order_id;

  insert into public.paper_order_events(order_id, from_state, to_state, reason_code, payload)
  values (
    p_order_id, v_order.status, v_new_status, 'FILL_APPLIED',
    jsonb_build_object('fill_id', v_fill_id, 'fill_seq', p_fill_seq, 'execution_attempt_id', p_execution_attempt_id)
  );

  select * into v_pos
  from public.paper_positions
  where strategy_version_id = v_order.strategy_version_id
    and pair = v_order.pair
    and status in ('OPENING','OPEN','EXIT_PENDING','CLOSING')
  for update;

  if v_order.intent_type = 'ENTRY' then
    if v_pos.id is null then
      insert into public.paper_positions(
        strategy_version_id, pair, side, quantity, average_entry_price,
        gross_entry_notional, cumulative_fees, entry_fees, remaining_entry_fees,
        cumulative_execution_cost, realized_pnl, status, opened_at
      )
      values (
        v_order.strategy_version_id, v_order.pair,
        case when v_order.side='BUY' then 'LONG' else 'SHORT' end,
        p_fill_quantity, p_fill_price, v_fill_notional,
        p_fee_amount, p_fee_amount, p_fee_amount,
        abs(p_impact_bps) * v_fill_notional / 10000,
        0, 'OPEN', p_filled_at
      )
      returning * into v_pos;

      insert into public.paper_position_events(position_id, event_type, state_before, state_after)
      values (v_pos.id, 'POSITION_OPENED', null, to_jsonb(v_pos));
    else
      if (v_pos.side='LONG' and v_order.side <> 'BUY')
         or (v_pos.side='SHORT' and v_order.side <> 'SELL') then
        raise exception 'entry_side_conflicts_with_position';
      end if;

      v_before := to_jsonb(v_pos);

      update public.paper_positions
      set average_entry_price =
            ((quantity * average_entry_price) + (p_fill_quantity * p_fill_price))
            / (quantity + p_fill_quantity),
          quantity = quantity + p_fill_quantity,
          gross_entry_notional = gross_entry_notional + v_fill_notional,
          cumulative_fees = cumulative_fees + p_fee_amount,
          entry_fees = entry_fees + p_fee_amount,
          remaining_entry_fees = remaining_entry_fees + p_fee_amount,
          cumulative_execution_cost = cumulative_execution_cost + abs(p_impact_bps) * v_fill_notional / 10000,
          state_version = state_version + 1,
          updated_at = clock_timestamp()
      where id = v_pos.id
      returning * into v_pos;

      insert into public.paper_position_events(position_id, event_type, state_before, state_after)
      values (v_pos.id, 'ENTRY_FILL_APPLIED', v_before, to_jsonb(v_pos));
    end if;

    if v_res.id is null then
      raise exception 'entry_fill_without_risk_reservation';
    end if;

    v_consume := least(v_fill_notional, v_res.reserved_notional - v_res.consumed_notional);
    if v_consume <= 0 then
      raise exception 'risk_reservation_exhausted';
    end if;

    v_signed := case when v_res.side='LONG' then v_consume else -v_consume end;

    update public.risk_reservations
    set consumed_notional = consumed_notional + v_consume,
        status = case
          when consumed_notional + v_consume + 0.00000001 >= reserved_notional then 'CONSUMED'
          else 'PARTIALLY_CONSUMED'
        end,
        consumed_at = clock_timestamp(),
        state_version = state_version + 1
    where id = v_res.id;

    update public.portfolio_risk_state
    set reserved_gross_notional = greatest(0, reserved_gross_notional - v_consume),
        used_gross_notional = used_gross_notional + v_consume,
        reserved_net_notional = reserved_net_notional - v_signed,
        used_net_notional = used_net_notional + v_signed,
        state_version = state_version + 1,
        updated_at = clock_timestamp()
    where account_key = p_account_key;

  else
    if v_pos.id is null then raise exception 'exit_fill_without_position'; end if;
    if p_fill_quantity > v_pos.quantity + 0.00000001 then raise exception 'exit_fill_exceeds_position'; end if;
    if (v_pos.side='LONG' and v_order.side <> 'SELL')
       or (v_pos.side='SHORT' and v_order.side <> 'BUY') then
      raise exception 'exit_side_conflicts_with_position';
    end if;

    v_before := to_jsonb(v_pos);
    v_alloc_entry_fee := case
      when v_pos.quantity > 0 then v_pos.remaining_entry_fees * p_fill_quantity / v_pos.quantity
      else 0
    end;

    v_realized := case when v_pos.side='LONG'
      then (p_fill_price - v_pos.average_entry_price) * p_fill_quantity - v_alloc_entry_fee - p_fee_amount
      else (v_pos.average_entry_price - p_fill_price) * p_fill_quantity - v_alloc_entry_fee - p_fee_amount
    end;

    v_consume := case when v_pos.quantity > 0
      then v_pos.gross_entry_notional * p_fill_quantity / v_pos.quantity
      else 0
    end;

    v_signed := case when v_pos.side='LONG' then v_consume else -v_consume end;

    update public.paper_positions
    set quantity = greatest(0, quantity - p_fill_quantity),
        gross_entry_notional = greatest(0, gross_entry_notional - v_consume),
        cumulative_fees = cumulative_fees + p_fee_amount,
        exit_fees = exit_fees + p_fee_amount,
        remaining_entry_fees = greatest(0, remaining_entry_fees - v_alloc_entry_fee),
        cumulative_execution_cost = cumulative_execution_cost + abs(p_impact_bps) * v_fill_notional / 10000,
        realized_pnl = realized_pnl + v_realized,
        status = case when quantity - p_fill_quantity <= 0.00000001 then 'CLOSED' else 'OPEN' end,
        closed_at = case when quantity - p_fill_quantity <= 0.00000001 then p_filled_at else closed_at end,
        state_version = state_version + 1,
        updated_at = clock_timestamp()
    where id = v_pos.id
    returning * into v_pos;

    insert into public.paper_position_events(position_id, event_type, state_before, state_after)
    values (v_pos.id, 'EXIT_FILL_APPLIED', v_before, to_jsonb(v_pos));

    if v_state.risk_utc_day <> (p_filled_at at time zone 'utc')::date then
      v_state.realized_pnl_utc_day := 0;
      v_state.risk_utc_day := (p_filled_at at time zone 'utc')::date;
    end if;

    update public.portfolio_risk_state
    set used_gross_notional = greatest(0, used_gross_notional - v_consume),
        used_net_notional = used_net_notional - v_signed,
        realized_pnl_utc_day = v_state.realized_pnl_utc_day + v_realized,
        risk_utc_day = v_state.risk_utc_day,
        state_version = state_version + 1,
        updated_at = clock_timestamp()
    where account_key = p_account_key;
  end if;

  update public.paper_execution_attempts
  set status = 'APPLIED',
      applied_at = clock_timestamp()
  where id = p_execution_attempt_id;

  if v_new_status = 'PARTIALLY_FILLED' then
    insert into public.execution_outbox(
      event_type, entity_type, entity_id, correlation_id, payload, idempotency_key
    )
    values (
      'BIND_FILL_ATTEMPT', 'order', p_order_id, v_signal.correlation_id,
      jsonb_build_object(
        'order_id', p_order_id,
        'attempt_seq', v_attempt.attempt_seq + 1,
        'remaining_notional', greatest(0, v_order.intended_notional - v_new_filled_notional)
      ),
      encode(extensions.digest(convert_to(
        'OUTBOX|BIND_FILL_ATTEMPT|order|' || p_order_id::text || '|attempt:' || (v_attempt.attempt_seq + 1)::text,
        'UTF8'
      ), 'sha256'), 'hex')
    )
    on conflict (idempotency_key) do nothing;
  end if;

  perform public.paper_append_audit(
    'FILL_APPLIED', 'order', p_order_id, v_signal.correlation_id,
    v_order.strategy_version_id, v_signal.signal_close_time,
    clock_timestamp(), clock_timestamp(),
    to_jsonb(v_order),
    jsonb_build_object('fill_id', v_fill_id, 'order_status', v_new_status, 'position_id', v_pos.id),
    v_snapshot.snapshot_hash, 'FILL_APPLIED', p_worker_id, p_software_commit, v_signal.correlation_id
  );

  return jsonb_build_object(
    'fill_id', v_fill_id,
    'order_status', v_new_status,
    'position_id', v_pos.id,
    'realized_pnl_delta', coalesce(v_realized, 0),
    'idempotent_replay', false
  );
end;
$$;


create or replace function public.paper_terminalize_order(
  p_order_id uuid,
  p_terminal_state text,
  p_reason_code text,
  p_worker_id text default 'order-manager',
  p_software_commit text default 'unknown',
  p_account_key text default 'paper-default'
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $
declare
  v_order public.paper_orders;
  v_signal public.paper_signals;
  v_res public.risk_reservations;
  v_state public.portfolio_risk_state;
  v_remaining numeric(30,12);
  v_signed numeric(30,12);
  v_from text;
begin
  if p_terminal_state not in ('CANCELLED','EXPIRED','FAILED','REJECTED') then
    raise exception 'invalid_terminal_state';
  end if;

  select * into v_order from public.paper_orders where id=p_order_id for update;
  if not found then raise exception 'order_not_found'; end if;

  if v_order.status in ('FILLED','CANCELLED','REJECTED','EXPIRED','FAILED') then
    return jsonb_build_object('order_id', v_order.id, 'status', v_order.status, 'idempotent_replay', true);
  end if;

  if p_terminal_state='CANCELLED' then
    v_from := v_order.status;
    update public.paper_orders
    set status='CANCEL_REQUESTED', cancel_requested_at=coalesce(cancel_requested_at,clock_timestamp()),
        state_version=state_version+1, updated_at=clock_timestamp()
    where id=p_order_id
    returning * into v_order;
    insert into public.paper_order_events(order_id,from_state,to_state,reason_code)
    values(p_order_id,v_from,'CANCEL_REQUESTED',p_reason_code);
  end if;

  v_from := v_order.status;
  update public.paper_orders
  set status=p_terminal_state, terminal_at=clock_timestamp(),
      rejection_reason=case when p_terminal_state in ('FAILED','REJECTED') then p_reason_code else rejection_reason end,
      state_version=state_version+1, updated_at=clock_timestamp()
  where id=p_order_id
  returning * into v_order;

  insert into public.paper_order_events(order_id,from_state,to_state,reason_code)
  values(p_order_id,v_from,p_terminal_state,p_reason_code);

  select * into v_res from public.risk_reservations where signal_id=v_order.signal_id for update;
  if found and v_res.status in ('RESERVED','PARTIALLY_CONSUMED') then
    v_remaining := greatest(0, v_res.reserved_notional - v_res.consumed_notional);
    v_signed := case when v_res.side='LONG' then v_remaining else -v_remaining end;

    select * into v_state from public.portfolio_risk_state where account_key=p_account_key for update;

    update public.risk_reservations
    set status='RELEASED', released_at=clock_timestamp(), release_reason=p_reason_code,
        state_version=state_version+1
    where id=v_res.id;

    update public.portfolio_risk_state
    set reserved_gross_notional=greatest(0,reserved_gross_notional-v_remaining),
        reserved_net_notional=reserved_net_notional-v_signed,
        state_version=state_version+1, updated_at=clock_timestamp()
    where account_key=p_account_key;
  end if;

  select * into v_signal from public.paper_signals where id=v_order.signal_id;

  perform public.paper_append_audit(
    'ORDER_TERMINAL', 'order', p_order_id, v_signal.correlation_id,
    v_order.strategy_version_id, v_signal.signal_close_time,
    clock_timestamp(), clock_timestamp(), null, to_jsonb(v_order), null,
    p_reason_code, p_worker_id, p_software_commit, v_signal.correlation_id
  );

  return jsonb_build_object('order_id', v_order.id, 'status', v_order.status, 'idempotent_replay', false);
end;
$;

-- Sensitive execution tables: service-role/RPC only.
do $$
declare
  t text;
begin
  foreach t in array array[
    'execution_market_snapshots','paper_signals','risk_decisions','risk_reservations',
    'paper_orders','paper_order_events','execution_outbox','paper_execution_attempts',
    'paper_fills','paper_positions','paper_position_events','risk_policy_versions',
    'portfolio_risk_state','kill_switch_state','kill_switch_events',
    'execution_audit','audit_stream_heads','reconciliation_runs',
    'reconciliation_issues','recovery_actions','blind_test_policies'
  ]
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on table public.%I from anon, authenticated', t);
    execute format('grant all on table public.%I to service_role', t);
  end loop;
end;
$$;

alter table public.market_data_health enable row level security;
revoke all on table public.market_data_health from anon, authenticated;
grant all on table public.market_data_health to service_role;

revoke all on function public.paper_claim_outbox(text,integer,integer) from public, anon, authenticated;
grant execute on function public.paper_claim_outbox(text,integer,integer) to service_role;

revoke all on function public.paper_ack_outbox(uuid,text,text) from public, anon, authenticated;
grant execute on function public.paper_ack_outbox(uuid,text,text) to service_role;

revoke all on function public.paper_append_audit(text,text,uuid,text,text,timestamptz,timestamptz,timestamptz,jsonb,jsonb,text,text,text,text,text) from public, anon, authenticated;
grant execute on function public.paper_append_audit(text,text,uuid,text,text,timestamptz,timestamptz,timestamptz,jsonb,jsonb,text,text,text,text,text) to service_role;

revoke all on function public.paper_ingest_signal(text,text,text,text,text,timestamptz,timestamptz,numeric,text,text,text,text,text,text,timestamptz,text) from public, anon, authenticated;
grant execute on function public.paper_ingest_signal(text,text,text,text,text,timestamptz,timestamptz,numeric,text,text,text,text,text,text,timestamptz,text) to service_role;

revoke all on function public.paper_create_order_from_approved_signal(uuid,text,text,text,text,text) from public, anon, authenticated;
grant execute on function public.paper_create_order_from_approved_signal(uuid,text,text,text,text,text) to service_role;

revoke all on function public.paper_bind_execution_attempt(uuid,uuid,uuid,integer,text,text) from public, anon, authenticated;
grant execute on function public.paper_bind_execution_attempt(uuid,uuid,uuid,integer,text,text) to service_role;

revoke all on function public.paper_reserve_risk(uuid,uuid,text,text,text) from public, anon, authenticated;
grant execute on function public.paper_reserve_risk(uuid,uuid,text,text,text) to service_role;

revoke all on function public.paper_apply_fill(uuid,uuid,integer,numeric,numeric,numeric,numeric,numeric,timestamptz,text,text,text) from public, anon, authenticated;
grant execute on function public.paper_apply_fill(uuid,uuid,integer,numeric,numeric,numeric,numeric,numeric,timestamptz,text,text,text) to service_role;

revoke all on function public.paper_terminalize_order(uuid,text,text,text,text,text) from public, anon, authenticated;
grant execute on function public.paper_terminalize_order(uuid,text,text,text,text,text) to service_role;

-- Remove sensitive tables from Supabase Realtime publication if present.
do $$
declare
  t text;
begin
  foreach t in array array[
    'execution_market_snapshots','paper_signals','risk_decisions','risk_reservations',
    'paper_orders','paper_order_events','paper_execution_attempts','paper_fills',
    'paper_positions','paper_position_events','execution_audit'
  ]
  loop
    if exists (
      select 1
      from pg_publication_tables
      where pubname = 'supabase_realtime'
        and schemaname = 'public'
        and tablename = t
    ) then
      execute format('alter publication supabase_realtime drop table public.%I', t);
    end if;
  end loop;
end;
$$;
