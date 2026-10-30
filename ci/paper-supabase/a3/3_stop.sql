-- A3 (NAVRH) faza 3: odstranenie jobu a navrat povodnych prav v jednej transakcii.
-- Po unschedule zmizne riadok cron.job, takze tick skusky uz nebude overitelny.
begin;
grant paper_cron_owner to postgres with inherit false, set true;
grant usage on schema cron to paper_cron_owner;
set local role paper_cron_owner;
select cron.unschedule(jobid) from cron.job where jobname = 'paper_tick' and username = 'paper_cron_owner';
reset role;
revoke usage on schema cron from paper_cron_owner;
revoke paper_cron_owner from postgres;
alter role paper_cron_owner nologin;
do $$ begin
  if exists(select from cron.job where jobname = 'paper_tick') then raise exception 'a3_job_left'; end if;
  if has_schema_privilege('paper_cron_owner', 'cron', 'USAGE') then raise exception 'a3_cron_usage_left'; end if;
  if (select rolcanlogin from pg_roles where rolname = 'paper_cron_owner') then raise exception 'a3_login_left'; end if;
  if exists(select from pg_auth_members where roleid = 'paper_cron_owner'::regrole
            and member = 'postgres'::regrole and (set_option or inherit_option)) then
    raise exception 'a3_membership_left'; end if;
end $$;
commit;
