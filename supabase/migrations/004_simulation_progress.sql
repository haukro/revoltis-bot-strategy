alter table public.simulation_runs add column if not exists started_at timestamptz;
alter table public.simulation_runs add column if not exists test_start bigint;
alter table public.simulation_runs add column if not exists test_end bigint;
alter table public.simulation_runs add column if not exists progress jsonb not null default '{}'::jsonb;
