-- A4 (NAVRH) faza 1: rovnaky postup ako A3 1_schedule.sql, iba prikaz jobu odosiela tick.
-- Spusta postgres (bez superusera). Docasne prava vzniknu a zaniknu v tejto
-- jedinej transakcii, takze zvonka nie su nikdy viditelne.
begin;
do $$ begin
  if current_user <> 'postgres' then raise exception 'a3_requires_postgres'; end if;
  if exists(select from cron.job where jobname = 'paper_tick') then raise exception 'a3_job_already_exists'; end if;
end $$;
grant paper_cron_owner to postgres with inherit false, set true;
grant usage on schema cron to paper_cron_owner;
alter role paper_cron_owner login;
set local role paper_cron_owner;
-- username = current_user = paper_cron_owner, database = current_database().
select cron.schedule('paper_tick', '* * * * *', 'select paper_dispatch.send_tick(public.paper_tick_start())');
reset role;
revoke usage on schema cron from paper_cron_owner;
revoke paper_cron_owner from postgres;
do $$ begin
  if has_schema_privilege('paper_cron_owner', 'cron', 'USAGE') then raise exception 'a3_cron_usage_left'; end if;
  if exists(select from pg_auth_members where roleid = 'paper_cron_owner'::regrole
            and member = 'postgres'::regrole and (set_option or inherit_option)) then
    raise exception 'a3_membership_left'; end if;
  if (select count(*) from cron.job where jobname = 'paper_tick' and username = 'paper_cron_owner'
        and database = current_database() and command = 'select paper_dispatch.send_tick(public.paper_tick_start())' and active) <> 1 then
    raise exception 'a3_job_missing'; end if;
end $$;
commit;
