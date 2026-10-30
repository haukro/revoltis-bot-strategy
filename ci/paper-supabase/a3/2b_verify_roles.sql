-- A3 (NAVRH) faza 2b: overenie pod spravnymi rolami. Cela transakcia sa vrati
-- (ROLLBACK): docasne SET clenstva, vysledky ani ziadna ina zmena neostanu.
begin;
grant paper_rpc_owner to postgres with inherit false, set true;
grant paper_cron_owner to postgres with inherit false, set true;
do $$ declare t uuid; r text; begin
  select tick_id into t from public.paper_measure_ticks order by created_at desc limit 1;
  perform set_config('a3.tick', coalesce(t::text, 'none'), true);
  -- origin presne v kontexte assess RPC (vlastnik paper_rpc_owner).
  execute 'set local role paper_rpc_owner';
  if t is not null then
    r := paper_private.origin(t, floor(extract(epoch from clock_timestamp())*1000)::bigint, 600000);
  end if;
  perform set_config('a3.origin', coalesce(r, 'no_tick'), true);
  begin
    perform count(*) from cron.job; r := 'allowed';
  exception when insufficient_privilege then r := 'denied: ' || sqlerrm;
    when others then r := 'reached: ' || sqlerrm; end;
  perform set_config('a3.reader_cron_read', r, true);
  execute 'reset role';
  -- Rola cronu nesmie mazat vlastne logy ani spravovat joby.
  execute 'set local role paper_cron_owner';
  begin
    delete from cron.job_run_details where false; r := 'allowed';
  exception when insufficient_privilege then r := 'denied: ' || sqlerrm;
    when others then r := 'reached: ' || sqlerrm; end;
  perform set_config('a3.cron_owner_delete_logs', r, true);
  begin
    perform cron.unschedule(-1::bigint); r := 'allowed';
  exception when insufficient_privilege then r := 'denied: ' || sqlerrm;
    when others then r := 'reached: ' || sqlerrm; end;
  perform set_config('a3.cron_owner_unschedule', r, true);
  execute 'reset role';
end $$;
select json_build_object('tick', current_setting('a3.tick'), 'origin', current_setting('a3.origin'),
  'reader_cron_read', current_setting('a3.reader_cron_read'),
  'cron_owner_delete_logs', current_setting('a3.cron_owner_delete_logs'),
  'cron_owner_unschedule', current_setting('a3.cron_owner_unschedule')) as a3_roles;
rollback;
