-- A3 (NAVRH) faza 3b po 3_pause.sql: iba citanie. Deaktivacia nezastavi beh,
-- ktory uz zacal. Caka najviac 120 s, kym job paper_tick nema ziadny beh
-- v stave starting/running; ak ostane rozbehnuty, skonci chybou.
do $$ declare i int := 0; begin
  if (select count(*) from cron.job where jobname = 'paper_tick' and username = 'paper_cron_owner' and not active) <> 1 then
    raise exception 'a3_job_not_paused'; end if;
  while i < 120 and exists(select from cron.job_run_details d join cron.job j on j.jobid = d.jobid
        where j.jobname = 'paper_tick' and j.username = 'paper_cron_owner' and d.status in ('starting', 'running')) loop
    perform pg_sleep(1); i := i + 1;
  end loop;
  if exists(select from cron.job_run_details d join cron.job j on j.jobid = d.jobid
        where j.jobname = 'paper_tick' and j.username = 'paper_cron_owner' and d.status in ('starting', 'running')) then
    raise exception 'a3_runs_still_active'; end if;
end $$;
select json_build_object(
  'active', (select active from cron.job where jobname = 'paper_tick' and username = 'paper_cron_owner'),
  'unfinished', (select count(*) from cron.job_run_details d join cron.job j on j.jobid = d.jobid
                 where j.jobname = 'paper_tick' and j.username = 'paper_cron_owner' and d.status in ('starting', 'running')),
  'runs', (select count(*) from cron.job_run_details d join cron.job j on j.jobid = d.jobid
           where j.jobname = 'paper_tick' and j.username = 'paper_cron_owner'),
  'last_finished', (select max(d.end_time) from cron.job_run_details d join cron.job j on j.jobid = d.jobid
                    where j.jobname = 'paper_tick' and j.username = 'paper_cron_owner')) as a3_idle;
