create table if not exists public.signal_diagnostics (
  id text primary key,
  pair text not null,
  occurred_at timestamptz not null,
  decision text not null check (decision in ('entered', 'rejected')),
  reason text not null,
  strategy_version_id text references public.strategy_versions(id) on delete set null,
  values jsonb not null default '{}'::jsonb
);
create index if not exists signal_diagnostics_occurred_at_idx on public.signal_diagnostics (occurred_at desc);
alter table public.signal_diagnostics enable row level security;
