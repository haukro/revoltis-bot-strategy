-- Atomic guard against overlapping frozen paper trades.
--
-- Migration 028 keys rows by label|pair|opened_at. Two refreshes that read an
-- empty ledger at the same time could still freeze two DIFFERENT entries whose
-- holding periods overlap (max_open_trades = 1), double-counting PnL. A check in
-- the application cannot close that race; this exclusion constraint does: the
-- second concurrent INSERT waits on the first transaction and is then rejected.
--
-- Ranges are half-open [opened_at, closed_at), so back-to-back trades where the
-- next entry starts at the previous exit stay allowed. Labels and pairs are
-- independent. Existing rows are never updated (see 028 triggers).
--
-- Applying this fails if the ledger already holds overlapping rows; resolve
-- those by hand first instead of weakening the constraint.

create schema if not exists extensions;
create extension if not exists btree_gist with schema extensions;

-- Empty ranges never overlap anything and could otherwise freeze invalid PnL.
alter table public.paper_trade_ledger
  drop constraint if exists paper_trade_ledger_positive_interval;
alter table public.paper_trade_ledger
  add constraint paper_trade_ledger_positive_interval
  check (closed_at > opened_at);

alter table public.paper_trade_ledger
  drop constraint if exists paper_trade_ledger_no_overlap;
alter table public.paper_trade_ledger
  add constraint paper_trade_ledger_no_overlap
  exclude using gist (
    paper_label with =,
    pair with =,
    tstzrange(opened_at, closed_at, '[)') with &&
  );

-- Insert rows once. Each row reports one outcome:
--   inserted  - frozen now
--   duplicate - same id already frozen (first value kept)
--   overlap   - another frozen trade of the same label and pair overlaps it
-- ON CONFLICT DO NOTHING without a target also arbitrates exclusion constraints,
-- so neither a duplicate nor an overlap aborts the rest of the batch.
create or replace function public.append_paper_trade_ledger(p_rows jsonb)
returns table (ledger_id text, outcome text)
language plpgsql
set search_path = public
as $func$
declare
  r jsonb;
  v_id text;
begin
  if jsonb_typeof(p_rows) is distinct from 'array' then
    raise exception 'paper_ledger_rows_must_be_array';
  end if;
  -- Earliest entry first, so one batch resolves its own overlaps deterministically.
  for r in
    select value from jsonb_array_elements(p_rows)
    order by (value->>'opened_at')::timestamptz, (value->>'closed_at')::timestamptz, value->>'id'
  loop
    v_id := null;
    insert into public.paper_trade_ledger (
      id, paper_label, run_id, pair, opened_at, closed_at, entry_rate, exit_rate,
      stake_amount, profit_usdt, exit_reason, cost_book_ts, cost_snapshot, raw, recorded_at
    ) values (
      r->>'id', r->>'paper_label', r->>'run_id', r->>'pair',
      (r->>'opened_at')::timestamptz, (r->>'closed_at')::timestamptz,
      (r->>'entry_rate')::numeric, (r->>'exit_rate')::numeric,
      (r->>'stake_amount')::numeric, (r->>'profit_usdt')::numeric,
      r->>'exit_reason', r->>'cost_book_ts',
      coalesce(r->'cost_snapshot', '{}'::jsonb), coalesce(r->'raw', '{}'::jsonb),
      coalesce((r->>'recorded_at')::timestamptz, now())
    )
    on conflict do nothing
    returning id into v_id;

    ledger_id := r->>'id';
    if v_id is not null then
      outcome := 'inserted';
    elsif exists (select 1 from public.paper_trade_ledger l where l.id = r->>'id') then
      outcome := 'duplicate';
    else
      outcome := 'overlap';
    end if;
    return next;
  end loop;
end;
$func$;

revoke all on function public.append_paper_trade_ledger(jsonb) from public;
do $grant$
begin
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function public.append_paper_trade_ledger(jsonb) to service_role;
  end if;
end;
$grant$;
