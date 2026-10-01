-- Append-only ledger of closed paper trades (HYPE v8 official / scale tests).
-- Rows are written once and can never be updated or deleted.

create table if not exists public.paper_trade_ledger (
  id text primary key,
  paper_label text not null check (paper_label in ('official', 'scale_300_150')),
  run_id text,
  pair text not null,
  opened_at timestamptz not null,
  closed_at timestamptz not null,
  entry_rate numeric not null,
  exit_rate numeric not null,
  stake_amount numeric not null,
  profit_usdt numeric not null,
  exit_reason text not null,
  cost_book_ts text,
  cost_snapshot jsonb not null default '{}'::jsonb,
  raw jsonb not null default '{}'::jsonb,
  recorded_at timestamptz not null default now()
);

create index if not exists paper_trade_ledger_label_closed_idx
  on public.paper_trade_ledger (paper_label, closed_at);

alter table public.paper_trade_ledger enable row level security;

create or replace function public.paper_trade_ledger_reject_change()
returns trigger
language plpgsql
set search_path = public
as $func$
begin
  raise exception 'immutable_paper_trade_ledger_row';
end;
$func$;

drop trigger if exists paper_trade_ledger_immutable_trg on public.paper_trade_ledger;
create trigger paper_trade_ledger_immutable_trg
before update or delete on public.paper_trade_ledger
for each row execute function public.paper_trade_ledger_reject_change();

drop trigger if exists paper_trade_ledger_no_truncate_trg on public.paper_trade_ledger;
create trigger paper_trade_ledger_no_truncate_trg
before truncate on public.paper_trade_ledger
for each statement execute function public.paper_trade_ledger_reject_change();
