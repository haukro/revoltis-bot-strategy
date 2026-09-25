-- Synced from live Supabase migration 20260925093113 (spec006_indexes_acl).
-- IMPLEMENTATION-SPEC-006. Runtime B remains disabled.

-- SPEC-006 supporting indexes and trigger-helper ACL hardening.

create index if not exists paper_strategy_actions_linked_entry_idx
  on public.paper_strategy_actions(linked_entry_action_id)
  where linked_entry_action_id is not null;

create index if not exists paper_shadow_reference_entry_idx
  on public.paper_strategy_shadow_state(reference_entry_action_id)
  where reference_entry_action_id is not null;

create index if not exists paper_exit_intents_linked_entry_idx
  on public.paper_exit_intents(linked_entry_action_id)
  where linked_entry_action_id is not null;

create index if not exists paper_exit_intents_exit_order_idx
  on public.paper_exit_intents(paper_exit_order_id)
  where paper_exit_order_id is not null;

revoke execute on function public.paper_spec006_sync_entry_order_to_lifecycle()
from public,anon,authenticated;
revoke execute on function public.paper_spec006_sync_reservation_to_lifecycle()
from public,anon,authenticated;

revoke execute on function public.paper_spec006_shadow_cycle_guard()
from public,anon,authenticated;
revoke execute on function public.paper_positions_reject_reopen()
from public,anon,authenticated;

