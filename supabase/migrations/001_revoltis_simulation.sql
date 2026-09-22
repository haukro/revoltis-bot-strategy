create table if not exists public.strategy_settings (
  id text primary key,
  settings jsonb not null,
  updated_at timestamptz not null default now()
);
create table if not exists public.simulated_trades (
  id text primary key,
  pair text not null,
  status text not null check (status in ('open','closed','cancelled')),
  opened_at timestamptz,
  closed_at timestamptz,
  entry_rate numeric,
  exit_rate numeric,
  stake_amount numeric,
  profit_usdt numeric default 0,
  exit_reason text,
  raw jsonb,
  created_at timestamptz not null default now()
);
create table if not exists public.sync_events (
  id text primary key,
  source text not null,
  received_at timestamptz not null,
  trade_count integer not null default 0
);
create table if not exists public.strategy_versions (
  id text primary key,
  name text not null,
  note text not null default '',
  settings jsonb not null,
  created_at timestamptz not null default now()
);
create table if not exists public.backtest_runs (
  id text primary key,
  strategy_version_id text not null references public.strategy_versions(id) on delete cascade,
  timerange text not null,
  data_source text not null,
  metrics jsonb not null,
  report jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
alter table public.strategy_settings enable row level security;
alter table public.simulated_trades enable row level security;
alter table public.sync_events enable row level security;
alter table public.strategy_versions enable row level security;
alter table public.backtest_runs enable row level security;
-- The FastAPI server uses the Supabase service-role key; browser clients never access these tables directly.
