create table if not exists public.optimizer_runs (
  id uuid primary key,
  status text not null,
  created_at timestamptz not null default now(),
  finished_at timestamptz,
  request jsonb not null default '{}'::jsonb,
  result jsonb not null default '{}'::jsonb
);
