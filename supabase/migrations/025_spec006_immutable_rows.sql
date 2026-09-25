-- Synced from live Supabase migration 20260925093415 (spec006_immutable_rows).
-- IMPLEMENTATION-SPEC-006. Runtime B remains disabled.

-- SPEC-006 immutable reference/action ownership protection.

create or replace function public.paper_spec006_reject_immutable_change()
returns trigger
language plpgsql
set search_path=public
as $func$
begin
  if tg_op='UPDATE' then
    if to_jsonb(new) is not distinct from to_jsonb(old) then
      return new;
    end if;
    raise exception 'immutable_spec006_row:%',tg_table_name;
  end if;
  raise exception 'immutable_spec006_row:%',tg_table_name;
end;
$func$;

drop trigger if exists paper_strategy_actions_immutable_trg
on public.paper_strategy_actions;
create trigger paper_strategy_actions_immutable_trg
before update or delete on public.paper_strategy_actions
for each row execute function public.paper_spec006_reject_immutable_change();

drop trigger if exists paper_position_entry_ownership_immutable_trg
on public.paper_position_entry_ownership;
create trigger paper_position_entry_ownership_immutable_trg
before update or delete on public.paper_position_entry_ownership
for each row execute function public.paper_spec006_reject_immutable_change();

drop trigger if exists paper_strategy_reference_bars_5m_immutable_trg
on public.paper_strategy_reference_bars_5m;
create trigger paper_strategy_reference_bars_5m_immutable_trg
before update or delete on public.paper_strategy_reference_bars_5m
for each row execute function public.paper_spec006_reject_immutable_change();

revoke execute on function public.paper_spec006_reject_immutable_change()
from public,anon,authenticated;

