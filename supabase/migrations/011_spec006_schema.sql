-- Synced from live Supabase migration 20260925085831 (spec006_schema).
-- IMPLEMENTATION-SPEC-006. Runtime B remains disabled.

-- IMPLEMENTATION-SPEC-006 — locked schema primitives.
-- No alpha changes. No live routing. TEST-SPEC-002 runtime binding remains disabled.

create table if not exists public.paper_strategy_execution_fence (
  strategy_version_id text not null,
  pair text not null,
  fence_version bigint not null default 1,
  updated_at timestamptz not null default now(),
  primary key(strategy_version_id, pair)
);

create table if not exists public.paper_strategy_shadow_state (
  strategy_version_id text not null,
  pair text not null,
  adapter_epoch_id uuid not null,
  data_state text not null check (data_state in ('CONTIGUOUS','INVALID')),
  reference_position_state text not null check (reference_position_state in ('FLAT','LONG','SHORT')),
  reference_entry_action_id uuid,
  reference_signal_close_time timestamptz,
  reference_entry_time timestamptz,
  reference_entry_price numeric(30,12),
  reference_entry_atr numeric(30,12),
  active_stop numeric(30,12),
  peak_high numeric(30,12),
  trough_low numeric(30,12),
  last_processed_5m_open_time timestamptz,
  last_processed_1h_close_time timestamptz,
  dispatch_frontier_5m_open_time timestamptz,
  dispatch_enable_commit_time timestamptz,
  invalid_reason text,
  invalid_at_5m_open_time timestamptz,
  state_version bigint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key(strategy_version_id, pair),
  check (
    (reference_position_state='FLAT')
    or (
      reference_entry_time is not null
      and reference_entry_price is not null
      and reference_entry_atr is not null
      and active_stop is not null
    )
  )
);

create table if not exists public.paper_strategy_actions (
  id uuid primary key default gen_random_uuid(),
  adapter_epoch_id uuid not null,
  strategy_version_id text not null,
  pair text not null,
  action_type text not null check (action_type in ('ENTRY','EXIT_TO_FLAT')),
  position_side text not null check (position_side in ('LONG','SHORT')),
  reference_decision_time timestamptz not null,
  required_execution_time timestamptz not null,
  reference_price numeric(30,12) not null check (reference_price > 0),
  reason_code text not null,
  rule_id text not null,
  reference_atr numeric(30,12),
  active_stop numeric(30,12),
  linked_entry_action_id uuid references public.paper_strategy_actions(id),
  shadow_state_before jsonb not null,
  shadow_state_after jsonb not null,
  idempotency_key text not null unique,
  software_commit text not null,
  created_at timestamptz not null default now(),
  check (
    (action_type='ENTRY' and linked_entry_action_id is null)
    or (action_type='EXIT_TO_FLAT' and linked_entry_action_id is not null)
  )
);

alter table public.paper_strategy_shadow_state
  drop constraint if exists paper_strategy_shadow_state_reference_entry_action_id_fkey;
alter table public.paper_strategy_shadow_state
  add constraint paper_strategy_shadow_state_reference_entry_action_id_fkey
  foreign key(reference_entry_action_id) references public.paper_strategy_actions(id);

create table if not exists public.paper_entry_lifecycles (
  entry_action_id uuid primary key references public.paper_strategy_actions(id),
  adapter_epoch_id uuid not null,
  strategy_version_id text not null,
  pair text not null,
  status text not null check (
    status in (
      'PENDING_DISPATCH','ORDER_ACTIVE','TERMINAL_FILLED',
      'TERMINAL_NO_FILL','NEVER_CREATED_FENCED','TERMINAL_REJECTED'
    )
  ),
  paper_entry_order_id uuid unique references public.paper_orders(id),
  reservation_id uuid unique references public.risk_reservations(id),
  state_version bigint not null default 1,
  last_error_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.paper_position_entry_ownership (
  position_id uuid primary key references public.paper_positions(id),
  entry_action_id uuid not null unique references public.paper_strategy_actions(id),
  paper_entry_order_id uuid not null unique references public.paper_orders(id),
  strategy_version_id text not null,
  pair text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.paper_exit_intents (
  id uuid primary key default gen_random_uuid(),
  adapter_epoch_id uuid not null,
  intent_origin text not null check (
    intent_origin in ('REFERENCE_EXIT','INVALID_RECOVERY','INTEGRITY_CRITICAL')
  ),
  exit_action_id uuid references public.paper_strategy_actions(id),
  linked_entry_action_id uuid references public.paper_strategy_actions(id),
  recovery_key text,
  integrity_key text,
  claimed_position_id uuid references public.paper_positions(id),
  strategy_version_id text not null,
  pair text not null,
  reference_position_side text check (reference_position_side in ('LONG','SHORT')),
  locked_reduce_side text not null check (locked_reduce_side in ('BUY','SELL')),
  status text not null check (status in ('OPEN','PAUSED','SATISFIED','CRITICAL')),
  paper_exit_order_id uuid,
  last_error_code text,
  state_version bigint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  satisfied_at timestamptz,
  check (
    (
      intent_origin='REFERENCE_EXIT'
      and exit_action_id is not null
      and linked_entry_action_id is not null
      and reference_position_side is not null
      and recovery_key is null
      and integrity_key is null
    )
    or (
      intent_origin='INVALID_RECOVERY'
      and exit_action_id is null
      and linked_entry_action_id is null
      and recovery_key is not null
      and integrity_key is null
    )
    or (
      intent_origin='INTEGRITY_CRITICAL'
      and exit_action_id is null
      and recovery_key is null
      and integrity_key is not null
      and claimed_position_id is not null
      and status='CRITICAL'
    )
  ),
  check (
    (reference_position_side='LONG' and locked_reduce_side='SELL')
    or (reference_position_side='SHORT' and locked_reduce_side='BUY')
    or reference_position_side is null
  )
);

create unique index if not exists paper_exit_intents_exit_action_unique
  on public.paper_exit_intents(exit_action_id)
  where exit_action_id is not null;

create unique index if not exists paper_exit_intents_recovery_key_unique
  on public.paper_exit_intents(recovery_key)
  where recovery_key is not null;

create unique index if not exists paper_exit_intents_integrity_key_unique
  on public.paper_exit_intents(integrity_key)
  where integrity_key is not null;

create unique index if not exists paper_exit_intents_live_position_claim_unique
  on public.paper_exit_intents(claimed_position_id)
  where claimed_position_id is not null
    and status in ('OPEN','PAUSED','CRITICAL');

alter table public.paper_orders
  add column if not exists exit_intent_id uuid references public.paper_exit_intents(id);

create unique index if not exists paper_orders_one_exit_per_intent
  on public.paper_orders(exit_intent_id)
  where exit_intent_id is not null;

alter table public.paper_exit_intents
  drop constraint if exists paper_exit_intents_paper_exit_order_id_fkey;
alter table public.paper_exit_intents
  add constraint paper_exit_intents_paper_exit_order_id_fkey
  foreign key(paper_exit_order_id) references public.paper_orders(id);

create index if not exists paper_strategy_actions_cycle_idx
  on public.paper_strategy_actions(strategy_version_id,pair,adapter_epoch_id,reference_decision_time);

create index if not exists paper_entry_lifecycles_pair_status_idx
  on public.paper_entry_lifecycles(strategy_version_id,pair,status);

create index if not exists paper_exit_intents_pair_status_idx
  on public.paper_exit_intents(strategy_version_id,pair,status);

create or replace function public.paper_positions_reject_reopen()
returns trigger
language plpgsql
set search_path=public
as $func$
begin
  if old.status='CLOSED'
     and new.status in ('OPENING','OPEN','EXIT_PENDING','CLOSING') then
    raise exception 'closed_position_reopen_forbidden';
  end if;
  return new;
end;
$func$;

drop trigger if exists paper_positions_reject_reopen_trg on public.paper_positions;
create trigger paper_positions_reject_reopen_trg
before update on public.paper_positions
for each row execute function public.paper_positions_reject_reopen();

alter table public.paper_strategy_execution_fence enable row level security;
alter table public.paper_strategy_shadow_state enable row level security;
alter table public.paper_strategy_actions enable row level security;
alter table public.paper_entry_lifecycles enable row level security;
alter table public.paper_position_entry_ownership enable row level security;
alter table public.paper_exit_intents enable row level security;

revoke all on table public.paper_strategy_execution_fence from public,anon,authenticated;
revoke all on table public.paper_strategy_shadow_state from public,anon,authenticated;
revoke all on table public.paper_strategy_actions from public,anon,authenticated;
revoke all on table public.paper_entry_lifecycles from public,anon,authenticated;
revoke all on table public.paper_position_entry_ownership from public,anon,authenticated;
revoke all on table public.paper_exit_intents from public,anon,authenticated;

grant all on table public.paper_strategy_execution_fence to service_role;
grant all on table public.paper_strategy_shadow_state to service_role;
grant all on table public.paper_strategy_actions to service_role;
grant all on table public.paper_entry_lifecycles to service_role;
grant all on table public.paper_position_entry_ownership to service_role;
grant all on table public.paper_exit_intents to service_role;

revoke execute on function public.paper_positions_reject_reopen() from public,anon,authenticated;

