-- A3 (NAVRH) faza 2: iba citanie. Posledne behy jobu a token pomocnej funkcie A2
-- (vola ju jej vlastnik postgres). Ziadne granty, zapisy ani zmena jobu.
select json_build_object(
  'runs', (select json_agg(r) from (select d.runid, d.status, d.return_message, d.start_time, d.end_time
             from cron.job_run_details d join cron.job j on j.jobid = d.jobid
             where j.jobname = 'paper_tick' and j.username = 'paper_cron_owner'
             order by d.runid desc limit 3) r),
  'ticks', (select json_agg(t) from (select tick_id, created_at, paper_cron_proof.tick_run_status(tick_id) as proof
             from public.paper_measure_ticks order by created_at desc limit 3) t)) as a3_verify;
