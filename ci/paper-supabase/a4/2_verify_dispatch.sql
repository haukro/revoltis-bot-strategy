-- A4 rev2: vyhodnotenie kratkej skusky (iba citanie, spusta postgres v SQL editore testovacieho projektu).
-- Vyplnte TRI hodnoty v set_config nizsie: shadow test(y), zaciatok a koniec okna skusky.
-- Spustit az po 3_pause + 3b_wait_idle. Zastavenie cronu NIE JE dokaz dokoncenia: odoslane
-- poziadavky a workery mozu este bezat. Verdikt je konecny az po final_at (tick + 120 s,
-- vyprsanie vsetkych lease) a bez poziadaviek vo fronte pg_net; inak FAIL s dovodom not_final
-- alebo requests_in_flight. Pri FAIL skript skonci chybou, prazdne okno je FAIL (no_ticks).
begin;
set transaction read only;
select set_config('paper_a4.test_ids', '{<SHADOW_TEST_ID>}', true),
       set_config('paper_a4.from', '<FROM_TIMESTAMPTZ>', true),
       set_config('paper_a4.to', '<TO_TIMESTAMPTZ>', true);
select q.* from paper_dispatch.trial_ticks(current_setting('paper_a4.test_ids')::uuid[],
  current_setting('paper_a4.from')::timestamptz, current_setting('paper_a4.to')::timestamptz) q
  where q.state not in ('run_valid', 'completed_no_run') or q.pg_net_conflict or q.in_flight;
do $$ declare v record; begin
  if current_user <> 'postgres' then raise exception 'a4_verify_requires_postgres'; end if;
  select * into strict v from paper_dispatch.trial_verdict(current_setting('paper_a4.test_ids')::uuid[],
    current_setting('paper_a4.from')::timestamptz, current_setting('paper_a4.to')::timestamptz);
  if v.verdict is distinct from 'PASS' then
    raise exception 'A4 trial FAIL: reasons=% tests=% ticks=% pairs_ok=%/% final_at=%',
      v.reasons, v.tests, v.ticks, v.pairs_ok, v.pairs_total, v.final_at;
  end if;
  raise notice 'A4 trial PASS: tests=% ticks=% pairs_ok=%/% final_at=%', v.tests, v.ticks, v.pairs_ok, v.pairs_total, v.final_at;
end $$;
rollback;
