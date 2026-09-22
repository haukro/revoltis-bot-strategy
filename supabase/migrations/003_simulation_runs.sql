create table if not exists public.simulation_runs (
  id text primary key,
  status text not null check (status in ('completed', 'failed')),
  finished_at timestamptz not null,
  timeframe text not null,
  pairs jsonb not null,
  summary jsonb not null
);
alter table public.simulation_runs enable row level security;
