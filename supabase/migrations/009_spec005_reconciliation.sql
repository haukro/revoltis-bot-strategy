-- IMPLEMENTATION-SPEC-005 reconciliation engine.
-- Detection-only. No alpha changes. No live routing.

alter table public.paper_fills
  add column if not exists position_effect_seq bigint;

create table if not exists public.position_effect_heads (
  strategy_version_id text not null,
  pair text not null,
  last_sequence bigint not null default 0,
  updated_at timestamptz not null default now(),
  primary key(strategy_version_id,pair)
);

create or replace function public.paper_assign_position_effect_seq()
returns trigger
language plpgsql
security definer
set search_path=public
as $func$
declare
  v_next bigint;
begin
  insert into public.position_effect_heads(strategy_version_id,pair,last_sequence)
  values(new.strategy_version_id,new.pair,0)
  on conflict(strategy_version_id,pair) do nothing;

  select last_sequence into v_next
  from public.position_effect_heads
  where strategy_version_id=new.strategy_version_id
    and pair=new.pair
  for update;

  v_next := v_next + 1;

  update public.position_effect_heads
  set last_sequence=v_next,updated_at=clock_timestamp()
  where strategy_version_id=new.strategy_version_id
    and pair=new.pair;

  new.position_effect_seq := v_next;
  return new;
end;
$func$;

drop trigger if exists paper_fills_position_effect_seq on public.paper_fills;
create trigger paper_fills_position_effect_seq
before insert on public.paper_fills
for each row execute function public.paper_assign_position_effect_seq();

-- No existing fills are expected before Phase 2. If any exist, stop rather than guess ordering.
do $guard$
begin
  if exists(select 1 from public.paper_fills where position_effect_seq is null) then
    raise exception 'existing_fill_requires_explicit_position_effect_backfill';
  end if;
end;
$guard$;

alter table public.paper_fills
  alter column position_effect_seq set not null;

create unique index if not exists paper_fills_position_effect_seq_unique
on public.paper_fills(strategy_version_id,pair,position_effect_seq);

alter table public.position_effect_heads enable row level security;
revoke all on table public.position_effect_heads from anon,authenticated;
grant all on table public.position_effect_heads to service_role;

create or replace function public.paper_reconstruct_position(
  p_strategy_version_id text,
  p_pair text
)
returns jsonb
language plpgsql
security definer
set search_path=public
as $func$
declare
  r record;
  v_side text;
  v_qty numeric(30,12) := 0;
  v_avg numeric(30,12) := 0;
  v_gross numeric(30,12) := 0;
  v_cumulative_fees numeric(30,12) := 0;
  v_entry_fees numeric(30,12) := 0;
  v_exit_fees numeric(30,12) := 0;
  v_remaining_entry_fees numeric(30,12) := 0;
  v_realized numeric(30,12) := 0;
  v_lifetime_realized numeric(30,12) := 0;
  v_alloc_entry_fee numeric(30,12);
  v_gross_reduction numeric(30,12);
  v_realized_delta numeric(30,12);
  v_status text := 'CLOSED';
  v_opened_at timestamptz;
  v_closed_at timestamptz;
  v_daily jsonb := '{}'::jsonb;
  v_day_key text;
  v_day_value numeric(30,12);
  v_expected_side text;
begin
  for r in
    select
      f.*,
      o.intent_type,
      o.side as order_side
    from public.paper_fills f
    join public.paper_orders o on o.id=f.order_id
    where f.strategy_version_id=p_strategy_version_id
      and f.pair=p_pair
    order by f.position_effect_seq
  loop
    if r.intent_type='ENTRY' then
      v_expected_side := case when r.order_side='BUY' then 'LONG' else 'SHORT' end;

      if v_qty > 0 and v_side is distinct from v_expected_side then
        return jsonb_build_object(
          'valid',false,
          'error','entry_side_conflict',
          'position_effect_seq',r.position_effect_seq
        );
      end if;

      if v_qty = 0 then
        v_side := v_expected_side;
        v_avg := 0;
        v_gross := 0;
        v_cumulative_fees := 0;
        v_entry_fees := 0;
        v_exit_fees := 0;
        v_remaining_entry_fees := 0;
        v_realized := 0;
        v_opened_at := r.filled_at;
        v_closed_at := null;
      end if;

      v_avg := case
        when v_qty + r.fill_quantity > 0
        then ((v_qty*v_avg)+(r.fill_quantity*r.fill_price))/(v_qty+r.fill_quantity)
        else 0
      end;

      v_qty := v_qty+r.fill_quantity;
      v_gross := v_gross+(r.fill_quantity*r.fill_price);
      v_cumulative_fees := v_cumulative_fees+r.fee_amount;
      v_entry_fees := v_entry_fees+r.fee_amount;
      v_remaining_entry_fees := v_remaining_entry_fees+r.fee_amount;
      v_status := 'OPEN';

    elsif r.intent_type in ('EXIT','EMERGENCY_EXIT') then
      if v_qty <= 0 then
        return jsonb_build_object(
          'valid',false,
          'error','exit_without_open_quantity',
          'position_effect_seq',r.position_effect_seq
        );
      end if;

      if r.fill_quantity > v_qty + 0.00000001 then
        return jsonb_build_object(
          'valid',false,
          'error','exit_exceeds_open_quantity',
          'position_effect_seq',r.position_effect_seq
        );
      end if;

      if (v_side='LONG' and r.order_side<>'SELL')
         or (v_side='SHORT' and r.order_side<>'BUY') then
        return jsonb_build_object(
          'valid',false,
          'error','exit_side_conflict',
          'position_effect_seq',r.position_effect_seq
        );
      end if;

      v_alloc_entry_fee := case
        when v_qty>0 then v_remaining_entry_fees*r.fill_quantity/v_qty
        else 0
      end;

      v_realized_delta := case
        when v_side='LONG'
          then (r.fill_price-v_avg)*r.fill_quantity-v_alloc_entry_fee-r.fee_amount
        else (v_avg-r.fill_price)*r.fill_quantity-v_alloc_entry_fee-r.fee_amount
      end;

      v_gross_reduction := case
        when v_qty>0 then v_gross*r.fill_quantity/v_qty
        else 0
      end;

      v_day_key := to_char(r.filled_at at time zone 'UTC','YYYY-MM-DD');
      v_day_value := coalesce((v_daily->>v_day_key)::numeric,0)+v_realized_delta;
      v_daily := jsonb_set(v_daily,array[v_day_key],to_jsonb(v_day_value),true);

      v_qty := greatest(0,v_qty-r.fill_quantity);
      v_gross := greatest(0,v_gross-v_gross_reduction);
      v_cumulative_fees := v_cumulative_fees+r.fee_amount;
      v_exit_fees := v_exit_fees+r.fee_amount;
      v_remaining_entry_fees := greatest(0,v_remaining_entry_fees-v_alloc_entry_fee);
      v_realized := v_realized+v_realized_delta;
      v_lifetime_realized := v_lifetime_realized+v_realized_delta;

      if v_qty <= 0.00000001 then
        v_qty := 0;
        v_gross := 0;
        v_remaining_entry_fees := 0;
        v_status := 'CLOSED';
        v_closed_at := r.filled_at;
      else
        v_status := 'OPEN';
      end if;
    else
      return jsonb_build_object(
        'valid',false,
        'error','unknown_intent_type',
        'position_effect_seq',r.position_effect_seq
      );
    end if;
  end loop;

  return jsonb_build_object(
    'valid',true,
    'strategy_version_id',p_strategy_version_id,
    'pair',p_pair,
    'side',v_side,
    'quantity',v_qty,
    'average_entry_price',v_avg,
    'gross_entry_notional',v_gross,
    'cumulative_fees',v_cumulative_fees,
    'entry_fees',v_entry_fees,
    'exit_fees',v_exit_fees,
    'remaining_entry_fees',v_remaining_entry_fees,
    'realized_pnl',v_realized,
    'lifetime_realized_pnl',v_lifetime_realized,
    'status',v_status,
    'opened_at',v_opened_at,
    'closed_at',v_closed_at,
    'daily_realized',v_daily
  );
end;
$func$;

create or replace function public.paper_recon_issue_fingerprint(
  p_check_code text,
  p_entity_type text,
  p_entity_id uuid,
  p_proof jsonb
)
returns text
language sql
immutable
set search_path=public,extensions
as $func$
  select encode(
    digest(
      convert_to(
        coalesce(p_check_code,'') || '|' ||
        coalesce(p_entity_type,'') || '|' ||
        coalesce(p_entity_id::text,'') || '|' ||
        coalesce(p_proof::text,'{}'),
        'UTF8'
      ),
      'sha256'
    ),
    'hex'
  );
$func$;

create or replace function public.paper_run_reconciliation(
  p_account_key text default 'paper-default',
  p_worker_id text default 'reconciliation-worker',
  p_software_commit text default 'unknown'
)
returns jsonb
language plpgsql
security definer
set search_path=public,extensions
as $func$
declare
  v_run_id uuid;
  v_snapshot_at timestamptz;
  v_issue_count integer;
  v_critical_count integer;
  v_warning_count integer;
  v_audit_ok boolean;
  v_status text;
  v_halt jsonb;
begin
  /*
   * PostgreSQL READ COMMITTED gives one MVCC snapshot per statement.
   * All reconciliation detection queries are materialized by ONE CREATE TEMP TABLE
   * AS SELECT statement below. That is the locked SPEC-005 "equivalent as-of"
   * snapshot: every check sees exactly the same committed database snapshot.
   *
   * The unique partial index reconciliation_one_running_per_account provides the
   * single-run guard without session/advisory locks.
   */
  insert into public.reconciliation_runs(
    account_key,status,checks_performed,issues_found,started_at,software_commit
  )
  values(p_account_key,'RUNNING',0,0,clock_timestamp(),p_software_commit)
  on conflict do nothing
  returning id into v_run_id;

  if v_run_id is null then
    return jsonb_build_object(
      'started',false,
      'reason','RECONCILIATION_ALREADY_RUNNING'
    );
  end if;

  v_snapshot_at := clock_timestamp();

  create temp table if not exists pg_temp.paper_recon_detected(
    check_code text not null,
    severity text not null,
    entity_type text,
    entity_id uuid,
    fingerprint text not null,
    details jsonb not null
  ) on commit drop;

  truncate pg_temp.paper_recon_detected;

  insert into pg_temp.paper_recon_detected(
    check_code,severity,entity_type,entity_id,fingerprint,details
  )
  with active_ops as (
    select p.*
    from public.ops_policy_state s
    join public.ops_policy_versions p on p.id=s.policy_version_id
    where s.account_key=p_account_key
  ),
  live_leases as (
    select entity_type,entity_id,event_type
    from public.execution_outbox
    where processed_at is null
      and claimed_by is not null
      and claim_expires_at > v_snapshot_at
  ),
  sig_risk as (
    select
      'CHK_SIG_RISK'::text as check_code,
      case
        when o.processed_at is not null then 'CRITICAL'
        else 'WARNING'
      end::text as severity,
      'signal'::text as entity_type,
      s.id as entity_id,
      jsonb_build_object(
        'signal_id',s.id,
        'outbox_processed',o.processed_at is not null,
        'outbox_id',o.id
      ) as proof
    from public.paper_signals s
    left join public.risk_decisions d
      on d.signal_id=s.id and d.decision_stage='PRETRADE'
    left join lateral (
      select x.*
      from public.execution_outbox x
      where x.entity_type='signal'
        and x.entity_id=s.id
        and x.event_type='RISK_EVALUATE'
      order by x.created_at desc
      limit 1
    ) o on true
    cross join active_ops p
    where d.id is null
      and not exists(
        select 1 from live_leases l
        where l.entity_type='signal'
          and l.entity_id=s.id
          and l.event_type='RISK_EVALUATE'
      )
      and (
        o.processed_at is not null
        or (
          p.reconciliation_warning_age_ms is not null
          and extract(epoch from (v_snapshot_at-s.persisted_at))*1000 >= p.reconciliation_warning_age_ms
        )
      )
  ),
  risk_res as (
    select
      'CHK_RISK_RES'::text as check_code,
      'CRITICAL'::text as severity,
      'signal'::text as entity_type,
      d.signal_id as entity_id,
      jsonb_build_object(
        'signal_id',d.signal_id,
        'decision_id',d.id,
        'reservation_count',count(r.id)
      ) as proof
    from public.risk_decisions d
    left join public.risk_reservations r on r.signal_id=d.signal_id
    where d.decision='APPROVED'
    group by d.signal_id,d.id
    having count(r.id)<>1
  ),
  res_order as (
    select
      'CHK_RES_ORDER'::text as check_code,
      'WARNING'::text as severity,
      'risk_reservation'::text as entity_type,
      r.id as entity_id,
      jsonb_build_object(
        'reservation_id',r.id,
        'expires_at',r.expires_at,
        'status',r.status
      ) as proof
    from public.risk_reservations r
    where r.status in ('RESERVED','PARTIALLY_CONSUMED')
      and r.expires_at is not null
      and r.expires_at < v_snapshot_at
      and not exists(
        select 1
        from public.paper_orders o
        where o.signal_id=r.signal_id
          and o.status in ('CREATED','ACCEPTED','PENDING_FILL','PARTIALLY_FILLED','CANCEL_REQUESTED')
      )
  ),
  order_attempt as (
    select
      'CHK_ORDER_ATTEMPT'::text as check_code,
      case when o.processed_at is not null then 'CRITICAL' else 'WARNING' end::text as severity,
      'order'::text as entity_type,
      po.id as entity_id,
      jsonb_build_object(
        'order_id',po.id,
        'order_status',po.status,
        'outbox_id',o.id,
        'outbox_processed',o.processed_at is not null
      ) as proof
    from public.paper_orders po
    left join public.paper_execution_attempts a on a.order_id=po.id
    left join lateral (
      select x.*
      from public.execution_outbox x
      where x.entity_type='order'
        and x.entity_id=po.id
        and x.event_type='BIND_FILL_ATTEMPT'
      order by x.created_at desc
      limit 1
    ) o on true
    cross join active_ops p
    where po.status in ('ACCEPTED','PENDING_FILL','PARTIALLY_FILLED')
      and a.id is null
      and not exists(
        select 1 from live_leases l
        where l.entity_type='order'
          and l.entity_id=po.id
          and l.event_type='BIND_FILL_ATTEMPT'
      )
      and (
        o.processed_at is not null
        or (
          p.reconciliation_warning_age_ms is not null
          and extract(epoch from (v_snapshot_at-po.updated_at))*1000 >= p.reconciliation_warning_age_ms
        )
      )
  ),
  attempt_fill as (
    select
      'CHK_ATTEMPT_FILL'::text as check_code,
      'CRITICAL'::text as severity,
      'execution_attempt'::text as entity_type,
      a.id as entity_id,
      jsonb_build_object(
        'attempt_id',a.id,
        'order_id',a.order_id,
        'attempt_seq',a.attempt_seq,
        'status',a.status
      ) as proof
    from public.paper_execution_attempts a
    left join public.paper_fills f on f.execution_attempt_id=a.id
    where a.status='APPLIED'
      and f.id is null
  ),
  fill_sequence as (
    select
      'CHK_FILL_SEQUENCE'::text as check_code,
      'CRITICAL'::text as severity,
      'order'::text as entity_type,
      f.order_id as entity_id,
      jsonb_build_object(
        'order_id',f.order_id,
        'fill_count',count(*),
        'min_seq',min(f.fill_seq),
        'max_seq',max(f.fill_seq)
      ) as proof
    from public.paper_fills f
    group by f.order_id
    having min(f.fill_seq)<>1 or max(f.fill_seq)<>count(*)
  ),
  outbox_effect as (
    select
      'CHK_OUTBOX_EFFECT'::text as check_code,
      'CRITICAL'::text as severity,
      o.entity_type,
      o.entity_id,
      jsonb_build_object(
        'outbox_id',o.id,
        'event_type',o.event_type,
        'processed_at',o.processed_at
      ) as proof
    from public.execution_outbox o
    where o.processed_at is not null
      and (
        (
          o.event_type='RISK_EVALUATE'
          and not exists(
            select 1 from public.risk_decisions d where d.signal_id=o.entity_id
          )
        )
        or
        (
          o.event_type='BIND_FILL_ATTEMPT'
          and not exists(
            select 1 from public.paper_execution_attempts a where a.order_id=o.entity_id
          )
          and not exists(
            select 1
            from public.paper_orders po
            where po.id=o.entity_id
              and po.status in ('CANCELLED','REJECTED','EXPIRED','FAILED')
          )
        )
      )
  ),
  order_companion as (
    select
      'CHK_IMMUTABLE_COMPANION'::text as check_code,
      'CRITICAL'::text as severity,
      'order'::text as entity_type,
      o.id as entity_id,
      jsonb_build_object('order_id',o.id,'status',o.status,'missing','paper_order_event') as proof
    from public.paper_orders o
    where not exists(
      select 1 from public.paper_order_events e
      where e.order_id=o.id and e.to_state=o.status
    )
  ),
  position_companion as (
    select
      'CHK_IMMUTABLE_COMPANION'::text as check_code,
      'CRITICAL'::text as severity,
      'position'::text as entity_type,
      p.id as entity_id,
      jsonb_build_object('position_id',p.id,'status',p.status,'missing','paper_position_event') as proof
    from public.paper_positions p
    where not exists(
      select 1 from public.paper_position_events e
      where e.position_id=p.id
        and e.state_after->>'status'=p.status
    )
  ),
  reconstructed as (
    select distinct
      p.strategy_version_id,
      p.pair,
      public.paper_reconstruct_position(p.strategy_version_id,p.pair) as r
    from public.paper_positions p
  ),
  active_position as (
    select *
    from public.paper_positions
    where status in ('OPENING','OPEN','EXIT_PENDING','CLOSING')
  ),
  position_realized_aggregate as (
    select strategy_version_id,pair,coalesce(sum(realized_pnl),0)::numeric as lifetime_realized
    from public.paper_positions
    group by strategy_version_id,pair
  ),
  fill_position as (
    select
      'CHK_FILL_POSITION'::text as check_code,
      'CRITICAL'::text as severity,
      'position'::text as entity_type,
      p.id as entity_id,
      jsonb_build_object(
        'position_id',p.id,
        'stored_status',p.status,
        'reconstructed_status',r.r->>'status',
        'stored_quantity',p.quantity,
        'reconstructed_quantity',r.r->>'quantity',
        'stored_realized_pnl',p.realized_pnl,
        'reconstructed_current_realized_pnl',r.r->>'realized_pnl'
      ) as proof
    from active_position p
    join reconstructed r
      on r.strategy_version_id=p.strategy_version_id and r.pair=p.pair
    where coalesce((r.r->>'valid')::boolean,false) is not true
       or coalesce(r.r->>'side','') is distinct from coalesce(p.side,'')
       or abs(coalesce((r.r->>'quantity')::numeric,0)-p.quantity)>0.00000001
       or abs(coalesce((r.r->>'average_entry_price')::numeric,0)-p.average_entry_price)>0.00000001
       or abs(coalesce((r.r->>'gross_entry_notional')::numeric,0)-p.gross_entry_notional)>0.00000001
       or abs(coalesce((r.r->>'entry_fees')::numeric,0)-p.entry_fees)>0.00000001
       or abs(coalesce((r.r->>'exit_fees')::numeric,0)-p.exit_fees)>0.00000001
       or abs(coalesce((r.r->>'remaining_entry_fees')::numeric,0)-p.remaining_entry_fees)>0.00000001
       or abs(coalesce((r.r->>'realized_pnl')::numeric,0)-p.realized_pnl)>0.00000001
       or coalesce(r.r->>'status','') not in ('OPEN','OPENING','EXIT_PENDING','CLOSING')
  ),
  closed_without_active_mismatch as (
    select
      'CHK_FILL_POSITION'::text as check_code,
      'CRITICAL'::text as severity,
      'position_ledger'::text as entity_type,
      null::uuid as entity_id,
      jsonb_build_object(
        'strategy_version_id',r.strategy_version_id,
        'pair',r.pair,
        'reconstructed_status',r.r->>'status',
        'reconstructed_quantity',r.r->>'quantity'
      ) as proof
    from reconstructed r
    where not exists(
      select 1 from active_position p
      where p.strategy_version_id=r.strategy_version_id and p.pair=r.pair
    )
      and (
        coalesce((r.r->>'valid')::boolean,false) is not true
        or coalesce((r.r->>'quantity')::numeric,0)<>0
        or coalesce(r.r->>'status','')<>'CLOSED'
      )
  ),
  lifetime_realized_mismatch as (
    select
      'CHK_FILL_POSITION'::text as check_code,
      'CRITICAL'::text as severity,
      'position_ledger'::text as entity_type,
      null::uuid as entity_id,
      jsonb_build_object(
        'strategy_version_id',r.strategy_version_id,
        'pair',r.pair,
        'stored_lifetime_realized',coalesce(a.lifetime_realized,0),
        'reconstructed_lifetime_realized',r.r->>'lifetime_realized_pnl'
      ) as proof
    from reconstructed r
    left join position_realized_aggregate a
      on a.strategy_version_id=r.strategy_version_id and a.pair=r.pair
    where abs(
      coalesce((r.r->>'lifetime_realized_pnl')::numeric,0)-coalesce(a.lifetime_realized,0)
    )>0.00000001
  ),
  exposure_calc as (
    select
      coalesce(sum(gross_entry_notional) filter(where status in ('OPENING','OPEN','EXIT_PENDING','CLOSING')),0)::numeric as used_gross,
      coalesce(sum(case when side='LONG' then gross_entry_notional else -gross_entry_notional end)
        filter(where status in ('OPENING','OPEN','EXIT_PENDING','CLOSING')),0)::numeric as used_net
    from public.paper_positions
  ),
  reservation_calc as (
    select
      coalesce(sum(reserved_notional-consumed_notional)
        filter(where status in ('RESERVED','PARTIALLY_CONSUMED')),0)::numeric as reserved_gross,
      coalesce(sum(case when side='LONG'
        then reserved_notional-consumed_notional
        else -(reserved_notional-consumed_notional) end)
        filter(where status in ('RESERVED','PARTIALLY_CONSUMED')),0)::numeric as reserved_net
    from public.risk_reservations
  ),
  exposure_mismatch as (
    select
      'CHK_POSITION_EXPOSURE'::text as check_code,
      'CRITICAL'::text as severity,
      'portfolio_risk_state'::text as entity_type,
      null::uuid as entity_id,
      jsonb_build_object(
        'stored_used_gross',s.used_gross_notional,
        'calc_used_gross',e.used_gross,
        'stored_reserved_gross',s.reserved_gross_notional,
        'calc_reserved_gross',r.reserved_gross,
        'stored_used_net',s.used_net_notional,
        'calc_used_net',e.used_net,
        'stored_reserved_net',s.reserved_net_notional,
        'calc_reserved_net',r.reserved_net
      ) as proof
    from public.portfolio_risk_state s
    cross join exposure_calc e
    cross join reservation_calc r
    where s.account_key=p_account_key
      and (
        abs(s.used_gross_notional-e.used_gross)>0.00000001
        or abs(s.reserved_gross_notional-r.reserved_gross)>0.00000001
        or abs(s.used_net_notional-e.used_net)>0.00000001
        or abs(s.reserved_net_notional-r.reserved_net)>0.00000001
      )
  ),
  daily_calc as (
    select coalesce(sum(
      coalesce(
        (
          public.paper_reconstruct_position(x.strategy_version_id,x.pair)
          ->'daily_realized'
          ->>to_char(s.risk_utc_day,'YYYY-MM-DD')
        )::numeric,
        0
      )
    ),0)::numeric as daily_realized
    from (
      select distinct strategy_version_id,pair
      from public.paper_fills
    ) x
    cross join public.portfolio_risk_state s
    where s.account_key=p_account_key
  ),
  daily_mismatch as (
    select
      'CHK_DAILY_REALIZED'::text as check_code,
      'CRITICAL'::text as severity,
      'portfolio_risk_state'::text as entity_type,
      null::uuid as entity_id,
      jsonb_build_object(
        'risk_utc_day',s.risk_utc_day,
        'stored_realized',s.realized_pnl_utc_day,
        'calc_realized',d.daily_realized
      ) as proof
    from public.portfolio_risk_state s
    cross join daily_calc d
    where s.account_key=p_account_key
      and abs(s.realized_pnl_utc_day-d.daily_realized)>0.00000001
  ),
  audit_rows as (
    select
      a.*,
      row_number() over(partition by a.stream_key order by a.stream_sequence) as expected_seq,
      lag(a.event_hash) over(partition by a.stream_key order by a.stream_sequence) as expected_prev_hash,
      encode(
        digest(
          convert_to(
            concat_ws('|',
              a.stream_key,
              a.stream_sequence::text,
              coalesce(a.prev_hash,''),
              a.event_type,
              a.entity_type,
              a.entity_id::text,
              a.correlation_id,
              coalesce(a.strategy_version_id,''),
              coalesce(a.market_time::text,''),
              a.received_at::text,
              coalesce(a.decision_at::text,''),
              coalesce(a.state_before::text,'null'),
              coalesce(a.state_after::text,'null'),
              coalesce(a.input_snapshot_hash,''),
              a.reason_code,
              a.worker_id,
              a.software_commit
            ),
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      ) as recomputed_hash
    from public.execution_audit a
  ),
  audit_bad as (
    select
      'CHK_AUDIT_STREAM'::text as check_code,
      'CRITICAL'::text as severity,
      'audit_event'::text as entity_type,
      a.event_id as entity_id,
      jsonb_build_object(
        'stream_key',a.stream_key,
        'stream_sequence',a.stream_sequence,
        'expected_sequence',a.expected_seq,
        'prev_hash',a.prev_hash,
        'expected_prev_hash',a.expected_prev_hash,
        'event_hash',a.event_hash,
        'recomputed_hash',a.recomputed_hash
      ) as proof
    from audit_rows a
    where a.stream_sequence<>a.expected_seq
       or coalesce(a.prev_hash,'')<>coalesce(a.expected_prev_hash,'')
       or a.event_hash<>a.recomputed_hash
  ),
  audit_head_bad as (
    select
      'CHK_AUDIT_STREAM'::text as check_code,
      'CRITICAL'::text as severity,
      'audit_stream_head'::text as entity_type,
      null::uuid as entity_id,
      jsonb_build_object(
        'stream_key',h.stream_key,
        'head_sequence',h.last_sequence,
        'actual_sequence',coalesce(max(a.stream_sequence),0),
        'head_hash',h.last_hash,
        'actual_hash',(array_agg(a.event_hash order by a.stream_sequence desc))[1]
      ) as proof
    from public.audit_stream_heads h
    left join public.execution_audit a on a.stream_key=h.stream_key
    group by h.stream_key,h.last_sequence,h.last_hash
    having h.last_sequence<>coalesce(max(a.stream_sequence),0)
       or coalesce(h.last_hash,'')<>coalesce((array_agg(a.event_hash order by a.stream_sequence desc))[1],'')
  ),
  kill_fill as (
    select
      'CHK_KILL_SWITCH_FILL'::text as check_code,
      'CRITICAL'::text as severity,
      'fill'::text as entity_type,
      f.id as entity_id,
      jsonb_build_object(
        'fill_id',f.id,
        'fill_created_at',f.created_at,
        'kill_state',k.to_state,
        'kill_event_id',k.id
      ) as proof
    from public.paper_fills f
    join lateral (
      select e.*
      from public.kill_switch_events e
      where e.account_key=p_account_key
        and e.created_at<=f.created_at
      order by e.created_at desc,e.id desc
      limit 1
    ) k on true
    where k.to_state in ('HALTED','RECOVERY_PENDING')
  ),
  all_issues as (
    select * from sig_risk
    union all select * from risk_res
    union all select * from res_order
    union all select * from order_attempt
    union all select * from attempt_fill
    union all select * from fill_sequence
    union all select * from outbox_effect
    union all select * from order_companion
    union all select * from position_companion
    union all select * from fill_position
    union all select * from closed_without_active_mismatch
    union all select * from lifetime_realized_mismatch
    union all select * from exposure_mismatch
    union all select * from daily_mismatch
    union all select * from audit_bad
    union all select * from audit_head_bad
    union all select * from kill_fill
  )
  select
    check_code,
    severity,
    entity_type,
    entity_id,
    public.paper_recon_issue_fingerprint(
      check_code,entity_type,entity_id,proof
    ) as fingerprint,
    proof as details
  from all_issues;

  -- Resolve conditions that are no longer present in this consistent snapshot.
  update public.reconciliation_issues i
  set resolved_at=clock_timestamp()
  where i.resolved_at is null
    and not exists(
      select 1
      from pg_temp.paper_recon_detected d
      where d.check_code=i.check_code
        and coalesce(d.entity_type,'')=coalesce(i.entity_type,'')
        and coalesce(d.entity_id,'00000000-0000-0000-0000-000000000000'::uuid)
            =coalesce(i.entity_id,'00000000-0000-0000-0000-000000000000'::uuid)
        and d.fingerprint=i.fingerprint
    );

  insert into public.reconciliation_issues(
    run_id,issue_type,severity,entity_type,entity_id,details,check_code,fingerprint
  )
  select
    v_run_id,d.check_code,d.severity,d.entity_type,d.entity_id,d.details,d.check_code,d.fingerprint
  from pg_temp.paper_recon_detected d
  on conflict do nothing;

  select count(*) into v_issue_count from pg_temp.paper_recon_detected;
  select count(*) into v_critical_count from pg_temp.paper_recon_detected where severity='CRITICAL';
  select count(*) into v_warning_count from pg_temp.paper_recon_detected where severity='WARNING';

  v_audit_ok := not exists(
    select 1 from pg_temp.paper_recon_detected where check_code='CHK_AUDIT_STREAM'
  );

  v_status := case
    when v_critical_count>0 then 'FAILED'
    when v_warning_count>0 then 'WARNING'
    else 'PASSED'
  end;

  update public.reconciliation_runs
  set status=v_status,
      checks_performed=15,
      issues_found=v_issue_count,
      snapshot_at=v_snapshot_at,
      audit_integrity_passed=v_audit_ok,
      summary=jsonb_build_object(
        'critical_count',v_critical_count,
        'warning_count',v_warning_count,
        'snapshot_mode','single_materialized_statement',
        'lease_aware',true
      ),
      completed_at=clock_timestamp()
  where id=v_run_id;

  if v_critical_count>0 then
    select public.paper_set_kill_switch_state(
      'HALTED','RECONCILIATION_CRITICAL',p_worker_id,p_software_commit,p_account_key
    ) into v_halt;
  end if;

  return jsonb_build_object(
    'started',true,
    'run_id',v_run_id,
    'status',v_status,
    'critical_count',v_critical_count,
    'warning_count',v_warning_count,
    'issue_count',v_issue_count,
    'audit_integrity_passed',v_audit_ok,
    'snapshot_at',v_snapshot_at,
    'kill_switch_action',v_halt
  );
exception when others then
  -- If this transaction aborts, RUNNING run insertion also rolls back.
  raise;
end;
$func$;

-- Phase 2 reconciliation RPCs are internal/service-role only.
alter table public.position_effect_heads enable row level security;
revoke all on table public.position_effect_heads from anon,authenticated;
grant all on table public.position_effect_heads to service_role;

do $revoke$
declare
  r record;
begin
  for r in
    select p.oid::regprocedure as signature
    from pg_proc p
    join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public'
      and p.proname like 'paper_%'
  loop
    execute format('revoke all on function %s from PUBLIC, anon, authenticated',r.signature);
    execute format('grant execute on function %s to service_role',r.signature);
  end loop;
end;
$revoke$;
