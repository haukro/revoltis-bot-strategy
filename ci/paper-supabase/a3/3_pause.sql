-- A3 (NAVRH) faza 3 pre P4: zastavenie DEAKTIVACIOU. Job aj logy ostavaju, takze
-- ticky ostavaju overitelne podla A2. Docasne prava iba v tejto transakcii.
-- cron.alter_job nema PUBLIC EXECUTE; postgres ho ma s GRANT OPTION.
begin;
do $$ begin
  if (select count(*) from cron.job where jobname = 'paper_tick' and username = 'paper_cron_owner') <> 1 then
    raise exception 'a3_job_not_found'; end if;
end $$;
grant paper_cron_owner to postgres with inherit false, set true;
grant usage on schema cron to paper_cron_owner;
grant execute on function cron.alter_job(bigint, text, text, text, text, boolean) to paper_cron_owner;
set local role paper_cron_owner;
select cron.alter_job(jobid, active := false) from cron.job where jobname = 'paper_tick' and username = 'paper_cron_owner';
reset role;
revoke execute on function cron.alter_job(bigint, text, text, text, text, boolean) from paper_cron_owner;
revoke usage on schema cron from paper_cron_owner;
revoke paper_cron_owner from postgres;
alter role paper_cron_owner nologin;
do $$ begin
  if (select count(*) from cron.job where jobname = 'paper_tick' and username = 'paper_cron_owner' and not active) <> 1 then
    raise exception 'a3_job_not_paused'; end if;
  if has_schema_privilege('paper_cron_owner', 'cron', 'USAGE') then raise exception 'a3_cron_usage_left'; end if;
  if has_function_privilege('paper_cron_owner', 'cron.alter_job(bigint,text,text,text,text,boolean)', 'EXECUTE') then
    raise exception 'a3_alter_job_left'; end if;
  if (select rolcanlogin from pg_roles where rolname = 'paper_cron_owner') then raise exception 'a3_login_left'; end if;
  if exists(select from pg_auth_members where roleid = 'paper_cron_owner'::regrole
            and member = 'postgres'::regrole and (set_option or inherit_option)) then
    raise exception 'a3_membership_left'; end if;
end $$;
commit;
